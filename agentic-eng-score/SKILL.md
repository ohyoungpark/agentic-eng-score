---
name: agentic-eng-score
description: Use when someone asks to measure, score, or level their own prompt engineering, context engineering, or agentic engineering skill from their coding-agent session history (Claude Code, Codex CLI, opencode, Devin CLI) — "에이전틱 엔지니어링 점수", "내 AI 활용 실력 측정", "프롬프트 실력 평가해줘", "코딩 에이전트 숙련도", "how good am I at agentic coding", "score my agent sessions".
---

# 에이전틱 엔지니어링 점수

이 머신에 남아 있는 코딩 에이전트 세션 기록에서 **사람이 이끈 세션**을 표본으로 뽑아 세 축을 0–100으로 채점한다: Prompt Engineering(지시가 한 번에 통했는가), Context Engineering, Agentic Engineering. 세션 채점 기준은 `rubric.md`에 있고, 맥락 설정(CLAUDE.md·AGENTS.md·스킬·훅·MCP·메모리)은 파일을 직접 세서 점검한다.

읽는 기록은 네 가지다. 있는 것만 알아서 읽는다.

| 도구 | 기록 위치 | 상태 |
|---|---|---|
| Claude Code | `~/.claude/projects/*/*.jsonl` | 검증됨 |
| Codex CLI | `~/.codex/sessions/**/rollout-*.jsonl[.zst]` | 검증됨. `.zst`는 Python 3.14+ 또는 `zstd` 명령이 있어야 읽는다 |
| opencode | `~/.local/share/opencode/opencode.db` | 검증됨 |
| Devin CLI | `~/.local/share/devin/cli/sessions.db` | 실험적. 실제 기록으로 확인하지 못했다. 결과에 Devin 세션이 있으면 보고에 이 점을 적는다 |

한 도구만 보려면 수집 명령에 `--source claude|codex|opencode|devin`을 붙인다.

**모든 데이터는 이 머신 안에만 둔다.** transcript에 비밀값이 섞여 있을 수 있다. 결과물을 외부 서비스에 올리거나 보내지 않는다.

`<skill-dir>`는 이 스킬의 `SKILL.md`가 있는 폴더다. 스크립트는 Python 3.9+ 표준 라이브러리만 쓴다.

## 절차

### 1. 자동화 메시지 조사

```bash
python3 "<skill-dir>/scripts/collect.py" --survey
```

봇·오케스트레이터·cron이 사용자 역할로 넣은 메시지는 사람 발화가 아니다. 출력의 "most repeated message prefixes"를 보고 **여러 세션에 같은 틀로 반복되는 머리말**(이모지 헤더, "새 알림이 왔어" 같은 템플릿, 봇이 붙이는 고정 문구)을 고른다. 같은 계열이 끝부분만 달라 여러 줄로 나오면 공통되는 가장 짧은 머리말 하나로 묶는다. 사람이 자주 쓰는 짧은 말("진행해", "응")은 남긴다.

목록 위쪽은 가장 흔한 봇 메시지가 차지해서, 덜 흔한 봇 메시지는 첫 조사에 보이지 않는다. 고른 머리말을 `--exclude-prefix`로 넣고 `--survey`를 다시 돌린다. 새로 드러난 봇 머리말이 없을 때까지 반복한다.

```bash
python3 "<skill-dir>/scripts/collect.py" --survey --exclude-prefix "📣 봇 알림" --exclude-prefix "새 멘션이 왔어"
```

최종 목록을 사용자에게 한 줄로 알리고 진행한다. 첫 조사에서 반복 머리말이 없으면 이 단계는 건너뛴다.

### 2. 표본 수집

```bash
python3 "<skill-dir>/scripts/collect.py" --exclude-prefix "📣 봇 알림" --exclude-prefix "새 멘션이 왔어"
```

기본값: 최근 30일, 사람 발화 3회 이상, headless 세션(`claude -p`, SDK, `codex exec`, 서브에이전트 세션) 제외, 20세션, 프로젝트당 최대 3개, seed 7. 사용자가 기간이나 표본 수를 말했으면 `--days`, `--sample`로 맞춘다. 출력의 `out:` 경로가 이후의 `<out-dir>`이고, `batch N:` 줄이 채점 묶음이다.

eligible이 5개 미만이면 `--days 90`이나 `--min-turns 2`로 넓히고, 그래도 적으면 표본이 작다는 사실을 리포트에 남긴다.

### 3. 맥락 설정 점검

