# OMF Retrieval 초고속 MVP 상세 구현 계획

> **문서 정본:** 이 Markdown 파일이 실행 계획의 정본이다. 시스템 설계 HTML은 설계 결정의 독립 정본이며 이 파일의 파생물이 아니다. 실행 절차가 충돌하면 이 Markdown을 우선한다.

| 문서 항목 | 값 |
|---|---|
| 작성자 | Codex — 사용자 승인 반영 |
| 작성일시 | 2026-08-31 07:51 KST |
| 문서 버전 | v2.1 |
| 열람 대상 | 프로젝트 관련자 |
| 기준 설계 | `docs/design/2026-08-13-omf-retrieval-mvp-system-design.html` v2.1 |
| 상태 | MVP 완료 · v2.1 corrective 완료 · 공유 배포 완료 |

## 용어

| 용어 | 뜻 |
|---|---|
| RAG | Retrieval-Augmented Generation. 이 MVP에서는 생성 없이 검색과 근거 반환까지만 담당 |
| 근거 패키지 | 인용문과 원문 좌표, 검색 순위, 색인 재현 정보를 묶은 API 결과 |
| 현재본 | 고정 OMF commit에서 `design/wiki/**/*.md`로 선택되는 문서 집합 |
| WIP | Work In Progress. 작업 8~10에서 시작해 이번 MVP에 안정화한 기존 구현 |
| RRF | Reciprocal Rank Fusion. 키워드·벡터 결과의 순위를 역수로 합치는 방식 |
| 검색 lane | 키워드 또는 벡터 검색이 독립적으로 후보와 순위를 만드는 경로 |
| 원시 점수 | RRF 결합 전의 lane별 관련도. 키워드는 `pg_trgm` similarity, 벡터는 정규화한 임베딩의 cosine similarity |
| 근거 하한선 | 후보가 해당 lane의 RRF 입력이 되기 위해 충족해야 하는 원시 점수 최솟값 |
| 분리 margin | 고정 smoke에서 정상 질의의 acceptable evidence가 문서에 없는 질의의 lane별 최고점보다 앞선 최소 여유 |
| 수직 절편 | 검색 core부터 API·CLI까지 하나의 사용자 동작으로 연결하는 구현 단위 |
| Index identity | 원본·parse·chunk·document embedding처럼 저장 문서나 vector를 바꾸는 설정의 재현 identity |
| Search policy identity | query embedding·후보 수·RRF·근거 하한선처럼 질의 시점 결과를 바꾸는 설정의 재현 identity |
| Policy manifest | 검색 정책의 canonical JSON snapshot과 SHA-256 config hash를 보존하는 immutable DB record |

## 개정 이력

| 버전 | 작성일시 | 변경 | 작성자 |
|---|---|---|---|
| v2.1 | 2026-08-31 07:51 KST | Corrective 단위 1~4 완료, CUDA policy·보존 run·image·rollback 좌표, 최종 smoke·보안·재시작과 공유 API 공개 상태 반영 | Codex — 사용자 승인 반영 |
| v2.1 | 2026-08-29 13:56 KST | Index identity와 search policy identity 분리, additive policy manifest·API 좌표·rollback, CUDA 보정 corrective plan과 공유 배포 상태 및 승인된 연속 진행 계약 반영 | Codex — 사용자 승인 반영 |
| v2.0 | 2026-08-27 15:53 KST | 확정 근거 하한선·`no_evidence` 정책, 실제 색인·6개 smoke·품질 관찰과 단위 1~4 독립 검증 결과 반영 | Codex — 사용자 승인 반영 |
| v2.0 | 2026-08-25 18:52 KST | 사용자 기능을 현재본 근거 검색으로 축소하고 남은 개발을 4개 단위로 재편; v1.2 작업 13~18 전체를 후속으로 이동 | Codex — 사용자 승인 반영 |
| v1.3 | 2026-08-25 16:27 KST | Parse artifact manifest와 activation lifecycle의 migration 분리 계획 | Codex — 사용자 승인 반영 |
| v1.2 | 2026-08-19 KST | 운영·평가까지 포함한 18개 작업 계획 | CREFLE / Codex |

## 1. 목표와 완료선

고정된 OMF 현재본을 색인하고, 인증된 HTTP 질의에 재현 가능한 근거 패키지를 반환한다. 자연어 답변 생성은 호출 Agent의 책임이며 이 서비스는 원문 근거만 반환한다.

MVP 완료 조건은 다음과 같다.

