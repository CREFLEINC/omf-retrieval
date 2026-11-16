# Data Model: 리랭커 연구 — 하이브리드 후보 재정렬의 근거 순위 개선 검증(reranker-research)

**작성일**: 2026-11-12 | **입력**: [spec.md](./spec.md) Key Entities, [plan.md](./plan.md), [research.md](./research.md) | **상태**: 초안(2026-11-13 갱신). 승인 목록(1절)은 사용자 결정 2026-11-12. `[DR-xx]` 표시가 붙은 구조·값은 승인된 결정(사용자 2026-11-13)을 따른다. 14절 후보 목록 고정 파일은 사용자 결정 2026-11-13(실험 구조 (가))이며 형식 확정과 생성은 `002` ⑨(DR-11)의 몫이다.

**표기 규칙**: "사용자 결정 2026-11-12"·"사용자 결정 2026-11-13" = 확정 입력. "승인(사용자 2026-11-13)" = DR 권장안 승인. "기존 승인(날짜)" = 이전 승인 결정. "제안" = 012가 `002`·`009`에 넘기는 안(이름·형식 확정은 그쪽).

## 용어

| 용어 | 뜻 |
|---|---|
| 개체 | 이 문서가 정의하는 데이터 묶음. 012는 새 DB 테이블을 만들지 않으므로 대부분 코드 안 값(상수·불변 객체)이거나 다른 명세의 저장소에 들어가는 값이다. |
| 코드 상수 | 소스 코드에 직접 적은 불변 값. 바꾸려면 코드 변경(계획·승인·검증)이 필요하다(설계서 D-08과 같은 방식). |
| 불변 객체 | 만든 뒤 바뀌지 않는 Python 값(`frozen` dataclass). |
| 정규화 JSON | 키를 정렬하고 공백 없이 쓴 JSON. 같은 내용이면 같은 바이트가 나와 해시가 같다(기존 `canonical_json`). |
| 설정 해시 | 정규화 JSON의 SHA-256(64자 16진수). |
| `FusedCandidate` | 기존 RRF 결합 결과 후보(`application/search/rrf.py`): 후보(`Candidate`), 키워드 순위, 벡터 순위, RRF 점수. |
| `Candidate` | 기존 검색 후보: 청크 id, 상위 절 id, 제목 계층, 원문(`excerpt`), 행 범위, 원본 목록. |
| 리랭커, 후보 깊이, 입력 구성, 자르기 규칙, 시간 초과 한도, 리랭크 적용 상태, 통과 모듈, 근거 항목, 일치 조각 | spec 용어표와 같다. |
| cross-encoder, yes/no logit 방식, instruction, 무결성 매니페스트, 오프라인 로딩, 탐색·확인 개발셋, Recall@N(후보 목록) | research.md 용어표와 같다. |
| 후보 목록 고정 파일(고정 파일), 파일 A·파일 B, 파일 식별값, 오프라인 실험 | 실험 구조 (가)(사용자 결정 2026-11-13). S1에서 검색 1회로 저장한 질의별 근거 묶기 전 후보 목록 파일(A = 탐색 개발셋, B = 확인 개발셋), 그 파일을 가리키는 네 값, 그 파일만 읽는 S2 개발셋 실험. 14절. |
| 연구 장비, 운영 개시 전 예외 | research.md 용어표와 같다. 운영 개시 전까지 연구 장비는 운영 서버 `192.168.1.185`이고 연구용 파일은 `/home/phoebe-deploy/experiments` 아래에 둔다(DR-12). |
| `002`, `009` | 평가 하네스, 통합 조회 API 명세. |
| LFS | Git Large File Storage. Hugging Face가 큰 모델 파일을 저장하는 방식이며 파일마다 SHA-256을 알려 준다. |
| dtype | 계산 정밀도(숫자를 몇 비트로 저장·계산하는지). `float32`는 32비트, `bfloat16`·`float16`은 16비트다. |
| bfloat16 | 16비트 부동소수점 형식의 하나. 메모리를 float32의 절반만 쓰며 GPU 추론에 흔히 쓴다. 점수에 아주 작은 차이를 만들 수 있어 결정성 기준(8절)과 함께 고정한다. |

## 0. 저장 위치 요약

