# CREFLE 정보 조회 플랫폼 Agent 협업 규칙

이 파일은 Codex 세션이 바뀌어도 유지해야 하는 사용자 지시와 설계 상태다. 직접적인 최신 사용자 지시가 이 파일과 충돌하면 최신 지시를 우선하고, 충돌 사실을 사용자에게 알린다.

## 공통 개발 작업 하네스

Codex와 Claude Code는 이 파일을 공통 정책의 정본으로 사용한다. 저장소를
읽거나 변경하는 실행형 작업은 `.agents/skills/development-workflow/SKILL.md`를
먼저 적용한다. 단순 대화, 이미 승인된 작업의 상태 전달, 사용자의 승인·반려
응답은 새 작업으로 보지 않는다.

### 계획 보고와 승인 게이트

- 모든 작업은 시작 전에 **주제, 목적, 내용, 기대 결과, 검증 방법**을 담은
  계획 보고서를 사용자에게 제시하고 명시적 승인을 기다린다.
- 코드 개발 계획에는 관찰할 동작, 정상·경계·실패 사례와 예상 결과를 포함한
  **Unit test 설계**를 반드시 넣는다.
- 객관적 지표를 정의하기 어려운 결과물은 검토자가 일관되게 판단할 수 있는
  **정성적 평가 기준**과 합격 예시를 계획에 명시한다.
- 약어, 은어와 다른 문서에서 가져온 키워드는 첫 사용 위치에서 설명하거나 문서
  최상단에 **용어표**를 두어 독자가 다른 문서를 추측해 찾지 않게 한다.
- 승인 전에는 파일 변경, 명령 실행, 외부 시스템 변경과 Agent 위임을 시작하지
  않는다. 승인 범위가 일부이면 승인된 부분만 실행한다.
- 계획을 작성하는 동안에도 사용자 요청과 이미 제공된 문맥을 넘는 저장소 조사나
  실행은 하지 않는다. 필요한 사전 조사가 있으면 그 조사 자체를 계획에 넣는다.
- 새 가정, 범위 변경, 예상 밖의 제약, 계획된 검증의 변경이 생기면 즉시 멈추고
  차이와 영향을 보고한 뒤 재승인을 받는다.
- 전체 계획이 한 번 승인되면 각 작업 단위의 결과·검증 증거와 진척을 계속
  보고하되, 범위 변경·새 의사결정·검증 실패가 없으면 사용자 확인을 기다리지
  않고 다음 단위로 자동 진행한다. 검증 실패는 아래 실패 루프로 처리한다.

### 작업 분할 우선순위

작업은 다음 순서로 분할한다. 뒤의 기준을 맞추기 위해 앞의 기준을 훼손하지 않는다.

1. **Single Intent**: 한 작업 단위는 하나의 사용자 의도와 관찰 가능한 결과만 가진다.
2. **Backward Compatibility & Risk Isolation**: 호환성 위험과 되돌리기 어려운 변경을
   다른 변경에서 격리한다.
3. **Independent Testability**: 각 단위는 가능한 한 독립적으로 실패·성공을 검증할 수
   있어야 한다.
4. **Layered Slicing**: 필요할 때 기반 계층부터 소비 계층으로 얇게 나눈다.
5. **변경량 휴리스틱**: 한 단위의 목표는 변경 400줄 이하이다. 500줄을 초과할 것으로
   보이면 위 우선순위를 지키면서 재분할할 실질적 이점이 있는지 검토하고 계획에
   판단 근거를 기록한다. 줄 수만 맞추는 기계적 분할은 하지 않는다.

UI 작업은 레이아웃 구조의 컴포넌트 단위로 나눈다. 페이지나 화면의 배치와 상태
전파에 가장 큰 영향을 주는 최상위 영향 컴포넌트부터 구현·검증한 뒤 하위
컴포넌트로 진행한다.

### 실행과 독립 검증

- 승인 후 오케스트레이터는 실행 Agent와 검증 Agent를 반드시 별도 인스턴스로
  호출한다. 같은 Agent가 구현과 최종 합격 판정을 함께 맡지 않는다.