1. OMF commit `a8f46f23cd3fb9c5f7042e987dff8103d23f0fa2`의 `design/wiki/**/*.md`만 색인·활성화한다.
2. Bearer token과 OMF source grant가 있는 사용자가 `POST /v1/search`로 top 5 근거를 요청할 수 있다.
3. 근거마다 인용, 경로, 제목 계층, 1-based inclusive 행 범위, 키워드·벡터 순위, RRF 점수, 모든 원본 경로, 내용 해시, run ID와 commit SHA를 반환한다.
4. 로컬 PostgreSQL과 실제 Qwen 임베딩 모델로 6개 smoke 질의를 검증한다.
5. 기존 작업 1~7과 작업 8~10 WIP는 삭제하지 않고 재사용한다.

로컬 실제 기능 검증이 v2.0 MVP 완료선이었다. 공유 Ubuntu·Docker Compose 배포는
당시 후속으로 분리했으며 이후 별도 사용자 승인으로 착수했다.

## 2. 확정 범위와 기술 계약

### 2.1 소스와 버전

- source ID: `omf`
- 고정 commit: `a8f46f23cd3fb9c5f7042e987dff8103d23f0fa2`
- include: `design/wiki/**/*.md`
- exclude: `design/raw/**`, `design/schema/**`, `docs/**`, `_workspace/**`, Agent 설정·작업 파일, HTML, PDF, Excel, 이미지, 생성물, 임시 작업물
- OMF 저장소는 읽기 전용이며 깨끗한 고정 commit에서 색인한다.
- 현재본만 검색한다. 과거본·임의 commit·path·decision·history·context filter는 노출하지 않는다.
- 같은 내용은 해시로 재사용하되 모든 원본 경로를 보존한다.

### 2.2 검색

- 임베딩: `Qwen/Qwen3-Embedding-0.6B`, 고정 revision `97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3`, 1024차원
- 키워드 후보: 활성 OMF run의 `pg_trgm` top 50
- 벡터 후보: 같은 범위의 exact cosine top 50
- 결합: RRF `k=60`, keyword/vector weight 각각 `1.0`
- 키워드 lane은 raw `pg_trgm` similarity가 `0.03658536400000001` 이상인 후보만,
  벡터 lane은 정규화한 embedding의 raw cosine similarity가
  `0.48344050397156374` 이상인 후보만 RRF에 투입한다. 비교 연산은 `>=`다.
- 두 하한선은 문서에 없는 smoke 질의에서 관찰한 lane별 최고점에
  `nextafter(+inf)`를 적용한 다음 표현 가능 부동소수점 값이다. 따라서 관찰된
  unknown 최고점은 배제되고 하한선 자체와 같은 점수는 포함된다.
- v2.0은 후보 수 `50/50`, RRF `60/1.0/1.0`, 두 하한선과
  `evidence_floor_status: calibrated`를 index config에 byte-exact 값으로 저장했다.
  이 값과 분리 margin은 로컬 CPU 검증 provenance로 보존한다.
- 두 lane 모두 하한선을 통과한 후보가 없으면 HTTP 200,
  `status: no_evidence`, 빈 `evidence_items`를 반환한다.
- 고정 6개 smoke의 현재 acceptable-evidence 분리 margin은
  `0.16857380984674064`다. 정식 품질 metric이나 성능 기준이 아닌 재현성 관찰값이다.
- 작은 child chunk로 검색하고 같은 parent section의 child를 하나의 evidence item으로 묶는다.
- evidence 순서는 그룹에서 가장 높은 child RRF 점수로 정한다.
- 리랭커, ANN index, ParadeDB는 MVP에 추가하지 않는다.

#### 2.2.1 v2.1 identity 분리

- Index identity에는 source profile·commit, parser, chunker, tokenizer, document
  embedding provider·model·revision·dimension·normalization·library behavior처럼
  저장 문서·chunk·vector를 바꾸는 설정만 포함한다.
- Search policy identity에는 query embedding model·revision·dimension·normalization과
  instruction, keyword/vector candidate limits, RRF k·weights, 두 similarity floor와
  calibration status처럼 query-time 결과를 바꾸는 설정을 포함한다.
- Search policy는 exact-key canonical JSON의 SHA-256 `config_hash`로 식별하는
  immutable DB manifest다. Runtime settings가 policy를 선택하며 `serve`가 readiness
  전에 idempotent register·resolve한다.
- 공개 policy 관리 CLI와 active-policy pointer는 추가하지 않는다. 단일 Compose API
  replica가 MVP 전제이며 replica 간 config consistency는 후속이다.
- Active index의 document embedding descriptor와 policy의 query
  model·revision·dimension·normalization 호환성을 강제한다. Query instruction, RRF,
  floor 차이는 index 불일치가 아니다.
- Search policy 변경은 새 manifest와 API restart만 필요하다. Index run, chunk,
  embedding pipeline을 호출해서는 안 된다.
