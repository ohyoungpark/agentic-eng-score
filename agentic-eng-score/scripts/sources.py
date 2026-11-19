"""Session readers for Codex CLI, opencode and Devin CLI.

Each reader lists session refs and turns one ref into the same (stats, lines)
pair that collect.parse_session produces for Claude Code. Standard library only.

  Codex CLI   ~/.codex/sessions/**/rollout-*.jsonl[.zst]      verified on real data
  opencode    ~/.local/share/opencode/opencode.db (SQLite)     verified on real data
  Devin CLI   ~/.local/share/devin/cli/sessions.db (SQLite)    EXPERIMENTAL: written from an
              unofficial schema description, never run against a real store
"""
import datetime
import glob
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.parse

from common import add_tool, add_user_text, new_stats

if sys.version_info >= (3, 14):
    from compression import zstd as _zstd
else:
    _zstd = None

EDIT_NAMES = {"edit", "write", "patch", "multiedit", "apply_patch", "str_replace", "create_file"}
SHELL_NAMES = {"bash", "shell", "exec", "exec_command", "local_shell", "container.exec", "run_command"}
# Text Codex puts in the user role that the user did not type.
CODEX_INJECTED = ("# AGENTS.md", "<environment_context", "<user_instructions", "<INSTRUCTIONS", "<turn_aborted")


def project_dir_of(cwd):
    return (cwd or "unknown").replace("/", "-")


