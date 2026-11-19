#!/usr/bin/env python3
"""Validate per-session scores and write report.md + summary.json.

  python3 aggregate.py <out-dir>

Pass 1 is scores/<id>.json. If any scores/<id>.b.json exists the run is
treated as two-pass (override with --passes): a missed instruction counts
only when both passes flagged the same turn, and numeric scores are averaged.

Exit code 2 when a sampled session has no valid score file yet; the list of
missing or invalid ids is printed so they can be re-scored.
"""
import argparse
import collections
import glob
import json
import os
import re
import sys

WORK_MODES = {"Plan", "Build", "Fix", "Understand", "Test", "Operate", "Analyze", "Orchestrate", "Communicate"}
OUTCOMES = {"Succeeded", "Partial", "Failed", "No clear goal"}
SHARES = {"most", "half", "few"}
PROMPT_DIMS = ("clarity", "specificity", "actionability", "scope")
MISS_KINDS = {"clarify", "misread"}
MISS_KO = {"clarify": "되물음", "misread": "오해"}
DIM_KO = {"clarity": "명확성", "specificity": "구체성", "actionability": "실행 가능성", "scope": "범위"}

# Setup checklist for the context axis: (key, label, points)
CHECKLIST = (
    ("global_md", "전역 지시 파일(CLAUDE.md·AGENTS.md) 있음", 10),
    ("global_md_rich", "전역 지시 파일 30줄 이상", 5),
    ("project_md", "작업한 프로젝트의 절반 이상에 CLAUDE.md/AGENTS.md", 15),
    ("skills", "직접 만든 스킬 1개 이상", 10),
    ("skills_many", "직접 만든 스킬 5개 이상", 5),
    ("agents", "직접 정의한 서브에이전트 1개 이상", 5),
    ("hooks", "훅 1개 이상", 10),
    ("mcp", "MCP 서버 1개 이상", 10),
    ("mcp_many", "MCP 서버 3개 이상", 5),
    ("memory", "메모리 파일 사용", 10),
    ("allow_rules", "권한 허용 규칙 5개 이상", 10),
    ("commands_plugins", "커스텀 명령 또는 플러그인 사용", 5),
)


def is_int_in(v, lo, hi):
    return isinstance(v, int) and not isinstance(v, bool) and lo <= v <= hi


def validate(sc, sid):
    errs = []
    if sc.get("session_id") != sid:
        errs.append("session_id mismatch")
    if sc.get("work_mode") not in WORK_MODES:
        errs.append("work_mode")
    if not (sc.get("expertise") == "Unclear" or is_int_in(sc.get("expertise"), 1, 5)):
        errs.append("expertise")
    prompt = sc.get("prompt") if isinstance(sc.get("prompt"), dict) else {}
    for d in PROMPT_DIMS:
        if not is_int_in(prompt.get(d), 0, 10):
            errs.append("prompt." + d)
    misses = prompt.get("misses")
    if not is_int_in(prompt.get("instructions"), 1, 100000):
        errs.append("prompt.instructions")
    elif not isinstance(misses, list) or len(misses) > prompt["instructions"]:
        errs.append("prompt.misses")
    elif not all(isinstance(m, dict) and m.get("kind") in MISS_KINDS and isinstance(m.get("evidence"), str)
                 and re.match(r"U\d+$", str(m.get("turn", ""))) for m in misses):
        errs.append("prompt.misses item")
    for axis in ("context", "agentic"):
        block = sc.get(axis) if isinstance(sc.get(axis), dict) else {}
        if not is_int_in(block.get("score"), 1, 5):
            errs.append(axis + ".score")
    for k in ("planning_by_user", "execution_by_user"):
        if sc.get(k) not in SHARES:
            errs.append(k)
    if sc.get("outcome") not in OUTCOMES:
        errs.append("outcome")
    if not isinstance(sc.get("hard_signal"), bool):
        errs.append("hard_signal")
    elif sc["hard_signal"] and not str(sc.get("hard_signal_evidence") or "").strip():
        errs.append("hard_signal_evidence")
    return errs