| 개체 | 어디에 있나 | 누가 만든다 |
|---|---|---|
| 1 후보 모델 승인 목록 | 코드 상수(`application/search/rerank_descriptor.py`) | 012 P4 |
| 2 모델 반입 기록·무결성 매니페스트 | 연구 장비 모델 저장소의 매니페스트 파일(운영 적용 시 운영 서버에도 같은 파일) | 012 O1(도구는 P4) |
| 3 리랭커 기술자 | 검색 정책 snapshot 안(`009` 새 형식), 실험 기록 펼침(`002`) | 012 P4가 값 생성, 저장은 `009`·`002` |
| 4 리랭커 설정 | 3의 설정 원문 안[DR-01 권장 A] | 012 P3·P4 |
| 5 후보 깊이 | 검색 정책 필드(`009`, 설계서 7.1) | `009`. 값은 012 S2 결과 |
| 6 리랭크 입력·출력 | 메모리(질의 처리 중) | 012 P1·P2 |
| 7 리랭크 적용 상태 | 메모리 → 응답·감사 필드(`009`), 실험 기록(`002` 연동 ③) | 012 P1·P2가 값 결정 |
| 8 결정성 기준 | 코드 상수 + 연구 결과 요약 | 012 P7, S2 |
| 9 Recall@N 진단 결과 | `002` 실험 결과(연동 ②) | `002` |
| 10 연구 실험 계획 행 | 연구 결과 요약(실험 기록 id 참조) | 012 S1~S3 |
| 11 확인 개발셋 사용 기록 | `002` 실험 기록(연동 ④) + 연구 결과 요약 | `002`, 012 |
| 12 연구 결과 요약 | 사람이 읽는 보고서(`crefle-doc`) | 012 S5 |
| 15 개선 목표 | 연구 결과 요약·실험 보고(목표 값), 실험 기록 옆 달성 기록 | 012 P7 함수가 계산, S1 뒤 사용자 확인 |
| 14 후보 목록 고정 파일 | 연구 장비의 파일(운영 개시 전에는 운영 서버 `/home/phoebe-deploy/experiments` 아래), 파일 식별값은 실험 기록 | `002` ⑨가 S1에서 생성. 읽는 쪽은 `002` ⑨ 오프라인 실행기와 012 P8 |

## 1. 후보 모델 승인 목록(코드 상수, 사용자 결정 2026-11-12)

| 필드 | 형식 | 규칙 |
|---|---|---|
| `model_id` | 문자열 | Hugging Face 저장소 이름. 정확히 일치 비교(대소문자 구분) |
| `revision` | 40자 16진수 | 2026-11-12 `main` 커밋 SHA. 정확히 일치 |
| `role` | `apply_candidate` 또는 `upper_bound_only` | `upper_bound_only`는 평가 실행기(연구 장비)에서만 만들 수 있고 운영 모드 생성은 거부 |
| `method` | `cross_encoder` 또는 `yes_no_logit` | 어댑터 선택 |
| `license` | 문자열 | 기록용 |
| `params` | 문자열 | 기록용 |
| `max_input_tokens` | 정수 | 모델 입력 상한(기록·검사용) |
| `max_total_bytes` | 정수 | 반입 파일 합계 상한[DR-10 5] |
| `approval_ref` | 문자열 | `user-decision-2026-11-12` |

값:

| `model_id` | `revision` | `role` | `method` | `license` | `params` | `max_input_tokens` |
|---|---|---|---|---|---|---|
| `nlpai-lab/KURE-Reranker-base` | `0ca23195dc2c37177e7458330e2286e2f9fa04b3` | `apply_candidate` | `yes_no_logit` | Apache-2.0 | 1.7B | 8192 |
| `nlpai-lab/KURE-Reranker-nano` | `8514b9e113f3b27c441ed9c2b50f81d80388b2fd` | `apply_candidate` | `cross_encoder` | Apache-2.0 | 149M | 8192 |
| `Qwen/Qwen3-Reranker-0.6B` | `e61197ed45024b0ed8a2d74b80b4d909f1255473` | `apply_candidate` | `yes_no_logit` | Apache-2.0 | 0.6B | 32768 |
| `BAAI/bge-reranker-v2-m3` | `953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e` | `apply_candidate` | `cross_encoder` | Apache-2.0 | 568M | 8192 |
| `Qwen/Qwen3-Reranker-4B` | `22e683669bc0f0bd69640a1354a6d0aebcfeede5` | `upper_bound_only` | `yes_no_logit` | Apache-2.0 | 4.0B | 32768 |

- 예비 `Alibaba-NLP/gte-multilingual-reranker-base`(`8215cf04918ba6f7b6a62bb44238ce2953d8831c`)는 승인 목록 밖이라 **이 상수에 넣지 않는다**. 별도 승인 시 추가하며, 원격 코드가 필요해 DR-10의 원격 코드 금지와도 충돌한다.
- KURE-Reranker-base의 `method`는 사용자 제공 정보("Qwen3-1.7B 기반 yes/no logit 방식")에 따른다. 반입 시 모델 카드의 공식 사용법이 다르면 멈추고 보고한다(R-11).
- 32K 입력 상한은 32,768토큰으로 적었다. 반입 시 모델 설정 파일 값과 다르면 설정 파일 값을 쓰고 보고한다.