- 실행 Agent는 승인된 한 작업 단위만 구현한다. TDD를 적용하고,
  **외부 의존성 없는 쉬운 검증**만 수행하며 최종 합격 판정을 하지 않는다.
- 검증 Agent는 read-only 역할로 새 인스턴스에서 시작한다. 실행 Agent의 결론을
  신뢰하거나 이어받는 대신 승인 계획과 현재 diff를 읽고 계획된 모든 검증을
  독립적으로 다시 실행한다.
- 검증 Agent는 명령, 결과, 증거, 누락·편차, 발견 사항과 판정을 검증 보고서로
  반환한다. 계획된 검증을 실행할 수 없으면 통과로 간주하지 않고 이유를 명시한다.

### 조정 Agent와 검증 실패 루프

- 조정 Agent는 승인, 역할 배정과 보고만 조율하며 구현하지 않고
  자기 결과에 합격 판정을 내리지 않는다.
- 검증 실패가 나면 조정 Agent는 검증 Agent의 발견 전체를 실행 Agent에 반환한다.
  실행 Agent가 승인 범위 안에서 모두 수정한 뒤 동일한 검증 Agent 인스턴스가
  계획된 모든 검증을 처음부터 재실행한다.
- 수정에 계획 밖 변경이 필요하면 루프를 멈추고 재승인을 받은 뒤 계속한다.

### TDD

- 개발은 RED → GREEN → REFACTOR 순서의 TDD로 진행한다.
- 승인 계획에 테스트 설계가 있으면 실행 Agent는 그것을 그대로 사용한다.
- 승인 계획에 테스트 관련 내용이 없을 때만 실행 Agent가 스스로 테스트 코드를
  설계할 수 있다.
- RED는 구현 부재 때문에 실패하는 assertion을 실제로 확인한다. import·문법·fixture
  오류 같은 테스트 오류는 RED로 인정하지 않는다.
- GREEN에서는 테스트를 통과시키는 최소 구현을 작성하고, refactor 뒤 같은 테스트가
  계속 통과하는지 확인한다.

## 프로젝트 목표와 경계

- 이 저장소는 특정 RAG 구현에 한정하지 않는 범용 정보 조회 플랫폼을 만든다.
- 첫 번째 검증 대상은 OMF-MES 설계 문서이며, 이후 다른 프로젝트를 같은 방식으로 연결할 수 있어야 한다.
- OMF 문서 저장소는 검색 원본이고 이 저장소는 색인·검색·Agent 인터페이스·배포를 소유한다. 두 저장소의 수명주기와 릴리스를 분리한다.
- 개발 시 OMF 저장소(`/Users/rangkim/projects/crefle/ohmyfactory/apps/omf`)는 읽기 전용 데이터 소스로 취급한다. 수정이 필요하면 별도 승인받는다.
- 첫 MVP는 고정된 OMF 현재본을 색인하고 인증된 HTTP 질의에 재현 가능한 근거
  패키지를 반환하는 사용자 기능을 가장 빠르게 완성하는 것이 목표다.
- v2.0 MVP 완료선은 로컬 PostgreSQL과 실제 임베딩 모델을 사용한 기능 검증이었다.
  Ubuntu와 Docker Compose 공유 배포는 당시 후속으로 분리했으며 이후 별도 사용자
  승인으로 착수했다.

## 단계별 공동 진행

- 구현 전에 설계를 완성하고 사용자 승인을 받는다. 설계 승인 전에는 코드·스키마·서비스 스캐폴딩을 만들지 않는다.
- 각 단계를 시작하기 전에 목표, 산출물, 확인할 의사결정을 설명한다.
- 한 단계를 마치면 결과와 검증 내용을 공유하고, 승인된 전체 계획 범위 안이면
  별도 확인 대기 없이 다음 단계로 진행한다.
- 승인된 전체 계획의 여러 단계를 연속으로 진행할 수 있으며, 진척·검증 증거는
  각 단계에서 계속 공유한다.
- 질문은 가능한 한 한 번에 하나씩 제시하고, 권장안·대안·트레이드오프를 함께 설명한다.
- 새 가정, 범위 변경, 예상 밖의 제약이 생기면 즉시 공유하고 다시 합의한다.

