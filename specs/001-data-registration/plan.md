# Implementation Plan: 문서 자료 등록·원본 보관·불변 revision·권한·스키마 사전(data-registration)

**Branch**: `001-data-registration` | **Date**: 2026-11-09 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/001-data-registration/spec.md`

**Note**: This template is filled in by the `/speckit-plan` command; its definition describes the execution workflow.

**상태**: 결정 승인 완료. [research.md](./research.md) 3절의 결정 요청 DR-01~DR-24를 사용자가 2026-11-10 모두 승인했다(DR-11은 spec FR-046·FR-047 유지, DR-03·DR-15에는 추가 결정). 이 계획은 승인된 안 기준이며 `[DR-xx]`는 근거 표시다. **tasks 단계(`speckit-tasks`)를 시작할 수 있다.**

**개정(2026-11-10, 2차)**: DR-04 안전 상한 수치와 xlsx ZIP 경계(선언이 xlsx인 파일만: ZIP 서명(`PK\x03\x04`)으로 시작하지만 ZIP으로 해석 불가 → `unreadable_content`, ZIP이지만 유효 OOXML 아님 → `unsupported_file_format`, 서명 없는 알 수 없는 바이트는 `unsupported_file_format`) 승인 반영(U-INSP-01·02), 같은 바이트 파일의 제출 이름 대응 참고 추가, 의존성 승인 확정 문구 갱신.

**개정(2026-11-10)**: 승인 반영 — DR-03 ZIP·압축 파일 미허용, DR-11 외부 호출자 원본 읽기 예외안 기각 기록, DR-15 이미지 순서 없는 집합·여러 쪽 문서 PDF 합치기 안내, 재검증 Low L-1~L-4(단계표 C-API-09·10, U-ORDER-01을 P6로, C-API-11 정책 실패 분기, C-API-10 표식 위치).

**개정**: 2026-11-09 독립 검증 FAIL(M-1~M-6, L-1~L-6) 반영. Markdown 형식 판정(M-1, DR-23), 선언 검증 없는 계약과 판정 순서(M-2·M-3, research R-04), 마이그레이션 정적 단위 테스트(M-4), 전 DR 형식 통일(M-5), 응답 내용 비노출(M-6, research R-11), quickstart·data-model·H-03·문구 보정(L-1~L-5), 이전 R-01~R-03의 DR-20~DR-22 승격(L-6).

## 용어

| 용어 | 뜻 |
|---|---|
| FR, SC | spec의 기능 요구사항과 성공 기준. 이 계획의 추적 단위다. |
| DR | 결정 요청(Decision Request). [research.md](./research.md) 3절. 사용자 승인 필요. |
| R-xx | research.md 2절의 기술 판단(새 의존성 없음, 계획 승인 시 함께 승인). |
| M1·M2·M3 | spec Assumptions의 이전 검토 이월 미결 항목. |
| 설계서 | 공통 모듈 구조 설계서 `docs/design/2026-11-09-common-module-architecture.md` v0.3(사용자 승인 2026-11-09). `D-xx`는 승인 결정, `O-13`은 미승인 열린 항목. |
| 헌법 | `.specify/memory/constitution.md` v2.1.0(작업 트리). 원칙 I~VII. |
| MVP | 현재 운영 중인 최소 기능 제품(Git 고정 commit OMF 색인 + `POST /v1/search`). |
| 자료 ID, revision, 스키마 ID, 스키마 사전, 정보 자료, 묶음 | spec 용어표와 같다. |
| 대상 파일 | 묶음에서 정보 자료가 설명하는 쪽 파일(이미지, 전체 스캔 PDF, 그림이 든 Excel·PDF). |
| 효력 있는 원본 읽기 권한 | 원본 읽기 권한 기록이 있고 외부 LLM 사용 표시가 `internal`인 상태(FR-031, FR-047). |
| 내용 확인기 | 등록 시점에 실제 형식, 손상·암호, 텍스트 해석 가능 여부, PDF 글자 층 유무, 포함 이미지 위치를 결정적으로 확인하고, 스키마 맞음 판정에 필요한 구조 값(시트 이름, 스키마가 지정한 셀 값, 정보 자료의 제목 줄)을 읽어 넘기는 프로그램. 읽은 값은 그 요청의 비교에만 쓰고 저장·색인·응답·로그에 남기지 않는다. LLM·OCR을 쓰지 않으며 문서 전체 글자 추출·청킹은 하지 않는다(파서는 `003`). |
| 형식 판정 결과 | 내용 확인기가 정한 `xlsx`·`pdf`·`png`·`jpeg`·`text`·`markdown`. revision에 불변으로 기록하고 동일성 판정에 넣는다[DR-23]. |
| 구성 1차·2차 | 요청 구성 판정의 두 단계. 1차는 파트 개수·이름만(내용 불필요), 2차는 형식 판정 결과로 판정한다(research R-04). |
| 프레임워크 선언 검증 | FastAPI가 매개변수 선언(타입·pattern·개수)으로 핸들러 전에 하는 검증. 실패 시 기존 처리기가 422 `invalid_request`를 낸다. 새 경로는 이것이 인증보다 먼저 일어나지 않게 선언을 비운다. |
| 관리자 CLI | 서버에서 실행하는 `omf-retrieval admin ...` 명령. 서버 관리 경로의 권장 형태[DR-10]. |
| TDD | 테스트 주도 개발. RED(구현 부재로 assertion 실패 확인) → GREEN(최소 구현) → REFACTOR(동작 유지 정리). |
| RED | 구현이 없어서 생기는 assertion 실패. import·문법·fixture 오류는 RED가 아니다. |
| 계약 테스트 | 공개 인터페이스(HTTP 응답 형식, CLI 출력·exit code)가 계약 문서와 같은지 확인하는 테스트(`tests/contract`). |
| 통합 테스트 | 실제 PostgreSQL(`compose.test.yaml`)과 함께 저장·트리거·트랜잭션을 확인하는 테스트(`tests/integration`). |
| 인수 하네스 | SC를 실제 HTTP 앱과 PostgreSQL로 판정하는 테스트 묶음. [quickstart.md](./quickstart.md) 2절. |
| Phase | 이 계획의 구현 단계 묶음. 각 Phase는 하나의 의도와 독립 검증을 가진다. tasks 분해는 다음 단계(`speckit-tasks`)다. |
| advisory lock | PostgreSQL의 응용 정의 잠금. |
| append-only | INSERT만 허용하고 UPDATE·DELETE를 트리거로 거부하는 저장 방식. |

## Summary

RDB에 보관되지 않는 문서 자료(Excel `.xlsx`, PDF, 이미지, 텍스트)를 `factory` source에 REST API로 등록하고, 원본을 받은 그대로 사내 PostgreSQL에 보관하며[DR-01], 성공한 등록마다 서버가 발급한 불변 revision(UUID)[DR-09]을 돌려준다. 모든 등록은 스키마 사전의 스키마 하나에 연결되고(텍스트는 기본 스키마), 스키마는 ID가 곧 버전인 append-only 사전에 보관한다. 권한은 기존 Bearer 토큰으로 인증하고, 검색 권한(기존 source grant)과 독립인 쓰기 권한·원본 읽기 권한을 새 테이블에 둔다. 원본 읽기 권한은 관리자가 정한 외부 LLM 사용 표시가 `internal`일 때만 부여·효력을 가진다. source 생성, 권한 부여·회수, 표시 변경, 완전 삭제는 서버 관리 경로(관리자 CLI)[DR-10]로 한다.

기술 접근은 기존 스택(Python 3.12, FastAPI, SQLAlchemy 2.0, Alembic, PostgreSQL) 재사용이다. 등록은 DB 트랜잭션 하나로 원자적으로 처리하고, 판정 순서는 인증 → source → 쓰기 권한 → ID 규칙 → 크기 → 요청 구성 → 형식·내용 → 스키마로 고정한다(FR-042). 등록 경로는 LLM·외부 서비스·임베딩 모델을 쓰지 않는다. 기존 검색 기능, Git 색인 경로, 기존 테이블은 바꾸지 않는다(FR-035·FR-036).

새 외부 의존성은 `python-multipart`[DR-05], `pypdf`·`defusedxml`[DR-04] 세 개이며 2026-11-10 사용자 승인으로 확정되었다. 의존 Phase(P4a, P7)에서만 추가한다.

## Technical Context

**Language/Version**: Python `>=3.12,<3.13`(기존 `pyproject.toml`)

**Primary Dependencies**: 기존 FastAPI `0.141.x`, pydantic(v2, pydantic-settings 경유), SQLAlchemy `2.0.x`, Alembic `1.19.x`, psycopg 3, typer, uvicorn. 새 의존성(승인 2026-11-10): multipart 처리 `python-multipart`[DR-05], PDF·Excel 내용 확인 `pypdf`+`defusedxml`[DR-04]. 스키마 파일 검증은 pydantic으로 하며 새 의존성 없음[DR-06].

**Storage**: 기존 플랫폼 PostgreSQL. 새 테이블만 추가(새 Alembic revision 2개: P2a `0004`, P2b `0005`, `0003_search_policy_manifest` 다음). 원본 바이트는 `bytea` 테이블[DR-01, 승인 2026-11-10]. 대안(로컬 파일 시스템, 객체 저장소)은 검토 기록으로만 남긴다.

**Testing**: pytest(unit, contract, integration), ruff. 통합 테스트는 `compose.test.yaml` PostgreSQL과 `OMF_RETRIEVAL_TEST_DATABASE_URL`. 표본 파일은 테스트가 실행 중 합성한다[DR-18].

**Target Platform**: Linux 서버(Docker Compose 단일 API replica)와 macOS 개발 환경. 공유 서버 배포는 001 범위 밖.

**Project Type**: web-service(기존 FastAPI 앱에 경로 추가) + 서버 관리 CLI(기존 typer 앱에 `admin` 하위 명령 추가).

**Performance Goals**: 승인된 수치 없음(spec Assumptions). 인수 하네스에서 등록·되찾기 소요 시간을 측정·기록만 한다.

**Constraints**: 파일당 50 MiB·요청 100 MiB 기본 상한[DR-02]. 등록 경로의 외부 네트워크 호출 0건. 거부 시 저장 결과 0건. 응답·로그에 비밀값·원본 내용 0건. 내용 확인기 안전 상한 기본 설정값(사용자 승인 2026-11-10)[DR-04]: xlsx 압축 해제 총량 파일당 512 MiB, 항목별 압축 비율 100배(설정값은 양의 정수, `해제 바이트 ≤ 100 × 압축 바이트`, 0/0 항목 통과), 파트(ZIP 항목) 수 10,000개, PDF 2,000쪽, 파일당 확인 시간 30초. 상한과 같으면 통과, 넘으면 `unreadable_content`. GPU·임베딩 모델 불필요.

**Scale/Scope**: source 1개(`factory`), REST 경로 5개, 관리자 명령 10개, 새 테이블 14개. 동시 등록 수 목표 없음.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| 원칙 | 점검 내용 | 판정(설계 전) | 근거·조건 |
|---|---|---|---|
| I. 계획 보고와 사용자 승인 게이트 | 새 외부 의존성·데이터 모델·외부 API 계약·인증·운영 방식을 단독 확정하지 않는가 | 통과 | 의존성·저장·식별값·관리 경로·계약 형식·서비스 경계·권한 저장 구조·미결 항목을 DR-01~DR-24로 올렸고 사용자가 2026-11-10 모두 승인했다(DR-17은 이월 목록 승인). 조건(DR 승인 전 의존 Phase 시작 금지)은 해소되었다. |
| II. 작업 분할 우선순위 | Single Intent, 위험 격리, 독립 검증, 계층 분할, 400줄 휴리스틱 | 통과 | `구현 단계 묶음`에서 12개 Phase로 나눴다. 마이그레이션을 권한(P2a)과 자료(P2b)로 분리해 위험을 격리했다. 500줄 초과 예상 Phase 없음. |
| III. 실행과 독립 검증의 분리 | Phase마다 실행 Agent·검증 Agent 분리 가능한가 | 통과 | Phase마다 실행 Agent의 쉬운 검증과 검증 Agent의 전체 재검증 명령을 분리해 적었다. |
| IV. TDD | 관찰할 동작, 정상·경계·실패 사례와 예상 결과를 담은 테스트 설계가 있는가 | 통과 | `테스트 전략`에 계층별 사례와 예상 결과를 적었다. RED는 구현 부재 assertion 실패로만 인정한다. |
| V. 근거 재현성 | revision 불변, 내용 해시, 원본 재현 | 통과 | revision·원본·등록 기록 불변 트리거, 내용 해시 응답, 바이트 일치 되찾기. 001은 근거를 만들지 않는다(FR-035). 근거 좌표 일반화는 `006`. |
| VI. 보안·데이터 경계 | 사내 저장, 외부 전송 금지, 비밀값 비기록, 외부 표시 호출자의 원본 읽기 금지 | 통과 | 저장은 플랫폼 PostgreSQL[DR-01]. 등록 경로 외부 호출 없음(R-09). 표시 `external`·미표시 호출자 부여 거부(FR-046)와 효력 상실(FR-047). 오류·로그 비노출(R-08, DR-16). |
| VII. 조회 경로 모델의 역할 경계와 LLM 사용 경계 | LLM·OCR 사용 여부, 파서 글자 추출 | 통과 | 001은 LLM을 쓰지 않는다. 내용 확인기는 결정적이며 OCR을 하지 않는다(FR-017). 스키마 맞음 판정을 위해 시트 이름·지정 셀 값·정보 자료 제목 줄을 읽지만 저장·색인·응답하지 않고, 문서 전체 글자 추출·청킹은 하지 않는다. 문서 분석 Agent는 `003`. |

게이트 결과: 위반 없음. Complexity Tracking 기록 대상 없음. I의 조건("DR 승인 전 의존 Phase 금지")은 2026-11-10 전부 승인으로 해소되었다.

## 설계 개요

### 구성 요소

| 구성 요소 | 계층 | 책임 | DR |
|---|---|---|---|
| 등록 규칙(`RegistrationRules`) | domain | 스키마 ID·자료 ID 규칙, 거부 사유 목록(16개)과 코드, 구성 1차·2차 분류, `manifest_hash` 계산 | DR-08, DR-15, DR-16 |
| 내용 확인기 포트(`ContentInspector`)와 구현 | application 포트 / infrastructure 구현 | 실제 형식 판정, 선언 대조, 손상·암호·빈 파일, 텍스트 해석, PDF 글자 층, 포함 이미지 위치 | DR-03, DR-04, DR-14, DR-23 |
| 스키마 검증·사전(`SchemaValidator`, `SchemaDictionary`) | application | v1 스키마 구조 검증, 맞음 판정, 같은 내용 판정, 기본 스키마 멱등 등록 | DR-06, DR-07 |
| 등록 접근 정책(`RegistrationAccessPolicy`) | application | 쓰기 권한, 효력 있는 원본 읽기 권한, 스키마 조회 권한 판정 | DR-11 |
| 등록 서비스(`RegistrationService`) | application | 판정 순서 강제, 스키마 입력 표(FR-012), 동일 재등록, 원자적 저장 호출 | DR-15 |
| 되찾기 서비스(`OriginalRetrievalService`) | application | 기록·파일·현재본 조회, FR-043 같은 응답 | DR-11(a), DR-13 |
| 등록 관리 서비스(`RegistrationAdminService`) | application | source 생성, 권한 부여·회수, 표시 변경과 자동 회수, 완전 삭제, 삭제 기록 조회 | DR-10, DR-11, DR-12, DR-19 |
| 저장소 어댑터 | infrastructure/database | 새 테이블 ORM, 트랜잭션·advisory lock, 원본 바이트 저장 포트(`BlobStore`) 구현 | DR-01, DR-09 |
| 등록 HTTP 경로 | interfaces/api/routes | multipart 수신과 크기 계수, 오류 매핑, 응답 직렬화 | DR-05, DR-16 |
| 관리자 CLI | interfaces/cli | `admin` 하위 명령, `--actor` 기록, exit code | DR-10 |
| 마이그레이션 | migrations | 새 테이블·CHECK·트리거. 기존 테이블 불변 | — |

계층 규칙: application은 infrastructure와 `Settings`를 import하지 않는다(설계서 16절의 기존 위반을 새 코드에서 반복하지 않음). 조립은 `interfaces/api/runtime.py`와 CLI에서만 한다. 새 계층 규칙 테스트를 둔다(U-ARCH-01).

### 등록 처리 흐름

```text
POST /v1/registrations?source=&material_id=&schema_id=
 1 Bearer 인증(기존 ClientAccessService.authenticate)      → 401 invalid_token
 2 source 매개변수 존재                                   → 400 source_required
 3 registration_sources + 활성 write 권한                  → 403 write_access_denied (없음·omf·권한없음 동일)
 4 material_id 규칙[DR-08]                               → 400 invalid_material_id
 5 schema_id 규칙(있을 때, 빈 문자열 포함)                   → 400 invalid_schema_id
 6 본문 수신, 크기 계수[DR-02], multipart 해석[DR-05]               → 413 file_too_large / 해석 불가 400 invalid_request_composition
 7 구성 1차(파트 개수·이름, 내용 불필요)[DR-15]                    → 400 invalid_request_composition
 8 형식·내용: file 파트 순서대로 빈 파일·서명·선언 대조·손상·암호(순서는 research R-04 세부 목록) → 415 unsupported_file_format / 422 unreadable_content
   그다음 info: 0바이트·공백뿐                                     → 422 info_material_missing (FR-005 빈 파일의 예외)
              그 밖에 text·markdown이 아님                         → 415 invalid_info_material_format
 9 구성 2차(형식 판정 결과로 정보 자료 필요·표시, 대상 종류)          → 422 info_material_missing / 400 invalid_request_composition