## 2. 모델 반입 기록과 무결성 매니페스트[DR-10]

형식 상세는 [contracts/model-intake.md](./contracts/model-intake.md).

| 필드 | 뜻 |
|---|---|
| `schema`, `version` | 매니페스트 형식 이름·버전(리랭커용) |
| `model_id`, `revision` | 1절 값과 정확히 같아야 함 |
| `files[]` | 상대 경로, 바이트 크기, SHA-256 |
| `hub_lfs_sha256[]` | 반입 시 Hugging Face가 알려 준 LFS 파일 SHA-256(대조 결과 포함) |
| `remote_code_required` | 항상 `false`여야 반입 성공(설정에 `auto_map` 등 있으면 반입 거부) |
| `ingested_at`, `ingested_by`, `ingest_host` | 반입 시각·담당·장비 |
| `manifest_sha256` | 매니페스트 정규화 JSON의 SHA-256 |

상태 전이: `반입 시작 → 커밋 SHA 확인 → 파일 해시·LFS 대조 → 원격 코드 확인 → 매니페스트 공개(완료)`. 어느 단계든 실패하면 매니페스트를 만들지 않고 받은 파일을 지운다(부분 반입 없음).

## 3. 리랭커 기술자(설계서 5절)

| 필드 | 형식 | 규칙 |
|---|---|---|
| `kind` | 문자열 | 항상 `reranker` |
| `implementation` | 문자열 | 레지스트리 이름: `passthrough`, `cross_encoder`, `yes_no_logit` |
| `version` | 문자열 | 구현 동작 버전(예: `v1`). 출력이 바뀌는 코드 변경이면 반드시 올림 |
| `settings` | 정규화 JSON 객체 | 4절. 원문을 함께 저장(설계서 5절) |
| `settings_hash` | 64자 16진수 | `settings` 정규화 JSON의 SHA-256 |
| `model_id`, `revision` | 문자열 | 1절 값. 통과 모듈은 빈 문자열 |

- 통과 모듈 기술자: (`reranker`, `passthrough`, `v1`, 빈 설정 `{}`의 해시, 빈 값, 빈 값).
- 검색 정책 snapshot 안에서 기술자가 어떤 키 이름으로 놓이는지는 `009`가 정한다.

## 4. 리랭커 설정(기술자 `settings`)[DR-01 권장 A, DR-06, DR-07, DR-02, R-01, R-02]

| 키 | 형식 | 예 | 비고 |
|---|---|---|---|
| `input_composition` | `raw` · `heading_raw` · `heading_raw_parent` | `heading_raw` | DR-06 (a)·(b)·(c) |
| `truncation` | 객체 | `{"query_max_tokens":256,"document_max_tokens":1024,"order":["parent_context","raw_tail","heading_tail"],"query_keep":"head"}` | DR-06. 토큰은 그 모델의 토크나이저로 셈 |
| `instruction` | 문자열 또는 null | I-1 또는 I-2(DR-07) | `yes_no_logit`만. cross-encoder는 null |
| `prompt_format_version` | 문자열 | `qwen3-reranker-v1` | yes/no 틀(contracts/reranker-module.md 6절) 식별 |
| `score_transform` | `sigmoid` · `softmax_yes_no` | `sigmoid` | R-01 |
| `batch_size` | 정수 ≥ 1 | 16 | R-02 |
| `dtype` | `float32` · `bfloat16` · `float16` | `bfloat16` | R-02 |
| `timeout_ms` | 정수 ≥ 1 | 500 | DR-02. DR-01 권장 A 승인으로 기술자 설정 안에 둠 |

- 장치(`cuda:0`, `cpu`)는 실행 환경이지 식별값이 아니다(서로 다른 하드웨어의 순위 동일은 요구하지 않음, spec Assumptions). 실험 기록 환경에 남는다(`002` R-08).
- 검증 규칙: 키 집합 정확 일치, 모르는 키 거부, 값 범위 검사. 실패하면 시작 거부.

## 5. 후보 깊이(검색 정책 필드, `009`)

| 필드 | 형식 | 규칙 |
|---|---|---|
| `rerank_candidate_depth` | 정수 1~100 | 키워드·벡터 후보 수 합 이하. 통과 모듈이면 결과에 영향 없음(FR-018). 이름은 `009`가 정함 |

- 비교값 3개는 DR-05 규칙으로 S1에서 정하고 최종값은 S2 결과로 `009`에 넘긴다(D-07).

## 6. 리랭크 입력·출력(메모리, contracts/reranker-module.md)

### 6.1 입력 `RerankRequest`