## 사용자 승인 필수 의사결정

다음 항목은 Agent가 단독으로 확정하거나 구현하지 않는다.

- 핵심 기능의 범위와 동작
- 시스템 아키텍처와 서비스 경계
- 데이터 모델과 정본·버전·권한 정책
- Agent 도구 및 외부 API 계약
- 검색·색인·랭킹·생성 전략
- 데이터베이스, 검색 확장, 모델, 프레임워크 등 외부 의존성의 선택·추가·교체
- 인증, 보안, 배포, 운영 방식

의사결정이 필요하면 권장안과 근거, 2~3개 대안, 비용·복잡도·향후 변경 가능성을 먼저 설명하고 사용자 확인 후 반영한다. 승인된 결정도 전제가 바뀌면 다시 확인한다.

## 문서 작성

- 설계서, 계획서, 보고서, 의사결정 기록 등 사람이 읽는 문서를 만들거나 수정할 때는 전역 `crefle-doc` 스킬을 먼저 사용한다.
- 작업 시작 시 스킬을 사용하는 이유를 사용자에게 알린다.
- 작성자, 작성일시, 문서 버전, 열람 대상을 사용자에게 확인한 후 문서를 완성한다.
- HTML 등 파생 산출물이 있으면 정본과 생성물의 관계를 확인하고 생성물을 직접 편집하지 않는다.
- 스킬이 보이지 않거나 동작하지 않으면 조용히 대체하지 말고 사용자와 복구 방법을 합의한다.

## Agent 조회 동작

- 사내 Agent의 LLM은 Codex API 사용을 전제로 한다.
- Agent가 조회 필요성을 자율 판단하되, 내부 설계 사실·규정·정책·API·데이터 모델에 답할 때는 조회를 강제한다.
- 대표 질의는 “특정 기능의 요구사항 및 정책 결정 사항 원문을 찾아줘” 유형이다.
- 호출 Agent의 최종 응답에는 요약과 직접 인용을 포함한다. MVP 검색 서비스는 이를
  위해 저장소 기준 파일 경로, 제목 계층, 원문 행 범위, 검색 순위, 모든 원본 경로,
  색인 run·commit SHA와 내용 해시를 반환한다.
- 근거가 없으면 추측하지 않고 확인 가능한 근거가 없음을 명시한다.
- 문서가 충돌하면 하나를 임의로 선택하지 않고 양쪽 근거와 충돌 내용을 제시한다.

## 확정된 MVP 설계 결정

### 소스 범위

- 첫 source profile은 OMF다.
- 고정 원본 commit은 `a8f46f23cd3fb9c5f7042e987dff8103d23f0fa2`다.
- 포함: `design/wiki/**/*.md`의 현재본 Markdown.
- 제외: `design/raw/**`, `design/schema/**`, `docs/**`, `_workspace/**`, Agent 설정·작업
  파일, HTML, PDF, Excel, 이미지, 생성물과 임시 작업물.

### 정본·버전·충돌

- MVP는 고정 commit의 현재본만 색인·검색한다. 과거본과 임의 commit 검색은 후속 범위다.
- 완전히 같은 내용은 해시로 감지하되 모든 원본 경로를 보존한다.
- `확정기록`, `결정서`, `[확정]` 표기는 일반 조사 문서보다 우선한다. 같은 지위에서는 버전과 결정일을 비교하고, 남은 충돌은 사용자에게 공개한다.
- Git 이력 전체를 DB에 복제하지 않는다. 색인은 커밋된 깨끗한 작업 트리에서 수행하고 색인 실행의 `commit_sha`, `indexed_at`과 문서 `content_hash`를 기록한다.
- Git 커밋 SHA는 이력 저장 기능이 아니라 인용 원문을 재현하기 위한 좌표다. Git 이력 전체 검색은 MVP 이후 범위다.

### 문서 분할과 검색

