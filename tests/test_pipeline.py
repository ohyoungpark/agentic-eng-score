"""End-to-end tests for agentic-eng-score scripts on a synthetic ~/.claude tree.

  python3 -m unittest discover -s tests -v
"""
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

SCRIPTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "agentic-eng-score", "scripts")
sys.path.insert(0, SCRIPTS)

import aggregate  # noqa: E402
import collect  # noqa: E402
import context_audit  # noqa: E402


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
            rc = collect.main(["--claude-dir", self.claude, "--exclude-prefix", "📣 Bot", "--out", self.out])
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
        self.assertEqual(s["actions_per_prompt"], 1.5)

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
            context_audit.main([self.out, "--claude-dir", self.claude, "--claude-json", self.claude_json])
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
            context_audit.main([self.out, "--claude-dir", self.claude, "--claude-json", self.claude_json])
        score = {
            "session_id": "main", "work_mode": "Build", "expertise": 4,
            "prompt": {"clarity": 8, "specificity": 6, "actionability": 7, "scope": 7, "evidence": "U1"},
            "context": {"score": 3, "evidence": "U2"}, "agentic": {"score": 4, "evidence": "T"},
            "planning_by_user": "most", "execution_by_user": "few",
            "outcome": "Succeeded", "hard_signal": True, "strength": "s", "gap": "g",
        }
        with open(os.path.join(self.out, "scores", "main.json"), "w") as fh:
            json.dump(score, fh)
        with redirect_stdout(io.StringIO()):
            rc = aggregate.main([self.out])
        self.assertEqual(rc, 0)
        with open(os.path.join(self.out, "summary.json")) as fh:
            summary = json.load(fh)
        self.assertEqual(summary["prompt"]["score"], 70.0)
        # setup: everything but skills_many(5), agents(5), commands_plugins(5) = 85.
        # mcp_many passes on 2 configured (a, b) + 1 called in the session (srv).
        self.assertEqual(summary["context"]["setup_points"], 85)
        self.assertEqual(summary["context"]["score"], 67.5)  # 0.5*50 + 0.5*85
        self.assertEqual(summary["agentic"]["score"], 69.4)  # 0.7*75 + 0.15*100 + 0.15*12.5
        self.assertEqual(summary["agentic"]["level"], "L3")
        with open(os.path.join(self.out, "report.md")) as fh:
            self.assertIn("| Prompt Engineering | 70.0 | L3 |", fh.read())

    def test_aggregate_rejects_quotes_missing_from_transcript(self):
        transcript = '[U1] Add retry with exponential backoff to fetch()\n'
        good = {"prompt": {"evidence": 'U1: "Add retry with exponential backoff…"'}}
        bad = {"context": {"evidence": 'U2: "please make it idempotent and fast"'}}
        self.assertEqual(aggregate.unverified_quotes(good, transcript), [])
        self.assertEqual(aggregate.unverified_quotes(bad, transcript), ["please make it idempotent and fast"])

    def test_aggregate_flags_invalid_scores(self):
        self.run_collect()
        with redirect_stdout(io.StringIO()):
            context_audit.main([self.out, "--claude-dir", self.claude, "--claude-json", self.claude_json])
        with open(os.path.join(self.out, "scores", "main.json"), "w") as fh:
            json.dump({"session_id": "main", "expertise": 6, "hard_signal": "yes"}, fh)
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = aggregate.main([self.out])
        self.assertEqual(rc, 2)
        self.assertIn("needs rescoring: main", buf.getvalue())
        errs = aggregate.validate({"session_id": "main", "expertise": 6, "hard_signal": "yes"}, "main")
        self.assertIn("expertise", errs)
        self.assertIn("hard_signal", errs)


if __name__ == "__main__":
    unittest.main()