QUOTE_RE = re.compile(r'U\d+:\s*"([^"]+)"')
TAG_PREFIX_RE = re.compile(r'^\s*(\[[^\]]{1,5}\]|[UATE]\d*:)\s*')


def probe_of(quote):
    """First 20 characters of a quote, without a leading line tag or quote marks."""
    bare = TAG_PREFIX_RE.sub("", quote).strip().strip('"').rstrip(".…").strip()
    return bare[:20]


def unverified_quotes(sc, transcript):
    """Return quoted evidence snippets that do not appear in the transcript."""
    missing = []
    for block in (sc.get("prompt"), sc.get("context"), sc.get("agentic")):
        evidence = block.get("evidence", "") if isinstance(block, dict) else ""
        for quote in QUOTE_RE.findall(evidence):
            probe = quote.strip().rstrip(".…").strip()[:20]
            if probe and probe not in transcript:
                missing.append(quote)
    prompt = sc.get("prompt") if isinstance(sc.get("prompt"), dict) else {}
    for miss in prompt.get("misses") or []:
        quote = miss.get("evidence", "") if isinstance(miss, dict) else ""
        probe = probe_of(quote)
        if not probe or probe not in transcript:
            missing.append(quote or "(empty miss evidence)")
    if sc.get("hard_signal") is True:
        quote = str(sc.get("hard_signal_evidence") or "")
        probe = probe_of(quote)
        if probe and probe not in transcript:
            missing.append(quote)
    return missing


OUTCOME_RANK = {"Succeeded": 3, "Partial": 2, "Failed": 1, "No clear goal": 0}


def merge(passes):
    """Combine scoring passes of one session into one conservative score."""
    a = passes[0]
    if len(passes) == 1:
        return a
    turns_everywhere = set.intersection(*[{m["turn"] for m in p["prompt"]["misses"]} for p in passes])
    misses = [m for m in a["prompt"]["misses"] if m["turn"] in turns_everywhere]
    prompt = {d: mean(p["prompt"][d] for p in passes) for d in PROMPT_DIMS}
    prompt["misses"] = misses
    prompt["instructions"] = max(int(mean(p["prompt"]["instructions"] for p in passes) + 0.5), len(misses), 1)
    rated = [p["expertise"] for p in passes if p["expertise"] != "Unclear"]
    goals = [p["outcome"] for p in passes if p["outcome"] != "No clear goal"]
    out = dict(a)
    out["prompt"] = prompt
    out["context"] = dict(a["context"], score=mean(p["context"]["score"] for p in passes))
    out["agentic"] = dict(a["agentic"], score=mean(p["agentic"]["score"] for p in passes))
    out["expertise"] = int(mean(rated) + 0.5) if rated else "Unclear"
    out["outcome"] = min(goals, key=OUTCOME_RANK.get) if goals else "No clear goal"
    out["hard_signal"] = all(p["hard_signal"] for p in passes)
    return out


PASS_SUFFIX = ("", ".b")


def load_pass(out_dir, s, suffix):
    """Return (score, errors) for one scoring pass of one session."""
    path = os.path.join(out_dir, "scores", s["id"] + suffix + ".json")
    if not os.path.isfile(path):
        return None, ["missing"]
    with open(path, encoding="utf-8") as fh:
        text = fh.read().strip()
    if not text.startswith("{"):
        return None, ["not a JSON object"]
    sc = json.loads(text)
    errs = validate(sc, s["id"])
    with open(os.path.join(out_dir, s["transcript"]), encoding="utf-8") as fh:
        missing = unverified_quotes(sc, fh.read())
    if missing:
        errs.append("evidence not found in transcript: " + " | ".join(m[:40] for m in missing))
    return sc, errs


def load_scores(out_dir, sessions, passes):
    """Return ({id: [score per pass]}, {score file name: errors})."""
    scores, problems = {}, {}
    for s in sessions:
        loaded = []
        for suffix in PASS_SUFFIX[:passes]:
            sc, errs = load_pass(out_dir, s, suffix)
            if errs:
                problems[s["id"] + suffix + ".json"] = errs
            else:
                loaded.append(sc)
        if len(loaded) == passes:
            scores[s["id"]] = loaded
    return scores, problems


