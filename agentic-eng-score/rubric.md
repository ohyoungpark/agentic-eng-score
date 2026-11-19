# 세션 채점 기준

채점 대상은 **사용자(사람)**다. Claude가 얼마나 잘했는지, 작업이 얼마나 어려웠는지는 점수에 넣지 않는다. 이 세션에서 사용자가 보여 준 지시, 맥락 제공, 위임 방식만 본다.

## transcript 읽는 법

| 태그 | 뜻 |
|---|---|
| `[U3]` | 사용자의 세 번째 발화 (사람이 쓴 글) |
| `[PASTED n chars]` | 사용자가 붙여 넣은 자료 (앞부분만 보임) |
| `[/]` | 사용자가 친 슬래시 명령 (`/clear`, `/compact`, `/review` …) |
| `[!]` | 사용자가 직접 실행한 셸 명령 |
| `[A]` | Claude의 답변 텍스트 |
| `[T]` | Claude의 도구 호출 (Bash 명령, 편집한 파일, 서브에이전트, 스킬 …) |
| `[E]` | 도구 오류 또는 사용자의 중단 |

- 헤더의 human turns·tool calls·subagents·tests·commits는 로그에서 센 사실이다. 판단의 보조 근거로 써라.
- 사용자가 에이전트의 질문에 "진행해", "ok", "고고"처럼 짧게 답한 것은 정상이다. 감점하지 마라. 프롬프트 점수는 **작업을 거는 발화**와 **방향을 바꾸는 발화**로 매긴다.
- 발화는 **그 시점의 맥락에서** 평가한다. 앞선 대화, 규칙 파일, 스킬, 프로젝트 기록이 이미 정해 둔 것을 다시 쓰지 않았다고 감점하지 마라. 감점 근거는 에이전트가 실제로 추측하거나, 되묻거나, 엇나가서 사용자가 바로잡은 흔적이다.
- 근거(`evidence`)는 `[U]` 줄에서 원문 그대로 120자 이내로 인용하고 앞에 발화 번호를 붙인다 (예: `U3: "…"`). `[U]` 줄에 근거가 없을 때만 `[T]` 줄을 요약해 적는다.

## 1. Prompt Engineering — 지시가 한 번에 통했는가

문장의 생김새가 아니라 결과를 센다. 짧은 지시라도 에이전트가 되묻지 않고 맞게 수행했으면 통한 것이다.

**지시(`instructions`)** 는 에이전트가 무언가를 해야 하는 사용자 발화다. 새 작업을 거는 발화, 방향을 바꾸는 발화, 조사·확인을 시키는 질문이 해당한다. 다음은 세지 않는다: 승인("진행해", "머지해"), 에이전트의 질문에 대한 답, 진행 확인("끝?", "현재 상황?"), 감탄·혼잣말.

**통하지 않은 지시(`misses`)** 는 두 종류뿐이다.

- `clarify`: 지시에 필요한 정보가 없어서 에이전트가 되물었다. 또는 에이전트가 추측으로 채웠다고 스스로 밝혔다.
- `misread`: 에이전트가 지시를 다르게 이해해 다른 일을 했고, 사용자가 다시 설명해야 했다. 그 시점에 보이던 맥락으로 에이전트의 해석이 합리적이었을 때만 센다.

다음은 `misses`가 아니다.

- 승인 게이트, 위험한 작업 전 확인, 사용자만 내릴 수 있는 결정을 묻는 질문
- 지시는 분명했는데 에이전트가 사실·도메인을 틀려서 사용자가 바로잡은 것 (이것은 숙련도의 교정 방향 신호다)
- 사용자가 결과를 보고 마음을 바꾸거나 요구를 더한 것
- 도구 오류, 권한 문제로 생긴 재시도

에이전트의 질문에 사용자가 답한 발화("사내", "2번", "Fulfillment")는 지시가 아니므로 `misses`가 될 수 없다. 에이전트가 되물었다면 그 되물음을 부른 **앞선 지시**가 `misses`다.

`misses`의 각 항목에는 해당 사용자 발화 번호(`turn`)와 그 발화의 원문 인용(`evidence`, 120자 이내, 따옴표 없이 원문만)을 적는다. transcript가 잘려서 지시 뒤의 전개가 보이지 않으면 통한 것으로 센다.

아래 네 항목(각 0–10)은 **참고용**으로만 매긴다. 축 점수에는 들어가지 않는다.

| 항목 | 0–2 | 5 | 8–10 |
|---|---|---|---|
| `clarity` 명확성 | "고쳐줘", "이거 왜 이래" | 목표는 보이지만 해석이 갈림 | 원하는 결과와 완료 상태가 한 번에 읽힘 |
| `specificity` 구체성 | 대상·제약 없음 | 대상은 있으나 제약·형태가 빠짐 | 그 분야를 알아야 꺼낼 수 있는 제약·불변식·엣지 케이스·입출력 형태를 명시 |
| `actionability` 실행 가능성 | 되묻지 않으면 시작할 수 없음 | 시작은 되지만 성공 기준이 없음 | 성공 기준·검증 방법·범위 밖을 줘서 바로 끝까지 갈 수 있음 |
| `scope` 범위 | 거대하고 모호한 한 덩어리, 또는 한 줄씩 손으로 시키는 마이크로 지시 | 대체로 적당하나 가끔 너무 크거나 잘게 쪼갬 | 한 요청이 검증 가능한 한 단위 |