| 필드 | 뜻 |
|---|---|
| `query` | 정규화된 질의문(공백 제거 후) |
| `candidates` | `tuple[FusedCandidate, ...]`, RRF 순서 그대로, 중복 청크 id 없음 |
| `depth` | 후보 깊이 |
| `parent_bodies` | 입력 구성 (c)일 때만: 상위 절 id → 상위 절 본문(색인 저장 내용, FR-014) |

### 6.2 출력 `RerankResult`

| 필드 | 뜻 |
|---|---|
| `candidates` | `tuple[RerankedCandidate, ...]`. 집합 = 입력 집합(R-03) |
| `status` | 7절 값 |
| `failure_kind` | `null` · `error` · `timeout`(운영 미적용일 때만) |
| `rerank_ms` | 정수 ms(올림). 통과 모듈·후보 0이면 0 |

`RerankedCandidate`:

| 필드 | 뜻 |
|---|---|
| `fused` | 입력 `FusedCandidate` 객체 그대로(원문·좌표·원본·RRF 순위·점수 불변, R-04) |
| `rrf_rank` | 입력 목록에서의 1부터 순위 |
| `rerank_score` | 0~1 실수 또는 null(깊이 밖·통과 모듈·미적용) |
| `rerank_rank` | 1~N 정수 또는 null |
| `reranked` | 참·거짓(일치 조각의 "리랭크되지 않음" 표시의 원천, FR-017) |

순서 규칙(FR-013): 리랭크된 후보를 (`-rerank_score`, `rrf_rank`, 청크 id 정수) 순으로 먼저, 깊이 밖 후보를 `rrf_rank` 순으로 뒤에.

### 6.3 평가 오류 `RerankEvaluationError`

| 필드 | 뜻 |
|---|---|
| `code` | `rerank_failed`, `rerank_timeout`, `rerank_contract_violation` |

메시지·필드에 원문·질의문·내부 예외 문자열을 넣지 않는다.

## 7. 리랭크 적용 상태[DR-04, 승인 2026-11-13]

| 값 | 한국어 표기 | 조건 | 운영 | 평가 |
|---|---|---|---|---|
| `applied` | 적용 | 모델이 깊이 안 후보를 모두 채점 | 가능 | 가능 |
| `not_applied_error` | 리랭크 미적용(오류) | 오류·계약 위반 → RRF 순서 | 가능 | 생기지 않음(오류) |
| `not_applied_timeout` | 리랭크 미적용(시간 초과) | 한도 초과 → RRF 순서 | 가능 | 생기지 않음(오류) |
| `passthrough` | 해당 없음(통과 모듈) | 정책 리랭커가 통과 모듈 | 가능 | 가능 |
| `skipped_no_candidates` | 호출 안 함(근거 없음) | 하한선 통과 후보 0 → `no_evidence` | 가능 | 가능 |

## 8. 결정성 기준[DR-03, 승인 2026-11-13]

| 필드 | 값 | 비고 |
|---|---|---|
| `rank_identity` | `required` | 근거 항목 순위(`002` DR-13 필수 검사) + 후보 목록 리랭크 순위 |
| `score_abs_tolerance` | 0.0001 | 0~1 척도(R-01), 이하 통과 |
| `scope` | 같은 연구 장비, 같은 구성(S2는 리랭커 기술자 설정 해시 + 후보 깊이, S3는 정책 `config_hash`), 같은 스냅숏(S2는 같은 고정 파일 A) | 다른 하드웨어 사이는 요구 안 함. 운영 개시 뒤 장비가 바뀌면 새 장비에서 다시 잰다 |
| `fixed_runtime` | 배치 크기·정밀도·torch 설정 고정(R-02) | 실험 기록 환경에 남김 |

판정 결과 기록: 질의 수, 순위 불일치 수, 점수 최대 차이, 허용 범위, 통과 여부(원문 없음).

## 9. Recall@N 진단 결과(`002` 연동 ②, DR-11)

| 필드 | 뜻 |
|---|---|
| `recall_at_n_candidates` | N(5·10·20·30·50·최대) → 분수(분자/분모)와 10진 표기 |
| `candidate_count` | 질의별 후보 수의 최소·중앙값·최대 |
| `evidence_recall_at_5` | 같은 실험의 `002` 주 지표(근거 항목 단위) |
| `upper_gap_pp` | 상한 차이(%p) = Recall@최대 − Recall@5(후보 목록) |

## 10. 연구 실험 계획 행(연구 결과 요약의 표)

