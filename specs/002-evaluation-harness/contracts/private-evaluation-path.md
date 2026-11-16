# Contract: 비공개 평가 경로(내부 포트) — spec 002

**상태**: 승인(사용자 2026-11-10, DR-18 권장안). 설계서 D-04(승인: 평가 실행기가 run id 지정으로 비공개 경로 질의, 공개 API 불변), D-05(승인: 평가 실행기만 run별 모델 레지스트리), D-06(승인: 평가에서는 조회 경로 실패를 대체하지 않고 오류), D-11(승인: 후보 정책 manifest 등록)과 DR-18(구현 방식, 승인) 기준이다.

**개정(2026-11-16, `012` 연동 ①⑤, 사용자 승인 2026-11-16)**: 후보 목록 반환(`include_candidates`), 순위 결정 함수 분할, 평가 전용 리랭커 자리와 리랭커 오류 코드 10개를 더했다. 결정 요청 DR-26(research.md, 사용자 승인 2026-11-16, 권장안)에 의존한다. 기본 호출(`include_candidates=False`, 리랭커 없는 정책)의 결과와 운영 경로는 바뀌지 않는다.

이 계약은 HTTP가 아니라 **프로세스 안 Python 포트**의 계약이다. 공개 API(`POST /v1/search`, `/health/*`)와 `create_app`의 경로 집합은 바꾸지 않는다.

## 용어

| 용어 | 뜻 |
|---|---|
| 포트 | application 계층이 정한 Protocol(구현을 바꿔 끼울 수 있게 함수 모양만 정한 Python 인터페이스). |
| 평가 구성 | (run id, 검색 정책 manifest) 쌍. |
| run 지정 범위 | 검색 SQL이 후보를 고르는 범위를 "활성 run + 호출자 grant" 대신 "지정한 run id"로 바꾼 것. |
| 순위 결정 함수 | 하한선 → RRF → `strict` 교집합 → parent 묶기 → 상위 `limit`을 하는 순수 함수 `rank_evidence`. 운영 `SearchService`와 평가 경로가 같은 함수를 쓴다[DR-18]. 개정[DR-26]: `rank_evidence` = `group_evidence_items(fuse_candidates(...), limit, order_key)`의 합성이며 평가 경로는 두 함수 사이에 리랭커 실행 경계를 둔다. |
| 모델 레지스트리 | (제공자, 모델 이름, revision, 차원, 정규화) → 질의 임베딩 제공자 생성 함수의 코드 안 명시적 목록(설계서 D-05, D-08). 평가 실행기만 쓴다. |
| 정책–run 대조 | run의 문서 임베딩 기술자와 정책의 질의 임베딩 모델·revision·차원·정규화가 같은지 확인하는 것(설계서 D-02, 현재 규칙). |
| 후보 목록 | 근거 묶기 전, 하한선·RRF(·`strict`)를 거친 청크 단위 후보의 순서 있는 목록. 리랭커가 있으면 리랭크 뒤 순서. `fuse_candidates`의 결과[DR-26]. |
| 리랭커 슬롯, 실행 경계 | 평가 경로 준비 결과(`PreparedConfig`)에 두는 리랭커 자리와, 리랭커 호출을 시간 초과와 함께 감싸는 `012` 함수(`012` P2). 정책에 리랭커가 없으면 통과 모듈로 본다. |
| 리랭크 적용 상태 | `applied`, `not_applied_error`, `not_applied_timeout`, `passthrough`, `skipped_no_candidates`(`012` DR-04). 평가 경로는 앞의 미적용 두 값을 내지 않고 오류로 끝낸다. |

## 1. 포트

