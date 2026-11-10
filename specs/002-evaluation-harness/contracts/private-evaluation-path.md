# Contract: 비공개 평가 경로(내부 포트) — spec 002

**상태**: 승인(사용자 2026-11-10, DR-18 권장안). 설계서 D-04(승인: 평가 실행기가 run id 지정으로 비공개 경로 질의, 공개 API 불변), D-05(승인: 평가 실행기만 run별 모델 레지스트리), D-06(승인: 평가에서는 조회 경로 실패를 대체하지 않고 오류), D-11(승인: 후보 정책 manifest 등록)과 DR-18(구현 방식, 승인) 기준이다.

이 계약은 HTTP가 아니라 **프로세스 안 Python 포트**의 계약이다. 공개 API(`POST /v1/search`, `/health/*`)와 `create_app`의 경로 집합은 바꾸지 않는다.

## 용어

| 용어 | 뜻 |
|---|---|
| 포트 | application 계층이 정한 Protocol(구현을 바꿔 끼울 수 있게 함수 모양만 정한 Python 인터페이스). |
| 평가 구성 | (run id, 검색 정책 manifest) 쌍. |
| run 지정 범위 | 검색 SQL이 후보를 고르는 범위를 "활성 run + 호출자 grant" 대신 "지정한 run id"로 바꾼 것. |
| 순위 결정 함수 | 하한선 → RRF → `strict` 교집합 → parent 묶기 → 상위 `limit`을 하는 순수 함수 `rank_evidence`. 운영 `SearchService`와 평가 경로가 같은 함수를 쓴다[DR-18]. |
| 모델 레지스트리 | (제공자, 모델 이름, revision, 차원, 정규화) → 질의 임베딩 제공자 생성 함수의 코드 안 명시적 목록(설계서 D-05, D-08). 평가 실행기만 쓴다. |
| 정책–run 대조 | run의 문서 임베딩 기술자와 정책의 질의 임베딩 모델·revision·차원·정규화가 같은지 확인하는 것(설계서 D-02, 현재 규칙). |

## 1. 포트

```text
EvaluationSearchPort
  prepare(config: EvaluationConfig) -> PreparedConfig
      - run 상태가 ready·active·previous인지
      - 정책 manifest를 config_hash로 resolve
      - 정책–run 대조
      - 레지스트리에서 질의 임베딩 제공자를 얻고 로컬 준비 확인
      실패: EvaluationPathError(code) — 대체 없음

  search(prepared: PreparedConfig, query: str, *, limit: int) -> EvaluationSearchResult
      - 질의 임베딩 → run 지정 범위의 키워드·벡터 후보 → rank_evidence
      - limit은 1~20(검색 API 최대와 같음). 평가 실행기는 20을 쓴다(research R-03, DR-11)
      실패: EvaluationPathError(code) — 대체 없음, 호출자는 실험을 failed로 끝낸다(R-14)
```

`EvaluationConfig`: `run_id`(UUID), `policy_config_hash`(64자 hex), `relevance_level`(`default`만. `strict`는 정책 식별값 포함 여부(O-10)가 정해지기 전까지 받지 않는다).

`EvaluationSearchResult`:

| 필드 | 뜻 |
|---|---|
| `status` | `ok` 또는 `no_evidence`(운영과 같은 규칙) |
| `run_id`, `commit_sha` | 질의한 run |
| `policy_config_hash` | 쓴 정책 |
| `evidence_items[]` | 운영 `EvidenceItem`과 같은 구조: `rank`, `parent_id`, `heading_path`, `score`, `matches[]`(`chunk_id`, `excerpt`, `line_start`, `line_end`, `keyword_rank`, `vector_rank`, `rrf_score`), `origins[]`(`source_path`, `content_hash`) |

운영 응답과 달리 `chunk_id`를 match에 포함한다(결정성 검사의 비교 키). 이 결과는 프로세스 밖으로 HTTP 응답으로 나가지 않는다.

## 2. 동작 규칙

| 규칙 | 내용 | 근거 |
|---|---|---|
| 공개 API 불변 | `create_app`의 (경로, 메서드) 집합과 `/v1/search` 요청·응답·인증·오류가 바뀌지 않는다. 기존 `tests/contract/api/test_search_api.py`·`test_web_delivery.py`가 수정 없이 통과한다 | D-04, FR-032 |
| 같은 순위 결정 | 같은 후보 묶음·정책·`limit`이면 운영 `SearchService`와 평가 경로의 근거 목록이 같다(같은 `rank_evidence`) | DR-18, US2 |
| 운영 SQL 불변 | 운영 저장소의 활성 범위 키워드·벡터·활성 run SQL 문자열이 리팩터링 전과 바이트 단위로 같다 | DR-18 |
| run 지정 범위 | 지정한 run의 occurrence·파싱·청크·임베딩만 후보가 된다. grant·활성 포인터를 보지 않는다. `building`·`failed`·`archived` run은 거부 | D-04 |
| 읽기 전용 | 모든 DB 접근은 읽기 전용 트랜잭션. 활성 포인터, run 상태, 감사 테이블에 쓰지 않는다 | FR-032, SC-008 |
| 대체 없음 | 임베딩·DB·대조 실패, 이후 리랭커 실패는 오류 코드로 올린다. RRF 순서로 대체하거나 `no_evidence`로 바꾸지 않는다 | D-06, R-14 |
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

## 4. 계약 테스트 대응

[plan.md](../plan.md) `테스트 전략`의 U-PRIV-01~04, C-SRCH-01~02, I-PRIV-01~03이 이 계약을 확인한다.