| 필드 | 뜻 |
|---|---|
| `stage` | 선별, 격자, instruction, 상한 참고, 확인, 관문(R-07) |
| `experiment_id` | `002` 실험 id |
| `set_snapshot_id`, `set_role` | 개발셋 스냅숏, `exploration`·`holdout`·`test` |
| `frozen_file_sha256` | S2 오프라인 실험이 읽은 고정 파일의 SHA-256(14절). 관문 시도는 빈 값 |
| `candidate_policy_hash` | S3 관문 시도: 등록된 후보 정책 `config_hash`(필수). S2 오프라인 실험: 빈 값(후보는 아래 세 식별값으로 식별) |
| `current_policy_config_hash` | FR-025 식별값 ①: 고정 파일 식별값에 든 현재 검색 정책 `config_hash`(14.2 `policy_config_hash`). S2 오프라인 실험 필수 |
| `reranker_settings_text`, `reranker_settings_hash` | FR-025 식별값 ②: 리랭커 기술자 설정 원문(정규화 JSON)과 그 SHA-256(3절). S2 오프라인 실험 필수, 관문 시도도 기록 |
| `candidate_depth` | FR-025 식별값 ③: 후보 깊이(5절). S2 오프라인 실험 필수, 관문 시도도 기록 |
| (규칙) | S2 오프라인 실험 기록은 위 세 식별값이 모두 있어야 하며 하나라도 빠진 기록은 0건이어야 한다. 관문 시도 기록은 `candidate_policy_hash`가 있어야 한다(spec FR-025·SC-007, 사용자 결정 2026-11-13 C-15 (1)) |
| `model_id`, `revision`, `input_composition`, `instruction`, `timeout_ms` | 설정 원문에서 펼친 값(SC-007). 후보 깊이는 `candidate_depth` |
| `strict` | 항상 "미사용"(R-12) |
| `resource_name` | 연구 장비 이름. 운영 개시 전 예외로 운영 서버에서 실행한 실험은 "운영 서버(운영 개시 전 예외)"(`002` FR-033 "운영 개시 전 예외", 사용자 결정 2026-11-13, 조건 ③) |
| `recall_at_5_delta_pp`, `latency_p95_delta_ms`, `gpu_peak_mib`, `rerank_ms_p95`, `rerank_ms_p99` | 결과 요약 |
| `mrr_at_3_current`, `mrr_at_3_candidate`, `mrr_at_3_delta` | 참고 지표 MRR@3(R-16, 사용자 결정 2026-11-13): 현재·후보 구성 값과 차이. 분수(분자/분모)와 10진 표기. 정답 있는 질의 0개면 `not_applicable`. 후보 선정·관문 판정에 쓰지 않음 |
| `ndcg_at_3_current`, `ndcg_at_3_candidate`, `ndcg_at_3_delta` | 참고 지표 nDCG@3(R-16): 현재·후보 구성 값과 차이. 부동소수점 값과 10진 표기(정밀도 방식은 `002` 제안). 정답 있는 질의 0개면 `not_applicable`. 후보 선정·관문 판정에 쓰지 않음 |
| `target_pp_used`, `target_basis_used`, `recall_at_5_target_met`, `mrr_at_3_target_met`, `ndcg_at_3_target_met`, `target_shortfall` | 개선 목표 달성 기록(15절, 사용자 결정 2026-11-16). 달성 필드와 `target_shortfall` 값은 참·거짓·`not_applicable`. 보고만, 후보 선정·관문 판정에 쓰지 않음. `target_shortfall`은 관문 시도 행에만 |

## 11. 확인 개발셋 사용 기록[DR-14, DR-11 ④, 사용자 결정 2026-11-13 F-4 (1)]

확인 횟수는 관문 시도 차수와 따로 센다. 트랙 ②와 리랭커 교체 건(`swap-reranker-*`)에서 시험셋 세대마다 3회까지 허용하고(새 세대에서 0부터, 이전 세대에 만들어져 아직 소진되지 않은 확인도 대조 키(파일 B SHA-256 포함)가 맞으면 새 세대 관문 시도에 쓸 수 있고, 새 세대 횟수에 다시 더하지 않음[결정 2026-11-16], 세대별로 세는 것은 사용자 결정 2026-11-16, `002` DR-28) 4회째부터는 사용자 승인이 있어야 한다. 관문 시도마다 그 시도에 보내는 후보의 확인 기록이 앞서 있어야 한다. 확인 기록과 관문 시도는 1:1이다. 확인 기록 하나는 관문 시도 하나에만 쓰이고, 같은 후보로 관문 시도를 다시 하려면 새 확인이 필요하다(확인 3회 중 1회를 쓴다). 관문 시도 사전 점검이 ① 실행 자원·② 평가 DB 확인을 통과한 직후 맞는 확인 기록을 찾으면 그 시점에 그 시도와 연결해 소진한다. 그 뒤 사전 점검 오류 등으로 판정 없이 끝나면 관문 시도 차수는 쓰지 않지만(`002` 규칙) 확인 기록은 소진된 채로 남는다. 이미 연결된 확인 기록을 다시 쓰려 하면 거부한다(제안 코드 `holdout_check_already_used`). 대조 키는 FR-025 세 식별값과 확인에 쓴 파일 B의 SHA-256(`holdout_file_sha256`, 파일 식별값을 통해 색인 run까지 정함)이다. 색인이 바뀌어 고정 파일을 다시 만들면 이전 확인 기록은 대조되지 않는다(사용자 결정 2026-11-13 G-2 (가), G-1).