```text
EvaluationSearchPort
  prepare(config: EvaluationConfig) -> PreparedConfig
      - run 상태가 ready·active·previous인지
      - 정책 manifest를 config_hash로 resolve
      - 정책–run 대조
      - 레지스트리에서 질의 임베딩 제공자를 얻고 로컬 준비 확인
      - (DR-26, P19) 정책에 리랭커 기술자가 있으면 012 레지스트리로 리랭커를 만들어 슬롯에 둔다.
        없으면 통과 모듈. 준비 실패 코드 7개(3절)는 대체 없이 올린다
      실패: EvaluationPathError(code) — 대체 없음

  search(prepared: PreparedConfig, query: str, *, limit: int,
         include_candidates: bool = False) -> EvaluationSearchResult
      - 질의 임베딩 → run 지정 범위의 키워드·벡터 후보
        → fuse_candidates(하한선·RRF·strict) = 후보 목록
        → (리랭커가 있으면) 012 실행 경계로 리랭크, 012 순서 규칙으로 다시 정렬
        → group_evidence_items(후보 목록, limit, order_key)
      - limit은 1~20(검색 API 최대와 같음). 평가 실행기는 20을 쓴다(research R-03, DR-11)
      - include_candidates=True면 같은 호출의 후보 목록을 결과에 더한다(다시 검색하지 않음)[DR-26]
      실패: EvaluationPathError(code) — 대체 없음, 호출자는 실험을 failed로 끝낸다(R-14).
            리랭커 오류·시간 초과·계약 위반도 RRF 순서로 대체하지 않는다(D-06)
```

`EvaluationConfig`: `run_id`(UUID), `policy_config_hash`(64자 hex), `relevance_level`(`default`만. `strict`는 정책 식별값 포함 여부(O-10)가 정해지기 전까지 받지 않는다).

`EvaluationSearchResult`:

| 필드 | 뜻 |
|---|---|
| `status` | `ok` 또는 `no_evidence`(운영과 같은 규칙) |
| `run_id`, `commit_sha` | 질의한 run |
| `policy_config_hash` | 쓴 정책 |
| `evidence_items[]` | 운영 `EvidenceItem`과 같은 구조: `rank`, `parent_id`, `heading_path`, `score`, `matches[]`(`chunk_id`, `excerpt`, `line_start`, `line_end`, `keyword_rank`, `vector_rank`, `rrf_score`, 개정 추가 `rerank_score`·`rerank_rank`·`reranked`[DR-26]), `origins[]`(`source_path`, `content_hash`) |
| `rerank_status`, `rerank_ms` | (개정 추가) 리랭크 적용 상태(`applied`·`passthrough`·`skipped_no_candidates` 중 하나)와 리랭크 소요 시간(정수 ms, 올림. 통과 모듈·후보 0개면 0) |
| `candidates[]` | (개정 추가, `include_candidates=True`일 때만) 후보 목록. 원소: `position`(1부터, 리랭커가 있으면 리랭크 뒤 순서), `chunk_id`, `parent_id`, `line_start`, `line_end`, `origins[]`, `keyword_rank`, `vector_rank`(없으면 null), `rrf_rank`, `rrf_score`, `rerank_score`, `rerank_rank`, `reranked`. 원문(`excerpt`)은 넣지 않는다. 최대 `CANDIDATE_LIST_MAX = 100`개 |
| `candidate_total`, `candidates_truncated` | (개정 추가, `include_candidates=True`일 때만) 잘리기 전 후보 수와 잘림 여부(100 초과면 앞 100개만 돌려주고 `true`. 근거 항목은 전체 후보로 만든다) |

운영 응답과 달리 `chunk_id`를 match에 포함한다(결정성 검사의 비교 키). 이 결과는 프로세스 밖으로 HTTP 응답으로 나가지 않는다. 후보 목록의 원문은 프로세스 안에서만 쓰고(후보 목록 고정 파일, ⑨ 개정) 평가 DB 기록에는 넣지 않는다.

## 2. 동작 규칙

