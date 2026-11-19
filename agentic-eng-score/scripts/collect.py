#!/usr/bin/env python3
"""Collect human-led Claude Code sessions and condense them for scoring.

Reads main session logs at <claude-dir>/projects/*/*.jsonl. Subagent
transcripts live in deeper subfolders and are skipped. Standard library only.

  python3 collect.py --survey            # list repeated message prefixes
  python3 collect.py --exclude-prefix "📣 Bot" --sample 20
"""
import argparse
import collections
import datetime
import glob
import json
import os
import random
import re
import sys

# Messages Claude Code itself writes into the user role. They are never human prompts.
MACHINE_PREFIXES = (
    "<task-notification",
    "<command-message>",
    "<command-args>",
    "<local-command-stdout",
    "<local-command-stderr",
    "<local-command-caveat",
    "Caveat: The messages below were generated",
    "<bash-stdout",
    "<bash-stderr",
    "<user-prompt-submit-hook",
    "This session is being continued from a previous conversation",
)
REMINDER_RE = re.compile(r"<system-reminder>.*?</system-reminder>", re.S)
PASTE_RE = re.compile(r"<pasted_content[^>]*>(.*?)</pasted_content[^>]*>", re.S)
SLASH_NAME_RE = re.compile(r"<command-name>\s*/?([^<\s]+)\s*</command-name>")
SLASH_ARGS_RE = re.compile(r"<command-args>(.*?)</command-args>", re.S)
BANG_RE = re.compile(r"<bash-input>(.*?)</bash-input>", re.S)
TEST_RE = re.compile(
    r"\b(pytest|unittest|npm (run )?test|pnpm (run )?test|yarn test|jest|vitest|go test|"
    r"cargo test|make test|rspec|phpunit|gradlew? \S*test|mvn \S*test|xcodebuild test|"
    r"tsc|mypy|pyright|ruff|eslint|flake8|pre-commit|playwright test)\b"
)
COMMIT_RE = re.compile(r"\bgit (commit|push)\b|\bgh pr (create|merge)\b")
SUBAGENT_TOOLS = {"Task", "Agent"}
EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
PLAN_TOOLS = {"EnterPlanMode", "ExitPlanMode"}
TODO_TOOLS = {"TodoWrite", "TaskCreate", "TaskUpdate"}

FULL_LIMITS = {"U": 3000, "A": 600, "T": 160, "E": 200, "!": 200, "/": 300}
TIGHT_LIMITS = {"U": 2000, "A": 200, "T": 90, "E": 100, "!": 120, "/": 200}
TRANSCRIPT_CAP = 30000


def default_claude_dir():
    return os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")


def read_entries(path):
    """Yield JSON objects from a JSONL file, skipping blank or half-written lines."""
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if line.startswith("{") and line.endswith("}"):
                yield json.loads(line)


def clip(text, limit):
    """Middle-truncate so both the opening and the ending survive."""
    text = text.strip()
    if len(text) <= limit:
        return text
    head = limit * 2 // 3
    tail = limit - head
    return "{} …[{} chars cut]… {}".format(text[:head], len(text) - limit, text[-tail:])


def block_text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(b.get("text", "") for b in content if isinstance(b, dict))
    return ""


def classify_user(content, exclude_prefixes):
    """Return (kind, text, probe) for one user-role message.

    kind: human | slash | bang | interrupt | automation | machine | tool_result
    probe: the text used for prefix matching (what --survey reports).
    """
    if isinstance(content, list):
        if any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
            return "tool_result", "", ""
        parts = []
        for b in content:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "text":
                parts.append(b.get("text", ""))
            elif b.get("type") == "image":
                parts.append("[image]")
        text = "\n".join(parts)
    elif isinstance(content, str):
        text = content
    else:
        return "machine", "", ""

    text = REMINDER_RE.sub("", text).strip()
    if not text:
        return "machine", "", ""
    name = SLASH_NAME_RE.search(text)
    if name:
        args = SLASH_ARGS_RE.search(text)
        arg_text = args.group(1).strip() if args else ""
        return "slash", "/" + name.group(1) + (" " + arg_text if arg_text else ""), ""
    if text.startswith("[Request interrupted"):
        return "interrupt", "", ""
    if text.startswith("<bash-input>"):
        bang = BANG_RE.search(text)
        return "bang", bang.group(1).strip() if bang else text, ""
    if text.startswith(MACHINE_PREFIXES):
        return "machine", "", ""

    pastes = PASTE_RE.findall(text)
    outside = PASTE_RE.sub("", text).strip()
    probe = (outside or (pastes[0].strip() if pastes else "")).lstrip()
    if any(probe.startswith(p) for p in exclude_prefixes):
        return "automation", "", probe
    if pastes:
        pasted = sum(len(p) for p in pastes)
        preview = clip(pastes[0], 400)
        text = (outside + "\n" if outside else "") + "[PASTED {} chars] {}".format(pasted, preview)
    return "human", text, probe