def setup_checks(audit, sessions):
    g = audit["global"]
    live = [p for p in audit["projects"] if p.get("exists")]
    with_md = sum(1 for p in live if p.get("instructions"))
    ratio = with_md / len(live) if live else 0.0
    skills = g["skills"] + sum(p.get("skills", 0) for p in live)
    agents = g["agents"] + sum(p.get("agents", 0) for p in live)
    hooks = g["settings"]["hooks"] + sum(p["settings"]["hooks"] for p in live)
    configured = set(g["mcp_servers"]).union(*[set(p.get("mcp_servers", [])) for p in live])
    used = set().union(*[set(s.get("mcp_servers", [])) for s in sessions])
    mcp = len(configured | used)
    allow = g["settings"]["allow_rules"] + sum(p["settings"]["allow_rules"] for p in live)
    cmds = g["commands"] + sum(p.get("commands", 0) for p in live) + g["settings"]["plugins"]
    facts = {
        "global_md": g["claude_md_lines"] > 0,
        "global_md_rich": g["claude_md_lines"] >= 30,
        "project_md": ratio >= 0.5,
        "skills": skills >= 1,
        "skills_many": skills >= 5,
        "agents": agents >= 1,
        "hooks": hooks >= 1,
        "mcp": mcp >= 1,
        "mcp_many": mcp >= 3,
        "memory": g["memory_files"] >= 1,
        "allow_rules": allow >= 5,
        "commands_plugins": cmds >= 1,
    }
    detail = {
        "global_md": "{}줄".format(g["claude_md_lines"]),
        "global_md_rich": "{}줄".format(g["claude_md_lines"]),
        "project_md": "{}/{} 프로젝트".format(with_md, len(live)),
        "skills": "{}개".format(skills),
        "skills_many": "{}개".format(skills),
        "agents": "{}개".format(agents),
        "hooks": "{}개 ({})".format(hooks, ", ".join(g["settings"]["hook_events"]) or "-"),
        "mcp": "{}개 (설정 {} · 세션에서 호출 {})".format(mcp, len(configured), len(used)),
        "mcp_many": "{}개".format(mcp),
        "memory": "{}개 파일".format(g["memory_files"]),
        "allow_rules": "{}개".format(allow),
        "commands_plugins": "명령 {} · 플러그인 {}".format(
            g["commands"] + sum(p.get("commands", 0) for p in live), g["settings"]["plugins"]),
    }
    rows, points = [], 0
    for key, label, pts in CHECKLIST:
        ok = facts[key]
        points += pts if ok else 0
        rows.append((label, ok, pts, detail[key]))
    return points, rows


def mean(xs):
    xs = list(xs)
    return sum(xs) / len(xs) if xs else 0.0


def to100(score_1_to_5):
    return (score_1_to_5 - 1) / 4 * 100


def level(x):
    if x >= 90:
        return "L5"
    if x >= 75:
        return "L4"
    if x >= 60:
        return "L3"
    if x >= 40:
        return "L2"
    return "L1"


def pct(x):
    return "{:.0f}%".format(x * 100)


def project_name(s):
    cwd = (s.get("cwd") or "").rstrip("/")
    return os.path.basename(cwd) if cwd else s["project_dir"][-28:]