| 규칙 | 내용 | 근거 |
|---|---|---|
| 공개 API 불변 | `create_app`의 (경로, 메서드) 집합과 `/v1/search` 요청·응답·인증·오류가 바뀌지 않는다. 기존 `tests/contract/api/test_search_api.py`·`test_web_delivery.py`가 수정 없이 통과한다 | D-04, FR-032 |
| 같은 순위 결정 | 같은 후보 묶음·정책·`limit`이면 운영 `SearchService`와 평가 경로의 근거 목록이 같다(같은 `rank_evidence`) | DR-18, US2 |
| 운영 SQL 불변 | 운영 저장소의 활성 범위 키워드·벡터·활성 run SQL 문자열이 리팩터링 전과 바이트 단위로 같다 | DR-18 |
| run 지정 범위 | 지정한 run의 occurrence·파싱·청크·임베딩만 후보가 된다. grant·활성 포인터를 보지 않는다. `building`·`failed`·`archived` run은 거부 | D-04 |
| 읽기 전용 | 모든 DB 접근은 읽기 전용 트랜잭션. 활성 포인터, run 상태, 감사 테이블에 쓰지 않는다 | FR-032, SC-008 |
| 대체 없음 | 임베딩·DB·대조 실패, 이후 리랭커 실패는 오류 코드로 올린다. RRF 순서로 대체하거나 `no_evidence`로 바꾸지 않는다 | D-06, R-14 |
| 후보 목록 | `include_candidates=False`(기본)면 결과에 `candidates[]`가 없고 근거 항목은 개정 전과 같다. `True`여도 근거 항목은 같다. 후보 목록 순서는 RRF 순서(동점은 기존 RRF 함수의 청크 id 순)이고 리랭커가 있으면 `012` 순서 규칙 뒤 순서다. 두 lane 모두 하한선 미만인 청크는 들어가지 않는다. 비면(`no_evidence`) `[]` | DR-26, `012` 연동 ① |
| 함수 분할 | `fuse_candidates`·`group_evidence_items`의 합성이 개정 전 `rank_evidence`와 같은 결과다. 운영 `SearchService`는 `rank_evidence`만 부르고 리랭커 슬롯이 없다(운영 리랭커 삽입은 `009`) | DR-26, DR-18 |
| 리랭커 슬롯 | 리랭커가 없는 정책은 통과 모듈(`passthrough`)로 순서를 바꾸지 않는다. 리랭커가 있으면 후보 깊이 안 후보만 채점하고 근거 항목 순서는 `012` DR-13 제안 식(`order_key`)으로 정한다. 평가 경로는 `not_applied_error`·`not_applied_timeout`을 내지 않는다 | DR-26, `012` 연동 ⑤ |
| 모델 레지스트리 | 레지스트리에 없는 모델이면 `model_not_registered`. 모델 파일은 사내 오프라인 준비본만(무결성 매니페스트) 쓰고 내려받지 않는다 | D-05, D-08, 헌법 VI |
| 비공개 | 이 포트를 쓰는 곳은 `interfaces/cli/evaluation.py` 조립 지점뿐이다. API 런타임(`interfaces/api/runtime.py`)은 이 포트를 조립하지 않는다 | D-04 |

## 3. 오류 코드

| 코드 | 뜻 |
|---|---|
| `run_not_evaluable` | run이 없거나 상태가 `ready`·`active`·`previous`가 아님 |
| `policy_not_found` | 정책 manifest 없음 |
| `policy_index_incompatible` | 정책–run 대조 실패 |
| `model_not_registered`, `model_not_prepared` | 레지스트리에 없음, 로컬 준비본 없음 |
| `query_embedding_failed`, `retrieval_failed`, `ranking_failed` | 질의 처리 실패(원문·질의문 없는 고정 코드) |
| `reranker_not_registered`, `reranker_model_not_approved`, `reranker_role_not_allowed`, `reranker_model_not_prepared`, `reranker_model_integrity_failed`, `reranker_remote_code_refused`, `reranker_settings_invalid` | (개정 추가, 준비 단계 7개, `012` contracts/reranker-module.md 5.1 정본) `prepare` 실패. 실행기는 사전 점검 7의 거부(exit 3)로 처리하고 실험 행을 만들지 않으며 시도로 세지 않는다 |
| `rerank_failed`, `rerank_timeout`, `rerank_contract_violation` | (개정 추가, 질의 단계 3개, 같은 정본) `search` 실패. 대체 없이 실험 `failed`(R-14). 관문 시도면 DR-24대로 결과 저장 여부로 시도를 센다 |

## 4. 계약 테스트 대응

[plan.md](../plan.md) `테스트 전략`의 U-PRIV-01~05, U-CAND-01, U-RERR-01, U-RREC-01, C-SRCH-01~02, I-PRIV-01~04가 이 계약을 확인한다.
