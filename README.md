# agentic-eng-score

A Claude Code skill that scores **your own** prompt engineering, context engineering, and agentic engineering from your local Claude Code session history. Everything runs on your machine. The skill instructions and the report are written in Korean.

Claude Code 세션 기록으로 **Prompt Engineering · Context Engineering · Agentic Engineering** 세 축을 0–100점, L1–L5로 매기는 스킬이다. 기록은 머신 밖으로 나가지 않는다.

## 설치

```bash
git clone https://github.com/ohyoungpark/agentic-eng-score.git
cp -r agentic-eng-score/agentic-eng-score ~/.claude/skills/
```

`~/.claude/skills/agentic-eng-score/SKILL.md`가 생기면 된다. Claude Code를 새로 열고 이렇게 말한다.

> 내 에이전틱 엔지니어링 점수 매겨줘

기간이나 표본 수를 바꾸려면 "최근 60일, 30세션으로"처럼 덧붙인다.

## 결과 예시

숫자는 형식을 보여 주기 위한 예시다.

| 축 | 점수 | 레벨 | 구성 |
|---|---:|:---:|---|
| Prompt Engineering | 62.0 | L3 | 명확성 6.5 · 구체성 6.0 · 실행 가능성 6.0 · 범위 6.3 |
| Context Engineering | 71.3 | L3 | 세션 맥락 3.50/5 · 설정 점검 80/100 |
| Agentic Engineering | 68.4 | L3 | 세션 위임 3.60/5 · 검증된 성공 35% · 프롬프트당 도구 호출 9.2 |

리포트에는 이 표와 함께 Anthropic 연구 집계와의 비교, 설정 점검표, 세션별 점수와 근거 인용, 강점 3개와 다음에 바꿀 행동 3개가 들어간다.

## 무엇을 보나

| 축 | 근거 |
|---|---|
| Prompt Engineering | 작업을 거는 발화의 명확성·구체성·실행 가능성·범위 (각 0–10) |
| Context Engineering | 세션에서 맥락을 고르고 주는 방식(1–5) + 이 머신의 설정 점검(CLAUDE.md·스킬·서브에이전트·훅·MCP·메모리·권한 규칙) |
| Agentic Engineering | 위임의 크기와 안전장치(1–5) + 검증된 성공률 + 프롬프트당 도구 호출 수 |

Anthropic의 세션 숙련도 5단계 분류도 함께 매기고, Anthropic 연구의 집계(검증된 성공률, 포기 비율, 프롬프트당 행동 수, 계획·실행 결정 귀속)와 나란히 보여 준다. 기준 전문과 합산 식은 [`rubric.md`](agentic-eng-score/rubric.md)에 있다.

## 동작

1. `collect.py`가 `~/.claude/projects/*/*.jsonl`에서 **사람이 이끈 세션**만 고른다.
   - headless(`claude -p`·SDK) 세션, 사람 발화가 3회 미만인 세션, 동기화 충돌·fork로 생긴 중복 사본은 뺀다.
   - 봇·오케스트레이터가 사용자 역할로 넣은 메시지는 `--survey`로 반복 머리말을 찾아 제외한다.
2. 고른 세션마다 사람 발화를 중심으로 transcript를 3만 자 이내로 줄인다.
3. `context_audit.py`가 설정 파일의 **이름과 개수만** 센다. 설정값(토큰·env)은 읽어 오지 않는다.
4. 서브에이전트(sonnet)가 `rubric.md`대로 세션마다 JSON 점수를 쓴다.
5. `aggregate.py`가 점수 형식을 검증하고, 근거로 인용한 문장이 실제 transcript에 있는지 대조한 뒤 `report.md`, `summary.json`을 만든다.

결과는 `~/.claude/usage-data/agentic-eng-score/<시각>/`에 남는다. transcript에는 비밀값이 섞여 있을 수 있으므로 이 폴더를 그대로 공유하지 말고, 공유할 때는 `report.md`의 점수 표만 옮긴다.

스크립트는 스킬 없이도 쓸 수 있다.

```bash
python3 agentic-eng-score/scripts/collect.py --survey
python3 agentic-eng-score/scripts/collect.py --help
```

## 요구 사항

- Claude Code (서브에이전트 사용 가능 권장)
- Python 3.8 이상, 표준 라이브러리만 사용
- macOS에서 확인했다(Python 3.9, 3.14). 테스트는 CI에서 Linux로도 돈다. Windows는 확인하지 않았다.

## 개발

```bash
python3 -m unittest discover -s tests -v
```

## 근거 자료

- Anthropic, "Agentic Coding and Persistent Returns to Expertise" (2026-06) 본문과 부록. 숙련도 분류기 원문, 결정 귀속, 검증된 성공의 정의를 가져왔다.
  - https://www.anthropic.com/research/claude-code-expertise
- 프롬프트 네 항목(Clarity·Specificity·Actionability·Scope)은 Claude Code Prompt Coach 스킬의 구성을 따랐다.
  - https://github.com/hancengiz/claude-code-prompt-coach-skill

## 한계

- 세션 하나의 라벨은 흔들린다. Anthropic의 검증에서도 5단계 점수가 기준 모델과 정확히 일치한 비율은 53–68%였다. 같은 transcript를 두 번 채점하면 축 점수가 몇 점씩 달라진다. 전체 경향으로 읽어야 한다.
- 세 축의 가중치와 설정 점검 배점은 이 스킬이 정한 휴리스틱이고, 사람 평가와 대조해 검증하지 않았다. 다른 사람과 비교하려면 같은 버전으로 돌려야 한다.
- 규칙 파일·스킬·메모리에 맥락을 실어 두고 짧게 지시하는 사람은 Prompt 축이 낮게 나오는 경향이 있다.
- 이 머신에 남아 있는 기록만 본다. claude.ai 웹에서 한 작업은 들어가지 않는다.
- 채용·인사 평가 용도로 만들지 않았다. 자기 점검용이다.

## License

MIT. `rubric.md`에 인용한 숙련도 분류 기준은 Anthropic 연구 부록에서 발췌했다.