def compute(manifest, audit, scores):
    rows = [s for s in manifest["sessions"] if s["id"] in scores]
    sc = [scores[s["id"]] for s in rows]
    n = len(sc)
    dims = {d: mean(x["prompt"][d] for x in sc) for d in PROMPT_DIMS}
    form = mean(mean(x["prompt"][d] for d in PROMPT_DIMS) for x in sc) * 10
    instructions = sum(x["prompt"]["instructions"] for x in sc)
    miss_kinds = collections.Counter(m["kind"] for x in sc for m in x["prompt"]["misses"])
    missed = sum(miss_kinds.values())
    pe = (instructions - missed) / instructions * 100
    ctx_mean = mean(x["context"]["score"] for x in sc)
    setup_points, setup_rows = setup_checks(audit, manifest["sessions"])
    ce = 0.5 * to100(ctx_mean) + 0.5 * setup_points
    ag_mean = mean(x["agentic"]["score"] for x in sc)
    verified = mean(1.0 if (x["outcome"] == "Succeeded" and x["hard_signal"]) else 0.0 for x in sc)
    app = mean(s["actions_per_prompt"] for s in rows)
    ae = 0.8 * to100(ag_mean) + 0.2 * verified * 100
    partial_plus = mean(1.0 if x["outcome"] in ("Succeeded", "Partial") else 0.0 for x in sc)
    abandoned = mean(1.0 if (x["outcome"] == "Failed" and s["edit_calls"] == 0) else 0.0 for x, s in zip(sc, rows))
    expertise = collections.Counter(str(x["expertise"]) for x in sc)
    rated = [x["expertise"] for x in sc if x["expertise"] != "Unclear"]
    return {
        "n": n,
        "prompt": {"score": round(pe, 1), "level": level(pe), "dims": {d: round(v, 2) for d, v in dims.items()},
                   "instructions": instructions, "landed": instructions - missed,
                   "clarify": miss_kinds.get("clarify", 0), "misread": miss_kinds.get("misread", 0),
                   "form_score": round(form, 1)},
        "context": {"score": round(ce, 1), "level": level(ce), "session_mean": round(ctx_mean, 2),
                    "setup_points": setup_points, "setup_rows": setup_rows},
        "agentic": {"score": round(ae, 1), "level": level(ae), "session_mean": round(ag_mean, 2),
                    "verified_success": round(verified, 3), "actions_per_prompt": round(app, 2)},
        "anthropic_compare": {
            "expertise_dist": {k: expertise.get(k, 0) for k in ("1", "2", "3", "4", "5", "Unclear")},
            "expertise_mean": round(mean(rated), 2) if rated else None,
            "verified_success": round(verified, 3),
            "partial_or_better": round(partial_plus, 3),
            "abandoned": round(abandoned, 3),
            "actions_per_prompt": round(app, 2),
        },
        "planning_by_user": dict(collections.Counter(x["planning_by_user"] for x in sc)),
        "execution_by_user": dict(collections.Counter(x["execution_by_user"] for x in sc)),
        "work_modes": dict(collections.Counter(x["work_mode"] for x in sc).most_common()),
        "rows": rows,
        "scores": sc,
    }


