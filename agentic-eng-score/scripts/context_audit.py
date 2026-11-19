#!/usr/bin/env python3
"""Audit the context-engineering setup around the sampled sessions.

Counts instruction files, skills, subagents, hooks, MCP servers, memory files
and permission rules for Claude Code, Codex CLI and opencode. Records names
and counts only: never the values inside settings (env vars, tokens, headers).
Standard library only.

  python3 context_audit.py <out-dir>
"""
import argparse
import glob
import json
import os
import re
import sys

INSTRUCTION_FILES = ("CLAUDE.md", "AGENTS.md", "CLAUDE.local.md", os.path.join(".claude", "CLAUDE.md"))
PROJECT_SKILL_DIRS = (".claude", ".agents", ".codex", ".opencode", ".devin", ".github")
CODEX_MCP_RE = re.compile(r"^\[mcp_servers\.([^\].]+)", re.M)


def codex_mcp_servers(path):
    """Server names from the [mcp_servers.<name>] tables of a Codex config.toml."""
    if not os.path.isfile(path):
        return set()
    with open(path, encoding="utf-8", errors="replace") as fh:
        return {name.strip('"') for name in CODEX_MCP_RE.findall(fh.read())}


def opencode_mcp_servers(path):
    mcp = load_json(path).get("mcp")
    return set(mcp) if isinstance(mcp, dict) else set()


def skill_count(roots):
    """SKILL.md files under <root>/skills/* or <root>/skill/*, counted once per real file."""
    found = set()
    for root in roots:
        for sub in ("skills", "skill"):
            found.update(os.path.realpath(p) for p in glob.glob(os.path.join(root, sub, "*", "SKILL.md")))
    return len(found)


def default_claude_dir():
    return os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")


def load_json(path):
    if not os.path.isfile(path):
        return {}
    with open(path, encoding="utf-8", errors="replace") as fh:
        text = fh.read().strip()
    if not text.startswith("{"):
        return {}
    return json.loads(text)


def line_count(path):
    if not os.path.isfile(path):
        return 0
    with open(path, encoding="utf-8", errors="replace") as fh:
        return sum(1 for ln in fh if ln.strip())


def count(pattern):
    return len(glob.glob(pattern, recursive=True))


def settings_facts(paths):
    hook_events, hook_count, allow_rules, plugins = set(), 0, 0, 0
    for p in paths:
        data = load_json(p)
        hooks = data.get("hooks") or {}
        if isinstance(hooks, dict):
            for event, groups in hooks.items():
                hook_events.add(event)
                for g in groups if isinstance(groups, list) else []:
                    hook_count += len(g.get("hooks", [])) if isinstance(g, dict) else 0
        perms = data.get("permissions") or {}
        allow_rules += len(perms.get("allow") or []) if isinstance(perms, dict) else 0
        enabled = data.get("enabledPlugins") or {}
        plugins += sum(1 for v in enabled.values() if v) if isinstance(enabled, dict) else 0
    return {"hook_events": sorted(hook_events), "hooks": hook_count, "allow_rules": allow_rules, "plugins": plugins}


def project_facts(cwd, claude_json):
    exists = bool(cwd) and os.path.isdir(cwd)
    facts = {"cwd": cwd, "exists": exists}
    if not exists:
        return facts
    facts["instructions"] = {f: line_count(os.path.join(cwd, f)) for f in INSTRUCTION_FILES if os.path.isfile(os.path.join(cwd, f))}
    facts["skills"] = skill_count([os.path.join(cwd, d) for d in PROJECT_SKILL_DIRS])
    facts["agents"] = count(os.path.join(cwd, ".claude", "agents", "*.md"))
    facts["commands"] = count(os.path.join(cwd, ".claude", "commands", "**", "*.md"))
    facts["settings"] = settings_facts([
        os.path.join(cwd, ".claude", "settings.json"),
        os.path.join(cwd, ".claude", "settings.local.json"),
    ])
    mcp_file = load_json(os.path.join(cwd, ".mcp.json")).get("mcpServers") or {}
    per_project = ((claude_json.get("projects") or {}).get(cwd) or {}).get("mcpServers") or {}
    facts["mcp_servers"] = sorted(set(mcp_file) | set(per_project)
                                  | codex_mcp_servers(os.path.join(cwd, ".codex", "config.toml"))
                                  | opencode_mcp_servers(os.path.join(cwd, "opencode.json")))
    return facts


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("out_dir")
    ap.add_argument("--claude-dir", default=default_claude_dir())
    ap.add_argument("--claude-json", default=os.path.expanduser("~/.claude.json"))
    ap.add_argument("--home", default=os.path.expanduser("~"), help="home directory holding .codex and .config")
    args = ap.parse_args(argv)
    user_home = args.home

    with open(os.path.join(args.out_dir, "manifest.json"), encoding="utf-8") as fh:
        manifest = json.load(fh)
    claude_json = load_json(args.claude_json)
    cd = args.claude_dir
    memory_files = [
        p for p in glob.glob(os.path.join(cd, "projects", "*", "memory", "*.md"))
        if os.path.basename(p) != "MEMORY.md"
    ]
    global_facts = {
        "claude_md_lines": max(line_count(os.path.join(cd, "CLAUDE.md")), line_count(os.path.join(user_home, ".codex", "AGENTS.md")),
                               line_count(os.path.join(user_home, ".config", "opencode", "AGENTS.md"))),
        "skills": skill_count([cd, os.path.join(user_home, ".codex"), os.path.join(user_home, ".agents"), os.path.join(user_home, ".config", "opencode")]),
        "agents": count(os.path.join(cd, "agents", "*.md")),
        "commands": count(os.path.join(cd, "commands", "**", "*.md")),
        "settings": settings_facts([os.path.join(cd, "settings.json"), os.path.join(cd, "settings.local.json")]),
        "mcp_servers": sorted(set(claude_json.get("mcpServers") or {}).union(*[
            set((p or {}).get("mcpServers") or {}) for p in (claude_json.get("projects") or {}).values()
            if isinstance(p, dict)
        ]) | codex_mcp_servers(os.path.join(user_home, ".codex", "config.toml"))
            | opencode_mcp_servers(os.path.join(user_home, ".config", "opencode", "opencode.json"))),
        "memory_files": len(memory_files),
        "memory_projects": len({os.path.dirname(p) for p in memory_files}),
    }
    cwds = sorted({s.get("cwd") for s in manifest["sessions"] if s.get("cwd")})
    audit = {"global": global_facts, "projects": [project_facts(c, claude_json) for c in cwds]}
    with open(os.path.join(args.out_dir, "context_audit.json"), "w", encoding="utf-8") as fh:
        json.dump(audit, fh, ensure_ascii=False, indent=2)
    live = [p for p in audit["projects"] if p["exists"]]
    print("global: instruction file {} lines, skills {}, agents {}, hooks {}, mcp {}, memory files {}".format(
        global_facts["claude_md_lines"], global_facts["skills"], global_facts["agents"],
        global_facts["settings"]["hooks"], len(global_facts["mcp_servers"]), global_facts["memory_files"]))
    print("projects: {} sampled cwd, {} still on disk, {} with instruction files".format(
        len(cwds), len(live), sum(1 for p in live if p["instructions"])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