| 필드 | 뜻 |
|---|---|
| `holdout_check_number` | 트랙 ②(또는 리랭커 교체 건)의 그 시험셋 세대 안 확인 횟수(1부터, 관문 시도 차수와 별도, 새 세대에서 1부터). 1~3은 허용 |
| `test_set_name`, `generation` | 이 확인을 쓸 관문 시도의 시험셋과 확인 시점의 시험셋 세대(`002` data-model 3.17). 관문 시도는 같은 시험셋의 미소진 확인을 쓰며 이전 세대 것도 대조 키가 맞으면 쓸 수 있다. 세대는 횟수를 세는 범위로만 쓰고 넘겨 쓴 확인은 새 세대 횟수에 더하지 않는다[결정 2026-11-16], `002` DR-28) |
| `approval_ref` | `holdout_check_number` ≥ 4일 때 필수인 사용자 승인 참조. 없으면 거부(제안 코드 `holdout_check_limit_exceeded`) |
| `current_policy_config_hash`, `reranker_settings_hash`, `candidate_depth`, `holdout_file_sha256` | 대조 키: 확인한 후보의 FR-025 세 식별값과 읽은 파일 B의 SHA-256(G-1). 관문 시도 사전 점검이 보내는 후보와 지금 파일 B로 대조한다. 맞는 미연결 기록이 없으면 제안 코드 `gate_without_holdout_check`. 파일 B를 다시 만들면 이전 기록은 맞지 않는다 |
| `consumed_at`, `consumed_by_gate_request` | 관문 시도 사전 점검이 이 기록을 연결한 시각과 그 관문 시도 요청 참조. 한 번만 채우며(빈 값 → 값), 채워진 기록을 다시 쓰면 제안 코드 `holdout_check_already_used`로 거부(G-2 (가)) |
| `gate_attempt_number` | 연결된 관문 시도가 받은 시도 차수. 한 번만 채운다. 판정 없이 끝나 시도 차수를 받지 못하면 빈 값이지만 `consumed_at`은 남아 기록은 소진된 상태다 |
| `mrr_at_3_delta`, `ndcg_at_3_delta` | 확인 실험의 참고 지표 차이(R-16). 보고만, DR-14 판단 조건에는 쓰지 않음 |
| `target_pp_used`, `recall_at_5_target_met`, `mrr_at_3_target_met`, `ndcg_at_3_target_met` | 확인 실험의 개선 목표 달성 기록(15절). 값은 참·거짓·`not_applicable`. 보고만 |
| `experiment_id` | 확인 실험 id(파일 B 오프라인 실험) |
| `exploration_delta_pp`, `holdout_delta_pp` | 탐색·확인 개발셋 Recall@5 차이 |
| `user_check_required` | 확인 차이 ≤ 0 또는 비용 한도 초과면 참 |

## 12. 연구 결과 요약(사람이 읽는 보고서, S5)

spec User Story 5 시나리오 4와 SC-011 항목: 시험한 후보 모델·revision 목록(10절 표), 최종 후보와 선정 근거(개발셋 실험 id), 후보 깊이 비교값·DR-05 계산·정한 값, 입력 구성, instruction, 결정성 기준과 측정값(8절), 비용 측정값, 확인 개발셋 결과(11절), 관문 판정·사유·시도 차수, Recall@N 진단(9절), 미세 조정 재검토 조건 판단(plan `진행 판단 규칙` 3), 공개 벤치마크 재현 결과(참고). `crefle-doc` 규칙으로 작성한다.

## 13. 상태 전이

### 13.1 후보 모델

```text
승인 목록(코드 상수)
 → 반입 완료(매니페스트 공개, O1)            실패: 반입 거부(보고)
 → 구현 재현 통과(G-REPRO-01, O2)           실패: 그 모델 실험 중단(보고)
 → 선별 비교(S2, 파일 A)                           → 제외(비용 초과·하위)
 → 격자 비교(S2, 파일 A)
 → 최종 후보(리랭커 기술자 + 후보 깊이 하나)
 → 확인 개발셋(파일 B, 확인 횟수 k, k ≤ 3 또는 사용자 승인)      → 사용자 확인(DR-14 조건)
 → 정책 불변 등록(config_hash, S3 준비)
 → 관문 시도 n(1~3, 같은 후보·같은 파일 B의 미연결 확인 기록 1건을 연결·소진) → 불합격 → 고쳐 새 후보로 S2 확인부터(확인 k+1, 시도 n+1). 같은 후보 재시도도 새 확인(k+1)
 → 판정: 적용 권고 → 사용자 승인 → 적용 전 확인(S4) → 적용
          유의한 차이 없음 / 불합격(한도 소진) → 현재 유지(통과 모듈)
```

