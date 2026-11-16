# Contract: 리랭커 모듈(내부 Python 계약) — spec 012

**상태**: 초안. 설계서 6.6·10절(기존 승인 2026-11-09)과 D-06·D-07·D-08을 구체화한다. `[DR-xx]` 표시 부분은 승인된 결정(사용자 2026-11-13)을 따른다. 운영 검색 경로·평가 경로에 연결하는 일은 `009`·`002`(연동 변경 DR-11 ⑤)의 몫이며 이 계약은 그 연결이 부를 함수 모양을 정한다.

이 계약은 HTTP가 아니라 **프로세스 안 Python 포트와 순수 함수**의 계약이다. 공개 API·검색 정책 형식·감사 테이블을 바꾸지 않는다.

## 용어

| 용어 | 뜻 |
|---|---|
| 포트 | application 계층이 정한 Protocol(함수 모양만 정한 Python 인터페이스). 구현은 infrastructure 계층에 둔다. |
| 백엔드 | 모델을 실제로 돌려 문자열 쌍에 점수를 매기는 구현. |
| 순수 함수 | 같은 입력이면 같은 출력을 내고 바깥 상태를 바꾸지 않는 함수. |
| 운영 모드, 평가 모드 | 실행 경계의 두 동작. 운영 모드는 실패 시 RRF 순서로 대체하고, 평가 모드는 대체 없이 오류를 올린다(설계서 D-06). |
| `FusedCandidate`, 리랭커 기술자, 리랭크 적용 상태 | [data-model.md](../data-model.md) 용어·3절·7절. |
| RRF, 후보 깊이, 입력 구성, 자르기 규칙 | spec 용어표와 같다. |

## 1. 포트

```text
RerankerBackend (Protocol, infrastructure 구현)
  descriptor -> RerankerDescriptor
  is_ready() -> bool                       # 네트워크 없이 로컬 준비 확인
  score(pairs: tuple[ScoringPair, ...]) -> tuple[float, ...]
      - pairs 순서대로 같은 길이의 점수 반환, 각 점수 0~1(R-01)
      - 생성(generate)을 하지 않는다. 문자열을 돌려주지 않는다(R-04)
      - 실패 시 예외(어떤 예외든 실행 경계가 고정 코드로 바꾼다)

ScoringPair
  query_text: str      # 자르기 규칙 적용 뒤 채점용 질의
  document_text: str   # 입력 구성·자르기 적용 뒤 채점용 문서
```

통과 모듈은 백엔드가 없다(레지스트리가 `passthrough`면 백엔드 생성 없음).

## 2. 순수 함수

```text
apply_rerank(request: RerankRequest, scores: tuple[float, ...] | None) -> RerankResult
    - scores가 None이면 통과 모듈: 입력 그대로, status=passthrough
    - candidates가 비면: 빈 결과, status=skipped_no_candidates, 백엔드 호출 없음
    - 그 밖: 상위 min(depth, len)개에 scores를 붙여 순서 규칙(3절) 적용
    - 불변 검사(R-03) 위반 시 RerankContractError

build_scoring_pairs(request, settings, tokenizer) -> tuple[ScoringPair, ...]   # P3
    - 상위 min(depth, len)개만, RRF 순서대로
    - 입력 구성(DR-06), 자르기(DR-06), instruction 틀(6절, DR-07)
    - 근거 본문(Candidate.excerpt)은 읽기만 한다
```

## 3. 순서 규칙(FR-013, 설계서 10절)

1. 리랭크된 후보(깊이 안): `rerank_score` 내림차순 → `rrf_rank` 오름차순 → 청크 id(UUID 정수) 오름차순. `rerank_rank`는 이 순서의 1부터.
2. 깊이 밖 후보: `rrf_rank` 오름차순(입력 순서 그대로), `rerank_score`·`rerank_rank`는 null, `reranked=false`.
3. 출력 = 1의 목록 + 2의 목록. 출력 집합 = 입력 집합.
4. 후보를 버리거나 더하지 않는다(O-7 범위 밖).

## 4. 실행 경계(P2)

```text
run_rerank(request, *, backend | None, settings, mode, clock, executor) -> RerankResult
    mode = "operational":
        성공                → status=applied, rerank_ms
        예외·계약 위반      → 입력 순서 그대로, status=not_applied_error, failure_kind=error
        소요 > timeout_ms   → 입력 순서 그대로, status=not_applied_timeout, failure_kind=timeout
        앞선 리랭크 실행 중 → 기다리지 않고 not_applied_timeout(R-05 제안, 최종은 009)
    mode = "evaluation":
        성공                → status=applied
        예외                → raise RerankEvaluationError("rerank_failed")
        계약 위반           → raise RerankEvaluationError("rerank_contract_violation")
        소요 > timeout_ms   → raise RerankEvaluationError("rerank_timeout")
        RRF 순서 결과를 만들지 않는다(대체 없음)
```

- 시간 비교: `소요 ms <= timeout_ms`면 통과(이하 통과). 소요 시간은 단조 시계 ns 차이를 ms로 올림.
- 결과·오류·로그에 원문, 질의문, 내부 예외 메시지·스택을 넣지 않는다. 고정 코드만.
- `timeout_ms` 값은 기술자 설정[DR-01 권장 A, 승인]에서 온다. 기본값 500[DR-02, 승인].

## 5. 레지스트리와 시작 검사(P4, 설계서 D-08)

