"""End-to-end tests for agentic-eng-score scripts on a synthetic ~/.claude tree.

  python3 -m unittest discover -s tests -v
"""
import io
import json
import os
import sqlite3
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout

SCRIPTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "agentic-eng-score", "scripts")
sys.path.insert(0, SCRIPTS)

import aggregate  # noqa: E402
import collect  # noqa: E402
import context_audit  # noqa: E402
import sources  # noqa: E402


def user(content, **extra):
    return dict({"type": "user", "message": {"role": "user", "content": content}}, **extra)


def tool_use(msg_id, name, inp):
    return {"type": "assistant", "message": {"id": msg_id, "model": "claude-test", "role": "assistant",
                                             "content": [{"type": "tool_use", "id": "t-" + msg_id + name,
                                                          "name": name, "input": inp}]}}


def text_reply(msg_id, text):
    return {"type": "assistant", "message": {"id": msg_id, "model": "claude-test", "role": "assistant",
                                             "content": [{"type": "text", "text": text}]}}


def write_session(path, entries, cwd, entrypoint="cli", session_id=None, uuid_prefix=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    sid = session_id or os.path.splitext(os.path.basename(path))[0]
    with open(path, "w", encoding="utf-8") as fh:
        for i, e in enumerate(entries):
            e = dict(e, cwd=cwd, entrypoint=entrypoint, sessionId=sid, uuid="{}-{}".format(uuid_prefix or sid, i),
                     timestamp="2026-11-18T0{}:00:00.000Z".format(min(i, 9)))
            fh.write(json.dumps(e, ensure_ascii=False) + "\n")
        fh.write('{"type": "user", "message": {"content": "half-writ')  # truncated last line


MAIN_SESSION = [
    user("Add retry with exponential backoff to fetch(); keep it idempotent"),
    user([{"type": "text", "text": "<system-reminder>ignore me</system-reminder>Then run the tests"}]),
    user('<pasted_content id="1">Traceback: boom</pasted_content> why does this fail?'),
    user("<command-message>review is running…</command-message>\n<command-name>/review</command-name>\n"
         "<command-args>PR 12</command-args>"),
    user("<bash-input>ls</bash-input>"),
    user("[Request interrupted by user]"),
    user("📣 Bot alert: deploy finished"),
    user("<task-notification>done</task-notification>"),
    user("skill body injected", isMeta=True),
    user("summary of earlier turns", isCompactSummary=True),
    user([{"type": "tool_result", "tool_use_id": "t1", "content": "exit 1", "is_error": True}]),
    tool_use("m1", "Bash", {"command": "pytest -q"}),
    tool_use("m1", "Edit", {"file_path": "/repo/fetch.py"}),
    text_reply("m2", "done"),
    tool_use("m3", "Bash", {"command": "git commit -m 'retry'"}),
    tool_use("m4", "Agent", {"subagent_type": "Explore", "model": "haiku", "description": "find callers"}),
    tool_use("m5", "Skill", {"skill": "foo"}),
    tool_use("m6", "mcp__srv__tool", {"q": 1}),
]


class PipelineTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = self.tmp.name
        self.claude = os.path.join(root, ".claude")
        self.repo = os.path.join(root, "repo")
        self.out = os.path.join(root, "out")
        os.makedirs(self.repo)
        with open(os.path.join(self.repo, "CLAUDE.md"), "w") as fh:
            fh.write("rules\n")
        proj = os.path.join(self.claude, "projects", "-repo")
        write_session(os.path.join(proj, "main.jsonl"), MAIN_SESSION, self.repo)
        write_session(os.path.join(proj, "headless.jsonl"), [user("a"), user("b"), user("c")], self.repo, "sdk-cli")
        write_session(os.path.join(proj, "short.jsonl"), [user("only one")], self.repo)
        write_session(os.path.join(proj, "main", "subagents", "agent-1.jsonl"), [user("a"), user("b"), user("c")], self.repo)
        with open(os.path.join(self.claude, "CLAUDE.md"), "w") as fh:
            fh.write("".join("rule {}\n".format(i) for i in range(31)))
        os.makedirs(os.path.join(self.claude, "skills", "s1"))
        open(os.path.join(self.claude, "skills", "s1", "SKILL.md"), "w").close()
        with open(os.path.join(self.claude, "settings.json"), "w") as fh:
            json.dump({"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "x"}]}]},
                       "permissions": {"allow": ["Bash(ls:*)", "Bash(git:*)", "Read", "Edit", "Write"]}}, fh)
        os.makedirs(os.path.join(proj, "memory"))
        open(os.path.join(proj, "memory", "fact.md"), "w").close()
        open(os.path.join(proj, "memory", "MEMORY.md"), "w").close()
        self.claude_json = os.path.join(root, ".claude.json")
        with open(self.claude_json, "w") as fh:
            json.dump({"mcpServers": {"a": {}, "b": {"env": {"TOKEN": "s3cr3t-value"}}}}, fh)

    def tearDown(self):
        self.tmp.cleanup()

    def run_collect(self):
        with redirect_stdout(io.StringIO()):
            rc = collect.main(["--claude-dir", self.claude, "--source", "claude",
                               "--exclude-prefix", "📣 Bot", "--out", self.out])
        self.assertEqual(rc, 0)
        with open(os.path.join(self.out, "manifest.json")) as fh:
            return json.load(fh)

    def test_collect_keeps_only_human_led_main_sessions(self):
        m = self.run_collect()
        self.assertEqual(m["counts"]["files"], 3)  # subagent transcript is not globbed
        self.assertEqual(m["counts"]["skipped"], {"headless": 1, "few_human_turns": 1})
        self.assertEqual([s["id"] for s in m["sessions"]], ["main"])

    def test_collect_drops_copies_of_the_same_conversation(self):
        proj = os.path.join(self.claude, "projects", "-repo")
        # sync-conflict copy: same sessionId, fewer turns
        write_session(os.path.join(proj, "main.sync-conflict-1.jsonl"), MAIN_SESSION[:3], self.repo, session_id="main")
        # fork/resume copy: new sessionId, same human-turn uuids
        write_session(os.path.join(proj, "fork.jsonl"), MAIN_SESSION, self.repo, uuid_prefix="main")
        m = self.run_collect()
        self.assertEqual(m["counts"]["skipped"]["duplicate_copy"], 2)
        # main and fork tie on human turns; the id order keeps "fork" and drops "main" as the overlap
        self.assertEqual([s["id"] for s in m["sessions"]], ["fork"])

    def test_collect_classifies_messages_and_counts_tools(self):
        s = self.run_collect()["sessions"][0]
        self.assertEqual(s["human_turns"], 4)  # 3 prompts + slash command with args
        self.assertEqual(s["automation_msgs"], 1)
        self.assertEqual(s["interrupts"], 1)
        self.assertEqual(s["bang_cmds"], 1)
        self.assertEqual(s["compactions"], 1)
        self.assertEqual(s["tool_errors"], 1)
        self.assertEqual(s["slash_cmds"], {"/review": 1})
        self.assertEqual(s["tool_calls"], 6)
        self.assertEqual((s["test_runs"], s["commits"], s["edit_calls"]), (1, 1, 1))
        self.assertEqual((s["subagent_calls"], s["skill_calls"], s["mcp_calls"]), (1, 1, 1))
        self.assertEqual(s["mcp_servers"], ["srv"])
        self.assertEqual(s["parallel_batches"], 1)
        # all 6 tool calls follow the excluded bot message, so none count toward a human prompt
        self.assertEqual((s["human_tool_calls"], s["actions_per_prompt"]), (0, 0.0))

    def test_transcript_shows_human_text_without_injections(self):
        self.run_collect()
        with open(os.path.join(self.out, "transcripts", "main.txt")) as fh:
            t = fh.read()
        self.assertIn("[U1] Add retry with exponential backoff", t)
        self.assertIn("[U2] Then run the tests", t)
        self.assertIn("[PASTED 15 chars] Traceback: boom", t)
        self.assertIn("[/] /review PR 12", t)
        self.assertIn("[T] Agent: [Explore model=haiku] find callers", t)
        for leaked in ("ignore me", "📣 Bot", "<task-notification>", "skill body injected"):
            self.assertNotIn(leaked, t)

    def test_clip_keeps_both_ends(self):
        clipped = collect.clip("A" * 50 + "B" * 50, 30)
        self.assertTrue(clipped.startswith("A" * 20) and clipped.endswith("B" * 10))
        self.assertIn("70 chars cut", clipped)

    def test_context_audit_counts_names_not_values(self):
        self.run_collect()
        with redirect_stdout(io.StringIO()):
            context_audit.main([self.out, "--claude-dir", self.claude, "--claude-json", self.claude_json,
                                "--home", self.tmp.name])
        with open(os.path.join(self.out, "context_audit.json")) as fh:
            raw = fh.read()
        self.assertNotIn("s3cr3t-value", raw)
        audit = json.loads(raw)
        g = audit["global"]
        self.assertEqual((g["claude_md_lines"], g["skills"], g["memory_files"]), (31, 1, 1))
        self.assertEqual(g["settings"]["hooks"], 1)
        self.assertEqual(g["mcp_servers"], ["a", "b"])
        self.assertEqual(audit["projects"][0]["instructions"], {"CLAUDE.md": 1})

    def test_aggregate_scores_three_axes(self):
        self.run_collect()
        with redirect_stdout(io.StringIO()):
            context_audit.main([self.out, "--claude-dir", self.claude, "--claude-json", self.claude_json,
                                "--home", self.tmp.name])
        score = {
            "session_id": "main", "work_mode": "Build", "expertise": 4,
            "prompt": {"instructions": 4, "misses": [{"turn": "U2", "kind": "clarify", "evidence": "Then run the tests"}],
                       "clarity": 8, "specificity": 6, "actionability": 7, "scope": 7, "evidence": "U1"},
            "context": {"score": 3, "evidence": "U2"}, "agentic": {"score": 4, "evidence": "T"},
            "planning_by_user": "most", "execution_by_user": "few",
            "outcome": "Succeeded", "hard_signal": True, "hard_signal_evidence": "git commit -m 'retry'",
            "strength": "s", "gap": "g",
        }
        with open(os.path.join(self.out, "scores", "main.json"), "w") as fh:
            json.dump(score, fh)
        with redirect_stdout(io.StringIO()):
            rc = aggregate.main([self.out])  # no .b.json yet: single pass
        self.assertEqual(rc, 0)
        with open(os.path.join(self.out, "summary.json")) as fh:
            summary = json.load(fh)
        self.assertEqual(summary["prompt"]["score"], 75.0)  # 3 of 4 instructions landed
        self.assertEqual(summary["prompt"]["form_score"], 70.0)
        # setup: everything but skills_many(5), agents(5), commands_plugins(5) = 85.
        # mcp_many passes on 2 configured (a, b) + 1 called in the session (srv).
        self.assertEqual(summary["context"]["setup_points"], 85)
        self.assertEqual(summary["context"]["score"], 67.5)  # 0.5*50 + 0.5*85
        self.assertEqual(summary["agentic"]["score"], 80.0)  # 0.8*75 + 0.2*100
        self.assertEqual(summary["agentic"]["level"], "L4")

        # Second pass disagrees: other missed turn, lower scores, no hard signal.
        second = json.loads(json.dumps(score))
        second["prompt"]["misses"] = [{"turn": "U1", "kind": "misread", "evidence": "Add retry with exponential"}]
        second["context"]["score"], second["agentic"]["score"] = 5, 3
        second["outcome"], second["hard_signal"], second["hard_signal_evidence"] = "Partial", False, ""
        with open(os.path.join(self.out, "scores", "main.b.json"), "w") as fh:
            json.dump(second, fh)
        with redirect_stdout(io.StringIO()):
            rc = aggregate.main([self.out])
        self.assertEqual(rc, 0)
        with open(os.path.join(self.out, "summary.json")) as fh:
            both = json.load(fh)
        self.assertEqual(both["prompt"]["score"], 100.0)  # no turn flagged by both passes
        self.assertEqual(both["spread"]["prompt"], [75.0, 75.0])
        self.assertEqual(both["context"]["session_mean"], 4.0)
        self.assertEqual(both["agentic"]["score"], 50.0)  # 0.8*62.5 + 0.2*0: Partial wins, no hard signal
        with open(os.path.join(self.out, "report.md")) as fh:
            report = fh.read()
        self.assertIn("| Prompt Engineering | 100.0 (75.0, 75.0) | L5 | 지시 4개 중 4개가 한 번에 통함 · 되물음 0 · 오해 0 |", report)
        self.assertNotIn("U2 되물음", report)  # flagged by one pass only

    def test_aggregate_rejects_quotes_missing_from_transcript(self):
        transcript = '[U1] Add retry with exponential backoff to fetch()\n'
        good = {"prompt": {"evidence": 'U1: "Add retry with exponential backoff…"'}}
        bad = {"context": {"evidence": 'U2: "please make it idempotent and fast"'}}
        self.assertEqual(aggregate.unverified_quotes(good, transcript), [])
        self.assertEqual(aggregate.unverified_quotes(bad, transcript), ["please make it idempotent and fast"])
        tagged = {"hard_signal": True, "hard_signal_evidence": 'U1: "Add retry with exponential backoff"'}
        self.assertEqual(aggregate.unverified_quotes(tagged, transcript), [])
        fake_signal = {"hard_signal": True, "hard_signal_evidence": "all 42 tests passed"}
        self.assertEqual(aggregate.unverified_quotes(fake_signal, transcript), ["all 42 tests passed"])
        fake_miss = {"prompt": {"misses": [{"turn": "U1", "kind": "misread", "evidence": "rewrite everything in Rust"}]}}
        self.assertEqual(aggregate.unverified_quotes(fake_miss, transcript), ["rewrite everything in Rust"])

    def test_aggregate_flags_invalid_scores(self):
        self.run_collect()
        with redirect_stdout(io.StringIO()):
            context_audit.main([self.out, "--claude-dir", self.claude, "--claude-json", self.claude_json,
                                "--home", self.tmp.name])
        with open(os.path.join(self.out, "scores", "main.json"), "w") as fh:
            json.dump({"session_id": "main", "expertise": 6, "hard_signal": "yes"}, fh)
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = aggregate.main([self.out])
        self.assertEqual(rc, 2)
        self.assertIn("needs rescoring: main.json", buf.getvalue())
        self.assertNotIn("main.b.json", buf.getvalue())
        buf = io.StringIO()
        with redirect_stdout(buf):
            aggregate.main([self.out, "--passes", "2"])
        self.assertIn("needs rescoring: main.b.json (missing)", buf.getvalue())
        errs = aggregate.validate({"session_id": "main", "expertise": 6, "hard_signal": "yes"}, "main")
        self.assertIn("expertise", errs)
        self.assertIn("hard_signal", errs)


NOW_MS = int(time.time() * 1000)


def codex_line(kind, payload):
    return json.dumps({"timestamp": "2026-11-18T01:00:00.000Z", "type": kind, "payload": payload}, ensure_ascii=False)


class OtherToolsTest(unittest.TestCase):
    """Readers for Codex CLI, opencode and Devin CLI on synthetic stores."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        self.saved = {k: os.environ.get(k) for k in ("CODEX_HOME", "XDG_DATA_HOME", "OPENCODE_DB", "DEVIN_SESSIONS_DIR")}
        os.environ["CODEX_HOME"] = os.path.join(self.root, "codex")
        os.environ["XDG_DATA_HOME"] = os.path.join(self.root, "share")
        os.environ["DEVIN_SESSIONS_DIR"] = os.path.join(self.root, "devin")
        os.environ.pop("OPENCODE_DB", None)

    def tearDown(self):
        for k, v in self.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()

    def write_codex(self, name, meta, body):
        day = os.path.join(self.root, "codex", "sessions", "2026", "11", "18")
        os.makedirs(day, exist_ok=True)
        with open(os.path.join(day, name), "w", encoding="utf-8") as fh:
            fh.write("\n".join([codex_line("session_meta", meta)] + body) + "\n")

    def test_codex_reads_typed_prompts_and_tool_calls(self):
        user_item = lambda text: codex_line("event_msg", {"type": "item_completed", "item": {  # noqa: E731
            "type": "UserMessage", "content": [{"type": "text", "text": text}]}})
        self.write_codex("rollout-a.jsonl", {"id": "abc", "cwd": "/repo", "originator": "codex-tui", "source": "cli"}, [
            codex_line("turn_context", {"model": "gpt-test", "cwd": "/repo"}),
            codex_line("response_item", {"type": "message", "role": "user",
                                         "content": [{"type": "input_text", "text": "# AGENTS.md instructions"}]}),
            user_item("Fix the flaky retry test"),
            codex_line("response_item", {"type": "function_call", "name": "shell",
                                         "arguments": json.dumps({"command": ["pytest", "-q"]})}),
            codex_line("response_item", {"type": "custom_tool_call", "name": "apply_patch",
                                         "input": "*** Begin Patch\n*** Update File: a.py"}),
            codex_line("response_item", {"type": "message", "role": "assistant",
                                         "content": [{"type": "output_text", "text": "Fixed."}]}),
            user_item("📣 Bot ping"),
            codex_line("response_item", {"type": "function_call", "name": "shell",
                                         "arguments": json.dumps({"command": ["git", "commit", "-m", "x"]})}),
        ])
        self.write_codex("rollout-b.jsonl", {"id": "sdk1", "cwd": "/repo", "originator": "codex_sdk_py", "source": "exec"},
                         [user_item("headless run")])
        refs = sources.codex_refs(30)
        self.assertEqual(len(refs), 2)
        parsed = {s["id"]: (s, lines) for s, lines in (sources.parse_codex(r, ["📣 Bot"]) for r in refs)}
        s, lines = parsed["codex-abc"]
        self.assertEqual((s["source"], s["cwd"], s["headless"]), ("codex", "/repo", False))
        self.assertEqual((s["human_turns"], s["automation_msgs"]), (1, 1))
        self.assertEqual((s["tool_calls"], s["human_tool_calls"], s["test_runs"], s["commits"], s["edit_calls"]),
                         (3, 2, 1, 1, 1))
        self.assertEqual(lines[0], ("U", "Fix the flaky retry test"))
        self.assertIn(("A", "Fixed."), lines)
        self.assertNotIn("AGENTS.md", json.dumps(lines))
        self.assertTrue(parsed["codex-sdk1"][0]["headless"])

    def test_codex_older_format_uses_user_message_event(self):
        self.write_codex("rollout-old.jsonl", {"id": "old", "cwd": "/repo", "originator": "codex-tui"}, [
            codex_line("event_msg", {"type": "user_message", "message": "Add a healthcheck endpoint"}),
            codex_line("response_item", {"type": "local_shell_call", "action": {"command": ["npm", "test"]}}),
        ])
        s, lines = sources.parse_codex(sources.codex_refs(30)[0], [])
        self.assertEqual((s["human_turns"], s["tool_calls"], s["test_runs"]), (1, 1, 1))
        self.assertEqual(lines[0], ("U", "Add a healthcheck endpoint"))
        self.assertFalse(s["headless"])

    def test_codex_skips_history_imported_from_another_agent(self):
        self.write_codex("rollout-imp.jsonl", {"id": "imp", "cwd": "/repo", "originator": "Codex Desktop"}, [
            codex_line("event_msg", {"type": "user_message", "message": "what theme is this?"}),
            codex_line("response_item", {"type": "message", "role": "assistant", "content": [
                {"type": "output_text", "text": "[external_agent_tool_call: Bash] ls"}]}),
        ])
        s, _ = sources.parse_codex(sources.codex_refs(30)[0], [])
        self.assertEqual((s["headless"], s["entrypoint"]), (True, "imported"))

    def test_opencode_reads_sqlite_store(self):
        os.makedirs(os.path.join(self.root, "share", "opencode"))
        conn = sqlite3.connect(sources.opencode_db())
        conn.executescript("""
            create table session (id text primary key, directory text, parent_id text,
                                  time_created integer, time_updated integer);
            create table message (id text primary key, session_id text, time_created integer, data text);
            create table part (id text primary key, message_id text, session_id text, data text);
        """)
        conn.execute("insert into session values ('s1', '/repo', null, ?, ?)", (NOW_MS - 60000, NOW_MS))
        conn.execute("insert into session values ('s2', '/repo', 's1', ?, ?)", (NOW_MS - 30000, NOW_MS))
        conn.execute("insert into session values ('old', '/repo', null, 1, 2)")
        rows = [
            ("m1", {"role": "user"}, [{"type": "text", "text": "Add pagination to /orders"},
                                      {"type": "text", "text": "injected context", "synthetic": True}]),
            ("m2", {"role": "assistant", "modelID": "model-x"}, [
                {"type": "text", "text": "On it."},
                {"type": "tool", "tool": "bash", "state": {"status": "completed", "input": {"command": "pytest -q"}}},
                {"type": "tool", "tool": "edit", "state": {"status": "error", "error": "no match",
                                                           "input": {"filePath": "/repo/orders.py"}}},
                {"type": "tool", "tool": "task", "state": {"status": "completed",
                                                           "input": {"subagent_type": "explore", "description": "scan"}}},
            ]),
        ]
        for i, (mid, data, parts) in enumerate(rows):
            conn.execute("insert into message values (?, 's1', ?, ?)", (mid, NOW_MS + i, json.dumps(data)))
            for j, part in enumerate(parts):
                conn.execute("insert into part values (?, ?, 's1', ?)", ("{}p{}".format(mid, j), mid, json.dumps(part)))
        conn.commit()
        conn.close()
        refs = sources.opencode_refs(30)
        self.assertEqual([r["session"] for r in refs], ["s1", "s2"])
        s, lines = sources.parse_opencode(refs[0], [])
        self.assertEqual((s["id"], s["cwd"], s["headless"]), ("opencode-s1", "/repo", False))
        self.assertEqual((s["human_turns"], s["tool_calls"], s["test_runs"], s["edit_calls"], s["subagent_calls"],
                          s["tool_errors"]), (1, 3, 1, 1, 1, 1))
        self.assertEqual(lines[0], ("U", "Add pagination to /orders"))
        self.assertIn(("T", "task: [explore] scan"), lines)
        self.assertEqual(sorted(s["models"]), ["model-x"])
        self.assertTrue(sources.parse_opencode(refs[1], [])[0]["headless"])

    def test_devin_reads_prompts_and_tool_calls(self):
        os.makedirs(os.path.join(self.root, "devin"))
        conn = sqlite3.connect(os.path.join(self.root, "devin", "sessions.db"))
        conn.executescript("""
            create table sessions (id text primary key, working_directory text, model text, created_at integer,
                                   last_activity_at integer, hidden integer default 0);
            create table prompt_history (id integer primary key autoincrement, content text, timestamp integer,
                                         session_id text, is_shell integer default 0);
            create table message_nodes (row_id integer primary key autoincrement, session_id text, node_id integer,
                                        parent_node_id integer, chat_message text, created_at integer);
            create table tool_call_state (session_id text, tool_call_id text, tool_call_json text,
                                          tool_call_update_json text);
        """)
        conn.execute("insert into sessions values ('d1', '/repo', 'model-d', ?, ?, 0)", (NOW_MS - 5000, NOW_MS))
        conn.execute("insert into sessions values ('gone', '/repo', 'model-d', ?, ?, 1)", (NOW_MS, NOW_MS))
        conn.execute("insert into prompt_history (content, timestamp, session_id, is_shell) values (?, ?, 'd1', 0)",
                     ("Rename the config loader", NOW_MS - 4000))
        conn.execute("insert into prompt_history (content, timestamp, session_id, is_shell) values (?, ?, 'd1', 1)",
                     ("ls", NOW_MS - 3000))
        conn.execute("insert into message_nodes (session_id, node_id, chat_message, created_at) values ('d1', 1, ?, ?)",
                     (json.dumps({"role": "assistant", "content": "Renamed."}), NOW_MS - 3500))
        conn.execute("insert into message_nodes (session_id, node_id, chat_message, created_at) values ('d1', 2, ?, ?)",
                     ("not json at all", NOW_MS - 3400))
        conn.execute("insert into tool_call_state values ('d1', 't1', ?, ?)",
                     (json.dumps({"kind": "execute", "title": "run tests", "rawInput": {"command": "cargo test"}}),
                      json.dumps({"status": "completed"})))
        conn.execute("insert into tool_call_state values ('d1', 't2', null, ?)", (json.dumps({"status": "failed"}),))
        conn.commit()
        conn.close()
        refs = sources.devin_refs(30)
        self.assertEqual([r["session"] for r in refs], ["d1"])  # hidden session skipped
        s, lines = sources.parse_devin(refs[0], [])
        self.assertEqual((s["id"], s["cwd"], sorted(s["models"])), ("devin-d1", "/repo", ["model-d"]))
        self.assertEqual((s["human_turns"], s["bang_cmds"], s["tool_calls"], s["test_runs"], s["tool_errors"]),
                         (1, 1, 2, 1, 1))
        self.assertEqual(lines[:3], [("U", "Rename the config loader"), ("A", "Renamed."), ("!", "ls")])

    def test_collect_mixes_tools_and_labels_them(self):
        user_item = lambda text: codex_line("event_msg", {"type": "item_completed", "item": {  # noqa: E731
            "type": "UserMessage", "content": [{"type": "text", "text": text}]}})
        self.write_codex("rollout-c.jsonl", {"id": "mix", "cwd": "/repo", "originator": "codex-tui"},
                         [user_item("one"), user_item("two"), user_item("three")])
        out = os.path.join(self.root, "out")
        with redirect_stdout(io.StringIO()):
            rc = collect.main(["--claude-dir", os.path.join(self.root, "none"), "--out", out])
        self.assertEqual(rc, 0)
        with open(os.path.join(out, "manifest.json")) as fh:
            manifest = json.load(fh)
        self.assertEqual(manifest["counts"]["picked_by_tool"], {"codex": 1})
        with open(os.path.join(out, "transcripts", "codex-mix.txt")) as fh:
            self.assertIn("tool: codex", fh.read())


if __name__ == "__main__":
    unittest.main()