`upper_bound_only` 모델은 "상한 참고"에서 멈춘다.

### 13.2 연구 트랙(트랙 ②)

```text
계획 승인 → S0 · (운영 서버 첫 접속 승인 → 실측·예외 표시 등록) → O1·O2 → S1 후보 고정·첫 측정
 → 상한 차이 < 3%p → 사용자 확인 → (중단: C-01 처리) / (지속)
 → S2 → S3(최대 3회) → S5 요약 → (적용 권고 + 승인이면 S4)
```

## 14. 후보 목록 고정 파일(사용자 결정 2026-11-13, 실험 구조 (가), DR-11 ⑨)

S1에서 현재 구성으로 검색을 한 번만 돌려 질의마다 근거 묶기 전 후보 목록을 얼려 둔 파일이다. S2의 모든 개발셋 실험은 검색을 다시 돌리지 않고 이 파일만 읽는다. 아래는 012가 리랭크 입력을 만들려면 필요한 내용(계약 기대값)이며, 파일 형식(직렬화 방식, 필드 이름)의 확정과 생성·대조 구현은 `002` ⑨의 몫이다.

### 14.1 역할

| 역할 | 대상 셋 | 질의 수 | 누가 읽는가 | 사용 제한 |
|---|---|---|---|---|
| 파일 A(`exploration`) | 탐색 개발셋 | 1,100(정답 있음 1,000 + 문서에 없음 100) | S1 첫 측정, S2 모든 탐색 실험(선별·격자·instruction·상한 참고), 최종 후보 선택, 결정성 확인 | 횟수 제한 없음 |
| 파일 B(`holdout`) | 확인 개발셋 | 550(정답 있음 500 + 문서에 없음 50) | S2 끝 확인 개발셋 실험(`holdout_check`)만 | 확인 횟수는 관문 시도와 따로 세어 시험셋 세대마다 3회(4회째부터 사용자 승인), 관문 시도마다 보내는 후보로 1회 선행. 탐색 목적 사용 거부(U-FRZ-04, DR-14) |

시험셋은 고정 파일로 만들지 않는다. 시험셋은 S3 관문 시도에서 실제 검색 경로로만 쓴다.

### 14.2 파일 식별값(머리 정보 3개 + 파일 SHA-256 = 네 값)

머리 정보(파일 안)는 `index_run_id`, `policy_config_hash`, `set_snapshot_id`의 세 값과 보조 필드(`role`, `created_at`, `resource_name`)다. 파일 식별값은 이 세 값과 `file_sha256`을 합한 네 값이다.

| 필드 | 뜻 |
|---|---|
| `index_run_id` | 검색에 쓴 현재 구성 색인 run id |
| `policy_config_hash` | 검색에 쓴 현재 검색 정책 `config_hash`(통과 모듈 구성) |
| `set_snapshot_id` | 개발셋 스냅숏 id(파일 A는 탐색, 파일 B는 확인) |
| `file_sha256` | 파일 전체 내용의 SHA-256(64자 16진수). 파일 자체의 해시라 파일 안 머리 정보에는 넣지 않는다. 파일을 만들 때 계산해 S1 실험 기록(파일 목록)에 머리 정보 3개와 함께 남기고, 읽을 때 다시 계산해 실험 요청 값과 대조한다 |
| `role` | `exploration` 또는 `holdout` |
| `created_at`, `resource_name` | 생성 시각, 생성 장비(운영 개시 전에는 "운영 서버(운영 개시 전 예외)") |

규칙: 실험 요청의 네 값(`index_run_id`, `policy_config_hash`, `set_snapshot_id`, `file_sha256`) 중 하나라도 다르면 실험을 고정 코드로 거부하고 부분 결과를 남기지 않는다(U-FRZ-01). 파일은 불변이며 색인이 바뀔 때(예: `006` 새 형식 run, C-05)만 새 파일을 만든다. 새 파일은 새 식별값을 가진다.

### 14.3 질의 레코드