```bash
python3 "<skill-dir>/scripts/context_audit.py" "<out-dir>"
```

### 4. 세션 채점 — 묶음마다 서브에이전트 하나, 병렬로

묶음마다 서브에이전트를 하나씩 띄워 동시에 돌린다. Claude Code에서는 `general-purpose` 서브에이전트를 `model: sonnet`으로 한 메시지 안에서 모두 호출한다. 다른 도구에서는 그 도구의 서브에이전트 기능을 쓴다. 지시문은 아래 형식을 그대로 쓰고, 꺾쇠 안만 채운다. `<suffix>`는 빈 문자열이다.

```text
너는 코딩 에이전트 세션 채점자다. 먼저 <skill-dir>/rubric.md 를 끝까지 읽어라.
그다음 아래 transcript를 하나씩 읽고, 세션마다 rubric의 "출력 JSON" 형식 그대로
<out-dir>/scores/<session_id><suffix>.json 파일로 저장하라.
- <out-dir>/transcripts/<id1>.txt
- <out-dir>/transcripts/<id2>.txt
(묶음의 나머지 id)
세션끼리 점수를 맞추지 말고 rubric 기준으로 하나씩 따로 채점하라.
scores 폴더에 이미 있는 다른 파일은 읽지 마라.
인용은 transcript의 한 줄에서 연속된 원문 그대로 가져오고 중간을 줄이지 마라.
다 저장했으면 "scored: <id 목록>" 한 줄만 답하라.
```

서브에이전트를 쓸 수 없는 환경이면 같은 지시를 묶음 하나씩 직접 수행한다. 한 묶음을 끝낼 때마다 점수 파일을 저장하고 다음 묶음으로 넘어간다.

**두 번 채점하는 경우.** 표본이 10세션 미만이거나 사용자가 "두 번 채점해줘"라고 하면, 묶음마다 서로 독립된 서브에이전트를 하나 더 띄우고 그쪽 지시문의 `<suffix>`를 `.b`로 한다. 표본이 작으면 한 세션의 판단 차이가 축 점수를 몇 점씩 움직이기 때문이다. 합산 스크립트는 `.b.json` 파일이 있으면 두 채점을 알아서 합친다.

### 5. 합산

```bash
python3 "<skill-dir>/scripts/aggregate.py" "<out-dir>"
```

`needs rescoring:` 줄이 나오면 거기 적힌 파일만 4단계 지시문으로 다시 채점한 뒤 합산을 다시 돌린다. 종료 코드 0이 될 때까지 반복한다.

### 6. 총평을 붙이고 보고

`<out-dir>/report.md`와 `<out-dir>/scores/*.json`의 `strength`·`gap`을 읽고, report.md 맨 끝에 `## 총평` 절을 덧붙인다. 총평은 다음 세 부분으로 쓴다.

1. 한 문단 요약: 세 축 가운데 가장 강한 축과 가장 약한 축, 그리고 그렇게 본 근거가 된 세션 패턴
2. 강점 3개: 각각 근거 세션 id 앞 8자리
3. 다음에 바꿀 행동 3개: 각각 구체적인 행동 하나와 근거 세션 id 앞 8자리

사용자에게는 세 축 표(점수·레벨), 강점과 바꿀 행동 각 3개, report.md 경로를 보여 준다.

## 빠른 참조

| 단계 | 명령 | 산출물 |
|---|---|---|
| 조사 | `collect.py --survey` | 반복 머리말 목록 |
| 수집 | `collect.py --exclude-prefix …` | `manifest.json`, `transcripts/*.txt` |
| 점검 | `context_audit.py <out-dir>` | `context_audit.json` |
| 채점 | 서브에이전트 | `scores/<id>.json` (두 번 채점하면 `<id>.b.json` 추가) |
| 합산 | `aggregate.py <out-dir>` | `report.md`, `summary.json` |

## 흔한 실수

- 조사 단계를 건너뛰면 봇이 넣은 메시지가 "사람 발화"로 채점되어 점수가 엉뚱해진다.
- 채점을 메인 세션에서 한꺼번에 하면 transcript 수십만 자가 메인 컨텍스트를 채운다. 묶음별 서브에이전트로 나눈다.
- 서브에이전트가 JSON 키 이름을 바꾸면 합산에서 빠진다. `needs rescoring`이 나온 세션만 다시 채점한다.
- 리포트 숫자를 세션 하나의 평가로 읽지 않는다. 세션 단위 라벨은 흔들리고, 의미 있는 것은 전체 경향이다.