- Markdown 제목 구조를 이용한 Parent–Child 분할을 사용한다.
- 제목 계층을 chunk에 포함하고, 긴 절만 child로 추가 분할하며 표·목록·인용문은 가능한 한 보존한다.
- 작은 child로 검색하고 Agent에는 상위 절 문맥을 함께 제공한다. 세부 chunk 크기는 임베딩 모델 검증 시 정한다.
- PostgreSQL 기반 하이브리드 검색을 사용한다.
- 키워드 검색은 `pg_trgm`, 의미 검색은 `pgvector`, 결과 결합은 RRF를 사용한다.
- 초기 코퍼스가 작으므로 벡터는 정확 검색으로 시작하고 HNSW·IVFFlat을 추가하지 않는다.
- 리랭커는 초기 제외하고 평가 기준 미달 시 추가한다.
- 한국어 키워드 품질이 부족하면 ParadeDB `pg_search`와 Korean Lindera를 우선 대안으로 검토한다.

### 근거 하한선과 no_evidence

- 각 검색 lane은 원시 점수(raw score)를 사용한다. 키워드 lane은 `pg_trgm`
  similarity, 벡터 lane은 정규화한 임베딩의 cosine similarity다.
- v2.0 로컬 CPU baseline의 키워드 하한선은 `0.03658536400000001`, 벡터 하한선은
  `0.48344050397156374`, 상태는 `calibrated`다. 점수가 lane별 하한선 이상(`>=`)인
  후보만 해당 lane의 RRF 후보가 된다. 이 값은 기존 검증 provenance로 보존하며
  공유 CUDA 정책의 확정값으로 간주하지 않는다.
- 하한선은 문서에 없는 smoke 질의에서 관찰한 lane별 최고 점수에
  `nextafter(+inf)`를 적용한 다음 표현 가능 부동소수점 값이다. 따라서 관찰된
  unknown 최고점은 하한선 비교에서 제외된다.
- 두 lane 모두 하한선을 통과한 후보가 없으면 HTTP 200,
  `status: no_evidence`, 빈 `evidence_items`를 반환한다.
- v2.0 구현은 후보 수 50/50, RRF `k=60`, 키워드·벡터 가중치 `1.0`과 위
  하한선·상태를 index config에 byte-exact 값으로 저장했다. v2.1은 이를 immutable
  search policy manifest로 분리하며 legacy snapshot은 provenance로 보존한다.
- 현재 6개 smoke에서 확인한 acceptable-evidence 분리 margin은
  `0.16857380984674064`다. 이는 고정 smoke의 관련 근거 분리 관찰값이며 정식 검색
  품질 metric이나 성능 기준은 아니다.

### Index identity와 search policy identity

- Index identity에는 source profile·commit, parser, chunker, tokenizer, document
  embedding provider·model·revision·dimension·normalization·library behavior처럼
  저장 문서·chunk·vector를 바꾸는 설정만 포함한다.
- Search policy identity에는 query embedding model·revision·dimension·normalization과
  instruction, keyword/vector candidate limits, RRF k·weights, 두 similarity floor와
  calibration status처럼 query-time 결과를 바꾸는 설정을 포함한다.
- Search policy는 exact-key canonical JSON의 SHA-256 `config_hash`로 식별하는
  append-only immutable DB manifest다. Runtime settings가 policy를 선택하며 `serve`는
  readiness 전에 같은 manifest를 idempotent register·resolve한다.
- 공개 policy 관리 CLI와 active-policy pointer는 추가하지 않는다. MVP 공유 배포는
  단일 Compose API replica를 전제로 하며 replica 간 config consistency는 후속이다.
- Active index의 document embedding descriptor와 policy의 query
  model·revision·dimension·normalization 호환성을 강제한다. Query instruction, RRF와
  floor 차이는 index 불일치가 아니며 index·embedding pipeline을 호출해서는 안 된다.
- Policy 변경은 새 immutable manifest와 API restart만 필요하다. 실패 시 이전 policy
  환경설정으로 되돌리고 API만 재시작하며 active run과 embedding은 보존한다.

### 사용자 인터페이스와 인증

- 공개 MVP 인터페이스는 `POST /v1/search`, `GET /health/live`, 인증된
  `GET /health/ready`다.