- 기존 `index_configs.config_hash`, query snapshot과 `rrf_config`는 v2.0 legacy
  combined identity provenance로 그대로 보존한다.

### 2.3 인증과 공개 인터페이스

- API 앞단과 검색 SQL 후보 CTE 양쪽에서 active Bearer token과 `omf` source grant를 강제한다.
- 공개 endpoint는 `POST /v1/search`, `GET /health/live`, 인증된 `GET /health/ready`뿐이다.
- CLI는 `model prepare`, `index`, `client create`, `serve`, `search`만 MVP 사용 경로로 문서화한다. 기존 추가 명령 구현은 삭제하지 않고 비노출로 둔다.
- Search CLI는 HTTP API를 호출하고 token을 명령 인자가 아닌 환경변수에서 읽는다.

검색 요청:

```json
{"query": "긴급 W/O 승인 정책 원문을 찾아줘", "limit": 5}
```

- `query`: 공백 제거 후 비어 있지 않은 문자열
- `limit`: 선택, 기본 5, 최소 1, 최대 20
- source와 version은 각각 `omf`, 활성 현재본으로 고정한다.

정상 응답:

```json
{
  "request_id": "...",
  "status": "ok",
  "index": {"run_id": "...", "commit_sha": "..."},
  "search_policy": {"policy_id": "...", "config_hash": "..."},
  "evidence_items": [{
    "rank": 1,
    "heading_path": ["...", "..."],
    "matches": [{
      "excerpt": "...", "line_start": 10, "line_end": 18,
      "keyword_rank": 2, "vector_rank": 1, "rrf_score": 0.0325
    }],
    "origins": [{"source_path": "design/wiki/...", "content_hash": "..."}]
  }]
}
```

- `search_policy`는 기존 응답에 추가되는 `policy_id`·`config_hash` 재현 좌표다.
  기존 `index`와 evidence provenance는 유지한다.
- 근거 없음: HTTP 200, `status: no_evidence`, 빈 `evidence_items`
- 인증 실패: 401
- source grant 실패: 403
- 활성 색인 없음: 409
- 요청 검증 실패: 422
- DB 또는 모델 불가: 503
- 오류 본문은 `request_id`, 안정적 `code`, 안전한 `message`만 반환한다.
- `GET /health/live`는 프로세스 생존만 반환한다. `GET /health/ready`는 인증 후 DB, 활성 색인과 모델 준비 상태를 확인한다.

### 2.4 기존 데이터 모델 채택

현재 WIP의 `0002_index_run_activation_lifecycle.py`를 MVP migration으로 채택한다. 하나의 migration이 parse artifact manifest의 `section_count`, `chunk_count`, `artifact_hash`와 activation lifecycle의 `activated_at`, `ARCHIVED`, 활성 버전 제약을 함께 소유한다. v1.3의 8A-1 `0002` manifest / 별도 `0003` lifecycle 분리 계획은 이 v2.0이 명시적으로 대체한다.

v2.1의 `0003`은 폐기한 lifecycle 분리안과 다른 새 additive migration이다. Immutable
search policy manifest storage와 SHA-256 config hash를 추가하고, 기존 index run이
참조하는 query·retrieval snapshot을 manifest로 backfill한다. 기존 index run,
`index_configs` snapshot, chunk와 5,584개 embedding은 변경·삭제하지 않는다. Migration
upgrade → downgrade → re-upgrade를 검증하되 운영 rollback은 DB downgrade보다 이전
image와 policy 환경설정으로 되돌린 API restart를 우선한다.

## 3. 실행 원칙과 후속 범위

- 모든 코드 동작 변경은 assertion 기반 RED → GREEN → REFACTOR 순서로 진행한다.
- Unit test는 네트워크, GPU, 실제 모델, 외부 Git 저장소를 요구하지 않는다.
- 실행 Agent는 승인 단위의 쉬운 무외부 의존 검증만 수행한다. 별도 검증 Agent가 계획된 Unit·PostgreSQL·계약·E2E 검증을 처음부터 다시 실행한다.
- Python 3.12 기존 가상환경과 lock된 의존성을 사용한다. 현재 `uv 0.9.28`은 blocker가 아니며 `uv 0.12.3` 재현을 요구하지 않는다.
- v2.1 corrective plan에 명시되지 않은 새 endpoint, DB table, runtime dependency,
  검색 가중치 또는 권한 정책이 필요하면 해당 단위를 멈추고 재승인받는다.
- 사용자 ZIP, cache와 범위 밖 WIP를 reset·stash·삭제하지 않는다.

후속으로 미루는 범위:

