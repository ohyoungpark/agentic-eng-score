"""Shared pieces for the session readers: message classification, tool
accounting and the per-session stats record. Standard library only."""
import collections
import json
import os
import re

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


def new_stats(path, session_id=None, project_dir=None, source="claude"):
    return {
        "id": session_id or os.path.splitext(os.path.basename(path))[0],
        "source": source,
        "headless": False,
        "path": path,
        "ref": {"source": source, "path": path},
        "session_key": None,
        "human_uuids": [],
        "project_dir": project_dir or os.path.basename(os.path.dirname(path)),
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
        "human_tool_calls": 0,
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


def add_user_text(s, lines, text, exclude_prefixes, probes=None, uuid=None):
    """Record one user-role message given as plain text.

    Returns "human" or "automation" when the message changes who triggered the
    work that follows, else None.
    """
    kind, clean, probe = classify_user(text, exclude_prefixes)
    if kind == "human":
        s["human_turns"] += 1
        s["human_chars"] += len(clean)
        if uuid:
            s["human_uuids"].append(uuid)
        lines.append(("U", clean))
        if probes is not None:
            probes.append(probe)
        return "human"
    if kind == "slash":
        s["slash_cmds"][clean.split(" ", 1)[0]] += 1
        lines.append(("/", clean))
        if " " in clean:
            s["human_turns"] += 1
            s["human_chars"] += len(clean)
            return "human"
    elif kind == "bang":
        s["bang_cmds"] += 1
        lines.append(("!", clean))
    elif kind == "interrupt":
        s["interrupts"] += 1
        lines.append(("E", "[user interrupted]"))
    elif kind == "automation":
        s["automation_msgs"] += 1
        return "automation"
    return None


def add_tool(s, lines, name, summary, trigger, command="", edit=False, subagent=False, skill=False,
             mcp_server=None, error=None):
    """Record one tool call made by the agent."""
    s["tool_calls"] += 1
    if trigger == "human":
        s["human_tool_calls"] += 1
    s["tools"][name] += 1
    if edit:
        s["edit_calls"] += 1
    if subagent:
        s["subagent_calls"] += 1
    if skill:
        s["skill_calls"] += 1
    if mcp_server:
        s["mcp_calls"] += 1
        s["mcp_servers"].add(mcp_server)
    if command:
        if TEST_RE.search(command):
            s["test_runs"] += 1
        if COMMIT_RE.search(command):
            s["commits"] += 1
    lines.append(("T", summary))
    if error:
        s["tool_errors"] += 1
        lines.append(("E", str(error)))