10 텍스트·Markdown 단일 파일이면 스키마 지정 금지 → 기본 스키마        → 400 text_schema_not_allowed
11 FR-012 스키마 입력 표, 구조 검증, 예약 접두사, 충돌                → 422 schema_missing / 400 invalid_schema_id / 422 incomplete_schema / 409 schema_id_conflict
12 스키마 맞음 판정(대상 형식, 가리키는 구조)                        → 422 schema_mismatch
13 트랜잭션: (source, 자료 ID) advisory lock → 스키마 INSERT/재사용 → 현재본 manifest_hash 비교
   → 같음: 등록 기록 추가, 200 reused / 다름: revision·파일·바이트·묶음·관계·등록 기록, 현재본 갱신, 201 created
   → 내부 실패: 전부 되돌림, 503 registration_unavailable
```

구성 1차·2차의 세부 조건(표 A·B)과 예시별 기대 사유 코드(정보 자료 + 손상된 xlsx → `unreadable_content` 등)는 research R-04 표가 정본이며 계약 설명·U-COMP-01·U-COMP-02·U-ORDER-01·H-02 표본 기대값이 이를 따른다.

새 경로는 매개변수·multipart 필드를 문자열·문자열 목록으로만 선언해 프레임워크 선언 검증(422 `invalid_request`)이 1번보다 먼저 일어나지 않게 한다. 기존 `app.py`의 `RequestValidationError` 처리기와 `/v1/search`의 422 동작은 그대로다.

### 되찾기 처리 흐름

```text
GET /v1/revisions/{id}[/files/{n}], GET /v1/materials/current
 1 Bearer 인증 → 401
 1a /v1/materials/current만: source·material_id 없음·빈 문자열 → 422 invalid_request(핸들러가 인증 뒤 판정, 기존 오류 형식)
 1b file_index가 0~20 정수가 아님 → 404 revision_not_found (스키마 조회는 권한 판정 뒤 규칙 밖 ID → 404 schema_not_found)
 2 트랜잭션: 호출자 표시 행 FOR SHARE → revision·source 조회 → 효력 있는 원본 읽기 권한 판정
   없음/삭제됨/권한 없음/표시 external·미표시 → 404 (같은 본문·헤더)
 3 기록 또는 바이트 읽기 → 커밋 → 응답