- LLM 생성 답변과 Codex/Kanana 연동
- 과거본·임의 commit 검색과 path·decision·history·context filter
- explicit conflict/relation 전용 API, rollback CLI와 2세대 운영 절차
- 30개 골드셋, 정식 검색 metric, 성능 benchmark
- 감사 HMAC, JSON audit logging, 운영 관측성
- Buildx push, digest 배포 자동화, 정식 서버 성능 gate와 운영 runbook
- 자동 배포·복구, MCP와 function tool

v1.2 작업 13~18 전체의 번호 추적은 다음과 같다.

| 기존 작업 | 기존 책임 | v2.0 상태 |
|---|---|---|
| 작업 13 | 평가 dataset·metric | 후속 |
| 작업 14 | Logging·audit | 후속 |
| 작업 15 | Docker·Compose | 후속 |
| 작업 16 | Buildx·digest deploy | 후속 |
| 작업 17 | Server E2E·performance | 후속 |
| 작업 18 | Operations handoff | 후속 |

이 표는 v1.2 작업 13~18 전체를 v2.0 MVP 완료선 밖으로 이동한 추적 기록이다.

기존 구현이 있는 후속 기능은 삭제하지 않고 MVP 공개 경로에서만 비노출로 보존한다.

## 4. 실행 단위 완료 기록

아래 네 단위는 실행 Agent와 별도 검증 Agent로 수행했고 모두 독립 검증 PASS했다.

### 단위 1. 정본 계획 v2.0 전환 — 완료 · 독립 검증 PASS

**주제:** 설계·실행 정본을 초고속 MVP 범위로 전환한다.

**목적:** 이후 Agent가 v1.3의 migration 분리와 운영 범위를 따르지 않도록 하나의 승인 기준을 만든다.

**내용:** 시스템 설계와 이 계획을 v2.0으로 개정하고 `AGENTS.md`의 고정 결정과 정본 포인터를 맞춘다. HTML의 기존 로컬 CREFLE 번들, link와 script를 보존한다.

**기대 결과:** 세 문서에서 목표, source, API, migration, 네 개 실행 단위와 후속 범위가 같다.

**검증:** 문서 단위라 Unit test와 TDD는 해당하지 않는다. 실행 Agent는 충돌 키워드 검색, HTML 구조·자산 확인과 `git diff --check`를 수행한다. 독립 검증 Agent는 세 문서를 처음부터 대조하고 headless Chrome 로컬 렌더를 시각 확인하며 승인된 세 파일 밖 새 변경이 없는지 baseline과 비교한다.

### 단위 2. 기존 색인·활성화·인증 WIP 안정화 — 완료 · 독립 검증 PASS

**주제:** 고정 OMF 현재본을 원자적으로 색인·활성화하고 인증 정보를 준비한다.

**목적:** 기존 작업 8~10 WIP를 최소 수정으로 검색 가능한 상태로 만든다.

**내용:** 통합 `0002` migration, parse artifact 재사용, config identity, 색인 pipeline, active pointer, token과 source grant를 정리한다. source profile을 `design/wiki/**/*.md`로 바꾼다. `index`는 고정 commit을 색인하고 READY 성공 시 즉시 활성화한다. rollback은 노출하지 않는다.

**기대 결과:** 신규 DB와 기존 `0001` DB 모두 `0002`로 올라가며, 첫 색인이 활성화되고 실패한 재색인은 기존 active run을 바꾸지 않는다. token과 `omf` grant를 만들 수 있다.

**Unit test 설계:**

| 사례 | RED assertion | 기대 GREEN |
|---|---|---|
| 정상 | `design/wiki/a.md`가 profile에 포함되고 색인 성공 후 active pointer가 새 READY run을 가리킴 | 고정 commit의 wiki Markdown만 저장·활성화 |
| 경계 | 같은 content hash가 여러 경로에 있을 때 artifact는 재사용되고 origins는 모두 남음 | 중복 본문 없이 모든 경로 보존 |
| 경계 | 기존 `0001` parse 행을 가진 DB를 upgrade | manifest backfill과 lifecycle 제약 모두 유효 |
| 실패 | raw/schema/Agent/비 Markdown 경로 입력 | 색인 대상에서 제외 |
| 실패 | parse·embedding·DB 저장 중 예외 | 새 run 실패, 기존 active pointer 불변 |
| 실패 | token 없음·폐기·만료 또는 source grant 없음 | 인증/권한 거부 |

기존 구경로 profile test의 assertion 실패를 실제 RED로 확인한 뒤 최소 변경한다. import, fixture 또는 환경 오류는 RED로 인정하지 않는다.