def iso_from_ms(value):
    """Epoch milliseconds (or seconds, when the value is small) to an ISO UTC string."""
    if not value:
        return None
    seconds = value / 1000.0 if value >= 1e12 else float(value)
    return datetime.datetime.fromtimestamp(seconds, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def text_of(content):
    """Join the text fields of a content value that is a string or a list of blocks."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text", "") for b in content if isinstance(b, dict) and isinstance(b.get("text"), str))
    return ""


def loads_object(text):
    """Parse text that looks like a JSON object; anything else gives {}."""
    text = (text or "").strip()
    if text.startswith("{") and text.endswith("}"):
        value = json.loads(text)
        return value if isinstance(value, dict) else {}
    return {}


def has_tables(conn, names):
    found = {r[0] for r in conn.execute("select name from sqlite_master where type='table'")}
    return set(names) <= found


def open_readonly(path):
    return sqlite3.connect("file:{}?mode=ro".format(urllib.parse.quote(path)), uri=True)


# ---------------------------------------------------------------- Codex CLI

def codex_dir():
    return os.environ.get("CODEX_HOME") or os.path.expanduser("~/.codex")


def can_read_zst():
    return _zstd is not None or shutil.which("zstd") is not None


def codex_refs(days):
    cutoff = time.time() - days * 86400
    root = os.path.join(codex_dir(), "sessions")
    files = glob.glob(os.path.join(root, "**", "rollout-*.jsonl"), recursive=True)
    if can_read_zst():
        files += glob.glob(os.path.join(root, "**", "rollout-*.jsonl.zst"), recursive=True)
    return [{"source": "codex", "path": f} for f in sorted(files) if os.path.getmtime(f) >= cutoff]


def codex_unreadable(days):
    """Number of recent compressed rollouts that cannot be read on this machine."""
    if can_read_zst():
        return 0
    cutoff = time.time() - days * 86400
    files = glob.glob(os.path.join(codex_dir(), "sessions", "**", "rollout-*.jsonl.zst"), recursive=True)
    return sum(1 for f in files if os.path.getmtime(f) >= cutoff)


def codex_entries(path):
    if not path.endswith(".zst"):
        with open(path, encoding="utf-8", errors="replace") as fh:
            raw = fh.read()
    elif _zstd is not None:
        with _zstd.open(path, "rt", encoding="utf-8", errors="replace") as fh:
            raw = fh.read()
    else:
        raw = subprocess.run(["zstd", "-dc", path], capture_output=True).stdout.decode("utf-8", "replace")
    out = []
    for line in raw.splitlines():
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            out.append(json.loads(line))
    return out


def codex_tool(s, lines, payload, trigger):
    kind = payload.get("type")
    if kind == "local_shell_call":
        action = payload.get("action") or {}
        command = action.get("command")
        name, args = "local_shell", " ".join(command) if isinstance(command, list) else str(command or "")
    else:
        name = str(payload.get("name") or "tool")
        args = payload.get("arguments") if kind == "function_call" else payload.get("input")
        args = args if isinstance(args, str) else json.dumps(args, ensure_ascii=False)
        # Shell tools pass {"command": ["git", "commit", ...]}; flatten it so the command reads as typed.
        inner = loads_object(args)
        command = inner.get("command") if inner.get("command") is not None else inner.get("cmd")
        if isinstance(command, list):
            args = " ".join(str(c) for c in command)
        elif isinstance(command, str):
            args = command
    base = name.split(".")[-1].lower()
    patching = "apply_patch" in name or "*** Begin Patch" in args[:4000] or "apply_patch" in args[:200]
    parts = name.split("__")
    add_tool(
        s, lines, name, "{}: {}".format(name, args), trigger,
        command=args if (base in SHELL_NAMES or kind == "local_shell_call") else "",
        edit=patching or base in EDIT_NAMES,
        subagent="spawn_agent" in name,
        mcp_server=parts[1] if name.startswith("mcp__") and len(parts) >= 3 else None,
    )


def parse_codex(ref, exclude_prefixes, probes=None):
    path = ref["path"]
    entries = codex_entries(path)
    meta = next((e.get("payload") or {} for e in entries if e.get("type") == "session_meta"), {})
    base = os.path.basename(path).split(".jsonl")[0]
    sid = str(meta.get("id") or meta.get("session_id") or base)
    cwd = meta.get("cwd")
    s = new_stats(path, session_id="codex-" + sid, project_dir=project_dir_of(cwd), source="codex")
    s["cwd"], s["session_key"] = cwd, "codex-" + sid
    originator, source = str(meta.get("originator") or ""), meta.get("source")
    s["entrypoint"] = originator or (source if isinstance(source, str) else None)
    s["headless"] = ("sdk" in originator.lower() or source == "exec" or isinstance(source, dict)
                     or meta.get("thread_source") == "subagent")

    def is_event(e, kind):
        return e.get("type") == "event_msg" and (e.get("payload") or {}).get("type") == kind

    # Newer Codex records what the user typed as an item, older as user_message;
    # the oldest only has the raw model input, which also carries injected text.
    if any(is_event(e, "item_completed") and (e["payload"].get("item") or {}).get("type") == "UserMessage"
           for e in entries):
        user_mode = "item"
    elif any(is_event(e, "user_message") for e in entries):
        user_mode = "event"
    else:
        user_mode = "raw"

    lines, trigger = [], None
    for e in entries:
        payload = e.get("payload") if isinstance(e.get("payload"), dict) else {}
        ts = e.get("timestamp")
        if ts:
            s["start"] = s["start"] or ts
            s["end"] = ts
        kind, sub = e.get("type"), payload.get("type")
        if kind == "turn_context":
            if payload.get("model"):
                s["models"].add(payload["model"])
            s["cwd"] = s["cwd"] or payload.get("cwd")
        elif kind == "compacted":
            s["compactions"] += 1
        elif kind == "event_msg":
            user_text = None
            if user_mode == "item" and sub == "item_completed":
                item = payload.get("item") or {}
                if item.get("type") == "UserMessage":
                    user_text = text_of(item.get("content"))
            elif user_mode == "event" and sub == "user_message":
                user_text = str(payload.get("message") or "")
            elif sub == "turn_aborted":
                s["interrupts"] += 1
                lines.append(("E", "[user interrupted]"))
            if user_text is not None:
                trigger = add_user_text(s, lines, user_text, exclude_prefixes, probes) or trigger
        elif kind == "response_item":
            if sub == "message":
                text = text_of(payload.get("content"))
                if payload.get("role") == "assistant" and text.strip():
                    lines.append(("A", text))
                    if text.lstrip().startswith("[external_agent_tool_"):
                        # History imported from another agent: a copy of a session that tool already holds.
                        s["headless"], s["entrypoint"] = True, "imported"
                elif payload.get("role") == "user" and user_mode == "raw" and not text.lstrip().startswith(CODEX_INJECTED):
                    trigger = add_user_text(s, lines, text, exclude_prefixes, probes) or trigger
            elif sub in ("function_call", "custom_tool_call", "local_shell_call"):
                codex_tool(s, lines, payload, trigger)
    return s, lines


# ----------------------------------------------------------------- opencode

def opencode_db():
    if os.environ.get("OPENCODE_DB"):
        return os.environ["OPENCODE_DB"]
    data_home = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return os.path.join(data_home, "opencode", "opencode.db")


def opencode_refs(days):
    db = opencode_db()
    if not os.path.isfile(db):
        return []
    conn = open_readonly(db)
    refs = []
    if has_tables(conn, ("session", "message", "part")):
        cutoff_ms = (time.time() - days * 86400) * 1000
        rows = conn.execute("select id from session where time_updated >= ? order by time_created", (cutoff_ms,))
        refs = [{"source": "opencode", "path": db, "session": r[0]} for r in rows]
    conn.close()
    return refs


def opencode_tool(s, lines, part, trigger):
    name = str(part.get("tool") or "tool")
    state = part.get("state") if isinstance(part.get("state"), dict) else {}
    inp = state.get("input") if isinstance(state.get("input"), dict) else {}
    base = name.lower()
    if base in SHELL_NAMES:
        detail = str(inp.get("command", ""))
    elif base in EDIT_NAMES or base == "read":
        detail = str(inp.get("filePath") or inp.get("path") or "")
    elif base == "task":
        detail = "[{}] {}".format(inp.get("subagent_type", "-"), inp.get("description", ""))
    else:
        detail = json.dumps(inp, ensure_ascii=False)[:150]
    add_tool(
        s, lines, name, "{}: {}".format(name, detail), trigger,
        command=detail if base in SHELL_NAMES else "",
        edit=base in EDIT_NAMES, subagent=base == "task", skill=base == "skill",
        error=(state.get("error") or "tool failed") if state.get("status") == "error" else None,
    )


def parse_opencode(ref, exclude_prefixes, probes=None):
    conn = open_readonly(ref["path"])
    sid = ref["session"]
    directory, parent_id, created, updated = conn.execute(
        "select directory, parent_id, time_created, time_updated from session where id = ?", (sid,)).fetchone()
    s = new_stats(ref["path"], session_id="opencode-" + sid, project_dir=project_dir_of(directory), source="opencode")
    s["ref"], s["cwd"], s["session_key"] = ref, directory, "opencode-" + sid
    s["start"], s["end"] = iso_from_ms(created), iso_from_ms(updated)
    s["headless"] = parent_id is not None  # a child session is a subagent run
    s["entrypoint"] = "subagent" if parent_id else "tui"
    parts = {}
    for message_id, data in conn.execute("select message_id, data from part where session_id = ? order by id", (sid,)):
        parts.setdefault(message_id, []).append(loads_object(data))
    lines, trigger = [], None
    messages = conn.execute(
        "select id, data from message where session_id = ? order by time_created, id", (sid,)).fetchall()
    conn.close()
    for message_id, data in messages:
        message = loads_object(data)
        own = parts.get(message_id, [])
        if message.get("role") == "user":
            text = "\n".join(p.get("text", "") for p in own
                             if p.get("type") == "text" and not p.get("synthetic") and not p.get("ignored"))
            if text.strip():
                trigger = add_user_text(s, lines, text, exclude_prefixes, probes, uuid=message_id) or trigger
            continue
        if message.get("modelID"):
            s["models"].add(message["modelID"])
        for p in own:
            if p.get("type") == "text" and str(p.get("text", "")).strip():
                lines.append(("A", p["text"]))
            elif p.get("type") == "tool":
                opencode_tool(s, lines, p, trigger)
            elif p.get("type") == "compaction":
                s["compactions"] += 1
    return s, lines


# ---------------------------------------------------- Devin CLI (experimental)

def devin_db():
    names = []
    if os.environ.get("DEVIN_SESSIONS_DIR"):
        names.append(os.path.join(os.environ["DEVIN_SESSIONS_DIR"], "sessions.db"))
    data_home = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    names.append(os.path.join(data_home, "devin", "cli", "sessions.db"))
    names.append(os.path.expanduser("~/Library/Application Support/devin/cli/sessions.db"))
    for base in (os.environ.get("APPDATA"), os.environ.get("LOCALAPPDATA")):
        if base:
            names.append(os.path.join(base, "devin", "cli", "sessions.db"))
    return next((n for n in names if os.path.isfile(n)), None)


def devin_refs(days):
    db = devin_db()
    if not db:
        return []
    conn = open_readonly(db)
    refs = []
    if has_tables(conn, ("sessions", "prompt_history")):
        cutoff = time.time() - days * 86400
        for sid, last in conn.execute("select id, last_activity_at from sessions where hidden = 0"):
            seconds = last / 1000.0 if last and last >= 1e12 else float(last or 0)
            if seconds >= cutoff:
                refs.append({"source": "devin", "path": db, "session": sid})
    conn.close()
    return refs


def parse_devin(ref, exclude_prefixes, probes=None):
    """Best effort. Human prompts come from prompt_history and tool calls from
    tool_call_state; both are described by the schema notes. The agent's own
    messages live in an undocumented payload and are read only when they look
    like a JSON object with a recognisable role and text."""
    conn = open_readonly(ref["path"])
    sid = ref["session"]
    cwd, model, created, last = conn.execute(
        "select working_directory, model, created_at, last_activity_at from sessions where id = ?", (sid,)).fetchone()
    s = new_stats(ref["path"], session_id="devin-" + sid, project_dir=project_dir_of(cwd), source="devin")
    s["ref"], s["cwd"], s["session_key"], s["entrypoint"] = ref, cwd, "devin-" + sid, "cli"
    s["start"], s["end"] = iso_from_ms(created), iso_from_ms(last)
    if model:
        s["models"].add(model)
    timeline = []
    for content, stamp, is_shell in conn.execute(
            "select content, timestamp, is_shell from prompt_history where session_id = ? order by timestamp, id", (sid,)):
        timeline.append((stamp or 0, 0, "shell" if is_shell else "user", content or ""))
    if has_tables(conn, ("message_nodes",)):
        for raw, stamp in conn.execute(
                "select chat_message, created_at from message_nodes where session_id = ? order by created_at, row_id", (sid,)):
            node = loads_object(raw)
            role = str(node.get("role") or node.get("type") or node.get("kind") or "").lower()
            text = text_of(node.get("content")) or str(node.get("text") or "")
            if ("assistant" in role or "agent" in role) and text.strip():
                timeline.append((stamp or 0, 1, "agent", text))
    lines, trigger = [], None
    for _, _, kind, text in sorted(timeline, key=lambda row: (row[0], row[1])):
        if kind == "user":
            trigger = add_user_text(s, lines, text, exclude_prefixes, probes) or trigger
        elif kind == "shell":
            s["bang_cmds"] += 1
            lines.append(("!", text))
        else:
            lines.append(("A", text))
    if has_tables(conn, ("tool_call_state",)):
        # Tool calls carry no timestamp, so they are listed after the conversation.
        for call_json, update_json in conn.execute(
                "select tool_call_json, tool_call_update_json from tool_call_state where session_id = ?", (sid,)):
            call, update = loads_object(call_json), loads_object(update_json)
            kind = str(call.get("kind") or update.get("kind") or "tool")
            raw_input = call.get("rawInput") if call.get("rawInput") is not None else update.get("rawInput")
            args = raw_input if isinstance(raw_input, str) else json.dumps(raw_input, ensure_ascii=False)
            failed = (update.get("status") or call.get("status")) == "failed"
            add_tool(
                s, lines, kind, "{}: {} {}".format(kind, call.get("title") or "", args), "human",
                command=args if kind == "execute" else "", edit=kind == "edit",
                error="tool failed" if failed else None,
            )
    conn.close()
    return s, lines


READERS = {
    "codex": (codex_refs, parse_codex),
    "opencode": (opencode_refs, parse_opencode),
    "devin": (devin_refs, parse_devin),
}