def summarize_tool(name, inp):
    if not isinstance(inp, dict):
        return name
    if name == "Bash":
        return "Bash: " + str(inp.get("command", ""))
    if name in EDIT_TOOLS or name == "Read":
        return "{}: {}".format(name, inp.get("file_path") or inp.get("notebook_path") or "")
    if name in ("Grep", "Glob"):
        return "{}: {}".format(name, inp.get("pattern", ""))
    if name in SUBAGENT_TOOLS:
        return "{}: [{} model={}] {}".format(
            name, inp.get("subagent_type", "-"), inp.get("model", "-"), inp.get("description", "")
        )
    if name == "Skill":
        return "Skill: {} {}".format(inp.get("skill", ""), inp.get("args", "") or "")
    if name == "WebSearch":
        return "WebSearch: " + str(inp.get("query", ""))
    if name == "WebFetch":
        return "WebFetch: " + str(inp.get("url", ""))
    return "{}: {}".format(name, json.dumps(inp, ensure_ascii=False)[:150])


def new_stats(path):
    return {
        "id": os.path.splitext(os.path.basename(path))[0],
        "path": path,
        "session_key": None,
        "human_uuids": [],
        "project_dir": os.path.basename(os.path.dirname(path)),
        "cwd": None,
        "branch": None,
        "entrypoint": None,
        "start": None,
        "end": None,
        "models": set(),
        "human_turns": 0,
        "human_chars": 0,
        "pasted_chars": 0,
        "automation_msgs": 0,
        "interrupts": 0,
        "bang_cmds": 0,
        "slash_cmds": collections.Counter(),
        "compactions": 0,
        "tool_calls": 0,
        "tools": collections.Counter(),
        "tool_errors": 0,
        "edit_calls": 0,
        "subagent_calls": 0,
        "skill_calls": 0,
        "mcp_calls": 0,
        "mcp_servers": set(),
        "parallel_batches": 0,
        "test_runs": 0,
        "commits": 0,
        "plan_mode_calls": 0,
        "todo_calls": 0,
    }


def parse_session(path, exclude_prefixes, probes=None):
    """Parse one session. Returns (stats, lines). lines are (tag, text) pairs."""
    s = new_stats(path)
    lines = []
    tool_uses_per_message = collections.Counter()
    for e in read_entries(path):
        kind_of_entry = e.get("type")
        if kind_of_entry not in ("user", "assistant") or e.get("isSidechain"):
            continue
        ts = e.get("timestamp")
        if ts:
            s["start"] = s["start"] or ts
            s["end"] = ts
        s["session_key"] = s["session_key"] or e.get("sessionId")
        s["cwd"] = s["cwd"] or e.get("cwd")
        s["branch"] = s["branch"] or e.get("gitBranch")
        s["entrypoint"] = s["entrypoint"] or e.get("entrypoint")
        message = e.get("message") or {}

        if kind_of_entry == "user":
            if e.get("isCompactSummary"):
                s["compactions"] += 1
                continue
            if e.get("isMeta"):
                continue
            content = message.get("content")
            kind, text, probe = classify_user(content, exclude_prefixes)
            if kind == "tool_result":
                for b in content:
                    if isinstance(b, dict) and b.get("type") == "tool_result" and b.get("is_error") is True:
                        s["tool_errors"] += 1
                        lines.append(("E", block_text(b.get("content"))))
            elif kind == "human":
                s["human_turns"] += 1
                s["human_chars"] += len(text)
                if e.get("uuid"):
                    s["human_uuids"].append(e["uuid"])
                if "[PASTED " in text:
                    s["pasted_chars"] += sum(len(p) for p in PASTE_RE.findall(block_text(content)))
                lines.append(("U", text))
                if probes is not None:
                    probes.append(probe)
            elif kind == "slash":
                s["slash_cmds"][text.split(" ", 1)[0]] += 1
                lines.append(("/", text))
                if " " in text:
                    s["human_turns"] += 1
                    s["human_chars"] += len(text)
            elif kind == "bang":
                s["bang_cmds"] += 1
                lines.append(("!", text))
            elif kind == "interrupt":
                s["interrupts"] += 1
                lines.append(("E", "[user interrupted]"))
            elif kind == "automation":
                s["automation_msgs"] += 1
            continue

        model = message.get("model")
        if model and not model.startswith("<"):
            s["models"].add(model)
        for b in message.get("content") or []:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "text" and b.get("text", "").strip():
                lines.append(("A", b["text"]))
            elif b.get("type") == "tool_use":
                name = b.get("name", "?")
                inp = b.get("input") or {}
                s["tool_calls"] += 1
                s["tools"][name] += 1
                tool_uses_per_message[message.get("id")] += 1
                if name in EDIT_TOOLS:
                    s["edit_calls"] += 1
                if name in SUBAGENT_TOOLS:
                    s["subagent_calls"] += 1
                if name == "Skill":
                    s["skill_calls"] += 1
                if name.startswith("mcp__"):
                    s["mcp_calls"] += 1
                    parts = name.split("__")
                    if len(parts) >= 3:
                        s["mcp_servers"].add(parts[1])
                if name in PLAN_TOOLS:
                    s["plan_mode_calls"] += 1
                if name in TODO_TOOLS:
                    s["todo_calls"] += 1
                if name == "Bash" and isinstance(inp, dict):
                    cmd = str(inp.get("command", ""))
                    if TEST_RE.search(cmd):
                        s["test_runs"] += 1
                    if COMMIT_RE.search(cmd):
                        s["commits"] += 1
                lines.append(("T", summarize_tool(name, inp)))
    s["parallel_batches"] = sum(1 for n in tool_uses_per_message.values() if n >= 2)
    return s, lines