**실행 Agent 검증:** 관련 Unit test, fake embedding·임시 Git 기반 indexing test, `ruff check`와 `git diff --check`. PostgreSQL, 실제 모델, 네트워크는 실행 Agent 검증에서 제외한다.

**독립 검증:** Unit 전체 baseline, migration upgrade → downgrade → re-upgrade, 신규·기존 DB backfill, 첫 색인·활성 pointer·중복 경로·실패 격리, 인증 repository integration과 범위 밖 diff를 처음부터 검증한다.

### 단위 3. 검색 core + API·CLI 수직 절편 — 완료 · 독립 검증 PASS

**주제:** 인증된 자연어 질의를 근거 패키지로 반환한다.

**목적:** 실제 사용자가 호출할 수 있는 최소 RAG 정보 조회 기능을 한 단위로 완성한다.

**내용:** 활성 OMF run에 한정한 pg_trgm·exact pgvector 후보 검색, RRF, parent grouping, evidence 조립, token/source grant, 세 endpoint와 Search CLI를 연결한다. 공개 계약은 2.3을 그대로 사용한다.

**기대 결과:** HTTP와 CLI가 동일한 순위와 재현 좌표를 반환하며 인증·권한·활성 색인 경계를 우회할 수 없다.

**Unit test 설계:**

| 사례 | RED assertion | 기대 GREEN |
|---|---|---|
| 정상 | keyword/vector rank fixture 입력 | `k=60`, 동일 가중치 RRF 순서와 점수 일치 |
| 정상 | 같은 parent의 여러 child와 중복 origin 입력 | 한 evidence item으로 묶이고 최고 child 점수로 정렬, origins 모두 보존 |
| 정상 | 유효 token·grant와 검색 요청 | 200 `ok`, limit 이하의 완전한 근거 패키지 |
| 경계 | `limit` 생략·1·20 | 각각 5·1·20으로 처리 |
| 경계 | 후보 없음 | 200 `no_evidence`, 빈 목록 |
| 경계 | raw score가 lane 하한선과 같음 | `>=` 비교로 해당 lane RRF 후보에 포함 |
| 실패 | raw score가 두 lane 하한선 모두 미만 | 200 `no_evidence`, 빈 목록 |
| 실패 | persisted 검색 config 누락·추가·타입·값 불일치 | Search·ready 503 fail-closed |
| 실패 | 빈 query 또는 limit 0·21 | 422 |
| 실패 | token 없음·무효·만료 | 401 |
| 실패 | `omf` grant 없음 | 403이며 source 존재 정보 비노출 |
| 실패 | active run 없음 | 409 |
| 실패 | DB 또는 embedding provider 실패 | 503, 비밀·원문·host path 비노출 |
| 정상/실패 | live와 ready | live는 무인증 생존, ready는 인증 및 의존성 상태 반영 |

새 search package의 동작 assertion 실패를 RED로 확인하고 최소 구현한다. API validation fixture만 실패하거나 import가 실패한 상태는 RED로 인정하지 않는다.

**실행 Agent 검증:** RRF·grouping·evidence·service Unit test, fake repository/provider를 쓴 API·CLI contract test, `ruff check`, `git diff --check`.

**독립 검증:** 실행 Agent 검증 전체를 재실행하고 실제 PostgreSQL에서 권한이 후보 CTE에 선적용되는지, top 50 exact search, API status/error body와 HTTP·CLI 일치를 검증한다.

이 단위는 500줄을 초과할 수 있다. core와 API를 분리하면 어느 쪽도 사용 가능한 사용자 기능이 되지 않으므로 Single Intent와 독립 사용자 가치가 같은 수직 절편으로 유지한다.

### 단위 4. 로컬 실제 모델 E2E와 최소 인계 — 완료 · 독립 검증 PASS

**주제:** 고정 OMF 현재본의 실제 검색 가능성을 로컬에서 입증한다.

**목적:** 운영 배포 없이도 MVP 사용자 기능과 원문 재현성을 확인하고 실행 절차를 인계한다.

**내용:** 로컬 PostgreSQL과 Python 3.12, 실제 Qwen CPU provider를 사용한다. 모델 cache가 없으면 고정 revision을 한 번 준비한다. 전체 wiki를 색인·활성화하고 token을 만든 뒤 API를 실행한다. README에는 DB 시작 → model prepare → migration → index → client create → serve → search 순서만 기록한다.

**검증 사례:**

| 분류 | 기대 결과 |
|---|---|
| 기능 요구사항 | top 5 안에 직접 근거와 정확한 provenance |
| 확정 의사결정·정책 | top 5 안에 직접 근거와 정확한 provenance |
| API 계약 | top 5 안에 직접 근거와 정확한 provenance |
| 사용자 업무 흐름 | top 5 안에 직접 근거와 정확한 provenance |
| 프로젝트 용어 | top 5 안에 직접 근거와 정확한 provenance |
| 문서에 없는 질문 | `no_evidence`와 빈 목록 |