| 키 (`reranker`, 이름, 버전) | 생성 | 비고 |
|---|---|---|
| (`reranker`, `passthrough`, `v1`) | 백엔드 없음 | 나이브 기본 구현 |
| (`reranker`, `cross_encoder`, `v1`) | `infrastructure/reranking/cross_encoder.py` | KURE-nano, bge-v2-m3 |
| (`reranker`, `yes_no_logit`, `v1`) | `infrastructure/reranking/yes_no.py` | Qwen3-Reranker 0.6B·4B, KURE-base |

시작 거부 코드(fail-closed, 원문 없음):

| 코드 | 조건 |
|---|---|
| `reranker_not_registered` | 레지스트리에 없는 (이름, 버전) |
| `reranker_model_not_approved` | (모델 id, revision)이 승인 목록 상수와 다름 |
| `reranker_role_not_allowed` | `upper_bound_only` 모델을 운영 모드로 만들려 함 |
| `reranker_model_not_prepared` | 매니페스트 없음 |
| `reranker_model_integrity_failed` | 파일 SHA-256·크기 불일치, 여분 파일, 매니페스트 revision 불일치 |
| `reranker_remote_code_refused` | 모델 설정이 원격 코드를 요구 |
| `reranker_settings_invalid` | 설정 키·값 검사 실패 |

### 5.1 오류 코드 전체 목록(정본)

012의 리랭커 오류 코드는 이 표가 정본이다. [eval-linkage-request.md](./eval-linkage-request.md) ⑤와 research.md DR-11 ⑤는 이 표를 가리킨다.

| 단계 | 코드 | 운영 경로 | 평가 경로 |
|---|---|---|---|
| 준비(시작) | `reranker_not_registered`, `reranker_model_not_approved`, `reranker_role_not_allowed`, `reranker_model_not_prepared`, `reranker_model_integrity_failed`, `reranker_remote_code_refused`, `reranker_settings_invalid` | 시작 거부(fail-closed) | 준비 실패, 실험 행 없음, 관문 시도로 세지 않음 |
| 질의 | `rerank_failed`, `rerank_timeout`, `rerank_contract_violation` | 발생하지 않음. 대신 `not_applied_error`·`not_applied_timeout` 상태로 RRF 순서 응답(계약 위반은 `not_applied_error`) | 평가 오류, 실험 `failed`(대체 없음) |

## 6. yes/no 방식 입력 틀(R-11, DR-07)

Qwen3-Reranker 계열 틀(`prompt_format_version = qwen3-reranker-v1`)은 모델 카드 공식 예제를 따른다. **아래 틀은 계획 시점의 기억에 기반한 초안이며, O1 반입 시 모델 카드 원문과 바이트 단위로 대조해 다르면 원문으로 고친 뒤 고정한다.**

```text
접두(시스템): "<|im_start|>system\nJudge whether the Document meets the requirements based on the Query and the Instruct provided. Note that the answer can only be \"yes\" or \"no\".<|im_end|>\n<|im_start|>user\n"
본문:         "<Instruct>: {instruction}\n<Query>: {query}\n<Document>: {document}"
접미:         "<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n"
점수:         마지막 위치의 logit에서 토큰 "yes"·"no" 두 값에 softmax → P(yes)
```

- KURE-Reranker-base는 O1에서 모델 카드의 공식 사용법을 확인해 별도 `prompt_format_version`으로 고정한다.
- 자르기는 본문의 `{query}`·`{document}` 부분에만 적용하고 접두·접미·instruction은 자르지 않는다.

## 7. 근거 항목 순서 제안 식과 검사기(P6, DR-13 승인 2026-11-13, 식 확정은 `009`)

```text
group_reranked_evidence(result: RerankResult, *, limit) -> tuple[EvidenceItem-like, ...]
    근거 항목 키: (리랭크된 일치 조각 보유 내림차순,
                  보유면 최고 rerank_score 내림차순 / 아니면 최고 RRF 점수 내림차순,
                  상위 절 id 정수 오름차순)
    일치 조각 키: (reranked 내림차순, rerank_score 내림차순, RRF 점수 내림차순, 청크 id)
    limit은 묶은 뒤 자른다

check_fr013a(result, items) -> list[Violation]
    ① 같은 입력 두 번 같은 순서 ② 통과 모듈이면 group_evidence와 같음
    ③ 리랭크 보유 항목이 깊이 밖 전용 항목보다 앞 ④ 그룹 안 정렬 규칙
```

통과 모듈이면 모든 조각이 `reranked=false`라 키가 (최고 RRF 점수 내림차순, 상위 절 id)로 돌아가 현재 `group_evidence`와 같다. RRF 점수가 같은 두 상위 절은 지금처럼 상위 절 id 순서다. 점수가 같을 때 RRF 순위(후보 목록 위치)로 먼저 가르면 청크 id 순서가 끼어들어 현재 동작과 달라지므로(spec FR-013a 본문의 동점 설명) 키에 넣지 않았다. U-ORD-02가 RRF 동점 표본으로 확인한다.

## 8. 테스트 대응

[plan.md](../plan.md) `테스트 전략`의 U-RR-01~08, U-PT-01, U-ST-01, U-EX-01~04, U-IN-01~05, U-DESC-01, U-REG-01~03, U-NET-01, U-AD-01·02, U-ORD-01~03, U-DET-01, G-REPRO-01, G-DET-01.