화면에 보이는 파일명·경로를 나열한 것은 구체성이 아니다. 누구나 할 수 있다.

## 2. Context Engineering — 1–5

에이전트가 무엇을 보게 하고 무엇을 치우는지를 설계하는 능력이다.

- **1** 맥락 없이 던진다. 에이전트가 추측한다. 같은 설명을 여러 번 반복한다.
- **2** 파일 정도만 지목한다. 목표·제약·배경이 빠진다. 또는 큰 로그를 거르지 않고 통째로 붙인다.
- **3** 목표, 관련 파일, 제약, 참고 문서를 갖춰서 준다.
- **4** 고른 맥락을 의도적으로 준다. 스펙·플랜·규칙 파일을 가리키고 수용 기준을 준다. CLAUDE.md 규칙, 메모리, 스킬이 세션을 이끄는 흔적이 있다. 맥락이 흐려지면 바로잡는다. 큰 탐색은 서브에이전트나 다른 세션으로 뺀다.
- **5** 맥락이 시스템으로 설계돼 있다. 지속 규칙, 스킬, 메모리, 핸드오프 문서가 에이전트의 행동을 결정하고, 에이전트가 그것을 인용하며 따른다. 세션 범위가 깔끔하다(`/clear`, 작업 단위 분리). 사용자가 에이전트가 볼 것을 설계한다.

## 3. Agentic Engineering — 1–5

얼마나 큰 일을, 어떤 안전장치를 걸고 맡기는가를 본다.

- **1** 단발 질의응답이거나, 한 단계씩 손으로 지시한다. 실행은 사용자가 직접 한다. 검증이 없다.
- **2** 작은 수정만 맡기고 눈으로 확인한다. 자주 끼어든다.
- **3** 작업 하나를 완료 기준과 함께 통째로 맡긴다. 에이전트가 테스트와 명령을 돌린다.
- **4** 여러 단계의 작업을 끝까지 맡기고 검증 루프(테스트·커밋·PR·리뷰)를 건다. 서브에이전트, 병렬 호출, 플랜 모드, 도구를 적절히 쓰게 한다. 사용자는 결정·승인 지점에만 들어온다.
- **5** 에이전트 체계를 운영한다. 여러 에이전트·세션을 조율하고, 훅·게이트·eval·백테스트 같은 자동 안전장치를 두고, 긴 자율 실행을 결정만으로 이끈다. 하네스 자체를 설계하고 고친다.

## 4. Expertise — 1–5 또는 Unclear

Anthropic "Agentic Coding and Persistent Returns to Expertise"(2026-06) 부록의 분류기 원문이다. 직업이 아니라 **이 세션의 작업**에 대한 숙련도를 본다.

> Weigh these three signals TOGETHER — they are co-equal, not ranked:
> SETUP SPECIFICITY — does the user frame the problem using named entities and constraints that require domain knowledge to even reach for? Naming files or paths that are visible on screen is NOT domain knowledge.
> VERIFICATION TYPE — Generic asks ('please double-check', 'are you sure?') are EPISTEMIC HUMILITY, not expertise. Targeted asks ('did you actually call commit()?', 'what is the cardinality of that join?') require knowing WHAT to check. Rate the TYPE, not the presence.
> DIRECTION OF CORRECTION — If Claude has to correct the user's terminology, mental model, or approach, that pulls the rating DOWN toward 1-2. If the user catches Claude's domain mistakes or steers away from a wrong approach Claude proposed, that pulls UP toward 4-5.
>
> - 1: Novice. Framing is generic or imprecise; does not use domain-specific names. Verification asks are absent or entirely generic. Does not notice when Claude produces wrong output. Claude has to supply or correct basic domain concepts.
> - 2: Beginner. Some correct terminology, but loosely or with occasional mis-use. Mostly generic verification. Pushes back on obviously wrong outputs but misses subtle ones. Claude corrects or reframes the user's approach at least once. A fluent technologist working OUTSIDE their domain lands here.
> - 3: Intermediate. Framing is precise at a directive level — names the files, the outputs, the libraries — but does not engage with methodology or tradeoffs. Mix of generic and targeted verification. Neither side is consistently correcting the other. A domain expert delegating a routine task can also land here.
> - 4: Advanced. Framing shows structural domain knowledge NOT readable off the screen: a specific edge case, non-obvious constraint or invariant, version-specific behavior, or known failure mode. Verification is moderately specific at least once. The user catches at least one of Claude's domain mistakes; Claude does not have to correct the user's domain model.
> - 5: Expert. ANY of: insider-only naming, unprompted discussion of tradeoffs, surgical verification asks, authoritative corrections invoking specific technical reasoning, preemptive edge-case handling, or test designs targeting specific failure modes. One or two is enough. Direction of correction is user→Claude, never Claude→user. The defining vibe is 'insider talking to an equal'.
> - Unclear: the user's contribution is so thin that NO direction-of-expertise signal exists. When ANY framing is present, pick 1-5.