모든 경로는 `design/wiki/**`여야 한다. 각 인용의 행 범위·본문·commit SHA·content hash를 `git show` 원문과 대조한다. 인증 실패, active run 없음, 모델 불가, SQL 실패 응답이 원문·token·host path를 노출하지 않는지 확인한다.

**Unit test 설계:** 새 사용자 동작을 추가하지 않는 검증·문서 단위다. 코드 결함이 발견되면 단위 2 또는 3의 해당 정상·경계·실패 assertion을 RED로 추가한 뒤 승인 범위 안에서 수정한다. README 명령은 help/argument contract test로 잘못된 순서·누락을 검출한다.

**실행 Agent 검증:** 외부 의존 없는 Unit·API contract·README 명령 정적 검증만 수행한다.

**독립 검증:** PostgreSQL integration, 실제 model prepare, 고정 commit 전체 색인, 6개 smoke, provenance 대조, 오류 정보 비노출, 전체 Unit test, Ruff와 `git diff --check`를 처음부터 수행한다. 모델 다운로드나 OMF 고정 commit 접근 권한이 없으면 통과로 간주하지 않고 외부 입력 blocker로 보고한다.

**실제 환경과 색인 결과:**

- `Qwen/Qwen3-Embedding-0.6B` revision
  `97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3`, CPU, 1024차원을 사용했다.
- 고정 source에서 158개 문서, 4,202개 section, 5,584개 chunk와 같은 수의
  embedding을 만들었으며 최대 chunk 크기는 800 token이었다.
- 첫 전체 embedding은 CPU에서 약 11시간 3분, artifact 재사용 재색인은 약
  20초였다. 11시간 3분은 성능 합격 기준이 아니라 장시간 로컬 작업의 운영 위험과
  진행 가시성 필요를 기록한 관찰값이다. 정식 성능 benchmark는 후속이다.

**6개 smoke 결과:**

| 분류 | Acceptable evidence 실측 | MVP gate |
|---|---:|---|
| 기능 요구사항 | 3위 | PASS |
| 확정 정책·의사결정 | 3위 | PASS |
| API 계약 | 1위 | PASS |
| 사용자 업무 흐름 | 1위 | PASS |
| 프로젝트 용어 | 1위 | PASS |
| 문서에 없는 질문 | `no_evidence`, 빈 목록 | PASS |

모든 반환 경로가 `design/wiki/**`임을 확인했고, 87개 provenance 좌표의 행
범위·본문·commit SHA·content hash를 원문과 대조했다. 인증 실패, active run 없음,
모델 불가와 SQL 실패의 원문·token·host path 비노출 및 전체 회귀 검증도 PASS했다.

품질 한계도 gate 결과와 분리해 기록한다. 사용자 업무 흐름 질의의 이상적
diagnostic target은 top 20에 없었고 프로젝트 용어 질의의 이상적 target은 8위였다.
두 질의 모두 별도의 직접 acceptable evidence가 1위여서 MVP top 5 gate는
PASS했지만, 이상적 문서의 순위 개선은 정식 평가와 리랭커 검토가 포함되는 후속
범위다.

## 5. v2.1 corrective 실행 단위

v2.0 완료 구현과 로컬 검증을 유지했다. 최초 공유 RTX 4090 배포에서 문서에 없는
질의가 vector-only 근거를 반환해 API를 중단한 뒤 다음 네 단위를 순서대로 수행했다.
각 단위는 실행 Agent와 별도 검증 Agent를 사용했고 모두 독립 검증 PASS했다.

### Corrective 단위 1. 정본 v2.1 전환 — 완료 · 독립 검증 PASS

**주제:** Index identity와 search policy identity의 경계를 세 정본에 고정한다.

**목적:** 검색 하한선 변경이 5,584개 embedding 재생성으로 이어지는 MVP 결합을
제거하면서 결과 재현성을 유지한다.

**내용:** 시스템 설계, 이 구현 계획과 `AGENTS.md`의 버전·용어·migration·API 계약,
rollback, 현재 배포 상태와 후속 단위 포인터를 일치시킨다. 기존 CREFLE 로컬 번들을
보존하고 코드·DB·서버는 변경하지 않는다.

**기대 결과:** 세 정본이 v2.1 목표와 identity 경계, additive `0003`, additive
`search_policy` 응답, 기존 run 재사용, corrective 단위 2~4를 동일하게 기술한다.