```

## 설계서 정합

설계서 v0.3과 맞춘 경계와, 설계서가 정하지 않아 001이 보완하거나 다르게 판단한 곳이다.

| 설계서 항목 | 001의 처리 | 관계 |
|---|---|---|
| revision 용어(설계서 용어표·7.1 "원본 revision 집합") | 등록 시 발급하는 UUID revision[DR-09]. `006`이 색인 식별값의 원본 revision 집합에 넣는다. | 일치 |
| 위치 좌표(행 범위·페이지·시트·셀) | 001은 좌표를 만들지 않는다. 대신 revision 안 파일 번호 `file_index`를 둔다. | **보완**: 묶음 revision은 파일이 여러 개라 좌표가 "revision 안 어느 파일"까지 가리켜야 한다. 설계서와 `002` FR-004는 이를 명시하지 않았다. `006`·`002` plan에서 좌표에 파일 지정을 넣을지 정하도록 넘긴다. |
| 모듈 기술자 6종(5절)과 P5 | 내용 확인기는 6종에 속하지 않는다. 결과(글자 층, 포함 이미지 관계)는 청크·벡터를 바꾸지 않아 색인 식별값에 넣지 않는다. 대신 revision에 `inspector_version`을 기록한다(R-10). | **보완**: 설계서에 없는 등록 시점 구성 요소. `003` 파서가 같은 판정을 다시 하면 그 결과는 파서 기술자로 식별된다. |
| P2(좌표는 파서가 만든다) | 포함 이미지 관계 정보의 위치(페이지·시트)는 등록 기록이며 청크 좌표가 아니다. `006`은 이를 근거 좌표로 바로 쓰지 말고 파서 좌표와 대조해야 한다. | 경계 명시 |
| 스키마 ID = 불변 버전, 청킹 기술자 설정 해시에 포함(7.1) | 스키마 사전 append-only, ID 전역 유일, 예약 접두사 `default-`[DR-07]. | 일치 |
| 분할 규칙의 의미(6.3, O-1) | 001은 v1 분할 규칙의 존재·정형성·대상 형식·가리키는 구조만 확인한다[DR-06]. 의미와 실행은 `003`. | 일치. v1 어휘가 `003`과 어긋날 위험을 DR-06에 적었다. |
| 원본 보관 | 설계서는 원본 저장 방식을 정하지 않는다. 001이 DR-01로 정한다. | 범위 분담 |
| 기존 데이터 보존 불필요 | 001은 기존 행·테이블을 바꿀 필요가 없어 이 결정을 쓰지 않는다. | 일치(활용 안 함) |
| 13절 적중 판정 입력(source, 자료 ID, revision, 좌표) | 001이 앞의 세 값을 제공한다. 같은 내용이 여러 자료 ID에 있으면 각각 별도 revision으로 보존한다(spec Edge Case). | 일치 |
| O-13(평가 환경·운영 적용, 미승인) | 001은 관련 없다. | 해당 없음 |
| 6.1 파서 입력 "형식"(텍스트, PDF, Excel, Markdown 구분) | 형식 판정 결과에 `markdown`을 두어 `text`와 구분한다. 내용으로 UTF-8 텍스트임을 판정한 뒤 `.md`·`text/markdown`이면 `markdown`, `.txt`·`text/plain`이면 `text`. 판정 결과를 revision 파일 기록과 `manifest_hash`에 넣어 불변으로 남긴다[DR-23]. | 일치(6.1의 구분을 등록 시점에 고정). 바이트만으로 둘을 구별할 수 없어 하위 형식은 선언에 의존한다는 점을 DR-23에 적었다. |

설계서와 **충돌**하는 판단은 없다. 위 "보완" 두 건(파일 번호, 확인기 버전)과 Markdown 구분은 승인 시 `003`·`006`·`002` plan의 입력이 된다.

## 응답 필드 내용 노출 점검

FR-034·FR-031·H-08과 맞추려고 계약의 모든 응답 필드를 점검했다(research R-11). "원본 내용"은 파일 안에서 읽은 값(시트 이름, 셀 값, 문서 제목, 글자, 정보 자료 문장)과 파일 바이트를 뜻한다.

| 응답 | 필요한 권한 | 필드 | 원본 내용 포함 | 처리 |
|---|---|---|---|---|
| 등록 결과 `RegistrationResult` | 쓰기 | `revision_id`, `source`, `material_id`(호출자 입력), `sequence`, `schema_id`, `outcome` | 없음 | — |
| 〃 `files[]` | 쓰기 | `file_index`, `role`, `detected_format`, `pdf_text_layer`, `byte_size`, `content_hash` | 없음(구조 값·해시. 해시는 FR-022 요구) | 제출 파일 이름도 넣지 않음 |
| 〃 `embedded_image_relations[]` | 쓰기 | `location_kind`, `location`, `relation_kind`, `image_count` | **수정 전 있음**: Excel 시트 이름 | `location`을 정수(페이지 번호, 시트 순번)로 바꾸고 시트 이름은 저장하지도 않음(data-model 3.11) |
| 〃 `bundle_links[]` | 쓰기 | 파일 번호, `human_authored_declared` | 없음 | — |
| 거부 `RegistrationError` | 없음(인증 실패 포함) | `code`, `message`, `detail` | `schema_mismatch`에서 시트 이름·머리글 값을 낼 위험 | `detail`은 길이·개수·한도·형식 값과 스키마 쪽 항목 경로(`schema_mismatch_part`, 고정 키와 배열 위치 번호만, 사용자 정의 이름 없음)만. 파일 값·스키마 값·받은 문자열은 넣지 않음(U-RULE-04, C-API-10) |
| 스키마 조회 | 아무 source 쓰기(외부 LLM 사용 표시가 `외부`·없음인 호출자 포함) | 스키마 파일 바이트 | 원본 파일에서 읽은 값은 아님. 다만 등록자가 적은 시트 이름·머리글 값·엔티티 이름을 그대로 담아 원본 구조를 반영할 수 있음 | FR-016·FR-031이 요구하는 동작이라 그대로 둠(research R-11 알려진 예외). C-API-10의 표식 검사 대상에서 스키마 작성자가 적은 값은 제외 |
| 현재본 `CurrentRevision` | 원본 읽기 | `state`, `revision_id`, `sequence` | 없음 | — |
| 등록 기록 `RegistrationRecord` | 원본 읽기 | 위 필드 + `created_by`(client 이름), `inspector_version`, `registrations[].submitted_filename` | 제출 파일 이름(등록자 입력 메타데이터) | 원본 읽기 권한 경로에서만. 삭제 뒤 제거 |
| 원본 파일 | 원본 읽기 | 바이트, `Content-Disposition` 파일 이름, `X-Content-SHA256` | 있음(목적 자체) | 원본 읽기 권한 경로에서만 |
| 관리자 CLI 출력 | 서버 셸 | `revision show`, `deletion-record list`, `permission list` | 없음(파일 이름·시트 이름 미출력, 삭제 기록 해시는 spec 확정) | C-CLI-05 |
| 거부 운영 로그 | — | `request_id`, `key_id`, 사유 코드 | 없음 | R-08 |

결과: 수정 뒤 원본 읽기 권한이 필요 없는 응답에는 원본 파일에서 읽은 값이 없다. 예외로 스키마 조회는 등록자가 적은 스키마 값(원본 구조를 반영할 수 있음)을 외부 표시 쓰기 권한자에게도 준다(FR-016). 남은 노출은 원본 읽기 권한 경로의 제출 파일 이름과 파일 바이트이며 FR-024·FR-023이 요구하는 것이다.

## MVP 검색 기능 영향

- **001은 MVP 검색을 깨지 않는다.** 이는 FR-035·FR-036·SC-007의 요구이며 사용자 결정 "기존 데이터 보존 불필요"와 무관하게 지킨다.
  - 마이그레이션은 새 테이블만 추가하고 기존 테이블의 컬럼·제약·행을 바꾸지 않는다[DR-22]. `factory` source 행 추가는 기존 검색 SQL의 범위(`omf` 고정, grant·활성 run 조인)에 들어가지 않는다.
  - `POST /v1/search`, `/health/*`, 검색 서비스·정책·색인 코드와 `index` 명령을 고치지 않는다. 등록 경로는 임베딩 모델 준비와 독립 조립한다[DR-21].
  - 공개 경로: 등록 경로는 `create_app`에 등록 서비스가 주입될 때만 등록한다(조건부 등록, DR-21). 운영 앱은 검색 정책 resolve 실패 분기를 포함해 항상 기존 3경로에 5경로가 추가되고, 등록 서비스 없이 만든 앱은 지금과 같은 3경로다. 그래서 기존 경로 수 계약 테스트(`test_only_three_mvp_routes_are_public`)를 고치지 않는다.
  - 검증: 기존 `tests/contract/api/test_search_api.py`와 검색·색인 단위·통합 테스트를 수정 없이 통과(C-API-06), 등록 전후 고정 질의 결과 동일(H-07).
- **처음 깨지는 시점**: `006`의 새 색인 식별값 형식 적용(설계서 D-01, 재색인) 때다. 001 범위가 아니다.
- **되돌리기**: 001 마이그레이션 downgrade는 새 테이블만 지운다(등록 데이터는 사라진다). MVP 테이블에는 영향이 없다. 공유 서버에는 001을 배포하지 않으므로 운영 되돌리기는 해당 없다.

## 미해결 사항 처리

| 출처 | 항목 | 처리 |
|---|---|---|
| checklist, Assumptions | FR-006 크기 한도 값 | DR-02 |
| checklist, Assumptions | FR-037 자료 ID 규칙 | DR-08 |
| checklist, Assumptions | FR-010 정형 판정 기준, 스키마 파일 형식 | DR-06 |
| checklist, Assumptions | FR-020 관계 정보 단위 | DR-14 |
| Assumptions | 허용 확장자·이미지 세부 형식·매크로 Excel | DR-03 |
| Assumptions | 기본 스키마 내용·갱신 | DR-07 |
| Assumptions | 묶음 세부 제한 | DR-15 |
| Assumptions, 용어 | 서버 관리 경로·관리자 경로 형태 | DR-10 |
| Assumptions 미결 | 표시 변경 직후 진행 중 되찾기 | DR-11(a) |
| Assumptions 미결 | 표시 복귀 시 기존 권한 효력 | DR-11(b) |
| Assumptions 미결 | 효력 잃은 권한 기록 처리 | DR-11(c) |
| Assumptions 미결 | 표시 변경 기록 | DR-11(d) |
| Assumptions 이월 | M1 삭제 시 내용 해시 제거 | DR-12 |
| Assumptions 이월 | M2 삭제 뒤 남는 항목의 관리자 확인 경로 | DR-12, `admin revision show` |
| Assumptions 이월 | M3 US2 시나리오 6 문구 | DR-13(spec 수정은 별도 작업) |
| checklist | FR-032 저장 위치 점검 | quickstart 4절 |
| checklist | 성공 기준 표본 규모 | DR-18 |
| Assumptions | 등록 처리 시간·동시 등록 수 | 정하지 않음(DR-17, 측정만) |
| Edge Case | 삭제의 색인 전파 | 001 범위 밖(`006`) |
| Out of Scope | `005`의 표시 사용 | 001 범위 밖. 001은 표시를 원본 읽기에만 적용 |
| 운영 | 저장 용량·보존 기간·백업·물리 소거·임시 파일·로그 보관·게이트웨이·직접 접속 차단·배포 | DR-17(결정하지 않음) |
| 설계서 6.1 | 텍스트·Markdown 구분 | DR-23 |
| 기존 CLI | `client create`의 `omf` 행 의존(수동 점검 선행 조건) | DR-24 |
| 이전 R-01~R-03 | 계약 형식, 서비스 경계, 권한 저장 구조 | DR-20, DR-21, DR-22 |

## FR 추적표

"계약"은 [contracts/registration-api.openapi.yaml](./contracts/registration-api.openapi.yaml)(API)과 [contracts/admin-cli.md](./contracts/admin-cli.md)(CLI), "모델"은 [data-model.md](./data-model.md) 절 번호, "테스트"는 아래 `테스트 전략`의 ID다.

| FR | 데이터 모델 | 계약 | 구성 요소 | 테스트 | Phase |
|---|---|---|---|---|---|
| FR-001 | 3.6~3.13 | `POST /v1/registrations` | 등록 HTTP 경로, 등록 서비스 | C-API-02, H-01 | P7 |
| FR-002 | 3.1 | `source` 매개변수, `source_required`, `write_access_denied` | 등록 접근 정책 | U-SVC-01, C-API-04 | P1, P7 |
| FR-003 | 3.8 `detected_format` | `unsupported_file_format`, `FileRecord.detected_format` | 내용 확인기 | U-INSP-01 | P4a |
| FR-004 | 3.8 | `unsupported_file_format` | 내용 확인기(선언 대조) | U-INSP-01 | P4a |
| FR-005 | — | `unreadable_content` | 내용 확인기 | U-INSP-02 | P4a |
| FR-006 | 3.8 `byte_size` | `file_too_large` | 등록 HTTP 경로(크기 계수) | U-HTTP-01, C-API-02 | P7 |
| FR-007 | 6절 원자성 | 모든 거부 응답, `registration_unavailable` | 등록 서비스, 저장소 트랜잭션 | I-REG-01, H-02 | P6 |
| FR-008 | — | `x-rejection-reasons` 16개 코드, `detail` | 등록 규칙(사유 목록), HTTP 오류 매핑 | U-RULE-04, C-API-02, H-02 | P1, P7 |
| FR-009 | 3.7 `schema_id` NOT NULL | `schema_missing` | 등록 서비스 | U-RULE-03 | P5, P6 |
| FR-010 | 3.5 `format_version`·`target_format` | `incomplete_schema`, `detail.missing_parts` | 스키마 검증 | U-SCH-01 | P5 |
| FR-011 | 3.5 `schema_id` CHECK | `invalid_schema_id` | 등록 규칙 | U-RULE-01, I-DB-03 | P1 |
| FR-012 | 3.5 | `schema_id` 매개변수, `schema` 파트 | 등록 서비스(입력 표) | U-RULE-03 | P1, P6 |
| FR-013 | 3.5 `target_format` | `schema_mismatch` | 스키마 검증(맞음 판정) | U-SCH-02 | P5 |
| FR-014 | 3.5 `is_platform_default` | `text_schema_not_allowed` | 등록 서비스, 기본 스키마 | U-RULE-03, U-SCH-04 | P5, P6 |
| FR-015 | 3.5 append-only | `schema_id_conflict` | 스키마 사전 | U-SCH-03, I-DB-02 | P2b, P5 |
| FR-016 | 3.5 | `GET /v1/schemas/{schema_id}` | 스키마 사전, 등록 접근 정책 | U-ACC-02, C-API-08 | P8 |
| FR-017 | 3.11(그림 내용 없음) | — | 내용 확인기(OCR·추출 없음) | U-INSP-06 | P4b |
| FR-018 | 3.8 `role=info`, 3.10 | `info_material_missing`, `invalid_info_material_format` | 등록 규칙(구성 2차), 등록 서비스 | U-COMP-02, U-ORDER-01 | P1, P6 |
| FR-019 | 3.8 `pdf_text_layer=none` | `info_material_missing` | 내용 확인기, 등록 서비스 | U-INSP-04, U-COMP-02 | P4b, P6 |
| FR-020 | 3.11 | `embedded_image_relations` 응답 | 내용 확인기 | U-INSP-05, H-05 | P4b, P6 |
| FR-021 | 3.10 `human_authored_declared` | `info_human_authored`, `bundle_links` | 등록 서비스 | U-COMP-02, H-05 | P6 |
| FR-022 | 3.7 불변 트리거 | `RegistrationResult` | 등록 서비스 | U-SVC-02, I-DB-02 | P2b, P6 |
| FR-023 | 3.9 | `GET /v1/revisions/{id}/files/{n}` | 되찾기 서비스, 원본 저장 포트 | I-REG-03, H-01 | P8 |
| FR-024 | 3.12, 3.13 | `GET /v1/revisions/{id}` `RegistrationRecord` | 되찾기 서비스 | C-API-02, H-01 | P6, P8 |
| FR-025 | 3.7~3.13 트리거 | — | 저장소 | I-REG-03, H-01 | P2b, P6 |
| FR-026 | 3.14, 4.1 | `admin revision delete`, `admin deletion-record list` | 등록 관리 서비스 | I-REG-05, C-CLI-04, H-06 | P9 |
| FR-027 | 기존 `api_clients` | `bearerAuth`, `invalid_token` | 기존 인증 재사용 | C-API-02 | P7, P8 |
| FR-028 | 3.2(검색 grant와 분리) | `admin permission` | 등록 접근 정책, 관리 서비스 | U-ACC-01, C-CLI-02 | P2a, P3 |
| FR-029 | 3.2 | `write_access_denied` | 등록 접근 정책 | U-ACC-01, H-03 | P3, P7 |
| FR-030 | 3.2 회수 컬럼 | `admin permission revoke` | 관리 서비스 | U-ADM-02, H-03 | P3 |
| FR-031 | 3.2, 3.3 | 되찾기 경로 404, `schema_access_denied` | 등록 접근 정책 | U-ACC-01·02, C-API-03 | P8 |
| FR-032 | 3.9(PostgreSQL) | — | 원본 저장 포트[DR-01] | quickstart 4절 점검 | P10 |
| FR-033 | — | — | 등록 서비스(외부 호출 없음) | U-SVC-03, H-08 | P6, P10 |
| FR-034 | — | 오류 `message`·`detail` 규칙, CLI 표준 오류 규칙 | HTTP 오류 매핑, 로깅, 응답 비노출(R-11) | U-RULE-04, C-API-10, H-08 | P7, P10 |
| FR-035 | 기존 테이블 불변 | 기존 검색 계약 불변 | 독립·조건부 조립[DR-21] | C-API-06, C-API-11, H-07 | P7, P10 |
| FR-036 | 기존 테이블 불변 | — | — | I-REG-06, H-07 | P2a, P2b, P10 |
| FR-037 | 3.6 `material_key` | `material_id` 매개변수, `invalid_material_id` | 등록 규칙 | U-RULE-02 | P1 |
| FR-038 | 3.6, 3.7 `sequence`·`manifest_hash`, 4.2 | 200 `reused` / 201 `created` | 등록 서비스 | U-HASH-01, U-SVC-02, H-01 | P1, P6 |
| FR-039 | 3.6 `current_revision_id` | `GET /v1/materials/current` | 되찾기 서비스 | U-RET-01, H-01 | P8 |
| FR-040 | 3.1 | `admin source create`, `write_access_denied` | 관리 서비스 | C-CLI-01, C-API-04 | P3 |
| FR-041 | 3.2 | `admin permission grant/revoke` | 관리 서비스 | U-ADM-01·02, C-CLI-02 | P3 |
| FR-042 | — | API 설명의 판정 순서, 선언 검증 없는 매개변수 | 등록 서비스(순서 강제), HTTP 경로 | U-SVC-01, U-ORDER-01, C-API-09 | P6, P7 |
| FR-043 | — | `revision_not_found` 동일 응답 | 되찾기 서비스 | C-API-03, H-03 | P8 |
| FR-044 | 3.7 `composition`, 3.8 `role` | `invalid_request_composition` | 등록 규칙(구성 1차·2차) | U-COMP-01, U-COMP-02 | P1 |
| FR-045 | 3.3, 3.4 | `admin llm-usage set/unset`, 호출자 변경 경로 없음 | 관리 서비스 | U-ADM-03, C-API-07 | P2a, P3 |
| FR-046 | 3.2 INSERT 조건 | `llm_usage_not_internal` | 관리 서비스 | U-ADM-01, I-DB-04, H-03 | P3 |
| FR-047 | 3.2 `llm_usage_changed`, 4.3 | 되찾기 404 | 관리 서비스(자동 회수), 등록 접근 정책(재확인) | U-ADM-03, I-REG-04, H-03 | P3, P8 |

SC 대응: SC-001·SC-005·SC-010 → H-01, SC-002·SC-009 → H-02, SC-003·SC-012·SC-013 → H-03, SC-004 → H-04, SC-006 → H-05, SC-011 → H-06, SC-007 → H-07, SC-008 → H-08.

## 테스트 전략

### 원칙

- TDD(헌법 IV): 각 Phase는 아래 사례로 테스트를 먼저 쓰고, 구현 부재로 인한 assertion 실패(RED)를 확인한 뒤 최소 구현(GREEN), 정리(REFACTOR) 순서로 진행한다. 실행 Agent는 이 설계를 그대로 쓴다.
- 표본 파일은 테스트 안에서 합성한다(표준 라이브러리로 최소 PDF·xlsx·PNG·JPEG 바이트 생성). 대외비 자료와 네트워크를 쓰지 않는다.
- 비밀값·원본 노출 검사는 알려진 표식 문자열을 원본·토큰에 심고 응답·로그·예외 문자열에서 찾는 방식으로 한다.
- 계층: 단위(U, DB·네트워크 없음) → 계약(C, 가짜 서비스 주입 앱·CLI 러너) → 통합(I, PostgreSQL) → 인수 하네스(H, 실제 앱+PostgreSQL, SC 판정).

### 단위 테스트(U)

| ID | 관찰할 동작 | 정상 사례 → 예상 | 경계 사례 → 예상 | 실패 사례 → 예상 |
|---|---|---|---|---|
| U-RULE-01 | 스키마 ID 규칙(FR-011) | `a`, `Ab_9-x` → 허용. `Abc`와 `abc`는 다른 값 | 1자·20자 → 허용, 21자 → `invalid_schema_id`(`detail.limit=20`, `actual_length=21`) | `""` → `invalid_schema_id`, `a.b`·`a b`·`가` → `invalid_schema_id` |
| U-RULE-02 | 자료 ID 규칙(FR-037)[DR-08] | `manual/ops-01`, `PO_2026.11` → 허용 | 1자·200자 → 허용, 201자 → `invalid_material_id` | 없음·`""`·`/a`·`a/`·`a//b`·`a/../b`·`./a`·`a b`·`가` → `invalid_material_id` |
| U-RULE-03 | 스키마 입력 표(FR-012, FR-014)를 가짜 사전으로 | 표 7행 각각 → 표의 결과(재사용, 새 등록, 충돌, 없음, ID 규칙 위반). 텍스트+둘 다 없음 → 기본 스키마 | 텍스트+같은 내용 기존 ID → `text_schema_not_allowed`(재사용하지 않음) | 텍스트+`schema_id` → `text_schema_not_allowed`, 텍스트+`schema` 파일 → 같음 |
| U-RULE-04 | 거부 사유 목록(FR-008, FR-034, DR-16) | 16개 코드가 서로 다르고 계약의 HTTP 상태와 같다 | 메시지 템플릿이 길이·한도만 채운다 | 템플릿에 받은 값(파일 이름·ID·내용) 삽입 자리가 있으면 실패 |
| U-COMP-01 | 구성 1차(FR-044, research R-04 표 A)[DR-15] | 파일 1개 → 통과. `info`+파일 3개 → 통과 | `info`+파일 20개 → 통과, 21개 → `invalid_request_composition`. 파일 이름 255자 → 통과, 256자 → `invalid_request_composition` | 파일 0개, `info` 없이 파일 2개, `info` 2개, `schema` 2개, 알 수 없는 파트, `info` 없이 `info_human_authored`만 → 모두 `invalid_request_composition`. 1차 위반이면 형식 확인을 호출하지 않음(가짜 확인기 호출 0회) |
| U-COMP-02 | 구성 2차(FR-018~FR-021, research R-04 표 B)를 형식 판정 결과 값으로 | 이미지 3개+`info`+표시 → 이미지 묶음. 그림 포함 xlsx 1개+`info`+표시 → 그림 포함 파일 묶음. 일부 스캔 PDF+`info`+표시 → 그림 포함 파일 묶음. 그림 포함 xlsx 단독 → 단일 | 표시 값 `True`·`1` → `info_material_missing`(정확히 `true`만) | R-04 예시 표의 ⑨ 행 전부가 표의 기대 코드와 같다: 이미지 단독·전체 스캔 PDF 단독 → `info_material_missing`, 표시 없음+그림 없는 xlsx → `info_material_missing`(B3이 B7보다 먼저), 그림 없는 xlsx·텍스트 대상·그림 없는 텍스트 PDF·혼합 → `invalid_request_composition` |
| U-ORDER-01 | ⑧ 단계 파트 순서(research R-04) | — | `file` 파트 2개(묶음) 중 두 번째만 손상 → `unreadable_content`, `detail.file_position=1` | `info`+손상 xlsx → `unreadable_content`. PDF `info`+손상 PNG → `unreadable_content`(`file` 먼저). PDF `info`+정상 PNG → `invalid_info_material_format`. 0바이트 `info`+이미지 → `info_material_missing`. 공백·줄바꿈뿐인 `info`(BOM 포함)+이미지 → `info_material_missing`. 0바이트 `info`+손상 PNG → `unreadable_content`(`file` 먼저). 0바이트 `info`·표시 없음+그림 없는 xlsx → `info_material_missing`(⑧-i가 ⑨보다 먼저). `info`+이름만 `.png`인 PDF → `unsupported_file_format` |
| U-HASH-01 | `manifest_hash`와 파일 번호(R-06, DR-15 추가 결정) | 같은 파일·형식 판정·스키마 → 같은 값 | 파일 이름만 다름 → 같은 값. 묶음 대상 순서만 다름 → 같은 값이고 각 파일이 받는 `file_index`도 같다(제출 순서와 무관한 결정적 정렬) | 스키마 ID만 다름 → 다른 값. 정보 자료 1바이트 다름 → 다른 값. 같은 바이트의 `text`와 `markdown` 판정 → 다른 값[DR-23] |
| U-INSP-01 | 형식 판정·선언 대조(FR-003·FR-004)[DR-03·DR-23] | 합성 xlsx·PDF·PNG·JPEG → 각 형식. 같은 UTF-8 바이트를 `a.txt`로 → `text`, `a.md`로 → `markdown` | Content-Type `application/octet-stream` + 맞는 확장자 → 허용. ZIP으로 해석되고 `[Content_Types].xml`에 workbook 주 파트 선언은 있으나 `xl/workbook.xml`이 없는 `.xlsx` → `unsupported_file_format`(유효 OOXML 아님, research DR-04 xlsx ZIP 경계) | PNG를 `.xlsx`로 → `unsupported_file_format`. `a.md` + `text/plain`처럼 확장자와 Content-Type의 텍스트 하위 형식이 다름 → `unsupported_file_format`. `.xlsm`(매크로 content type)·`.xls`(OLE)·`.csv`·GIF → `unsupported_file_format`. ZIP(`.zip`)·7z·tar.gz와 이름만 `.xlsx`인 일반 ZIP(OOXML workbook 파트 없음) → `unsupported_file_format`(DR-03 추가 결정). 알 수 없는 바이트 → 같음 |
| U-INSP-02 | 내용 확인 불가(FR-005)[DR-04, research R-04 ⑧ 확인 순서] | 정상 표본 → 통과. 기본 설정값 연결 확인: 상한을 주입하지 않은 확인기 설정이 총량 536,870,912바이트(512 MiB)·비율 100·항목 10,000·페이지 2,000·시간 30초다(실제 대용량 파일은 만들지 않음) | 작은 상한을 주입한 설정(예: 총량 4,096바이트, 비율 100, 항목 5, 페이지 3, 시간 30초)으로 각 상한을 확인. 같으면 통과·넘으면 `unreadable_content`: 해제 총량 4,096 / 4,097바이트, 항목별 비율은 두 층으로 정확히 확인: (a) 비율 판정 함수에 정수 입력 (해제 100·압축 1) → 통과, (101, 1) → 거부, (0, 0) → 통과, (1, 0) → 거부. (b) 실제 ZIP fixture: 0바이트 반복 데이터를 deflate로 압축해 항목을 만들고, 해제 길이 d를 늘려 가며 실제 압축 크기 c로 나누어떨어지는 d를 고른 뒤 비율 상한 r = d ÷ c(정수)를 주입 → 통과, r − 1을 주입 → `unreadable_content`(c는 fixture에서 잰 실제 압축 바이트라 경계가 정확). 항목 5 / 6개, PDF 3 / 4쪽. 압축 0바이트·해제 0바이트 항목(빈 파트·디렉터리) → 통과. 확인 시간은 확인기에 가짜 `clock`을 주입해 경과 30.0초 → 통과, 30.0초 초과 → `unreadable_content`(xlsx 항목 사이, PDF 페이지 사이 각각). 선언 `.xlsx`인 OOXML 아닌 ZIP + 항목 6개(상한 5) → `unreadable_content`(항목 수가 패키지 판정보다 먼저) | `file` 파트의 0바이트, IEND 없는 PNG, EOI 없는 JPEG(둘 다 서명 판정은 통과하고 7단계 손상 확인에서 거부), `/Encrypt` PDF, CFB 암호 xlsx, ZIP 서명(`PK\x03\x04`)으로 시작하지만 중앙 디렉터리가 잘려 ZIP으로 해석할 수 없는 `.xlsx`, 압축 0바이트라고 적혔지만 실제로 바이트가 풀리는 항목, `xlsx`로 판정된 뒤 시트 XML이 깨진 파일, 페이지 트리 없는 PDF → `unreadable_content`. 예외: `info` 파트의 0바이트는 `unreadable_content`가 아니라 `info_material_missing`(U-ORDER-01, research R-04 ⑧-i) |
| U-INSP-03 | 텍스트 해석(US4-2·3) | BOM 있는 UTF-8, CRLF·LF 혼용 → 허용, 확인기가 바이트를 바꾸지 않음 | 1바이트 텍스트 → 허용 | 잘못된 UTF-8 시퀀스, NUL 포함 → `unsupported_file_format` |
| U-INSP-04 | PDF 글자 층(FR-019·FR-020) | 모든 페이지 글자 → `full` | 1페이지 PDF 글자 없음 → `none`. 3페이지 중 1페이지만 없음 → `partial` + `page_without_text_layer` 1행 | 글자 연산자만 있고 폰트 없는 페이지 → 판정 규칙대로 `none`(규칙을 테스트로 고정) |
| U-INSP-05 | 포함 이미지 관계(FR-020)[DR-14] | `xl/media` + 둘째 시트 drawing → `xlsx_sheet` `location=1`(시트 순번) 1행 `image_count=1`, 결과 어디에도 시트 이름 문자열 없음. PDF 3쪽 이미지 XObject 2개 → `pdf_page` `location=3`, `image_count=2` | 그림 없는 파일 → 0행 | drawing 관계가 끊긴 xlsx → `unreadable_content` |
| U-INSP-06 | 실행·OCR 없음(FR-017, Edge Case) | `/OpenAction /JavaScript` PDF → 허용, 확인 중 subprocess·socket 호출 0회(monkeypatch로 호출 시 실패) | 글자가 그려진 PNG → 확인 결과에 글자 정보 필드 없음 | 확인기가 외부 프로그램을 부르면 테스트 실패 |
| U-SCH-01 | v1 스키마 구조(FR-010)[DR-06] | 완전한 xlsx·pdf·text·image_bundle 스키마 → 유효. `mapping: "none"` → 유효 | `concept`가 공백 1자 → `incomplete_schema` | `concept`·`split_rule`·`mapping` 각각 누락 → `incomplete_schema`(`missing_parts`). 모르는 최상위 키, 목록 밖 `kind`, `params` 추가 키, JSON 아님, UTF-8 아님 → `incomplete_schema` |
| U-SCH-02 | 맞음 판정(FR-013) | xlsx 스키마(시트 `발주`, 머리글 B1=`품번`) + 맞는 xlsx → 통과 | 시트 이름 대소문자만 다름 → 불일치(정확 비교) | pdf 파일에 xlsx 스키마 → `schema_mismatch`(`expected_target_format`). 시트 없음·머리글 다름 → `schema_mismatch`. image_bundle 스키마 제목이 정보 자료에 없음 → `schema_mismatch` |
| U-SCH-03 | 같은 내용·예약 접두사(FR-015, DR-07) | 같은 바이트 → 재사용 | 끝 줄바꿈 하나 차이 → `schema_id_conflict` | 사용자 `default-x` 새 등록 → `schema_id_conflict` |
| U-SCH-04 | 기본 스키마 상수(FR-014, DR-07) | 자기 검증기로 유효, ID 20자 이하, `target_format=text` | 사전에 같은 바이트 존재 → 멱등 | 사전에 같은 ID·다른 바이트 → 시작 실패 오류 |
| U-ACC-01 | 원본 읽기 효력(FR-028·FR-031·FR-047) | 권한+`internal` → 허용 | 권한+표시 행 없음 → 거부 | 권한+`external`, 검색 권한만, 쓰기 권한만, 다른 source 권한 → 모두 같은 "없음" 결과 |
| U-ACC-02 | 스키마 조회 권한(FR-016·FR-031) | 아무 source 활성 쓰기 → 허용 | 쓰기 회수 직후 → `schema_access_denied` | 원본 읽기만·검색만 → `schema_access_denied` |
| U-SVC-01 | 판정 순서(FR-042, US2-12) | 권한 있음+정상 → 성공 | 권한 없음+잘못된 형식 → `write_access_denied` | 권한 없음+기존 ID 다른 내용 → `write_access_denied`(충돌 아님). source 없음 → `source_required`(인증 뒤) |
| U-SVC-02 | 동일 재등록·현재본(FR-038)을 가짜 저장소로 | 첫 등록 → created, seq 1 | 현재본과 같음 → reused, 등록 기록 +1, revision 수 불변 | A→B 뒤 A 내용 → created seq 3. 현재본 삭제 뒤 같은 내용 → created. 모두 삭제 뒤 → created |
| U-SVC-03 | 외부 전송 없음(FR-033) | 등록 성공 | — | 등록 중 socket 연결 시도가 있으면 실패 |
| U-RET-01 | 현재본 조회(FR-039) | 현재본 있음 → `current` | 현재본 삭제 → `no_current_revision`, 직전으로 안 올라감 | 권한 없음·없는 자료 → 같은 `material_not_found` |
| U-ADM-01 | 원본 읽기 부여(FR-046) | `internal` → 활성 권한 | 같은 부여 반복 → `changed:false` | `external`·표시 없음 → `llm_usage_not_internal`, 권한·표시 불변 |
| U-ADM-02 | 회수(FR-030) | 활성 쓰기 회수 → 이후 쓰기 판정 거부 | 활성 권한 없음 회수 → `changed:false` | 회수가 원본 읽기·검색 권한과 revision을 바꾸면 실패 |
| U-ADM-03 | 표시 변경(FR-045·FR-047, DR-11) | `unset`→`internal` 기록 1건 | `internal`→`internal` → 기록 없음·변경 없음 | `internal`→`external`·`unset` → 활성 원본 읽기 전부 `llm_usage_changed`. 다시 `internal` → 복원 없음 |
| U-HTTP-01 | 크기 계수(FR-006)[DR-02] | 상한과 같은 크기 → 통과 | 상한+1바이트 → `file_too_large`, 상한 초과 즉시 수신 중단 | 요청 전체 상한 초과 → `file_too_large` |
| U-MIG-01 | P2a 마이그레이션 정적 검사(RED, 컨테이너 없음). 기존 `tests/unit/search/test_policy_migration.py`처럼 파일을 모듈로 읽고 `alembic.op`를 기록용 가짜로 바꿔 `upgrade()`·`downgrade()`를 호출 | `revision == "0004_registration_access"`, `down_revision == "0003_search_policy_manifest"`. upgrade가 data-model 3.1~3.4 테이블 4개와 CHECK·부분 유일·트리거 SQL을 만든다 | downgrade가 정확히 그 4개만 지운다 | upgrade·downgrade가 기존 테이블(`api_clients`, `client_source_grants`, `source_profiles` 등)에 alter·drop·UPDATE/DELETE를 하면 실패. 파일이 없으면 assertion 실패(RED) |
| U-MIG-02 | P2b 마이그레이션 정적 검사(RED, 컨테이너 없음) | `revision == "0005_material_registration"`, `down_revision == "0004_registration_access"`. 3.5~3.14 테이블 10개, `detected_format` CHECK에 `markdown` 포함, `location` 정수형, 불변 트리거 SQL 존재 | downgrade가 그 10개만 지운다 | 기존 테이블 변경, 시트 이름 컬럼 존재, 앱 코드 import가 있으면 실패 |
| U-ARCH-01 | 계층 규칙 | application 등록 모듈이 infrastructure·`Settings`를 import하지 않음 | — | 위반 import가 있으면 실패 |

### 계약 테스트(C)

| ID | 관찰할 동작 | 예상 결과 |
|---|---|---|
| C-API-01 | 앱 내부 OpenAPI와 계약 파일 대조[DR-20] | 5개 경로·메서드, 매개변수 이름·위치·타입(모두 문자열, 선언 제약 없음), 응답 코드, 응답 스키마 필드 집합이 같다(설명 문장 제외). 요청 측 pattern·길이·개수·enum·`required`(경로 매개변수 제외)·`additionalProperties`는 계약에도 앱에도 선언이 없어야 하며(있으면 실패), 응답 스키마의 `additionalProperties: false`는 대조 대상 규칙 동작은 C-API-02·09로 확인. 공개 `/openapi.json`은 계속 404 |
| C-API-02 | 16개 거부 사유와 성공 응답 형식 | 사유별 HTTP 상태·`code`가 `x-rejection-reasons`와 같다. ZIP 단일 등록 → 415 `unsupported_file_format`. 본문 키는 `{request_id, code, message}`(+`detail`). 401에 `WWW-Authenticate: Bearer`. 201/200 본문이 `RegistrationResult`와 같다 |
| C-API-03 | FR-043 구별 불가 | 없는 revision, 형식 아닌 id, 삭제된 revision, 검색 권한만, 쓰기 권한만, 표시 `external`, 표시 없음 → 상태·`code`·`message`·헤더 집합이 모두 같다(`request_id` 제외) |
| C-API-04 | source 구별 불가(FR-002, US2-5, US2-16) | 없는 source, `omf`, 권한 없는 `factory` → 같은 403 응답 |
| C-API-05 | 원본 응답 헤더(R-07) | `application/octet-stream`, `nosniff`, `X-Content-SHA256`. 파일 이름의 `../`, 줄바꿈, 따옴표, 한글이 RFC 5987로 인코딩되고 헤더 주입이 없다 |
| C-API-06 | 기존 검색 계약 불변(FR-035)[DR-21] | 등록 서비스를 주입하지 않은 `create_app`은 기존 3경로 그대로(기존 `test_only_three_mvp_routes_are_public` 포함 `test_search_api.py`·`test_web_delivery.py`가 수정 없이 통과). 등록 서비스를 주입하면 5경로가 추가될 뿐 기존 3경로의 응답은 같다. `/health/ready`가 등록 상태와 무관 |
| C-API-11 | 등록 서비스 주입 시 경로 집합 고정[DR-21] | 검색·등록 서비스를 모두 주입한 `create_app`의 (경로, 메서드) 집합이 정확히 기존 3개 + `POST /v1/registrations`, `GET /v1/revisions/{revision_id}`, `GET /v1/revisions/{revision_id}/files/{file_index}`, `GET /v1/materials/current`, `GET /v1/schemas/{schema_id}` 8개다. 운영 조립(`runtime.py`)이 정상 분기와 검색 정책 resolve 실패 분기(`_UnavailableSearchService`를 쓰는 89~96행) **둘 다**에서 등록 서비스를 주입해 8경로가 되는지 확인(정책 실패 분기에서도 등록 경로가 503이 아니라 동작) |
| C-API-07 | 호출자 표시 변경 경로 없음(FR-045, US2-22) | `/v1/clients/me/llm-usage` 등 후보 경로 PUT·POST·PATCH → 404 또는 405, 표시 불변 |
| C-API-09 | 선언 검증 미발동과 판정 순서(FR-042, research R-04) | 토큰 없이 `material_id`·`schema_id`가 규칙 밖이고 본문이 깨진 등록 요청 → 401(422 아님). 쓰기 권한 없는 토큰 + 같은 요청 → 403 `write_access_denied`. `/v1/materials/current`: 토큰 없음+매개변수 없음 → 401, 토큰 있음+매개변수 없음 → 422 `invalid_request`, 매개변수 있음+권한 없음 → 404 `material_not_found`. 숫자 아닌 `file_index` → 토큰 없으면 401, 있으면 404. 기존 `/v1/search`의 422 `invalid_request` 동작은 불변 |
| C-API-10 | 응답 내용 비노출(FR-034, research R-11) | 표식 세 종류를 다른 위치에 둔다. M-파일: 스키마가 참조하지 않는 파일 안 값(참조하지 않는 시트의 이름, 참조하지 않는 셀 값, 정보 자료 본문 문장, PDF 본문 글자). M-참조: 파일과 스키마 양쪽에 있는 값(스키마가 가리키는 시트 이름·머리글 셀 값, 정보 자료 제목). M-스키마: 스키마에만 있는 이름(엔티티·속성·관계 이름). 기대: 등록 성공·`schema_mismatch`·그 밖 거부·현재본 조회 응답에 세 표식 모두 0건. 스키마 조회 응답에는 M-파일 0건이고, M-참조·M-스키마는 FR-016상 스키마 바이트에 그대로 있다(예외로 기대). `schema_mismatch_part`는 고정 키와 `[n]` 위치 번호로만 이루어짐. `embedded_image_relations[].location`은 정수 |
| C-API-08 | 스키마 조회 | 쓰기 권한 없음 → 403 `schema_access_denied`(없는 ID여도 같음). 있음+없는 ID → 404 `schema_not_found`. 있음 → 200, 바이트 그대로, `X-Content-SHA256` |
| C-CLI-01 | `admin source create` | `factory` → exit 0, 출력 키 집합 고정. 두 번째 → exit 3 `source_exists`. `other` → exit 3 `source_name_not_allowed` |
| C-CLI-02 | `admin permission` | grant·revoke·list 출력 키 집합, `read-original`+외부 표시 → exit 3 `llm_usage_not_internal`, 없는 key_id → exit 5 |
| C-CLI-03 | `admin llm-usage` | set·unset·show 출력, 자동 회수 수 표시 |
| C-CLI-04 | `admin revision`·`deletion-record` | `--confirm` 없음 → exit 2. 삭제 성공 출력. 이미 삭제 → exit 3. `show`에 파일 이름·해시 없음. `deletion-record list`에 해시 있음 |
| C-CLI-05 | 비밀값 비노출 | 모든 명령의 표준 출력·오류에 DB URL 비밀번호, 토큰, 원본 표식 문자열 0건. `--actor` 누락 → exit 2 |

### 통합 테스트(I, PostgreSQL)

| ID | 관찰할 동작 | 예상 결과 |
|---|---|---|
| I-DB-01 | 마이그레이션 | upgrade 뒤 새 테이블 14개와 제약·트리거 존재, 기존 테이블 스키마 기대값(`schema_expectations.py`) 불변. downgrade 뒤 새 테이블만 사라짐. ORM 대응(`orm_parity.py`) 통과 |
| I-DB-02 | 불변 트리거 | 스키마 UPDATE·DELETE 거부. revision은 `available→deleted`만 허용, 그 외 UPDATE·DELETE 거부. 파일 `content_hash`는 삭제 때 NULL로만. 등록 기록·삭제 기록·표시 변경 기록 UPDATE·DELETE 거부. 권한 회수 컬럼은 한 번만 채움 |
| I-DB-03 | CHECK 제약 | `schema_id` 21자·`a.b` INSERT 거부. 비기본 행의 `default-` 접두사 거부. 자료 ID 규칙 위반 INSERT 거부 |
| I-DB-04 | 원본 읽기 권한 INSERT 조건(FR-046) | 표시 `external`·없음 상태에서 직접 INSERT → 트리거 거부 |
| I-REG-01 | 원자성(FR-007, Edge Case) | 원본 바이트 저장 뒤 고장 주입 → 새 테이블 전부 행 0 증가, 새 스키마 없음. 저장 공간 부족 모사(예외 주입) → 503, 기존 revision 영향 없음 |
| I-REG-02 | 동시성(Edge Case) | 같은 자료 ID 동시 2건 → 둘 다 성공, seq 1·2, 현재본 = 나중 커밋. 같은 새 스키마 ID 다른 내용 동시 → 하나 성공, 하나 `schema_id_conflict`. 같은 내용 → 둘 다 성공·한 행 |
| I-REG-03 | 재시작 보존(FR-025) | 엔진·앱을 새로 만든 뒤 되찾은 바이트·해시·등록 기록 동일 |
| I-REG-04 | 표시 변경과 진행 중 되찾기 순서(DR-11(a)) | 되찾기 트랜잭션이 공유 잠금을 쥔 동안 표시 변경이 대기, 되찾기는 성공. 변경 커밋 뒤 새 되찾기 → 404 |
| I-REG-05 | 완전 삭제(FR-026) | 바이트·묶음·관계 행 삭제, 파일 이름·내용 해시·`manifest_hash` NULL, 삭제 기록 1건(해시 포함), 스키마 행 유지, 현재본이면 NULL. 같은 내용 재등록 → 새 revision |
| I-REG-06 | MVP 격리(FR-036) | 등록 전후 `index_runs`·`chunks`·`chunk_embeddings`·`client_source_grants`·`search_policy_manifests` 행 수와 해시 동일 |

### 인수 하네스(H)

| ID | SC | 내용 | 합격 |
|---|---|---|---|
| H-01 | SC-001·SC-005·SC-010 | 형식별 표본[DR-18] 등록·되찾기·`cmp`, 후속 10회·재시작 1회, 현재본 표본 | 불일치 0, 변경 0, 규칙 위반 0 |
| H-02 | SC-002·SC-009 | 16개 사유 표본(대표+경계)과 research R-04 예시 표 전부(기대 코드는 그 표) | 사유 일치 100%, 거부 뒤 새 행 0. SC-009 정성 검토: 검토자가 응답만 보고 수정 방법을 적을 수 있다. 합격 예시 "스키마 ID가 21자라 20자 이하로 줄여야 한다", 불합격 예시 "등록 실패"만 있는 응답 |
| H-03 | SC-003·SC-012·SC-013 | 권한 없는 쓰기·되찾기 유형 전부, 외부·미표시 부여 시도, `내부` 호출자 부여, 호출자 자기 표시 변경 시도(외부·미표시 호출자가 후보 HTTP 경로로 `internal` 설정 시도), 표시 변경 뒤 되찾기 | 쓰기·되찾기 성공 0, 외부·미표시 부여 거부 100%와 그로 생긴 권한 0, `내부` 부여 성공 100%, 자기 표시 변경 성공 0과 바뀐 표시·권한 0, 404 구별 불가 |
| H-04 | SC-004 | 같은 스키마 연속 등록, 다른 내용 시도 | 두 번째부터 스키마 파일 없음, 사전 변경 0 |
| H-05 | SC-006 | 이미지·스캔 PDF 무정보 등록, 묶음, 그림 포함 Excel·PDF, 일부 스캔 PDF | 무정보 성공 0, 표시 확인 100%, 관계 정보 100% |
| H-06 | SC-011 | 관리자 삭제, 호출자 삭제 시도 | 삭제 후 되찾기 0, 삭제 기록 있음, 호출자 삭제 0, 스키마 삭제 0 |
| H-07 | SC-007 | 테스트용 소형 MVP 색인(기존 통합 테스트 fixture)에서 등록 전후 같은 고정 질의 | 결과 차이 0, 기존 호출자 변경 항목 0 |
| H-08 | SC-008 | 성공·거부 응답 16건 이상과 로그 전체, socket 차단. 원본 바이트와 파일 안 값(시트 이름·셀 값·제목)에 표식 문자열 | 비밀값·원본 표식 노출 0(원본 읽기 권한 경로 제외), 외부 연결 시도 0 |

## 구현 단계 묶음(Phase)

각 Phase는 하나의 의도와 독립 검증을 가진다. 줄 수는 테스트 포함 예상치다. 분할 순서는 헌법 II(Single Intent → 위험 격리 → 독립 검증 → 계층 분할 → 줄 수)를 따랐다. UI 작업이 없으므로 컴포넌트 순서 규칙은 해당 없다.

| Phase | 의도(관찰 가능한 결과) | 주요 테스트 | 예상 줄 수 | 선행 | 필요한 DR |
|---|---|---|---|---|---|
| P1 | 등록 규칙 순수 함수: ID 규칙, 사유 목록, 구성 1차·2차 분류, 스키마 입력 표 판정, `manifest_hash` | U-RULE-01~04, U-COMP-01·02, U-HASH-01, U-ARCH-01 | ~350 | — | DR-08, DR-15, DR-16, DR-23 |
| P2a | 권한·표시·등록 source 표시 테이블 마이그레이션(위험 격리: 인증 영역) | RED·GREEN: U-MIG-01(정적). 검증 Agent: I-DB-01(해당 테이블), I-DB-02(권한·기록), I-DB-04 | ~250 | P1 | DR-10, DR-11, DR-19, DR-22 |
| P3 | 관리자 CLI: source 생성, 권한 부여·회수, 표시 변경·자동 회수 | U-ADM-01~03, U-ACC-01·02, C-CLI-01~03, C-CLI-05 | ~400 | P2a | DR-10, DR-11, DR-19 |
| P2b | 자료·revision·파일·원본 바이트·묶음·관계·등록 기록·삭제 기록·스키마 사전 테이블 마이그레이션 | RED·GREEN: U-MIG-02(정적). 검증 Agent: I-DB-01, I-DB-02, I-DB-03 | ~400 | P2a | DR-01, DR-09, DR-12(M1), DR-14, DR-23 |
| P4a | 내용 확인기 1: 형식 판정·선언 대조(텍스트·Markdown 구분 포함)·손상·암호·빈 파일·텍스트 해석 | U-INSP-01~03 | ~350 | P1 | DR-03, DR-04, DR-23 |
| P4b | 내용 확인기 2: PDF 글자 층, 포함 이미지 관계, 실행·OCR 없음 | U-INSP-04~06 | ~300 | P4a | DR-04, DR-14 |
| P5 | 스키마 검증·맞음 판정·사전·기본 스키마 | U-SCH-01~04 | ~400 | P2b, P4b | DR-06, DR-07 |
| P6 | 등록 서비스와 저장소: 판정 순서, 동일 재등록, 원자적 저장, 동시성 | U-SVC-01~03, U-ORDER-01, U-RULE-03, I-REG-01~03 | ~400 | P5 | DR-01, DR-15 |
| P7 | 등록 HTTP 경로: multipart 수신, 크기 계수, 오류 매핑, 독립 조립 | U-HTTP-01, C-API-01(등록 경로)·02·04·06·09(등록 경로)·11 | ~350 | P6 | DR-05, DR-02, DR-16 |
| P8 | 되찾기·등록 기록·현재본·스키마 조회 경로 | U-RET-01, C-API-01(나머지)·03·05·07·08·09(되찾기·현재본·스키마 경로)·10, I-REG-04 | ~400 | P7, P3 | DR-11(a), DR-13 |
| P9 | 완전 삭제·삭제 기록·관리자 기록 조회 | C-CLI-04, I-REG-05 | ~300 | P8 | DR-12 |
| P10 | 인수 하네스·MVP 회귀·비밀값 검사·저장 위치 점검 | H-01~08, I-REG-06, quickstart 4절 | ~350 | P9 | DR-18 |

- P2를 둘로 나눈 근거: 권한·표시 테이블은 인증 경계라 위험 격리 대상이고(헌법 II-2), P3 관리 경로를 자료 테이블 없이 먼저 독립 검증할 수 있다(II-3). 둘 다 400줄 이하라 줄 수만의 분할이 아니다.
- P4를 둘로 나눈 근거: 형식 판정은 모든 등록이 쓰고, 글자 층·포함 이미지는 이미지 규칙에만 쓴다. 두 의도가 다르고 DR-14에 의존하는 부분을 분리한다.
- 500줄 초과 예상 Phase는 없다. P3·P5·P6·P8은 400줄 근처라 실행 중 초과가 보이면 멈추고 재분할을 보고한다.
- 마이그레이션 Phase(P2a·P2b)의 TDD: RED는 컨테이너 없이 도는 정적 단위 테스트 U-MIG-01·02(마이그레이션 파일이 없거나 테이블·revision 체인이 다르면 assertion 실패)로 확인한다. 실제 PostgreSQL에서의 적용·트리거·CHECK 확인(I-DB-01~04)은 검증 Agent 단계의 계획된 전체 검증이다. 기존 `tests/unit/search/test_policy_migration.py` 관례와 같다.
- 각 Phase의 실행 Agent 쉬운 검증: `uv run ruff check src tests`와 해당 Phase의 `tests/unit`·`tests/contract` 테스트(외부 의존 없음). 통합 테스트(PostgreSQL 컨테이너 필요)와 인수 하네스는 검증 Agent가 다시 실행하는 계획된 전체 검증에 포함한다.
- 각 Phase의 검증 Agent 전체 검증: ruff, 전체 `tests/unit`·`tests/contract`, 해당 Phase까지의 `tests/integration`(compose.test.yaml), 기존 검색 회귀(C-API-06), `git diff`가 그 Phase 범위 밖 파일을 바꾸지 않았는지.

## Project Structure

### Documentation (this feature)

```text
specs/001-data-registration/
├── spec.md
├── checklists/requirements.md
├── plan.md                              # 이 파일
├── research.md                          # Phase 0: 판단(R-xx)과 결정 요청(DR-xx)
├── data-model.md                        # Phase 1: 새 테이블 14개
├── quickstart.md                        # Phase 1: 검증 안내
├── contracts/
│   ├── registration-api.openapi.yaml    # 등록·되찾기 REST 계약(OpenAPI 3.1)
│   └── admin-cli.md                     # 서버 관리 경로(관리자 CLI) 계약
└── tasks.md                             # 다음 단계(speckit-tasks), 이번에 만들지 않음
```

### Source Code (repository root)

새 파일과 바꿀 파일의 계획이다. 이 계획 작업에서는 만들지 않는다.

```text
src/omf_retrieval/
├── domain/
│   └── registration.py                      # P1 규칙·사유 목록·구성 분류(신규)
├── application/
│   ├── registration/                        # 신규 패키지
│   │   ├── ports.py                         # ContentInspector, BlobStore, 저장소 Protocol
│   │   ├── schema_dictionary.py             # P5
│   │   ├── access.py                        # P3·P8 접근 정책
│   │   ├── service.py                       # P6 등록 서비스
│   │   └── retrieval.py                     # P8 되찾기 서비스
│   └── admin/
│       └── registration_admin.py            # P3·P9(신규)
├── infrastructure/
│   ├── registration/                        # 신규 패키지
│   │   ├── inspector.py                     # P4a·P4b 내용 확인기[DR-03·04]
│   │   └── default_schema.py                # P5 기본 스키마 상수[DR-07]
│   └── database/
│       ├── models_registration.py           # P2a·P2b ORM(신규, 기존 models.py 불변)
│       ├── repository_registration.py       # P6·P8
│       └── repository_registration_admin.py # P3·P9
├── interfaces/
│   ├── api/
│   │   ├── routes/registration.py           # P7·P8(신규)
│   │   ├── app.py                           # P7 등록 서비스 선택 인자와 조건부 경로 등록(수정, 기존 3경로 불변)
│   │   ├── registration_schemas.py          # P7 응답 모델(신규)
│   │   └── runtime.py                       # P7 독립 조립(수정)
│   └── cli/
│       ├── admin.py                         # P3·P9(신규)
│       └── main.py                          # admin 하위 명령 등록(수정)
└── settings.py                              # 크기 상한·확인 상한·기본 스키마 ID(수정)

migrations/versions/
├── 0004_registration_access.py             # P2a 권한·표시·등록 source 표시
└── 0005_material_registration.py           # P2b 자료·revision·원본·스키마 사전

tests/
├── unit/registration/                      # U-*(U-MIG-01·02는 test_registration_migrations.py)
├── contract/api/test_registration_api.py   # C-API-*
├── contract/cli/test_admin_commands.py     # C-CLI-*
├── integration/database/                   # I-DB-*(기존 파일에 새 테이블 기대값 추가)
└── integration/registration/               # I-REG-*, H-*
```

**Structure Decision**: 기존 단일 프로젝트(`src/omf_retrieval` 계층형 구조: domain → application → infrastructure → interfaces)를 그대로 쓴다. 새 기능은 `registration` 하위 패키지로 모은다. 기존 `models.py`를 고치지 않고 새 ORM 파일을 두어 기존 ORM 대응 테스트의 변경을 새 테이블 추가분으로 한정한다. 마이그레이션은 P2a(`0004`, 권한·표시)와 P2b(`0005`, 자료·스키마)의 두 revision으로 나눈다(위험 격리). 의존성 추가는 `pyproject.toml`·`uv.lock` 변경이며 DR-04·DR-05 승인(2026-11-10)에 따라 그 의존 Phase(P4a, P7)에서만 한다.

## Constitution Check 재평가(설계 후)

| 원칙 | 판정 | 설계 후 확인 |
|---|---|---|
| I | 통과 | DR 24건 모두 사용자 승인(2026-11-10). 새 의존성(`python-multipart`, `pypdf`, `defusedxml`)은 DR-04·DR-05 승인으로 확정되었고 해당 Phase(P4a, P7)에서만 추가한다. |
| II | 통과 | Phase 12개, 각 400줄 이하 예상, 분할 근거 기록. |
| III | 통과 | Phase별 쉬운 검증과 검증 Agent 전체 검증을 분리했다. |
| IV | 통과 | U 31건·C 16건·I 10건·H 8건 설계. 정상·경계·실패와 예상 결과를 적었다. 마이그레이션 Phase도 정적 단위 테스트로 RED를 확인한다. |
| V | 통과 | 불변 트리거·내용 해시·바이트 일치·`inspector_version`. 좌표의 파일 지정은 `006`에 보완 입력으로 넘겼다. |
| VI | 통과 | 사내 PostgreSQL 저장, 외부 호출 0 검증(H-08), 응답·로그 비노출 규칙, 외부 표시 호출자 원본 읽기 차단과 자동 회수, 응답 헤더 안전화. 운영 미정 항목은 DR-17. |
| VII | 통과 | LLM·OCR 없음, 확인기가 읽은 구조 값은 비교에만 쓰고 저장·응답하지 않음(U-INSP-06, C-API-10). |

## Complexity Tracking

> 헌법 위반이 없어 기록할 항목이 없다.

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| — | — | — |

## 스킬 단계 처리 기록

- `speckit-plan` Setup: `.specify/scripts/bash/setup-plan.sh --json`을 실행했다. 스크립트는 git 명령을 쓰지 않고 브랜치를 바꾸지 않음을 확인한 뒤, 로컬 포인터 `.specify/feature.json`을 `specs/001-data-registration`으로 바꿔 실행했다.
- `.specify/extensions.yml`이 없어 before/after plan hook은 없다.
- Agent context 갱신(`CLAUDE.md`·`AGENTS.md` 반영) 단계는 하지 않았다. 두 파일은 사용자가 직접 적용하는 대상이다.
- Phase 0의 "research agent 배포"는 하지 않았다. 조사는 이미 제공된 문맥(spec, 헌법, 설계서, `AGENTS.md`, 코드 구조 조사)과 저장소 파일 확인으로 한정했고, 외부 자료 조사는 하지 않았다. 대표 자료 크기와 OMF 경로 문자 조사는 범위 밖이라 DR-02·DR-08에 적었다.
- spec의 "NEEDS CLARIFICATION" 해소는 결정 요청으로 올렸고, 2026-11-10 사용자가 DR-01~DR-24를 모두 승인했다. tasks 단계로 넘어갈 수 있다.