def render(manifest, r, problems):
    p, c, a, cmp_ = r["prompt"], r["context"], r["agentic"], r["anthropic_compare"]
    par = manifest["params"]
    out = []
    out.append("# 에이전틱 엔지니어링 점수 리포트\n")
    out.append("생성 {} · 채점 세션 {}개 (최근 {}일, 사람 발화 {}회 이상, 표본 seed {})\n".format(
        manifest["generated_at"], r["n"], par["days"], par["min_turns"], par["seed"]))
    if problems:
        out.append("> ⚠️ 채점이 없거나 형식이 틀린 파일 {}개가 있어 해당 세션은 빠졌다: {}\n".format(
            len(problems), ", ".join(sorted(problems))))
    spread = r.get("spread")
    if spread:
        out.append("세션마다 두 번 채점했다. 점수는 두 채점을 합친 값이고, 괄호는 각 채점을 따로 계산했을 때의 값이다. "
                   "통하지 않은 지시는 두 채점이 같은 발화를 짚었을 때만 셌다.\n")
        for key in ("prompt", "context", "agentic"):
            r[key]["shown"] = "{} ({}, {})".format(r[key]["score"], *spread[key])
    else:
        for key in ("prompt", "context", "agentic"):
            r[key]["shown"] = str(r[key]["score"])
    out.append("| 축 | 점수 | 레벨 | 구성 |")
    out.append("|---|---:|:---:|---|")
    out.append("| Prompt Engineering | {} | {} | 지시 {}개 중 {}개가 한 번에 통함 · 되물음 {} · 오해 {} |".format(
        p["shown"], p["level"], p["instructions"], p["landed"], p["clarify"], p["misread"]))
    out.append("| Context Engineering | {} | {} | 세션 맥락 {:.2f}/5 · 설정 점검 {}/100 |".format(
        c["shown"], c["level"], c["session_mean"], c["setup_points"]))
    out.append("| Agentic Engineering | {} | {} | 세션 위임 {:.2f}/5 · 검증된 성공 {} |".format(
        a["shown"], a["level"], a["session_mean"], pct(a["verified_success"])))
    out.append("")
    out.append("## Anthropic 연구 집계와 비교 (참고)\n")
    out.append("Anthropic \"Agentic Coding and Persistent Returns to Expertise\"(2026-06, 약 40만 세션)의 숫자다.\n")
    d = cmp_["expertise_dist"]
    out.append("| 지표 | 나 | Anthropic |")
    out.append("|---|---|---|")
    out.append("| 숙련도 분포 (1/2/3/4/5/Unclear) | {} · 평균 {} | 세션 단위 5단계 분류 |".format(
        "/".join(str(d[k]) for k in ("1", "2", "3", "4", "5", "Unclear")), cmp_["expertise_mean"]))
    out.append("| 검증된 성공률 | {} | 초보 15% · 중급 이상 28–33% |".format(pct(cmp_["verified_success"])))
    out.append("| 부분 성공 이상 | {} | 초보 77% · 중급 이상 91–92% |".format(pct(cmp_["partial_or_better"])))
    out.append("| 포기 (실패 + 편집 0) | {} | 초보 19% · 그 외 5–7% |".format(pct(cmp_["abandoned"])))
    out.append("| 프롬프트당 도구 호출 | {:.1f} | 초보 약 5 · 전문가 약 12 |".format(cmp_["actions_per_prompt"]))
    out.append("| 계획 결정을 사용자가 (most/half/few) | {} | 사용자가 계획 결정의 약 70% |".format(
        "/".join(str(r["planning_by_user"].get(k, 0)) for k in ("most", "half", "few"))))
    out.append("| 실행 결정을 사용자가 (most/half/few) | {} | 사용자가 실행 결정의 약 20% |".format(
        "/".join(str(r["execution_by_user"].get(k, 0)) for k in ("most", "half", "few"))))
    out.append("")
    out.append("## 맥락 설정 점검 ({}/100)\n".format(c["setup_points"]))
    out.append("| 항목 | 결과 | 배점 | 근거 |")
    out.append("|---|:---:|---:|---|")
    for label, ok, pts, detail in c["setup_rows"]:
        out.append("| {} | {} | {} | {} |".format(label, "✅" if ok else "—", pts if ok else 0, detail))
    out.append("")
    out.append("## 세션별 점수\n")
    out.append("도구별 세션 수: {}\n".format(", ".join("{} {}".format(k, v) for k, v in sorted(
        collections.Counter(s.get("source", "claude") for s in r["rows"]).items()))))
    out.append("작업 유형 분포: {}\n".format(", ".join("{} {}".format(k, v) for k, v in r["work_modes"].items())))
    out.append("| 시작 | 도구 | 프로젝트 | 유형 | 숙련 | 통한 지시 | 맥락 | 위임 | 결과 | 사람 발화 | 도구 호출 |")
    out.append("|---|---|---|---|:---:|:---:|:---:|:---:|---|---:|---:|")
    for s, x in zip(r["rows"], r["scores"]):
        out.append("| {} | {} | {} | {} | {} | {} | {:g} | {:g} | {}{} | {} | {} |".format(
            (s.get("start") or "")[:10], s.get("source", "claude"), project_name(s), x["work_mode"], x["expertise"],
            "{}/{}".format(x["prompt"]["instructions"] - len(x["prompt"]["misses"]), x["prompt"]["instructions"]),
            x["context"]["score"], x["agentic"]["score"],
            x["outcome"], " ✔" if x["hard_signal"] else "", s["human_turns"], s["tool_calls"]))
    out.append("")
    out.append("## 한 번에 통하지 않은 지시\n")
    out.append("문장 형태 참고 점수(축 점수에 들어가지 않음): {} — {}\n".format(
        p["form_score"], " · ".join("{} {:.1f}".format(DIM_KO[d], v) for d, v in p["dims"].items())))
    for s, x in zip(r["rows"], r["scores"]):
        for m in x["prompt"]["misses"]:
            out.append("- `{}` {} {} — {}".format(s["id"][:8], m["turn"], MISS_KO[m["kind"]], m["evidence"].strip()[:120]))
    if not p["clarify"] + p["misread"]:
        out.append("- 없음")
    out.append("")
    out.append("## 세션별 강점과 보완점\n")
    for s, x in zip(r["rows"], r["scores"]):
        out.append("- **{}** `{}`".format(project_name(s), s["id"][:8]))
        out.append("  - 강점: {}".format(x.get("strength", "-")))
        out.append("  - 보완: {}".format(x.get("gap", "-")))
    out.append("")
    out.append("## 해석할 때 주의\n")
    out.append("- 세션 하나의 라벨은 흔들린다. Anthropic 검증에서 5단계 점수가 기준 모델과 정확히 일치한 비율은 53–68%였다(한 칸 차이까지 허용하면 78–99%). 개별 세션보다 전체 경향을 보라.")
    out.append("- 세 축의 가중치와 설정 점검 배점은 이 스킬의 휴리스틱이다(`rubric.md`). 다른 사람과 비교하려면 같은 버전·같은 설정으로 돌려라.")
    out.append("- 자동화가 주입한 메시지를 걸러 낸 뒤 사람 발화만 채점했다. 제외한 머리말: {}".format(
        ", ".join("`{}`".format(x) for x in par["exclude_prefixes"]) or "없음"))
    out.append("")
    return "\n".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("out_dir")
    ap.add_argument("--passes", type=int, choices=(1, 2), help="default: 2 if any <id>.b.json exists, else 1")
    args = ap.parse_args(argv)
    if args.passes is None:
        args.passes = 2 if glob.glob(os.path.join(args.out_dir, "scores", "*.b.json")) else 1
    with open(os.path.join(args.out_dir, "manifest.json"), encoding="utf-8") as fh:
        manifest = json.load(fh)
    with open(os.path.join(args.out_dir, "context_audit.json"), encoding="utf-8") as fh:
        audit = json.load(fh)
    by_pass, problems = load_scores(args.out_dir, manifest["sessions"], args.passes)
    for name, errs in sorted(problems.items()):
        print("needs rescoring: {} ({})".format(name, ", ".join(errs)))
    if not by_pass:
        print("no complete score sets yet")
        return 2
    r = compute(manifest, audit, {sid: merge(passes) for sid, passes in by_pass.items()})
    if args.passes == 2:
        singles = [compute(manifest, audit, {sid: passes[i] for sid, passes in by_pass.items()}) for i in (0, 1)]
        r["spread"] = {k: [x[k]["score"] for x in singles] for k in ("prompt", "context", "agentic")}
    report = render(manifest, r, problems)
    with open(os.path.join(args.out_dir, "report.md"), "w", encoding="utf-8") as fh:
        fh.write(report)
    summary = {k: v for k, v in r.items() if k not in ("rows", "scores")}
    summary["context"] = {k: v for k, v in r["context"].items() if k != "setup_rows"}
    summary["passes"] = args.passes
    summary["unscored"] = sorted(problems)
    with open(os.path.join(args.out_dir, "summary.json"), "w", encoding="utf-8") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=2)
    print("report: {}".format(os.path.join(args.out_dir, "report.md")))
    print("Prompt {} {} · Context {} {} · Agentic {} {} (n={})".format(
        r["prompt"]["score"], r["prompt"]["level"], r["context"]["score"], r["context"]["level"],
        r["agentic"]["score"], r["agentic"]["level"], r["n"]))
    return 2 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