def to_local(ts):
    if not ts:
        return None
    stamp = datetime.datetime.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=datetime.timezone.utc)
    return stamp.astimezone()


def render(s, lines):
    start, end = to_local(s["start"]), to_local(s["end"])
    minutes = int((end - start).total_seconds() // 60) if start and end else 0
    header = (
        "# session {id}\nproject: {proj}\ncwd: {cwd}\nstart: {start}  duration: {mins}m\n"
        "human turns: {ht}  tool calls: {tc}  subagents: {sa}  skills: {sk}  tests: {te}  commits: {co}\n"
        "models: {models}\n\n"
    ).format(
        id=s["id"], proj=s["project_dir"], cwd=s["cwd"],
        start=start.strftime("%Y-%m-%d %H:%M %Z") if start else "-", mins=minutes,
        ht=s["human_turns"], tc=s["tool_calls"], sa=s["subagent_calls"], sk=s["skill_calls"],
        te=s["test_runs"], co=s["commits"], models=", ".join(sorted(s["models"])) or "-",
    )
    body = ""
    for limits in (FULL_LIMITS, TIGHT_LIMITS):
        out = []
        turn = 0
        for tag, text in lines:
            if tag == "U":
                turn += 1
                out.append("[U{}] {}".format(turn, clip(text, limits["U"])))
            else:
                out.append("[{}] {}".format(tag, clip(text.replace("\n", " "), limits[tag])))
        body = "\n".join(out)
        if len(header) + len(body) <= TRANSCRIPT_CAP:
            break
    return header + clip(body, TRANSCRIPT_CAP - len(header))


def dedupe(stats):
    """Drop copies of the same conversation (sync-conflict files, resumed or forked sessions).

    Keeps one file per sessionId (most human turns, then latest end), then drops
    any session whose human turns are mostly already present in a kept one.
    Returns (kept, dropped_count).
    """
    best = {}
    for s in stats:
        key = s["session_key"] or s["id"]
        cur = best.get(key)
        if cur is None or (s["human_turns"], s["end"] or "") > (cur["human_turns"], cur["end"] or ""):
            best[key] = s
    kept, seen = [], set()
    for s in sorted(best.values(), key=lambda x: (-x["human_turns"], x["id"])):
        uuids = set(s["human_uuids"])
        if uuids and len(uuids & seen) * 2 >= len(uuids):
            continue
        seen |= uuids
        kept.append(s)
    return kept, len(stats) - len(kept)


def jsonable(s):
    out = dict(s)
    del out["human_uuids"]
    out["models"] = sorted(s["models"])
    out["mcp_servers"] = sorted(s["mcp_servers"])
    out["tools"] = dict(s["tools"].most_common(20))
    out["slash_cmds"] = dict(s["slash_cmds"])
    out["actions_per_prompt"] = round(s["tool_calls"] / s["human_turns"], 2) if s["human_turns"] else 0.0
    return out


def session_files(projects_dir, days):
    cutoff = datetime.datetime.now().timestamp() - days * 86400
    files = glob.glob(os.path.join(projects_dir, "*", "*.jsonl"))
    return sorted(f for f in files if os.path.getmtime(f) >= cutoff)


def survey(files, exclude_prefixes, top):
    stats, probes_by_session = [], {}
    for f in files:
        probes = []
        s, _ = parse_session(f, exclude_prefixes, probes)
        stats.append(s)
        probes_by_session[s["id"]] = probes
    kept, dropped = dedupe(stats)
    entrypoints = collections.Counter(s["entrypoint"] for s in kept)
    counts = collections.Counter()
    sessions = collections.defaultdict(set)
    for s in kept:
        for p in probes_by_session[s["id"]]:
            key = p.split("\n", 1)[0][:24]
            counts[key] += 1
            sessions[key].add(s["id"])
    total = sum(counts.values())
    print("scanned session files: {} ({} duplicate copies dropped)".format(len(files), dropped))
    print("entrypoints: {}".format(dict(entrypoints)))
    print("candidate human messages: {}".format(total))
    print("\nmost repeated message prefixes (count / sessions / prefix):")
    for key, n in counts.most_common(top):
        if n < 3:
            break
        print("  {:>6}  {:>5}  {}".format(n, len(sessions[key]), key))


def pick_sample(eligible, n, per_project_cap, seed):
    pool = sorted(eligible, key=lambda x: x["id"])
    random.Random(seed).shuffle(pool)
    picked, per_project, rest = [], collections.Counter(), []
    for item in pool:
        proj = item["project_dir"]
        if len(picked) < n and per_project[proj] < per_project_cap:
            picked.append(item)
            per_project[proj] += 1
        else:
            rest.append(item)
    for item in rest:
        if len(picked) >= n:
            break
        picked.append(item)
    return picked


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--claude-dir", default=default_claude_dir())
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--survey", action="store_true", help="print repeated message prefixes and exit")
    ap.add_argument("--top", type=int, default=30)
    ap.add_argument("--exclude-prefix", action="append", default=[], help="drop human messages starting with this")
    ap.add_argument("--exclude-file", help="file with one prefix per line")
    ap.add_argument("--min-turns", type=int, default=3)
    ap.add_argument("--sample", type=int, default=20)
    ap.add_argument("--per-project", type=int, default=3)
    ap.add_argument("--batch-size", type=int, default=5)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--include-sdk", action="store_true", help="keep headless (claude -p / SDK) sessions")
    ap.add_argument("--out", help="output dir (default <claude-dir>/usage-data/agentic-eng-score/<stamp>)")
    args = ap.parse_args(argv)

    prefixes = list(args.exclude_prefix)
    if args.exclude_file:
        with open(args.exclude_file, encoding="utf-8") as fh:
            prefixes += [ln.rstrip("\n") for ln in fh if ln.strip()]
    projects_dir = os.path.join(args.claude_dir, "projects")
    files = session_files(projects_dir, args.days)
    if not files:
        print("no session files under {} in the last {} days".format(projects_dir, args.days))
        return 1
    if args.survey:
        survey(files, prefixes, args.top)
        return 0

    stats = [parse_session(f, prefixes)[0] for f in files]
    unique, dropped = dedupe(stats)
    eligible = []
    skipped = collections.Counter()
    if dropped:
        skipped["duplicate_copy"] = dropped
    for s in unique:
        if not args.include_sdk and (s["entrypoint"] or "").startswith("sdk"):
            skipped["headless"] += 1
        elif s["human_turns"] < args.min_turns:
            skipped["few_human_turns"] += 1
        else:
            eligible.append(s)
    if not eligible:
        print("no eligible sessions (skipped: {})".format(dict(skipped)))
        return 1
    picked = pick_sample(eligible, args.sample, args.per_project, args.seed)

    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    out = args.out or os.path.join(args.claude_dir, "usage-data", "agentic-eng-score", stamp)
    os.makedirs(os.path.join(out, "transcripts"), exist_ok=True)
    os.makedirs(os.path.join(out, "scores"), exist_ok=True)
    sessions = []
    for picked_stats in picked:
        s, lines = parse_session(picked_stats["path"], prefixes)
        rel = os.path.join("transcripts", s["id"] + ".txt")
        with open(os.path.join(out, rel), "w", encoding="utf-8") as fh:
            fh.write(render(s, lines))
        row = jsonable(s)
        row["transcript"] = rel
        sessions.append(row)
    ids = [r["id"] for r in sessions]
    batches = [ids[i:i + args.batch_size] for i in range(0, len(ids), args.batch_size)]
    manifest = {
        "generated_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "claude_dir": args.claude_dir,
        "params": {
            "days": args.days, "min_turns": args.min_turns, "sample": args.sample,
            "per_project": args.per_project, "seed": args.seed, "include_sdk": args.include_sdk,
            "exclude_prefixes": prefixes,
        },
        "counts": {"files": len(files), "eligible": len(eligible), "picked": len(sessions), "skipped": dict(skipped)},
        "batches": batches,
        "sessions": sessions,
    }
    with open(os.path.join(out, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=2)
    print("out: {}".format(out))
    print("files {} → eligible {} → picked {} (skipped {})".format(
        len(files), len(eligible), len(sessions), dict(skipped)))
    for i, b in enumerate(batches, 1):
        print("batch {}: {}".format(i, " ".join(b)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
