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
| Prompt Engineering | 82.5 | L4 | 지시 80개 중 66개가 한 번에 통함 · 되물음 9 · 오해 5 |
| Context Engineering | 71.3 | L3 | 세션 맥락 3.50/5 · 설정 점검 80/100 |
| Agentic Engineering | 59.0 | L2 | 세션 위임 3.60/5 · 검증된 성공 35% |

리포트에는 이 표와 함께 Anthropic 연구 집계와의 비교, 설정 점검표, 세션별 점수, 한 번에 통하지 않은 지시 목록과 원문 인용, 강점 3개와 다음에 바꿀 행동 3개가 들어간다.

## 무엇을 보나

| 축 | 근거 |
|---|---|
| Prompt Engineering | 지시가 한 번에 통한 비율. 에이전트가 정보가 없어 되묻거나(되물음) 다르게 이해해 다시 설명해야 했던(오해) 지시를 뺀다. 문장이 짧은지 긴지는 보지 않는다 |
| Context Engineering | 세션에서 맥락을 고르고 주는 방식(1–5) + 이 머신의 설정 점검(CLAUDE.md·스킬·서브에이전트·훅·MCP·메모리·권한 규칙) |
| Agentic Engineering | 위임의 크기와 안전장치(1–5) + 검증된 성공률(커밋·통과한 테스트·사용자 확인 같은 신호가 transcript에 있는 성공) |

Anthropic의 세션 숙련도 5단계 분류도 함께 매기고, Anthropic 연구의 집계(검증된 성공률, 포기 비율, 프롬프트당 행동 수, 계획·실행 결정 귀속)와 나란히 보여 준다. 기준 전문과 합산 식은 [`rubric.md`](agentic-eng-score/rubric.md)에 있다.

## 동작

1. `collect.py`가 `~/.claude/projects/*/*.jsonl`에서 **사람이 이끈 세션**만 고른다.
   - headless(`claude -p`·SDK) 세션, 사람 발화가 3회 미만인 세션, 동기화 충돌·fork로 생긴 중복 사본은 뺀다.
   - 봇·오케스트레이터가 사용자 역할로 넣은 메시지는 `--survey`로 반복 머리말을 찾아 제외한다.
2. 고른 세션마다 사람 발화를 중심으로 transcript를 3만 자 이내로 줄인다.
3. `context_audit.py`가 설정 파일의 **이름과 개수만** 센다. 설정값(토큰·env)은 읽어 오지 않는다.
4. 서브에이전트(sonnet)가 `rubric.md`대로 세션마다 JSON 점수를 쓴다.
5. `aggregate.py`가 점수 형식을 검증하고, 근거로 인용한 문장이 실제 transcript에 있는지 대조한 뒤 `report.md`, `summary.json`을 만든다.

20세션을 채점하는 데 서브에이전트 4개, 토큰은 합쳐서 50만 개 안팎이 든다.

기본은 세션당 한 번 채점이다. "두 번 채점해줘"라고 하면 독립된 채점자 둘이 같은 세션을 따로 채점하고, 숫자 점수는 평균하고, 통하지 않은 지시는 둘 다 같은 발화를 짚었을 때만 세고, 성공 여부는 낮은 쪽을 따른다. 비용은 두 배다. 20세션으로 두 번 채점해 비교했을 때 두 채점의 축 점수 차이는 1.3점 이내였고, 세션 단위로는 20개 중 6개에서 맥락 점수가 한 칸씩 갈렸다. 그래서 표본이 10세션 미만일 때만 스킬이 알아서 두 번 채점한다.

결과는 `~/.claude/usage-data/agentic-eng-score/<시각>/`에 남는다. transcript에는 비밀값이 섞여 있을 수 있으므로 이 폴더를 그대로 공유하지 말고, 공유할 때는 `report.md`의 점수 표만 옮긴다.

스크립트는 스킬 없이도 쓸 수 있다.

```bash
python3 agentic-eng-score/scripts/collect.py --survey
python3 agentic-eng-score/scripts/collect.py --help
```

## 요구 사항

- Claude Code (서브에이전트 사용 가능 권장)
- Python 3.9 이상, 표준 라이브러리만 사용
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

- 세션 하나의 라벨은 흔들린다. Anthropic의 검증에서도 5단계 점수가 기준 모델과 정확히 일치한 비율은 53–68%였다. 20세션을 평균한 축 점수는 다시 채점해도 1–2점 안에서 움직였지만, 세션별 점수는 한 칸씩 달라진다. 전체 경향으로 읽어야 한다.
- 긴 세션은 3만 자로 줄여서 채점한다. 잘려 나간 구간의 되물음이나 실패는 채점자에게 보이지 않는다.
- 초보자의 기록으로 점수 분포를 확인하지 못했다. 높은 점수가 얼마나 드문지는 이 도구로 알 수 없다.
- 세 축의 가중치와 설정 점검 배점은 이 스킬이 정한 휴리스틱이고, 사람 평가와 대조해 검증하지 않았다. 다른 사람과 비교하려면 같은 버전으로 돌려야 한다.
- 다른 세션이나 스크립트가 틀 없이 넣은 첫 메시지는 사람 발화와 구분하지 못한다.
- 이 머신에 남아 있는 기록만 본다. claude.ai 웹에서 한 작업은 들어가지 않는다.
- 채용·인사 평가 용도로 만들지 않았다. 자기 점검용이다.

## License

MIT. `rubric.md`에 인용한 숙련도 분류 기준은 Anthropic 연구 부록에서 발췌했다.