## 5. 결정 귀속 (Anthropic)

- `planning_by_user`: **무엇을 할지**(목표·접근·범위·우선순위)를 누가 정했나. `most` / `half` / `few`
- `execution_by_user`: **어떻게 할지**(구현 세부·파일·명령)를 누가 정했나. `most` / `half` / `few`

## 6. 결과 (Anthropic)

- `outcome`: `Succeeded` / `Partial` / `Failed` / `No clear goal`
- `hard_signal`: 목표와 맞는 커밋, 통과한 테스트, 목표와 맞는 명령 출력, 사용자의 명시적 확인("됐다", "확인했어") 가운데 하나라도 transcript에 있으면 `true`다. **"에이전트가 열심히 일했다"와 "성공이 검증됐다"를 혼동하지 마라.** 에이전트가 "완료했습니다"라고 말한 것은 신호가 아니다.
- `hard_signal_evidence`: `hard_signal`이 `true`면 그 신호가 보이는 transcript 줄(`[T]`, `[U]`, `[A]` 어느 것이든)에서 연속된 원문을 120자 이내로 인용한다. 줄임표로 중간을 생략하지 않는다. `false`면 빈 문자열이다.

## 7. 작업 유형

`work_mode`: 분량이 가장 많은 활동 하나를 고른다. 첫 발화의 의도가 아니라 실제로 한 일로 정한다.
`Plan`(설계·스펙) · `Build`(코드가 산출물) · `Fix`(고장 수리) · `Understand`(코드 이해·조사) · `Test`(테스트·리뷰·QA·보안 점검) · `Operate`(배포·CI·인프라·설정·모니터링) · `Analyze`(데이터 분석) · `Orchestrate`(에이전트·프롬프트·eval 자체를 만드는 일) · `Communicate`(사람이 읽을 글·문서)

## 출력 JSON

세션 하나에 파일 하나: `<out-dir>/scores/<session_id>.json`. 키 이름과 값의 종류를 그대로 지킨다.

```json
{
  "session_id": "3f2a…",
  "work_mode": "Build",
  "expertise": 4,
  "prompt": {
    "instructions": 6,
    "misses": [{"turn": "U4", "kind": "clarify", "evidence": "그 발화의 원문 인용"}],
    "clarity": 7, "specificity": 6, "actionability": 8, "scope": 7,
    "evidence": "U1: \"…\""
  },
  "context": {"score": 3, "evidence": "U2: \"…\""},
  "agentic": {"score": 4, "evidence": "T: 테스트 → 커밋 → PR까지 맡김"},
  "planning_by_user": "most",
  "execution_by_user": "few",
  "outcome": "Succeeded",
  "hard_signal": true,
  "hard_signal_evidence": "git commit -m 'retry'",
  "strength": "이 세션에서 가장 잘한 점 한 문장",
  "gap": "가장 아쉬운 점과 다음에 바꿀 행동 한 문장"
}
```

`expertise`만 정수 대신 `"Unclear"`를 허용한다. 나머지 숫자 칸은 모두 정수다. `misses`가 없으면 빈 배열 `[]`을 쓴다. `kind`는 `clarify` 또는 `misread`다.

## 합산 방식 (`aggregate.py`가 계산한다)

| 축 | 계산 |
|---|---|
| Prompt Engineering | 전체 세션의 (지시 수 − 통하지 않은 지시 수) ÷ 지시 수 × 100 |
| Context Engineering | 0.5 × 세션 맥락 점수(1–5 → 0–100) + 0.5 × 설정 점검 점수(0–100) |
| Agentic Engineering | 0.8 × 세션 위임 점수(1–5 → 0–100) + 0.2 × 검증된 성공률(0–100%) |

레벨: 90 이상 L5 · 75–89 L4 · 60–74 L3 · 40–59 L2 · 40 미만 L1.
가중치는 이 스킬이 정한 휴리스틱이다. 프롬프트당 도구 호출 수는 점수에 넣지 않고 Anthropic 집계와의 비교 표에만 보인다. 많이 부른다고 더 잘하는 것이 아니기 때문이다.

세션마다 서로 독립된 채점을 두 번 한다. 숫자 점수는 평균하고, 통하지 않은 지시는 두 채점이 같은 발화를 짚었을 때만 세고, 결과는 둘 중 낮은 쪽을, `hard_signal`은 둘 다 `true`일 때만 인정한다.