- 검색 요청은 `query`와 선택적 `limit`만 받는다. source는 `omf`, version은 활성
  현재본으로 고정하며 path·decision·history·context filter는 제공하지 않는다.
- Bearer token과 source grant를 API와 검색 후보 범위 양쪽에서 강제한다.
- 응답은 자연어 생성 답변이 아니라 인용, 저장소 경로, 제목 계층, 원문 행 범위,
  개별 검색 순위와 RRF 점수, 모든 원본 경로, 색인 run·commit SHA와 내용 해시를 담은
  근거 패키지다.
- 응답에는 기존 `index.run_id`·`commit_sha`와 evidence provenance를 유지하면서
  additive `search_policy.policy_id`·`config_hash` 재현 좌표를 포함한다.

### 임베딩

- 최초 임베딩 모델은 `Qwen/Qwen3-Embedding-0.6B`, 고정 revision
  `97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3`, 출력 1024차원이다. 로컬 MVP
  baseline은 CPU였고 공유 배포 runtime은 `cuda:0`이다.
- 질의용 instruction과 모델명·차원은 설정으로 분리해 재색인과 모델 교체가 가능하게 한다.
- OMF 평가 질문에서 기준 미달일 때 `BAAI/bge-m3`와 비교한다.
- Kanana Instruct 모델은 임베딩 생성에 사용하지 않는다.

## 내부 모델 서버 현황

- 내부 CrefleAI 서버: `http://192.168.1.167:8000`
- GPU: Blackwell RTX PRO 6000 1개.
- 현재 생성 모델: Kanana-2 30B-A3B Instruct Q4_K_M.
- 확인된 공개 API는 `/v1/chat/completions`와 `/v1/models`이며 `/v1/embeddings`는 아직 없다. `/v1/models`는 인증이 필요하다.
- 비밀 토큰이나 관리자 인증정보를 저장소에 기록하지 않는다.

## 보안과 데이터 취급

- 내부 원문과 인덱스는 사내 환경에 저장한다.
- 검색된 최소 근거는 답변 생성을 위해 Codex API에 전송할 수 있다는 사용자 승인이 있다.
- 원문 전체나 불필요한 주변 문맥을 외부 서비스에 전송하지 않는다.
- `docs/raw`와 기타 대외비 자료를 외부 서비스에 업로드하지 않는다.

## 승인된 설계와 현재 구현 기준

- MVP 시스템 설계 정본은
  `docs/design/2026-08-13-omf-retrieval-mvp-system-design.html` v2.1이다.
- 상세 구현 계획 정본은
  `docs/superpowers/plans/2026-08-13-omf-retrieval-mvp-implementation.md` v2.1이며,
  상태는 **MVP 완료 · v2.1 corrective 완료 · 공유 배포 완료**다.
- 작업 1~7의 완료 구현과 작업 8~10에서 시작한 구현을 보존·재사용했다.
- 기존 통합 `0002` manifest·lifecycle migration을 MVP 기준으로 채택한다. v1.3의
  8A-1 분리 및 별도 `0003` migration 계획은 v2.0이 명시적으로 대체한다.
- v2.1의 새 additive `0003`은 폐기한 lifecycle 분리안과 다르다. Immutable search
  policy manifest와 config hash를 추가하고 기존 active/index-run의 legacy
  query·retrieval snapshot을 backfill한다. 기존 index run·config·5,584개 embedding은
  변경·삭제하지 않는다.
- 정본 전환, 기존 색인·활성화·인증 WIP 안정화, 검색 core와 API·CLI의 수직 절편,
  로컬 실제 모델 E2E와 6개 smoke 검증의 네 단위를 완료했고 각 단위의 독립 검증이
  PASS했다.
- 고정 source 실측은 158개 문서, 4,202개 section, 5,584개 chunk·embedding이며
  최대 chunk 크기는 800 token이다. 첫 CPU 전체 embedding은 약 11시간 3분이
  걸렸고 artifact 재사용 재색인은 약 20초였다. 전자는 MVP 성능 합격 기준이 아니라
  로컬 운영 위험과 진행 가시성에 관한 관찰값이다.