| 필드 | 뜻 |
|---|---|
| `query_id`, `query_text`, `intent`, `answer_kind` | 개발셋 질의 id, 질의문(리랭커 입력), 의도, 정답 구분(정답 있음·문서에 없음) |
| `gold_evidence[]` | 정답 근거 좌표(개발셋 스냅숏 그대로). 오프라인 지표 계산용 |
| `candidates[]` | 근거 묶기 전 후보 목록(하한선·RRF 뒤, `strict` 미사용, RRF 순서, 0~100개). 비어 있으면 그 질의는 `no_evidence` |
| `baseline_evidence[]` | S1 검색 때 나온 근거 항목(상위 절 id, 일치 조각 청크 id, 순위, 대표 점수). 통과 모듈 재계산 자기 점검용(R-15, U-FRZ-02) |

후보(`candidates[]`의 원소)는 `FusedCandidate`를 되살리고 입력 구성 (a)(b)(c)를 만드는 데 필요한 내용을 모두 가진다.

| 필드 | 뜻 |
|---|---|
| `chunk_id`, `parent_id` | 청크 id, 상위 절 id(UUID) |
| `raw_text` | 청크 원문(입력 구성 (a)(b)(c), 응답 `excerpt`) |
| `heading_path[]` | 제목 계층(입력 구성 (b)(c)) |
| `parent_body` | 상위 절 본문(`sections.body`, 입력 구성 (c)). 같은 상위 절의 후보끼리 공유해 한 번만 둘 수 있다(형식은 `002` ⑨) |
| `line_start`, `line_end`, `origins[]` | 행 좌표와 모든 원본(`source_path`, `content_hash`) |
| `keyword_rank`, `vector_rank`, `rrf_rank`, `rrf_score` | lane 순위(없으면 null), RRF 순위·점수 |

보안: 고정 파일은 원문을 담으므로 연구 장비 안에만 두고 외부로 보내지 않는다(FR-007). 운영 개시 전에는 운영 서버 `/home/phoebe-deploy/experiments` 아래에만 둔다(DR-12).

### 14.4 누가 만들고 누가 읽는가

| 일 | 담당 | 단계 |
|---|---|---|
| 파일 A·B 생성, 식별값 기록 | `002` ⑨(평가 실행기) | S1 |
| 식별값 대조, 파일 B 사용 제한, 오프라인 실험 실행·기록 | `002` ⑨(④와 함께) | S2 |
| 레코드 → `RerankRequest` 복원, 깊이 자르기, 근거 항목 재계산(같은 순수 묶기 함수) | 012 P8 | S2 |
| 첫 측정 Recall@N(후보 목록) 계산 | `002` ②(파일 A 입력) | S1 |

## 15. 개선 목표(사용자 결정 2026-11-16, research.md R-17)

트랙 ②에 하나씩 두는 값이다. 관문 기준·후보 선정 규칙이 아니라 보고용이다.

| 필드 | 뜻 |
|---|---|
| `target_pp` | 개선 목표(%p, 분수). Recall@5·MRR@3·nDCG@3 공통. 처음에는 5 |
| `target_basis` | `provisional`(임시 +5%p) 또는 `g_based`(S1 뒤 max(3, G × 3/10)) |
| `target_g_pp` | 재설정에 쓴 상한 차이 G(%p, 분수). `provisional`이면 빈 값 |
| `target_flag` | G를 계산할 수 없어 임시값을 유지했으면 `g_not_computable`, 아니면 빈 값 |
| `target_set_at`, `target_confirmation_ref` | 재설정 시각과 사용자 확인 참조(확인 전에는 빈 값) |

실험·확인·관문 보고마다 붙는 달성 기록(10절·11절 행에 함께 둔다):

| 필드 | 뜻 |
|---|---|
| `target_pp_used`, `target_basis_used` | 그 보고 시점의 목표와 근거 |
| `recall_at_5_target_met`, `mrr_at_3_target_met`, `ndcg_at_3_target_met` | 지표별 달성 여부: 참·거짓·`not_applicable`. 차이(%p) ≥ 목표면 참. MRR@3·nDCG@3 차이는 0~1 값 차이 × 100. Recall@5·MRR@3은 `Fraction`으로 비교, nDCG@3은 0~1 척도 부동소수점 Δ ≥ float(목표/100) − 1e-9이면 참(R-16 정밀도 방식, `002`에 제안). 그 지표 값이 `not_applicable`이면 `not_applicable` |
| `target_shortfall` | 관문 시도 기록에만. 참·거짓·`not_applicable`. 판정이 "적용 권고"이면 `not_applicable`이 아닌 지표만 보고, 하나라도 미달이면 참("목표 미달"), 모두 달성이면 거짓, 세 지표 모두 `not_applicable`이면 `not_applicable`. 판정이 "적용 권고"가 아니면 거짓. 판정은 바꾸지 않으며 미세 조정 재검토 입력(plan `진행 판단 규칙` 3) |