**검증:** 문서 전용 단위이므로 Unit test 대신 갱신 전 v2.1 계약 assertion 실패를
RED로 확인하고 갱신 뒤 같은 assertion을 GREEN으로 확인한다. HTML parser, 정적 계약
대조, 로컬 headless browser 렌더·육안 확인, 외부 URL·금지 style 추가 여부와
`git diff --check`를 실행한다.

### Corrective 단위 2. Search policy storage와 migration — 완료 · 독립 검증 PASS

**주제:** 기존 색인과 분리된 immutable search policy manifest를 저장한다.

**목적:** 정책 변경을 새 index run이 아니라 독립적인 재현 좌표로 보존한다.

**내용:** Additive `0003`, policy canonicalization·SHA-256 identity, idempotent
register/resolve repository와 legacy backfill을 구현한다. 기존 run·config·embedding은
변경하지 않는다.

**Unit test 설계:**

| 사례 | RED assertion | 기대 GREEN |
|---|---|---|
| 정상 | 같은 exact policy snapshot을 두 번 register | 같은 policy ID와 hash를 반환하고 중복 row 없음 |
| 경계 | similarity floor 하나만 변경 | 새 policy ID/hash가 생기고 index run·chunk·embedding 수 불변 |
| 경계 | 기존 active/index-run의 legacy query·RRF snapshot upgrade | 동등한 immutable manifest로 backfill되고 legacy snapshot 유지 |
| 실패 | missing·extra key, 잘못된 타입 또는 비정규 snapshot | 저장·resolve 거부, 기존 row 불변 |
| 실패 | 저장 snapshot과 config hash 불일치 | 안전한 repository invariant error |

**실행 Agent 검증:** Canonical hash·repository Unit test, migration 정적 검사, Ruff와
`git diff --check`; PostgreSQL·Docker·GPU는 사용하지 않는다.

**독립 검증:** 관련 Unit 전체, PostgreSQL upgrade → downgrade → re-upgrade, legacy
backfill, 같은 정책 동시·반복 register, row/count 불변과 범위 밖 diff를 처음부터
검증한다.

### Corrective 단위 3. Search core·API policy 분리 — 완료 · 독립 검증 PASS

**주제:** Runtime이 선택한 policy로 기존 active index를 검색하고 정책 좌표를 반환한다.

**목적:** 하한선·RRF·query instruction 변경 시 embedding을 다시 만들지 않고도 동일
index에서 재현 가능한 결과를 낸다.

**내용:** `serve` 시작 시 policy register·resolve, document/query descriptor
호환성, search·ready validation과 API/CLI additive response를 연결한다. 인증·grant,
기존 endpoint와 오류 계약은 유지한다. 공개 CLI는 추가하지 않는다.

**Unit test 설계:**

| 사례 | RED assertion | 기대 GREEN |
|---|---|---|
| 정상 | 같은 active index에 floor A와 B 적용 | A는 evidence, B는 `no_evidence`; run ID 동일, policy ID만 다름 |
| 정상 | 같은 runtime snapshot으로 app 두 번 시작 | 같은 manifest를 resolve하고 readiness 전에 선택 완료 |
| 경계 | 기존 API 응답 consumer | additive `search_policy` 외 기존 필드와 의미 불변 |
| 실패 | query model·revision·dimension·normalization 비호환 | 안전한 503, 원문·host path 비노출 |
| 실패 | policy snapshot/hash 불일치 또는 미resolve | Search·ready 안전한 503 |
| 실패 | active index 없음 | 기존 409 계약 유지 |
| 정상/실패 | threshold만 변경 | index·document embedding provider/pipeline 호출 0회 |

**실행 Agent 검증:** Fake repository/provider 기반 policy·service·API·CLI Unit/contract,
기존 인증·오류 회귀, Ruff와 `git diff --check`.

**독립 검증:** 실행 검증 전체와 PostgreSQL에서 policy register/resolve, active index
호환성, 권한 CTE 선적용, 기존 run·embedding count 불변과 API schema를 검증한다.

### Corrective 단위 4. CUDA 하한선 보정과 공유 배포 — 완료 · 독립 검증 PASS

**주제:** RTX 4090 실측으로 새 search policy를 결정하고 기존 index로 API를 재가동한다.

**목적:** 정상 5개 질의의 직접 근거를 유지하면서 문서에 없는 질의를
`no_evidence`로 분리한다.

**내용:** 6개 질의의 lane별 raw score를 측정한다. 정상 acceptable evidence와 unknown을
분리하는 floor가 있을 때만 새 calibrated policy를 등록하고 production 설정에 선택한
뒤 API image를 재빌드·재시작한다. 분리 가능한 값이 없으면 임의 보정하지 않고 검색
전략 재검토를 위해 중단한다.

**검증 사례:**