- 6개 smoke의 acceptable evidence 순위는 정상 5개가 각각 3·3·1·1·1위였고,
  문서에 없는 질의는 `no_evidence`였다. 모든 반환 경로는 `design/wiki/**`, 대조한
  provenance 좌표는 87개였다.
- 사용자 업무 흐름의 이상적 diagnostic target은 top 20에 없었고 프로젝트 용어의
  이상적 target은 8위였다. 직접 acceptable evidence는 두 질의 모두 top 5에 있어
  MVP gate는 통과했지만, 정식 평가와 리랭커 검토는 후속 범위로 유지한다.
- 인증·오류 정보 비노출, 원문 provenance와 전체 회귀 검증은 PASS했다.
- 최초 공유 RTX 4090 smoke에서는 정상 5개 질의가 3·4·1·1·2위였지만 문서에 없는
  질의가 vector-only 근거를 반환해 gate가 FAIL했다. 이는 corrective 착수 전의
  역사적 중단 기록이며 당시 API와 listener를 안전하게 중단했다.
- v2.1 corrective 네 단위인 정본 전환, policy storage·migration, search core·API
  분리, CUDA calibration·deploy는 모두 완료했고 각각 독립 검증 PASS했다.
- 공유 API는 2026-08-31 07:43 KST에 `http://192.168.1.185:9090`으로 공개했고
  container는 running/healthy다. 배포 code commit은
  `6a211448d156bf5381277cf6f183ac19ccc94b0f`, image는
  `sha256:8a8e684aabb28e43ca3b599fa8c6780a6f7c2350ce2bc91ad31976b2267041e6`다.
- PostgreSQL은 healthy이고 Alembic revision은 `0003_search_policy_manifest`다.
  Immutable policy row는 2개이며 활성 CUDA 정책의 안정 identity인 `config_hash`는
  `b1758182ed1bef3f87017cd5db45aa0e9829e785004545ddf48a9bc2be4b21bb`다. DB가 resolve한
  opaque `policy_id`는 `10e86bbd-55b9-457c-9f73-0ca29d09625b`이며 deterministic
  identity로 간주하지 않는다.
- 공유 환경의 active run은 `427f2c4a-ab06-486a-9801-4bde3ef17d63`이며 고정 commit과
  158개 문서·4,202개 section·5,584개 chunk·embedding을 보존한다. 최종 smoke는
  정상 5개 3·4·1·1·2위, unknown `no_evidence`로 PASS했고 25개 origin hash와 42개
  line coordinate를 대조했다. 재색인, embedding 재생성, client 재발급은 없었다.
- 보안·로그와 재시작 검증은 PASS했다. 사용자가 생성한 `local-agent`와 서버 내부
  mode `0600` deployment token, 이전 Compose-valid policy 설정과 rollback image
  `sha256:b7a1d0e678c783ffc3e9b0cd663435f7a7b0348739f249109f341863aaf1971e`를 보존하며
  비밀값은 기록하지 않는다.
- 생성형 답변, 과거본·상세 filter, 정식 평가·감사, Buildx·digest 배포 자동화,
  정식 서버 성능 gate와 운영 runbook은 후속 범위다. 기존 구현이 있으면 삭제하지
  않고 비노출 상태로 보존한다.
- v1.2 작업 13~18 전체는 v2.0 후속으로 이동했다: 작업 13 평가 dataset·metric,
  작업 14 logging·audit, 작업 15 Docker·Compose, 작업 16 Buildx·digest deploy,
  작업 17 server E2E·performance, 작업 18 operations handoff.
- 이전 세션의 미확정 목록과 설계 재개 절차는 더 이상 현재 상태가 아니다. 구현 시
  위 두 정본의 범위, 정지 조건, 단계별 진척 보고·독립 검증 지점과 테스트 설계를 따른다.
- 후속 재배포 전 입력값과 외부 변경은 설계서와 구현 계획에 적힌 checkpoint에서
  별도 확인한다.
- 제품 범위나 승인된 설계를 바꿀 필요가 생기면 구현을 멈추고 사용자 승인을 다시
  받는다.