| 사례 | 기대 결과 |
|---|---|
| 정상 5개 | 각각 top 5에 직접 근거, 모든 경로 `design/wiki/**` |
| 문서에 없는 질의 | 200 `no_evidence`, 빈 목록 |
| 재현 좌표 | active run `427f2c4a-ab06-486a-9801-4bde3ef17d63`와 고정 commit 유지, policy 좌표만 새 값 |
| 데이터 불변 | 158 documents·4,202 sections·5,584 chunks·5,584 embeddings 유지 |
| 호환성·보안 | 인증·grant·오류 비노출, 사용자 `local-agent`와 mode 0600 deployment token 보존 |
| 재시작 | PostgreSQL·API 재시작 후 같은 run·policy와 검색 결과 유지 |

실행 Agent는 외부 의존 없는 회귀만 수행한다. 독립 검증 Agent가 서버에서 raw score,
6개 smoke, provenance, 인증·LAN·재시작, 전체 Unit·PostgreSQL integration·API contract,
Ruff와 `git diff --check`를 다시 검증한다.

### 현재 공유 배포 상태와 rollback

- 최초 CUDA smoke에서 정상 5개는 3·4·1·1·2위였지만 unknown이 vector-only 근거를
  반환해 FAIL했고 API와 listener를 중단했다. 이는 corrective 전의 역사적 checkpoint다.
- 2026-08-31 07:43 KST 최종 독립 검증 뒤 API를
  `http://192.168.1.185:9090`에 공개했다. Container는 running/healthy다.
- 배포 code commit은 `6a211448d156bf5381277cf6f183ac19ccc94b0f`, image는
  `sha256:8a8e684aabb28e43ca3b599fa8c6780a6f7c2350ce2bc91ad31976b2267041e6`다.
- PostgreSQL은 healthy, Alembic은 `0003_search_policy_manifest`, immutable policy
  row는 2개다.
- 활성 CUDA policy의 안정 identity인 `config_hash`는
  `b1758182ed1bef3f87017cd5db45aa0e9829e785004545ddf48a9bc2be4b21bb`다. DB가 resolve한
  `policy_id` `10e86bbd-55b9-457c-9f73-0ca29d09625b`는 opaque row ID이며 deterministic
  identity로 사용하지 않는다.
- Active run은 `427f2c4a-ab06-486a-9801-4bde3ef17d63`, source commit은
  `a8f46f23cd3fb9c5f7042e987dff8103d23f0fa2`다.
- 158개 문서, 4,202개 section, 5,584개 chunk·embedding을 보존한다.
- 최종 6 smoke는 정상 5개 3·4·1·1·2위, unknown `no_evidence`로 PASS했다. 모든 origin은
  `design/wiki/**`였고 25개 origin hash와 42개 line coordinate를 대조했다.
- 보안·로그와 API 재시작 검증은 PASS했다. Mac live는 200, server 공개 URL의 인증된
  ready는 200이었다. Mac authenticated ready/search는 token boundary 때문에 실행하지 않았다.
- 사용자가 만든 `local-agent`와 서버 내부 mode `0600` deployment token을 보존한다.
  비밀값, DB URL과 host path는 기록하지 않는다.
- 재색인, embedding 재생성, client 재발급은 수행하지 않았다.
- Policy 실패 시 이전 policy 환경설정으로 되돌리고 API만 재시작한다. Policy manifest는
  append-only이며 active run과 embedding을 삭제하지 않는다.
- Rollback image
  `sha256:b7a1d0e678c783ffc3e9b0cd663435f7a7b0348739f249109f341863aaf1971e`와
  Compose-valid legacy policy 환경설정을 보존한다.

## 6. 단계별 정지 조건

- 각 단위는 사용자 승인 후 실행 Agent와 별도 검증 Agent가 수행한다.
- 검증 실패는 같은 실행 Agent가 승인 범위 안에서 수정하고 같은 검증 Agent가 전체를 처음부터 재실행한다.
- 승인 범위 밖 schema·API·dependency·검색 정책 변경이 필요하면 즉시 중단하고 재승인받는다.
- 단위 4의 독립 검증과 6개 smoke가 모두 끝나기 전에는 MVP 완료를 주장하지 않는다.
  이 조건은 2026-08-27에 충족되었다.
- v2.1 corrective 단위 2~4는 각각 독립 검증 PASS했다. CUDA에서 정상·unknown을
  분리할 하한선을 확인했고 계획 밖 schema/API 변경은 없었다.
- 공유 API 중단 조건은 corrective 단위 4의 독립 검증 완료로 해제되었고 현재 공개
  상태다. 후속 재배포에서도 같은 gate와 중단 조건을 다시 적용한다.
