---

description: "spec 001 문서 자료 등록 구현 작업 목록"
---

# Tasks: 문서 자료 등록·원본 보관·불변 revision·권한·스키마 사전(data-registration)

**Input**: `/specs/001-data-registration/`의 설계 문서([plan.md](./plan.md), [spec.md](./spec.md), [research.md](./research.md), [data-model.md](./data-model.md), [contracts/](./contracts/), [quickstart.md](./quickstart.md))

**작성일**: 2026-11-10 | **개정**: 2026-11-12(Q-09 반영, 독립 검증 FAIL F-1~F-8·F-10 반영. F-1은 Q-10, F-5는 Q-11로 처리. 재검증 비차단 지적 N-1~N-4 반영) | **상태**: 초안(검증 전). `확인 필요` Q-01~Q-08은 사용자 승인(2026-11-10), Q-09~Q-11은 사용자 승인(2026-11-12)으로 해소되어 각 작업에 반영했다. Q-03은 Q-09로 대체되었다.

**Tests**: plan.md `테스트 전략`의 테스트 설계(U 31건, C 16건, I 10건, H 8건, 합계 65건)를 그대로 쓴다. 실행 Agent는 테스트를 새로 설계하지 않는다(헌법 IV, `AGENTS.md` TDD). 각 테스트 ID는 정확히 한 작업에 **배정**한다. plan이 한 테스트를 두 Phase에 걸쳐 둔 경우는 앞 작업에 배정하고 뒤 작업은 **확장**으로 적는다(Q-01).

**Organization**: plan.md `구현 단계 묶음(Phase)`의 12개 Phase(P1~P10)를 기반 계층부터 소비 계층 순서로 따른다. Phase 안에서 의도가 둘로 나뉘는 4개(P3, P5, P8, P10)는 작업 두 개로 나눴다(아래 `Phase–작업 대응`). 사용자 스토리 라벨은 그 작업이 주로 완성하는 스토리를 표시한다.

## 용어

plan.md 용어표의 용어를 그대로 쓴다. 아래는 이 문서에서 쓰는 용어이며, plan 용어표에 있는 것도 일부 다시 적었다.

| 용어 | 뜻 |
|---|---|
| FR, SC | [spec.md](./spec.md)의 기능 요구사항(Functional Requirement)과 성공 기준(Success Criteria). |
| US1~US5 | spec의 사용자 스토리 1~5. `US2-12`는 스토리 2의 시나리오 12. |
| DR-xx, R-xx | [research.md](./research.md)의 결정 요청(2026-11-10 전부 사용자 승인)과 기술 판단. |
| P1~P10 | plan.md의 구현 단계 묶음(Phase) 번호. P2a·P2b·P4a·P4b 포함 12개. |
| T001~T016 | 이 문서의 작업 ID. 실행 순서대로 매겼다. 한 작업 = 실행 Agent 한 인스턴스가 맡는 승인 작업 단위. |
| TDD | 테스트 주도 개발. RED → GREEN → REFACTOR 순서. |
| RED | 구현이 없어서 생기는 assertion 실패를 실제로 확인한 상태. import·문법·fixture 오류는 RED가 아니다. |
| GREEN | RED 테스트를 통과시키는 최소 구현을 마친 상태. |
| REFACTOR | 동작을 유지한 정리. 정리 뒤 같은 테스트가 계속 통과해야 한다. |
| U-·C-·I-·H- | 테스트 ID 접두어. U 단위(DB·네트워크 없음), C 계약(가짜 서비스 주입 앱·CLI 러너), I 통합(`compose.test.yaml` PostgreSQL), H 인수 하네스(실제 앱+PostgreSQL, SC 판정). |
| 배정 테스트 | 그 작업이 RED부터 작성·통과시킬 책임을 지는 테스트 ID. 각 ID는 이 문서 전체에서 한 번만 배정한다. |
| 확장(배정 아님) | 이미 다른 작업에 배정된 테스트에 이 작업의 경로·명령 사례를 더하는 일. plan이 한 테스트를 두 Phase에 둔 경우와 사용자가 승인한 경우(Q-01·Q-02·Q-09·Q-10·Q-11)에만 쓴다. |
| `[P]` | Spec Kit 표기. 파일이 겹치지 않고 미완료 선행 작업이 없어 병렬 실행이 가능한 작업. 기본 운영은 한 번에 한 작업씩 실행한다. |
| 실행 Agent의 쉬운 검증 | 외부 서비스·네트워크·GPU·컨테이너 없이 돌리는 검증(`ruff`, `tests/unit`, `tests/contract`). |
| 검증 Agent의 전체 검증 | 별도 인스턴스가 다시 실행하는 계획된 모든 검증. PostgreSQL이 필요한 `tests/integration` 포함. |
| 예상 줄 수 | 테스트 포함 예상 변경 줄 수. 목표 400줄 이하, 500줄 초과 예상이면 분할 근거를 적는다(헌법 II-5). |
| 내용 확인기 | 등록 시점에 실제 형식·손상·암호·텍스트 해석·PDF 글자 층·포함 이미지 위치를 결정적으로 확인하는 프로그램(plan 용어표). |
| `compose.test.yaml` | 통합 테스트용 PostgreSQL 컨테이너 정의. 연결은 `OMF_RETRIEVAL_TEST_DATABASE_URL`. |
| MVP | 현재 운영 중인 최소 기능 제품(Minimum Viable Product). Git 고정 commit OMF 색인과 `POST /v1/search`(plan 용어표와 같음). |
| OMF | 첫 조회 대상 프로젝트. OMF 저장소는 그 설계 문서 저장소이며 읽기 전용이다. |
| M1·M2 | spec Assumptions의 이전 검토 이월 미결 항목 번호(plan 용어표와 같음). M1 삭제 시 내용 해시 제거, M2 삭제 뒤 남는 항목의 관리자 확인 경로. DR-12에서 결정. |
| append-only | INSERT만 허용하고 UPDATE·DELETE를 트리거로 거부하는 저장 방식(plan 용어표와 같음). |
| advisory lock | PostgreSQL의 응용 정의 잠금(plan 용어표와 같음). 같은 (source, 자료 ID) 등록을 직렬화한다. |
| 트리거 | DB가 행 변경 때 자동 실행하는 검사 함수. 여기서는 불변·상태 전이 제한을 강제한다. |
| API, REST, HTTP | 프로그램이 기능을 호출하는 규약(Application Programming Interface), HTTP 기반 웹 API 방식, 웹 요청 규약. `GET`·`POST`는 HTTP 메서드, `X-Content-SHA256`은 내용 해시를 싣는 응답 헤더. |
| CLI | 명령줄 인터페이스(Command-Line Interface). 여기서는 서버에서 실행하는 `omf-retrieval admin ...` 관리자 CLI. |
| DB, SQL, ORM | 데이터베이스, DB 질의 언어, DB 테이블을 코드 객체로 다루는 매핑(Object-Relational Mapping, 여기서는 SQLAlchemy). |
| SQL 용어 | `INSERT`·`UPDATE`·`DELETE`(행 추가·변경·삭제), `CHECK`(값 제약), `NOT NULL`, `PK`(Primary Key, 기본 키), `COLLATE "C"`(바이트 순 대소문자 구분 비교), `WHERE`(조건), `FOR SHARE`(읽은 행에 공유 잠금). |
| Alembic, downgrade | 이 저장소의 DB 마이그레이션 도구와 마이그레이션 되돌리기. |
| LLM, OCR, GPU | 대규모 언어 모델, 이미지 속 글자를 읽는 이미지 문자 인식, 그래픽 처리 장치. 001 등록 경로는 셋 다 쓰지 않는다. |
| PDF, PNG, JPEG, ZIP, JSON | 문서·이미지·압축 파일 형식과 텍스트 데이터 형식. ZIP과 모든 압축 파일은 등록하지 않는다(DR-03). |
| OOXML | Excel `.xlsx`가 쓰는 ZIP 기반 Office Open XML 형식. |
| CFB | OLE 복합 파일(Compound File Binary). 옛 `.xls`와 암호화된 xlsx를 담는 용기. |
| IHDR, IEND | PNG의 첫 필수 청크(이미지 머리 정보)와 끝 표지 청크. |
| SOI, EOI, FF D8 | JPEG의 시작 표지(`FF D8`)와 끝 표지(`FF D9`). |
| drawing, XObject | xlsx 안 그림 배치 파트, PDF 안 이미지 등 외부 객체. 포함 이미지 판정에 쓴다. |
| deflate | ZIP의 압축 방식. 압축 비율 상한 시험 표본을 만들 때 쓴다. |
| UTF-8, BOM, NUL | 문자 부호화 방식, 파일 앞 바이트 순서 표시(`EF BB BF`), 값 0인 문자. NUL이 있으면 텍스트로 보지 않는다. |
| SHA-256, canonical JSON | 내용 해시 함수, 키 정렬·고정 구분자의 JSON(기존 `canonical_json` 함수). |
| RFC 5987, nosniff, `WWW-Authenticate` | HTTP 헤더의 비ASCII 파일 이름 인코딩 규칙, 브라우저 형식 추측 금지 헤더 값, 인증 방식을 알리는 응답 헤더. |
| multipart | HTTP 본문에 여러 파일·필드를 나눠 담는 `multipart/form-data` 형식. |
| URL | 주소 문자열. 여기서는 DB 연결 주소(비밀번호를 담을 수 있어 출력 금지). |
| uv, ruff, pydantic, typer | 이 저장소의 Python 패키지·실행 도구, 정적 검사 도구, 데이터 검증 라이브러리, CLI 라이브러리. |
| Protocol | Python의 구조적 인터페이스 선언. application 포트(`ports.py`)를 정의할 때 쓴다. |
| fixture | 테스트 준비 데이터·환경을 만드는 pytest 기능. |
| stub, 변이(mutation) | 실제 동작 대신 고정 값을 돌려주는 가짜 구현, 일부러 코드를 바꿔 테스트가 실패하는지 보는 방법(이 문서는 쓰지 않음, Q-04). |
| 표식 | 원본·토큰에 심어 두고 응답·로그에서 찾는 알려진 문자열. 노출 검사에 쓴다. |
| Spec Kit | 명세 주도 개발 도구(`speckit-*` 스킬과 `.specify/`). |
| PASS | 검증 Agent의 합격 판정. |
| 헌법 II·IV | `.specify/memory/constitution.md` v2.1.0의 원칙 II(작업 분할 우선순위)·IV(TDD). |
| B1·B7 등 | research R-04 표 B(구성 2차)의 행 번호. |
| `AGENTS.md`, `CLAUDE.md` | 저장소의 Agent 협업 규칙 정본과 Claude Code 연결 파일. |
| Q-xx | 아래 `확인 필요` 표의 항목 번호. |
| MiB | 메비바이트. 1 MiB = 1,048,576바이트(50 MiB = 52,428,800바이트). |
| Bearer 토큰 | 요청의 `Authorization: Bearer <토큰>` 헤더로 호출자를 증명하는 기존 비밀 문자열(`omfr_<key_id>.<secret>`). |
| OpenAPI | HTTP API의 경로·요청·응답을 기계가 읽는 형식으로 적는 표준 명세(3.1). 계약 파일 `contracts/registration-api.openapi.yaml`. |
| Content-Type | 파일 파트나 응답의 형식을 알리는 헤더 값(예: `application/pdf`). 여기서는 확장자와 함께 호출자의 형식 선언이다. |
| monkeypatch, socket, subprocess | pytest의 임시 대체 기능, Python의 네트워크 연결 모듈, 외부 프로그램 실행 모듈. 외부 호출이 없음을 확인할 때 socket·subprocess를 monkeypatch로 막는다. |
| Single Intent | 헌법 II-1의 분할 기준. 한 작업 단위는 하나의 사용자 의도와 관찰 가능한 결과만 가진다. |
| EncryptionInfo, EncryptedPackage | 암호화된 xlsx를 담은 CFB 용기 안의 스트림 이름. 이 둘이 있으면 암호 사유(`unreadable_content`)다. |

## 확인 필요(사용자 확인 대상)

Q-01~Q-11은 문서 사이의 공백·모순이나 독립 검증 지적으로 작업 정의를 확정할 수 없던 항목이며, 모두 사용자 승인(Q-01~Q-08 2026-11-10, Q-09~Q-11 2026-11-12)을 받아 해소했다(Q-03은 Q-09로 대체). 실행 Agent는 아래 처리를 그대로 따르고 다시 정하지 않는다.

| ID | 내용 | 영향 작업 | 처리 | 상태 |
|---|---|---|---|---|
| Q-01 | plan 단계표가 한 테스트를 두 Phase에 둔다: `U-RULE-03`(P1·P6), `C-API-01`·`C-API-09`(P7·P8), `I-DB-01`·`I-DB-02`(P2a·P2b). | T001, T002, T005, T010, T011, T013 | 앞 작업에 배정하고 뒤 작업은 같은 테스트 파일에 해당 경로·테이블 사례를 더하는 "확장(배정 아님)"으로 둔다. 확장 사례는 plan 표의 문장 범위 안에서만 쓴다. | 해소(사용자 승인 2026-11-10) |
| Q-02 | `C-CLI-05`는 "모든 명령"의 비밀값 비노출을 보지만 P3에만 있다. P9 명령 3개는 P3 뒤에 생긴다. | T004, T014 | T004에 배정하고 T014가 새 명령 3개를 같은 테스트 대상 목록에 더하는 확장으로 둔다. | 해소(사용자 승인 2026-11-10) |
| Q-03 | `U-ARCH-01`이 P1에 있었으나 P1에는 검사 대상 application 등록 모듈(접근 정책 등)이 없어 구현 부재로 인한 RED가 생기지 않는다. | T001, T003 | (대체됨) 2026-11-10 승인으로 배정을 T003으로 옮겼으나 Q-09 결정으로 T001에 되돌렸다. 현재 처리는 Q-09를 따른다. | Q-09로 대체 |
| Q-04 | 실행 Agent는 컨테이너가 필요한 I·H 테스트를 실행할 수 없고, T015·T016은 배정 테스트가 모두 I·H이며 이미 구현된 동작을 확인하므로 구현 부재로 인한 RED가 생기지 않는다. | T002, T005, T010, T012, T014, T015, T016 | T002·T005·T010·T012·T014는 같은 작업의 U·C 테스트가 RED를 맡고 I 테스트는 실행 Agent가 작성만, 검증 Agent가 실행한다. T015·T016은 RED 선행 면제. 대신 검증 Agent가 컨테이너로 실행해 통과를 확인하고, 각 테스트가 가짜(stub)가 아닌 실제 대상 동작(실제 HTTP 앱·서비스·PostgreSQL)을 호출하는지 코드로 확인한다. 의도적 고장·변이(mutation) 단계는 두지 않는다. | 해소(사용자 승인 2026-11-10) |
| Q-05 | plan은 `manifest_hash`를 domain에 두고 research R-06은 application의 `canonical_json` 재사용을 말해 계층 방향이 뒤집혔다. | T001 | `manifest_hash` 계산을 application 계층 `src/omf_retrieval/application/registration/manifest.py`에 두고 기존 `application/indexing/hashing.py`의 `canonical_json`을 재사용한다. `canonical_json`은 옮기지 않는다. 해시 값은 R-06 그대로다. plan 구성 요소 표·P1 행·구조도를 고쳤다(research R-06은 위치를 적지 않아 수정 없음). | 해소(사용자 승인 2026-11-10) |
| Q-06 | 스키마 맞음 판정용 구조 값(시트 이름, 지정 셀 값, 정보 자료 제목 줄) 읽기를 맡은 Phase가 plan에 없다. | T008 | T008이 `inspector.py`에 구조 값 읽기를 더한다(읽은 값은 그 요청 비교에만 쓰고 저장·응답·로그에 남기지 않음, R-09). | 해소(사용자 승인 2026-11-10) |
| Q-07 | 승인 규칙 두 개에 명시 테스트 사례가 없었다. (a) 8바이트 PNG 서명 직후 잘린 파일 → `unsupported_file_format`. (b) 바이트가 같은 파일의 `file_index`·제출 이름을 multipart 파트 순서로 매김. | T006, T010 | 새 테스트 ID 없이 사례만 추가: (a)는 `U-INSP-01`(T006), (b)는 `U-ORDER-01`(T010). plan 테스트 표의 두 행을 고쳤다. 총 65건 유지. | 해소(사용자 승인 2026-11-10) |
| Q-08 | `settings.py` 수정(크기 상한, 확인 상한, 기본 스키마 ID)에 Phase가 없다. | T006, T011 | `settings.py`는 T011에서 바꾼다. 그 전 작업(T006 등)은 코드 기본값(확인기 설정 객체 등)에 대해 테스트한다. | 해소(사용자 승인 2026-11-10) |
| Q-09 | Q-05 처리로 T001이 `application/registration/manifest.py`를 만들므로, application 등록 모듈이 처음 생기는 작업은 T001이다. Q-03의 이동 근거와 어긋났다. | T001, T003, T008, T010, T012 | `U-ARCH-01`을 T001에 배정한다(plan P1 원래 위치). 테스트는 검사 대상 파일을 명시적으로 나열하며 `application/registration/manifest.py`를 포함한다. T001 시작 시 그 파일이 없어 assertion이 실패하는 것이 RED다. 이후 application 등록 모듈을 새로 만드는 작업(T003 `access.py`·`ports.py`·`application/admin/registration_admin.py`, T008 `schema_dictionary.py`, T010 `service.py`, T012 `retrieval.py`)은 대상 목록에 그 파일을 더하는 "확장(배정 아님)"을 한다. plan 단계표도 P1로 되돌렸다. | 해소(사용자 승인 2026-11-12) |
| Q-10 | (검증 F-1) `C-API-11`은 8경로 집합을 요구하지만 T011(P7) 시점에는 등록 경로가 `POST /v1/registrations` 하나뿐이다. `C-API-06`의 "5경로가 추가될 뿐"도 같은 문제다. | T011, T013 | 두 테스트 모두 T011에 배정한다. T011의 `C-API-11`은 기존 3개 + `POST /v1/registrations` = 4경로를, `C-API-06`은 등록 경로 1개만 추가되고 기존 3경로 응답이 같음을 확인한다. T013이 같은 테스트를 GET 4개를 더한 8경로(`C-API-06`은 5개 추가)로 넓히는 "확장(배정 아님)"을 한다. plan 테스트 표와 단계표 P7·P8 행도 고쳤다. | 해소(사용자 승인 2026-11-12) |
| Q-11 | (검증 F-5) T003의 관리자 저장소 SQL(`repository_registration_admin.py`)을 실제 DB로 검증하는 테스트가 T003에 없었고, 그 근거로 든 I-REG-04는 되찾기 잠금 순서를 보는 테스트라 맞지 않았다. | T002, T003 | 기존 `I-DB-04`(배정 T002)에 사례를 더한다(새 ID 없음). T003이 "확장(배정 아님)"으로 관리자 저장소 SQL로 source 생성 → 권한 부여 → 외부 LLM 사용 표시 설정을 한 뒤 DB 상태를 확인하는 사례를 추가하고, T003의 검증 Agent가 실제 PostgreSQL 컨테이너로 실행한다. plan I-DB-04 설명을 고쳤다. | 해소(사용자 승인 2026-11-12) |

## Phase–작업 대응

| plan Phase | 작업 | 분할 근거 |
|---|---|---|
| P1 | T001 | 그대로 |
| P2a | T002 | 그대로(위험 격리: 인증 영역 마이그레이션) |
| P3 | T003, T004 | 계층 분할(헌법 II-4): 접근 정책·관리 서비스(application)와 관리자 CLI(interfaces). 각각 단위·계약 테스트로 독립 RED 가능. plan이 "400줄 근처, 초과 시 재분할"로 적은 Phase |
| P2b | T005 | 그대로(위험 격리: 자료 테이블 마이그레이션) |
| P4a | T006 | 그대로 |
| P4b | T007 | 그대로 |
| P5 | T008, T009 | Single Intent: 스키마 구조·맞음 판정과 사전 불변 보관·기본 스키마는 관찰 결과가 다르다. plan의 "400줄 근처" Phase |
| P6 | T010 | 분할하지 않음. 저장소만 떼면 그 작업에는 실행 Agent가 RED를 확인할 단위 테스트가 없다(Q-04) |
| P7 | T011 | 그대로 |
| P8 | T012, T013 | 계층 분할: 되찾기 서비스·잠금 순서(application·저장소)와 HTTP 경로 4개(interfaces). plan의 "400줄 근처" Phase |
| P9 | T014 | 그대로 |
| P10 | T015, T016 | Single Intent: SC 기능 인수(H-01~H-06)와 MVP 격리·보안·저장 위치(H-07·H-08·I-REG-06·quickstart 4절) |

## 작업 공통 규칙

- 각 작업은 **RED → GREEN → REFACTOR** 순서다. 배정 테스트를 plan 표의 사례 그대로 먼저 쓰고, 구현 부재로 인한 assertion 실패를 실제로 확인한 뒤 최소 구현을 쓴다. REFACTOR 뒤 같은 테스트를 다시 통과시킨다.
- 표본 파일은 테스트 안에서 표준 라이브러리로 합성한다(DR-18). 대외비 자료·네트워크·OMF 저장소(`/Users/rangkim/projects/crefle/ohmyfactory/apps/omf`, 읽기 전용)를 쓰지 않는다.
- 비밀값·토큰을 테스트·코드·문서에 기록하지 않는다. 노출 검사는 알려진 표식 문자열로 한다.
- 기존 검색 기능, Git 색인 경로, 기존 테이블·ORM(`infrastructure/database/models.py`), 기존 테스트(`tests/contract/api/test_search_api.py`, `tests/contract/api/test_web_delivery.py` 등)를 고치지 않는다(FR-035·FR-036, DR-21·DR-22).
- 새 의존성은 승인된 3개뿐이며 지정 작업에서만 추가한다: `pypdf`·`defusedxml`(T006), `python-multipart`(T011).
- 클라이언트 생성이 필요한 테스트는 `source_profiles`에 `omf` 행을 넣는 fixture를 쓴다(DR-24). 공유·운영 DB를 쓰지 않는다.
- 실행 Agent의 쉬운 검증은 `uv run ruff check src tests`와 해당 작업의 `tests/unit`·`tests/contract` 선택자다. 검증 Agent는 여기에 전체 `tests/unit`·`tests/contract`, 그 작업까지의 `tests/integration`(compose.test.yaml), 기존 검색 회귀, `git diff` 범위 점검을 더해 다시 실행한다(plan `구현 단계 묶음` 끝 항목).
- 범위 밖 변경이나 새 의사결정이 필요하면 멈추고 보고한다. 예상 줄 수가 500줄을 넘을 것으로 보이면 멈추고 재분할을 보고한다.

## Format: `[ID] [P?] [Story] Description`

---

## Phase 1: Setup (Shared Infrastructure)

별도 작업 없음. 기존 단일 프로젝트 구조(`src/omf_retrieval`, `tests/`, `migrations/`)와 도구(uv, pytest, ruff, Alembic)를 그대로 쓴다. 새 의존성 추가는 그 의존 작업(T006, T011)에 넣었다(plan `Structure Decision`).

---

## Phase 2: Foundational — 등록 규칙과 권한 저장 기반

**Purpose**: 모든 스토리가 쓰는 순수 규칙과 인증 영역 테이블. 이 단계가 끝나기 전에는 스토리 작업을 시작하지 않는다.

- [ ] T001 등록 규칙 순수 함수를 `src/omf_retrieval/domain/registration.py`에, `manifest_hash` 계산을 `src/omf_retrieval/application/registration/manifest.py`에 구현한다(plan P1)
  - 의도: 스키마 ID·자료 ID 규칙, 16개 거부 사유 목록과 코드·HTTP 상태, 구성 1차·2차 분류, FR-012 스키마 입력 표 판정, `manifest_hash`와 `file_index` 결정적 정렬이 순수 함수로 결정적인 결과를 낸다.
  - 파일: `src/omf_retrieval/domain/registration.py`(신규), `tests/unit/registration/__init__.py`, `tests/unit/registration/test_rules.py`(U-RULE-01·02·04), `tests/unit/registration/test_schema_input.py`(U-RULE-03), `tests/unit/registration/test_composition.py`(U-COMP-01·02), `tests/unit/registration/test_manifest_hash.py`(U-HASH-01), `tests/unit/registration/test_architecture.py`(신규, U-ARCH-01), `src/omf_retrieval/application/registration/__init__.py`·`manifest.py`(신규, `manifest_hash`. 기존 `application/indexing/hashing.py`의 `canonical_json` 재사용, `canonical_json`은 옮기지 않음, Q-05)
  - 배정 테스트: `U-RULE-01` `U-RULE-02` `U-RULE-03` `U-RULE-04` `U-COMP-01` `U-COMP-02` `U-HASH-01` `U-ARCH-01`
  - 규칙 출처: 스키마 ID `^[A-Za-z0-9_-]{1,20}$`·대소문자 구분·빈 문자열 거부(FR-011). 자료 ID "1~200자 `[A-Za-z0-9._/-]`, 앞뒤 `/`·`//`·`.`/`..` 조각 금지"(DR-08). 사유 16개는 계약 `x-rejection-reasons`, 메시지 템플릿은 길이·한도만 채우고 받은 값을 넣지 않는다(DR-16, R-11). 구성 1차 = research R-04 표 A, 구성 2차 = 표 B(B1~B7, 첫 해당 행), `info_human_authored`는 정확히 `true`만 인정. 파일 이름 255자 초과는 자르지 않고 `invalid_request_composition`(data-model 5절). `manifest_hash` = R-06 canonical JSON SHA-256(파일 이름 제외, 묶음 대상은 (형식 판정 결과, 내용 해시) 정렬), application `manifest.py`에서 계산(Q-05). `file_index`는 (역할, 형식 판정 결과, 내용 해시) 정렬, 같은 키는 multipart 파트 순서로 안정 정렬(domain). 같은 바이트 파일 사례는 `U-ORDER-01`(T010)이 확인한다(Q-07 (b)).
  - TDD: RED — 위 8개 테스트를 plan 표 사례대로 작성하고 `domain/registration.py`·`manifest.py`의 함수 부재로 assertion이 실패함을 확인. U-ARCH-01은 검사 대상 파일을 명시적으로 나열하고(`application/registration/manifest.py` 포함) 각 파일이 존재하며 infrastructure·`Settings`를 import하지 않는지 검사한다. 작업 시작 시 `manifest.py`가 없어 assertion이 실패하는 것이 RED다(Q-09). GREEN — 최소 구현. REFACTOR — 사유 목록·규칙 상수 정리 후 재통과.
  - 선행: 없음 | 예상 줄 수: ~370 | 외부 의존: 없음
  - 실행 Agent 검증: `uv run ruff check src tests && uv run pytest tests/unit/registration -q`
  - 반영: Q-05(application 배치, 사용자 승인 2026-11-10), Q-09(U-ARCH-01 T001 배정, 사용자 승인 2026-11-12)

- [ ] T002 권한·표시·등록 source 표시 테이블 마이그레이션 `migrations/versions/0004_registration_access.py`를 만든다(plan P2a, 위험 격리)
  - 의도: data-model 3.1~3.4의 새 테이블 4개(`registration_sources`, `client_source_permissions`, `client_llm_usage`, `client_llm_usage_events`)와 CHECK·부분 유일·트리거가 생기고, 기존 테이블은 바뀌지 않는다.
  - 파일: `migrations/versions/0004_registration_access.py`(신규, `revision = "0004_registration_access"`, `down_revision = "0003_search_policy_manifest"`), `src/omf_retrieval/infrastructure/database/models_registration.py`(신규, 4개 테이블 ORM. 기존 `models.py` 불변), `tests/unit/registration/test_registration_migrations.py`(U-MIG-01), `tests/integration/database/schema_expectations.py`·`tests/integration/database/orm_parity.py`·`tests/integration/database/test_migrations.py`(I-DB-01: 새 테이블 기대값 추가), `tests/integration/database/test_registration_triggers.py`(신규, I-DB-02·I-DB-04)
  - 배정 테스트: `U-MIG-01` `I-DB-01` `I-DB-02` `I-DB-04`
  - 제약(data-model 원문): `permission IN ('write','read_original')`; "부분 유일 제약 `(client_id, source_profile_id, permission) WHERE revoked_at IS NULL`"; "`revoked_at·revoked_by_actor·revoke_reason`을 NULL에서 값으로 한 번만 채울 수 있다. 그 밖의 UPDATE·DELETE 거부"; `revoke_reason IN ('admin_revoked','llm_usage_changed')`; "`read_original` 행 INSERT는 같은 트랜잭션에서 `client_llm_usage.usage = 'internal'`을 공유 잠금으로 확인한 경우에만 허용"(DB 트리거); `usage IN ('internal','external')`; `revoked_read_permission_count >= 0`; `registration_sources`·`client_llm_usage_events`는 append-only.
  - TDD: RED — U-MIG-01을 기존 `tests/unit/search/test_policy_migration.py` 방식(파일을 모듈로 읽고 `alembic.op`를 기록용 가짜로 교체)으로 작성하고 마이그레이션 파일 부재로 assertion 실패 확인. I-DB-01(이 4개 테이블 범위)·I-DB-02(권한 회수 컬럼 1회 채움, 표시 변경 기록 UPDATE·DELETE 거부)·I-DB-04는 작성만 한다(Q-04). GREEN — 마이그레이션·ORM. REFACTOR — 트리거 SQL 정리 후 재통과.
  - 선행: T001 | 예상 줄 수: ~250 | 외부 의존: I-DB-*는 PostgreSQL(`compose.test.yaml`), 검증 Agent가 실행
  - 실행 Agent 검증: `uv run ruff check src tests && uv run pytest tests/unit/registration/test_registration_migrations.py -q`
  - 검증 Agent 추가: `uv run pytest tests/integration/database -q`(기존 테이블 기대값 불변 포함)

**Checkpoint**: 등록 규칙과 권한 저장 구조가 준비되었다. MVP 테이블은 그대로다.

---

## Phase 3: User Story 2 — 관리자가 source와 권한·외부 LLM 사용 표시를 관리한다 (Priority: P1)

**Goal**: 관리자가 서버 관리 경로(관리자 CLI)로 `factory`를 만들고, 쓰기·원본 읽기 권한을 부여·회수하며, 외부 LLM 사용 표시를 바꾸고 자동 회수를 일으킨다.

**Independent Test**: 가짜 저장소로 접근 정책·관리 서비스 단위 테스트, CLI 러너로 계약 테스트를 실행한다. 표시 `external`·없음 호출자에게 원본 읽기 부여가 `llm_usage_not_internal`로 거부되고 아무것도 바뀌지 않는다.

- [ ] T003 [US2] 등록 접근 정책과 등록 관리 서비스(source·권한·표시)를 `src/omf_retrieval/application/registration/access.py`, `src/omf_retrieval/application/admin/registration_admin.py`에 구현한다(plan P3 중 application·저장소)
  - 의도: 효력 있는 원본 읽기 권한(기록 ∧ 표시 `internal`), 스키마 조회 권한(어느 source든 활성 쓰기), 쓰기 판정이 결정적으로 나오고, 관리 서비스가 source 생성·권한 부여·회수·표시 변경과 자동 회수(`llm_usage_changed`)를 규칙대로 한다.
  - 파일: `src/omf_retrieval/application/registration/access.py`(신규), `src/omf_retrieval/application/registration/ports.py`(신규, 접근·관리 저장소 Protocol), `src/omf_retrieval/application/admin/registration_admin.py`(신규, source·권한·표시 부분), `src/omf_retrieval/infrastructure/database/repository_registration_admin.py`(신규, source·권한·표시 부분), `tests/unit/registration/test_access.py`(U-ACC-01·02), `tests/unit/registration/test_registration_admin.py`(U-ADM-01~03), `tests/unit/registration/test_architecture.py`(수정, U-ARCH-01 대상 목록 확장), `tests/integration/database/test_registration_triggers.py`(수정, I-DB-04 관리자 저장소 SQL 사례 확장)
  - 배정 테스트: `U-ACC-01` `U-ACC-02` `U-ADM-01` `U-ADM-02` `U-ADM-03`
  - 확장(배정 아님): U-ARCH-01(배정 T001) — 검사 대상 목록에 `application/registration/access.py`·`ports.py`, `application/admin/registration_admin.py`를 더한다(Q-09). I-DB-04(배정 T002) — 관리자 저장소 SQL(`repository_registration_admin.py`)로 `factory` source 생성 → 권한 부여 → 외부 LLM 사용 표시 설정을 한 뒤 `registration_sources`·`client_source_permissions`·`client_llm_usage`·`client_llm_usage_events` 행 상태를 확인하는 사례를 더한다(Q-11).
  - 규칙: DR-11 (a)~(d) 승인안. 표시 `internal`→`external`·해제 시 같은 트랜잭션에서 활성 `read_original` 전부 회수, `internal` 복귀 시 복원 없음, 변경 기록 append-only. 같은 부여 반복은 `changed:false`. 회수는 원본 읽기·검색 권한과 revision을 바꾸지 않는다(FR-030). `source create`는 `factory`만(DR-19). 쓰기·원본 읽기 명령은 `client_source_grants`를 읽거나 바꾸지 않는다(FR-028). application 모듈은 infrastructure·`Settings`를 import하지 않는다(U-ARCH-01).
  - TDD: RED — 5개 테스트를 가짜 저장소로 작성, 모듈 부재로 assertion 실패 확인(U-ARCH-01 확장 대상 파일 부재로 인한 실패도 함께 확인된다). GREEN — 최소 구현. 저장소 SQL 구현은 단위 테스트 대상이 아니며, 이 작업의 I-DB-04 확장 사례(실행 Agent는 작성만, Q-04)를 검증 Agent가 실제 PostgreSQL로 실행해 검증하고 이후 H-03(T015)이 다시 확인한다(Q-11). REFACTOR 후 재통과.
  - 선행: T002 | 예상 줄 수: ~270 | 외부 의존: I-DB-04 확장 사례는 PostgreSQL(`compose.test.yaml`), 검증 Agent 실행
  - 실행 Agent 검증: `uv run ruff check src tests && uv run pytest tests/unit/registration/test_access.py tests/unit/registration/test_registration_admin.py tests/unit/registration/test_architecture.py -q`
  - 검증 Agent 추가: `uv run pytest tests/integration/database/test_registration_triggers.py -q`(실제 PostgreSQL 컨테이너, Q-11)

- [ ] T004 [US2] 관리자 CLI `admin source|permission|llm-usage`를 `src/omf_retrieval/interfaces/cli/admin.py`에 구현하고 `src/omf_retrieval/interfaces/cli/main.py`에 등록한다(plan P3 중 CLI)
  - 의도: [contracts/admin-cli.md](./contracts/admin-cli.md)의 명령 7개(`source create`, `permission grant`·`revoke`·`list`, `llm-usage set`·`unset`·`show`)가 계약의 출력 키 집합·exit code(0/2/3/4/5)·표준 오류 규칙을 지킨다.
  - 파일: `src/omf_retrieval/interfaces/cli/admin.py`(신규), `src/omf_retrieval/interfaces/cli/main.py`(수정: `admin` 하위 명령 등록), `tests/contract/cli/test_admin_commands.py`(신규)
  - 배정 테스트: `C-CLI-01` `C-CLI-02` `C-CLI-03` `C-CLI-05`
  - 규칙: 성공 출력은 한 줄 JSON(키 정렬, 공백 없는 구분자). 변경 명령은 `--actor` 필수(1~100자, 공백만 불가), 기록은 `"<actor> (os:<user>)"`. 실패 시 고정 문장과 사유 코드만, DB URL·토큰·파일 이름·스택 추적 없음(FR-034). 표시 변경 잠금 대기 상한 기본 30초 초과 시 exit 4(이 작업은 코드 기본값 30초를 쓰고 테스트한다. `settings.py` 노출은 T011, Q-08).
  - TDD: RED — CLI 러너와 가짜 관리 서비스로 4개 테스트 작성, `admin` 명령 부재로 assertion 실패 확인. GREEN — 최소 구현. REFACTOR 후 재통과.
  - 선행: T003 | 예상 줄 수: ~170 | 외부 의존: 없음
  - 실행 Agent 검증: `uv run ruff check src tests && uv run pytest tests/contract/cli -q`(기존 `test_mvp_commands.py` 포함 통과)

**Checkpoint**: 관리 경로가 자료 테이블 없이 독립 검증된다.

---

## Phase 4: Foundational 2 — 자료 저장 구조와 내용 확인기

**Purpose**: 등록·되찾기·삭제가 공유하는 자료 테이블과, 모든 등록이 쓰는 내용 확인기.

- [ ] T005 자료·revision·원본·스키마 사전 테이블 마이그레이션 `migrations/versions/0005_material_registration.py`를 만든다(plan P2b, 위험 격리)
  - 의도: data-model 3.5~3.14의 새 테이블 10개와 CHECK·불변 트리거가 생기고, 기존 테이블과 0004 테이블은 바뀌지 않는다.
  - 파일: `migrations/versions/0005_material_registration.py`(신규, `revision = "0005_material_registration"`, `down_revision = "0004_registration_access"`), `src/omf_retrieval/infrastructure/database/models_registration.py`(수정: 10개 테이블 ORM 추가), `tests/unit/registration/test_registration_migrations.py`(수정: U-MIG-02), `tests/integration/database/test_constraints.py`(I-DB-03 추가), `tests/integration/database/schema_expectations.py`·`orm_parity.py`·`test_migrations.py`·`test_registration_triggers.py`(I-DB-01·I-DB-02 확장)
  - 배정 테스트: `U-MIG-02` `I-DB-03`
  - 확장(배정 아님): I-DB-01(새 테이블 14개 전체, downgrade 범위) — 배정 T002. I-DB-02(스키마 UPDATE·DELETE 거부, revision `available→deleted`만, 파일 `content_hash` NULL 전이만, 등록 기록·삭제 기록 UPDATE·DELETE 거부) — 배정 T002. (Q-01)
  - 제약(data-model 원문): `schema_id` "PK, `^[A-Za-z0-9_-]{1,20}$`(대소문자 구분, `COLLATE "C"`)"; "`is_platform_default = false`인 행은 `schema_id`가 `default-`로 시작할 수 없다(CHECK)"; `target_format IN ('xlsx','pdf','text','image_bundle')`; `material_key` "DR-08 규칙 CHECK, `COLLATE "C"`", 유일 `(source_profile_id, material_key)`; `composition IN ('single','image_bundle','embedded_image_bundle')`; `state IN ('available','deleted')`; `file_index` "`0..20`"; `role IN ('primary','bundle_target','info')`; `detected_format` "NOT NULL, `IN ('xlsx','pdf','png','jpeg','text','markdown')`. 불변(UPDATE 거부)"; `pdf_text_layer` "PDF만 `IN ('full','partial','none')`, 그 외 NULL"; `byte_size >= 1`; `human_authored_declared` "`CHECK (human_authored_declared)`"; `location` integer(시트 이름 컬럼 없음); `relation_kind IN ('embedded_image','page_without_text_layer')`; `image_count` "`embedded_image`이면 `>= 1`, 그 외 NULL"; `submitted_filename` "삭제 후 NULL, 최대 255자"; `revision_file_blobs`는 INSERT와 삭제 트랜잭션의 DELETE만, UPDATE 거부. 각 테이블의 상태 전이 제한 트리거는 data-model 3.5~3.14 문장 그대로.
  - TDD: RED — U-MIG-02 작성, 파일 부재로 assertion 실패 확인(`markdown` CHECK, 정수 `location`, 시트 이름 컬럼 없음, 앱 코드 import 없음 포함). I-DB-03과 I-DB 확장 사례는 작성만(Q-04). GREEN — 마이그레이션·ORM. REFACTOR 후 재통과.
  - 선행: T002, T003(T003이 I-DB-04 확장 사례로 같은 `tests/integration/database/test_registration_triggers.py`를 고친다. 파일이 겹쳐 T003 뒤에 실행하며 `[P]`가 아니다) | 예상 줄 수: ~400(목표 상한 근처. 테이블 10개와 트리거가 한 revision이어야 downgrade·체인 검증이 한 번에 되므로 더 나누지 않는다. 500줄 초과가 보이면 멈추고 보고) | 외부 의존: I-DB-*는 PostgreSQL, 검증 Agent 실행
  - 실행 Agent 검증: `uv run ruff check src tests && uv run pytest tests/unit/registration/test_registration_migrations.py -q`
  - 검증 Agent 추가: `uv run pytest tests/integration/database -q`

- [ ] T006 내용 확인기 1(형식 판정·선언 대조·손상·암호·빈 파일·텍스트 해석)을 `src/omf_retrieval/infrastructure/registration/inspector.py`에 구현한다(plan P4a)
  - 의도: 파일 하나를 받아 research R-04 ⑧ `file` 파트별 확인 순서 1~7단계대로 첫 위반 사유 또는 형식 판정 결과(`xlsx`·`pdf`·`png`·`jpeg`·`text`·`markdown`)를 결정적으로 낸다.
  - 파일: `src/omf_retrieval/infrastructure/registration/__init__.py`·`inspector.py`(신규), `src/omf_retrieval/application/registration/ports.py`(수정: `ContentInspector` Protocol), `pyproject.toml`·`uv.lock`(수정: `pypdf`, `defusedxml` 추가, DR-04 승인), `tests/unit/registration/samples.py`(신규, 합성 표본 생성 도우미), `tests/unit/registration/test_inspector_format.py`(U-INSP-01), `tests/unit/registration/test_inspector_limits.py`(U-INSP-02), `tests/unit/registration/test_inspector_text.py`(U-INSP-03)
  - 배정 테스트: `U-INSP-01` `U-INSP-02` `U-INSP-03`
  - 승인 규칙(바꾸지 않는다): 확인 순서 1 빈 파일 → 2 서명(ZIP `PK\x03\x04`, CFB, `%PDF-`, PNG 8바이트 서명 + 첫 청크 IHDR, JPEG SOI `FF D8`, UTF-8 텍스트 순, 첫 해당 계열) → 3 선언 대조(Content-Type이 `application/octet-stream`이거나 없으면 확장자만) → 4 (선언 xlsx + ZIP) 용기 해석 → 5 항목 수 → 6 OOXML 패키지 판정 → 7 손상·구조. PNG 서명 뒤 첫 청크가 IHDR가 아니면 `unsupported_file_format`(8바이트 서명 직후 잘린 PNG 포함. `U-INSP-01` 실패 사례로 확인, Q-07 (a)). **IEND 없는 PNG, EOI 없는 JPEG는 서명 판정을 통과하고 7단계에서 `unreadable_content`(사용자 승인 2026-11-10)**. xlsx ZIP 경계(선언 xlsx만): ZIP 서명이나 해석 불가 → `unreadable_content`, ZIP이나 유효 OOXML 아님 → `unsupported_file_format`, 서명 없는 알 수 없는 바이트 → `unsupported_file_format`. 선언 `.xlsx` + CFB는 암호화 패키지(`EncryptionInfo`·`EncryptedPackage`)만 `unreadable_content`, 그 밖 `unsupported_file_format`. ZIP·모든 압축 파일 → `unsupported_file_format`(DR-03 추가 결정). 텍스트 하위 형식은 선언으로(`.md`·`text/markdown` → `markdown`, `.txt`·`text/plain` → `text`), 확장자와 Content-Type이 서로 다른 하위 형식이면 `unsupported_file_format`(DR-23). 안전 상한 기본값: 압축 해제 총량 536,870,912바이트, 항목별 비율 100(양의 정수, `해제 바이트 ≤ 비율 × 압축 바이트`, 0/0 통과), 항목 10,000개, PDF 2,000쪽, 확인 시간 30초. 같으면 통과·넘으면 `unreadable_content`. 총량·비율은 실제로 풀며 센 바이트로 판정, 시각 공급원 `clock` 주입(기본 `time.monotonic`). 확인기는 바이트를 바꾸지 않는다.
  - 설정값(Q-08): 이 작업은 `settings.py`를 바꾸지 않는다. 안전 상한은 확인기 설정 객체의 코드 기본값으로 두고 U-INSP-02가 그 기본값을 확인한다. `Settings` 노출과 운영 조립 연결은 T011이 한다.
  - TDD: RED — 3개 테스트를 plan 표 사례 그대로 작성(U-INSP-01의 서명 직후 잘린 PNG 사례, U-INSP-02의 비율 두 층 (a) 정수 판정 함수, (b) 실제 deflate fixture 포함), 확인기 부재로 assertion 실패 확인. GREEN — 최소 구현. REFACTOR 후 재통과.
  - 선행: T001(사유 코드), T003(`ports.py` 생성. 파일이 겹쳐 `[P]` 아님) | 예상 줄 수: ~350 | 외부 의존: 없음
  - 실행 Agent 검증: `uv run ruff check src tests && uv run pytest tests/unit/registration/test_inspector_format.py tests/unit/registration/test_inspector_limits.py tests/unit/registration/test_inspector_text.py -q`

- [ ] T007 내용 확인기 2(PDF 글자 층, 포함 이미지 관계, 실행·OCR 없음)를 `src/omf_retrieval/infrastructure/registration/inspector.py`에 더한다(plan P4b)
  - 의도: PDF마다 `pdf_text_layer`(`full`·`partial`·`none`)와 `page_without_text_layer` 행, xlsx·PDF의 포함 이미지 관계(파일 × 위치 단위, `xlsx_sheet` 시트 순번 0부터·`pdf_page` 페이지 1부터, `image_count`)를 내고, 시트 이름·글자 정보를 결과에 넣지 않으며, 외부 프로그램·소켓을 호출하지 않는다.
  - 파일: `src/omf_retrieval/infrastructure/registration/inspector.py`(수정), `tests/unit/registration/samples.py`(수정), `tests/unit/registration/test_inspector_pdf.py`(U-INSP-04), `tests/unit/registration/test_inspector_images.py`(U-INSP-05), `tests/unit/registration/test_inspector_safety.py`(U-INSP-06)
  - 배정 테스트: `U-INSP-04` `U-INSP-05` `U-INSP-06`
  - 규칙: DR-14 승인안(파일 × 위치 단위, 시트 이름 저장 안 함), R-09·R-11, FR-017(OCR 없음). drawing 관계가 끊긴 xlsx → `unreadable_content`. 글자 연산자만 있고 폰트 없는 페이지의 판정은 U-INSP-04에서 고정한다.
  - TDD: RED — 3개 테스트 작성, 해당 판정 부재로 assertion 실패 확인(U-INSP-06은 subprocess·socket monkeypatch). GREEN — 최소 구현. REFACTOR 후 재통과.
  - 선행: T006 | 예상 줄 수: ~300 | 외부 의존: 없음
  - 실행 Agent 검증: `uv run ruff check src tests && uv run pytest tests/unit/registration -k inspector -q`

**Checkpoint**: 자료 테이블과 내용 확인기가 준비되었다.

---

## Phase 5: User Story 3·4 — 스키마 검증·맞음 판정·사전·기본 스키마 (Priority: P2)

**Goal**: v1 스키마 구조 검증과 파일 맞음 판정, ID가 곧 버전인 append-only 사전, 텍스트용 기본 스키마 `default-text-v1`.

**Independent Test**: 가짜 사전과 합성 표본으로 U-SCH-01~04를 실행한다.

- [ ] T008 [US3] 스키마 구조 검증과 맞음 판정을 `src/omf_retrieval/application/registration/schema_dictionary.py`에 구현한다(plan P5 중 검증·판정)
  - 의도: `omf-registration-schema/v1` JSON이 닫힌 구조로 검증되어 누락 부분을 `missing_parts`로 내고, 파일·묶음과 스키마의 맞음 여부를 `schema_mismatch`(+ `expected_target_format`, 고정 키·위치 번호뿐인 `schema_mismatch_part`)로 판정한다.
  - 파일: `src/omf_retrieval/application/registration/schema_dictionary.py`(신규, 검증·판정 부분), `src/omf_retrieval/infrastructure/registration/inspector.py`(수정: 맞음 판정용 구조 값 읽기 — 시트 이름, 스키마가 지정한 셀 값, 정보 자료 제목 줄. Q-06), `tests/unit/registration/test_schema_validation.py`(U-SCH-01), `tests/unit/registration/test_schema_match.py`(U-SCH-02), `tests/unit/registration/test_architecture.py`(수정)
  - 배정 테스트: `U-SCH-01` `U-SCH-02`
  - 확장(배정 아님): U-ARCH-01(배정 T001) — 검사 대상 목록에 `application/registration/schema_dictionary.py`를 더한다(Q-09).
  - 규칙(DR-06 원문): "최상위 키는 정확히 `format`·`concept`·`split_rule`·`mapping`. `concept`는 공백 아닌 문자열. `split_rule` = `{target_format ∈ {xlsx, pdf, text, image_bundle}, kind, params}`이며 kind는 형식별 닫힌 목록(xlsx `sheet_rows`, pdf `page`·`page_range`, text `heading`·`paragraph`, image_bundle `info_heading`·`info_paragraph`), params는 kind별 닫힌 구조. `target_format: text`는 판정 형식 `text`·`markdown` 모두에 맞는다. `mapping`은 `{entities, relations}` 또는 `"none"`." 검증은 pydantic `extra="forbid"`(새 의존성 없음). 묶음 맞음 판정: 이미지 묶음은 정보 자료 기준·`image_bundle`, 그림 포함 파일 묶음은 대상 파일 기준(DR-15). 구조 값은 그 요청의 비교에만 쓰고 저장·응답·로그에 남기지 않는다. 시트 이름 비교는 정확 비교.
  - TDD: RED — 2개 테스트 작성, 검증기·판정 부재로 assertion 실패 확인. GREEN — 최소 구현. REFACTOR 후 재통과.
  - 선행: T007 | 예상 줄 수: ~260 | 외부 의존: 없음
  - 실행 Agent 검증: `uv run ruff check src tests && uv run pytest tests/unit/registration/test_schema_validation.py tests/unit/registration/test_schema_match.py tests/unit/registration/test_architecture.py -q`
  - 반영: Q-06(T008이 구조 값 읽기를 맡음) — 사용자 승인 2026-11-10

- [ ] T009 [US3] 스키마 사전(같은 내용 재사용·충돌·예약 접두사)과 기본 스키마 상수를 `src/omf_retrieval/application/registration/schema_dictionary.py`, `src/omf_retrieval/infrastructure/registration/default_schema.py`에 구현한다(plan P5 중 사전·기본 스키마, US4 포함)
  - 의도: 같은 ID에 같은 바이트면 재사용, 다른 바이트(끝 줄바꿈 하나 차이 포함)면 `schema_id_conflict`, 사용자의 `default-` 접두사 새 등록은 `schema_id_conflict`. 기본 스키마 `default-text-v1`이 자기 검증기로 유효하고 멱등 등록되며 같은 ID·다른 바이트가 있으면 시작 실패 오류.
  - 파일: `src/omf_retrieval/application/registration/schema_dictionary.py`(수정: 사전 부분), `src/omf_retrieval/application/registration/ports.py`(수정: 스키마 저장소 Protocol), `src/omf_retrieval/infrastructure/registration/default_schema.py`(신규), `tests/unit/registration/test_schema_dictionary.py`(U-SCH-03), `tests/unit/registration/test_default_schema.py`(U-SCH-04)
  - 배정 테스트: `U-SCH-03` `U-SCH-04`
  - 규칙(DR-07 원문): "ID `default-text-v1`, 내용 `concept` "구조 정의 없는 텍스트 파일", `split_rule` `{target_format: text, kind: heading, params: {}}`, `mapping` `"none"`. 접두사 `default-`는 플랫폼 예약". 같은 내용 = 업로드 바이트 SHA-256(DR-06). 스키마 사전은 수정·삭제 경로가 없다(FR-015).
  - TDD: RED — 2개 테스트를 가짜 저장소로 작성, 사전·상수 부재로 assertion 실패 확인. GREEN — 최소 구현. REFACTOR 후 재통과.
  - 선행: T008, T005 | 예상 줄 수: ~140 | 외부 의존: 없음
  - 실행 Agent 검증: `uv run ruff check src tests && uv run pytest tests/unit/registration/test_schema_dictionary.py tests/unit/registration/test_default_schema.py -q`

**Checkpoint**: 스키마 판정·사전이 서비스 없이 독립 검증된다.

---

## Phase 6: User Story 1 — 자료를 등록하고 revision을 받는다 (Priority: P1) 🎯 MVP

**Goal**: 인증된 등록자가 REST API로 파일 1개 또는 묶음 1개를 등록해 불변 revision을 받거나, 고칠 수 있는 사유로 거부되고 아무것도 남지 않는다.

**Independent Test**: 가짜 저장소·실제 확인기로 판정 순서와 동일 재등록을 단위 검증하고, 가짜 서비스 주입 앱으로 계약 검증한다. 원자성·동시성·재시작은 PostgreSQL 통합 테스트(검증 Agent).

- [ ] T010 [US1] 등록 서비스(판정 순서, 동일 재등록, 원자적 저장, 동시성)와 쓰기 저장소를 `src/omf_retrieval/application/registration/service.py`, `src/omf_retrieval/infrastructure/database/repository_registration.py`에 구현한다(plan P6)
  - 의도: 등록 요청이 research R-04 ①~⑬ 순서로 판정되고(인증·권한이 형식·스키마·충돌보다 먼저), 현재본과 `manifest_hash`가 같으면 `reused`, 그 밖은 새 revision(`created`, sequence +1)이 되며, 한 트랜잭션에서 스키마·revision·파일·바이트·묶음·관계·등록 기록·현재본이 함께 커밋되거나 전부 되돌려진다.
  - 파일: `src/omf_retrieval/application/registration/service.py`(신규), `src/omf_retrieval/application/registration/ports.py`(수정: 등록 저장소·`BlobStore` Protocol), `src/omf_retrieval/infrastructure/database/repository_registration.py`(신규, 쓰기 경로·스키마 INSERT/재사용·advisory lock), `tests/unit/registration/test_service_order.py`(U-SVC-01, U-ORDER-01), `tests/unit/registration/test_service_reregistration.py`(U-SVC-02), `tests/unit/registration/test_service_no_network.py`(U-SVC-03), `tests/integration/registration/__init__.py`·`conftest.py`(신규, PostgreSQL·`omf` 행 fixture), `tests/integration/registration/test_registration_atomicity.py`(I-REG-01), `tests/integration/registration/test_registration_concurrency.py`(I-REG-02), `tests/integration/registration/test_registration_restart.py`(I-REG-03), `tests/unit/registration/test_schema_input.py`(수정, U-RULE-03 서비스 수준 확장), `tests/unit/registration/test_architecture.py`(수정, U-ARCH-01 대상 목록 확장)
  - 배정 테스트: `U-SVC-01` `U-SVC-02` `U-SVC-03` `U-ORDER-01` `I-REG-01` `I-REG-02` `I-REG-03`
  - 확장(배정 아님): U-RULE-03(배정 T001) — 서비스가 FR-012 입력 표를 같은 결과로 적용하는지 서비스 수준 사례를 더한다(Q-01). U-ARCH-01(배정 T001) — 검사 대상 목록에 `application/registration/service.py`를 더한다(`tests/unit/registration/test_architecture.py` 수정, Q-09).
  - 규칙: R-05(트랜잭션 하나, `(source, material_key)` advisory lock, 새 스키마는 PK 충돌 뒤 내용 비교), R-06, data-model 4.2(현재본 표)·6절. ⑧ 단계는 `file` 파트 순서(0부터) → `info` 파트(⑧-i 0바이트·BOM 뺀 공백뿐 → `info_material_missing`, ⑧-ii `text`·`markdown` 아님 → `invalid_info_material_format`). 텍스트·Markdown 단일 파일은 스키마 지정 금지 → 기본 스키마(⑩). `file_index`·제출 이름 대응은 T001 정렬 규칙(바이트가 같으면 multipart 파트 순서, `reused`이면 그 요청의 파트 순서로 기존 번호에 대응, `manifest_hash`에는 영향 없음). 이 사례는 `U-ORDER-01`에 추가된 사례로 확인한다(Q-07 (b)). 내부 실패는 `registration_unavailable`. 등록 경로는 네트워크를 호출하지 않는다(R-09). application은 infrastructure·`Settings`를 import하지 않는다.
  - TDD: RED — U-SVC-01~03·U-ORDER-01(같은 바이트 파일 번호 사례 포함)을 가짜 저장소로 작성, 서비스 부재로 assertion 실패 확인. I-REG-01~03은 작성만(Q-04). GREEN — 최소 구현. REFACTOR 후 재통과.
  - 선행: T009, T003(쓰기 권한 판정 `access.py`) | 예상 줄 수: ~400(목표 상한 근처. 저장소만 떼면 RED 가능한 단위 테스트가 없는 작업이 생겨 나누지 않는다. 500줄 초과가 보이면 멈추고 보고) | 외부 의존: I-REG-*는 PostgreSQL, 검증 Agent 실행
  - 실행 Agent 검증: `uv run ruff check src tests && uv run pytest tests/unit/registration -q`
  - 검증 Agent 추가: `uv run pytest tests/integration/registration -k "atomicity or concurrency or restart" -q`

- [ ] T011 [US1] 등록 HTTP 경로 `POST /v1/registrations`(multipart 수신, 크기 계수, 오류 매핑, 독립·조건부 조립)를 `src/omf_retrieval/interfaces/api/routes/registration.py`에 구현한다(plan P7)
  - 의도: 등록 서비스가 주입된 앱에서만 등록 경로가 생기고, 선언 검증 없이 핸들러가 판정 순서대로 계약의 상태·`code`·`detail`을 내며, 상한 초과 즉시 수신을 멈춘다. 운영 조립은 검색 정책 resolve 실패 분기에서도 등록 서비스를 주입한다.
  - 파일: `src/omf_retrieval/interfaces/api/routes/registration.py`(신규, POST 부분), `src/omf_retrieval/interfaces/api/registration_schemas.py`(신규, `RegistrationResult`·`RegistrationError` 응답 모델), `src/omf_retrieval/interfaces/api/app.py`(수정: 선택 인자와 조건부 경로 등록. 기존 3경로 불변), `src/omf_retrieval/interfaces/api/runtime.py`(수정: 정상 분기와 `_UnavailableSearchService` 분기 둘 다 등록 서비스 주입, 기본 스키마 멱등 등록 호출), `src/omf_retrieval/settings.py`(수정: 파일당 50 MiB·요청 100 MiB 기본 상한, 확인기 상한, 기본 스키마 ID, 관리자 CLI 표시 변경 잠금 대기 상한(기본 30초, contracts/admin-cli.md). `settings.py`를 바꾸는 유일한 작업이며 이전 작업은 코드 기본값으로 테스트했다. 설정 기본값은 T006 확인기 기본값·DR-02·DR-07 값·T004 잠금 대기 코드 기본값(30초)과 같아야 한다. Q-08), `pyproject.toml`·`uv.lock`(수정: `python-multipart`, DR-05 승인), `tests/unit/registration/test_http_size.py`(U-HTTP-01), `tests/contract/api/test_registration_api.py`(신규)
  - 배정 테스트: `U-HTTP-01` `C-API-01` `C-API-02` `C-API-04` `C-API-06` `C-API-09` `C-API-11`
  - C-API-01·C-API-09는 이 작업에서 등록 경로 사례만 쓴다. 되찾기·현재본·스키마 경로 사례는 T013 확장(Q-01). C-API-11은 이 작업에서 (경로, 메서드) 집합이 정확히 기존 3개 + `POST /v1/registrations` = 4경로임을 운영 조립의 정상 분기와 검색 정책 resolve 실패 분기 둘 다에서 확인하고, C-API-06은 등록 서비스 주입 시 등록 경로 1개만 추가되고 기존 3경로 응답이 같음을 확인한다. 8경로·5개 추가로 넓히는 일은 T013 확장(Q-10).
  - 규칙: 매개변수·multipart 필드는 문자열·문자열 목록·원시 요청으로만 선언(pattern·길이·개수·필수·enum 없음, R-04). 오류 본문 `{request_id, code, message}`(+`detail`), 401에 `WWW-Authenticate: Bearer`, 메시지는 고정 영어 문장(DR-16). 없는 source·`omf`·권한 없는 `factory`는 같은 403(FR-002). 거부 운영 로그는 `request_id`·`key_id`(인증 성공 시)·사유 코드·시각만(R-08). 업로드 임시 파일은 요청 종료 시 삭제(DR-17 구현 조건). 공개 `/openapi.json`은 계속 404. 기존 `RequestValidationError` 처리기와 `/v1/search` 422 동작 불변.
  - TDD: RED — U-HTTP-01과 계약 테스트를 가짜 서비스 주입으로 작성, 경로·계수기 부재로 assertion 실패 확인. C-API-06은 기존 계약 테스트의 무수정 통과와 주입 시 기존 3경로 응답 동일을 확인하는 회귀 성격이다. GREEN — 최소 구현. REFACTOR 후 재통과.
  - 선행: T010 | 예상 줄 수: ~350 | 외부 의존: 없음
  - 실행 Agent 검증: `uv run ruff check src tests && uv run pytest tests/unit/registration/test_http_size.py tests/contract/api -q`(기존 `test_search_api.py`·`test_web_delivery.py` 무수정 통과 포함)

**Checkpoint**: 등록이 HTTP로 동작하고 기존 검색 계약이 그대로다.

---

## Phase 7: User Story 1·2 — 원본·등록 기록·현재본·스키마를 되찾는다 (Priority: P1)

**Goal**: 효력 있는 원본 읽기 권한자만 원본과 등록 기록, 현재본을 되찾고, 그 밖은 없는 revision과 구별되지 않는다. 쓰기 권한자는 스키마를 조회한다.

**Independent Test**: 되찾기 서비스 단위 테스트와 계약 테스트. 표시 변경과 진행 중 되찾기의 순서는 PostgreSQL 통합 테스트(검증 Agent).

- [ ] T012 [US1] 되찾기 서비스와 읽기 저장소(호출자 표시 행 `FOR SHARE`, 효력 있는 권한 재확인)를 `src/omf_retrieval/application/registration/retrieval.py`, `src/omf_retrieval/infrastructure/database/repository_registration.py`에 구현한다(plan P8 중 application·저장소)
  - 의도: 등록 기록·원본 바이트·현재본 조회가 한 트랜잭션에서 표시 행 공유 잠금 → 권한 판정 → 읽기 순서로 처리되고, 없음·삭제됨·권한 없음·표시 `external`·없음이 같은 "없음" 결과가 되며, 현재본 삭제 뒤 `no_current_revision`을 내고 직전 revision으로 올리지 않는다.
  - 파일: `src/omf_retrieval/application/registration/retrieval.py`(신규), `src/omf_retrieval/application/registration/ports.py`(수정: 읽기 저장소 Protocol), `src/omf_retrieval/infrastructure/database/repository_registration.py`(수정: 읽기 경로), `tests/unit/registration/test_retrieval.py`(U-RET-01), `tests/integration/registration/test_retrieval_llm_usage_lock.py`(I-REG-04), `tests/unit/registration/test_architecture.py`(수정)
  - 배정 테스트: `U-RET-01` `I-REG-04`
  - 확장(배정 아님): U-ARCH-01(배정 T001) — 검사 대상 목록에 `application/registration/retrieval.py`를 더한다(Q-09).
  - 규칙: DR-11(a) 승인안(변경 전 시작한 되찾기는 끝까지, 변경 커밋 뒤 시작은 거부), DR-13(쓰기 권한만인 호출자도 404), FR-039, data-model 6절.
  - TDD: RED — U-RET-01을 가짜 저장소로 작성, 서비스 부재로 assertion 실패 확인. I-REG-04는 작성만(Q-04). GREEN — 최소 구현. REFACTOR 후 재통과.
  - 선행: T011, T003 | 예상 줄 수: ~150 | 외부 의존: I-REG-04는 PostgreSQL, 검증 Agent 실행
  - 실행 Agent 검증: `uv run ruff check src tests && uv run pytest tests/unit/registration/test_retrieval.py tests/unit/registration/test_architecture.py -q`

- [ ] T013 [US1] 되찾기·등록 기록·현재본·스키마 조회 HTTP 경로 4개를 `src/omf_retrieval/interfaces/api/routes/registration.py`에 더한다(plan P8 중 HTTP)
  - 의도: `GET /v1/revisions/{revision_id}`, `GET /v1/revisions/{revision_id}/files/{file_index}`, `GET /v1/materials/current`, `GET /v1/schemas/{schema_id}`가 계약대로 동작하고, 원본 읽기 권한이 필요 없는 응답에 파일 안 값이 없다.
  - 파일: `src/omf_retrieval/interfaces/api/routes/registration.py`(수정), `src/omf_retrieval/interfaces/api/registration_schemas.py`(수정: `RegistrationRecord`, `CurrentRevision`), `src/omf_retrieval/interfaces/api/runtime.py`(수정: 되찾기 서비스 주입), `tests/contract/api/test_registration_api.py`(수정)
  - 배정 테스트: `C-API-03` `C-API-05` `C-API-07` `C-API-08` `C-API-10`
  - 확장(배정 아님): C-API-01(되찾기·현재본·스키마 경로의 OpenAPI 대조) — 배정 T011. C-API-09(`/v1/materials/current`의 401·422·404 순서, 숫자 아닌 `file_index`의 401·404) — 배정 T011. (Q-01) C-API-11(경로 집합 기대를 GET 4개를 더한 8경로로 넓힘, 정상·정책 실패 분기 둘 다) — 배정 T011. C-API-06(주입 시 추가 경로 기대를 5개로 넓힘) — 배정 T011. (Q-10)
  - 규칙: 원본 응답은 `application/octet-stream`, `X-Content-Type-Options: nosniff`, `X-Content-SHA256`, `Content-Disposition: attachment; filename*=UTF-8''<RFC 5987>`(R-07). `file_index`가 0~20 정수가 아니면 인증 뒤 404 `revision_not_found`. `/v1/materials/current`는 매개변수 없음·빈 문자열이면 인증 뒤 422 `invalid_request`, 권한 없음·없는 자료는 404 `material_not_found`(403 없음). 스키마 조회는 쓰기 권한 없음 → 403 `schema_access_denied`(ID 존재와 무관), 있음+없는 ID → 404 `schema_not_found`. 호출자 표시 변경 경로를 만들지 않는다(FR-045). C-API-10의 표식 세 종류(M-파일·M-참조·M-스키마)와 스키마 조회 예외는 plan 표 그대로.
  - TDD: RED — 5개 테스트와 확장 사례를 가짜 서비스 주입으로 작성, 경로 부재로 assertion 실패 확인(C-API-07은 후보 경로가 404·405임을 확인). GREEN — 최소 구현. REFACTOR 후 재통과.
  - 선행: T012 | 예상 줄 수: ~250 | 외부 의존: 없음
  - 실행 Agent 검증: `uv run ruff check src tests && uv run pytest tests/contract/api -q`

**Checkpoint**: 등록과 되찾기가 HTTP로 끝까지 동작한다.

---

## Phase 8: User Story 2 — 관리자가 revision을 완전 삭제하고 기록을 확인한다 (Priority: P1)

**Goal**: 관리자만 서버 관리 경로로 revision을 완전 삭제하고, 삭제 기록과 남은 항목을 확인한다.

**Independent Test**: CLI 계약 테스트와 PostgreSQL 통합 테스트(검증 Agent).

- [ ] T014 [US2] 완전 삭제·삭제 기록·관리자 기록 조회(`admin revision delete|show`, `admin deletion-record list`)를 `src/omf_retrieval/application/admin/registration_admin.py`, `src/omf_retrieval/interfaces/cli/admin.py`에 구현한다(plan P9)
  - 의도: 삭제가 한 트랜잭션에서 바이트·묶음·관계 행을 지우고, 파일 이름·내용 해시·`manifest_hash`를 NULL로 바꾸고, 해시를 담은 삭제 기록 1건을 남기며, 현재본이면 현재본 없음으로 바꾸고, 스키마는 남긴다. `revision show`는 파일 이름·해시 없이 남은 항목과 삭제 기록을 보인다.
  - 파일: `src/omf_retrieval/application/admin/registration_admin.py`(수정), `src/omf_retrieval/infrastructure/database/repository_registration_admin.py`(수정), `src/omf_retrieval/interfaces/cli/admin.py`(수정), `tests/contract/cli/test_admin_commands.py`(수정), `tests/integration/registration/test_revision_deletion.py`(신규)
  - 배정 테스트: `C-CLI-04` `I-REG-05`
  - 확장(배정 아님): C-CLI-05(배정 T004) — 새 명령 3개를 비밀값·원본 표식 비노출 대상 목록에 더한다(Q-02).
  - 규칙: DR-12 승인안(M1 해시 제거·삭제 기록에만 남김, M2 `admin revision show`, 물리 소거는 DR-17 이월). data-model 6절 삭제 순서. `--confirm` 없으면 exit 2, 없는 revision exit 5, 이미 삭제 exit 3 `already_deleted`. 호출자 HTTP에는 삭제·삭제 기록 경로가 없다(US2-15).
  - TDD: RED — C-CLI-04를 가짜 관리 서비스로 작성, 명령 부재로 assertion 실패 확인. I-REG-05는 작성만(Q-04). GREEN — 최소 구현. REFACTOR 후 재통과.
  - 선행: T013, T004 | 예상 줄 수: ~300 | 외부 의존: I-REG-05는 PostgreSQL, 검증 Agent 실행
  - 실행 Agent 검증: `uv run ruff check src tests && uv run pytest tests/contract/cli -q`

**Checkpoint**: 모든 기능 경로가 구현되었다.

---

## Phase 9: Polish & Cross-Cutting — 인수 하네스·MVP 격리·보안·저장 위치

**Purpose**: SC를 실제 HTTP 앱과 PostgreSQL로 판정하고, MVP 검색 불변·비밀값 비노출·저장 위치를 확인한다. 표본 규모는 DR-18.

- [ ] T015 인수 하네스 H-01~H-06(SC-001~006, SC-009~013)을 `tests/integration/registration/test_acceptance_*.py`에 작성한다(plan P10 중 기능 인수)
  - 의도: 형식별 등록·되찾기 일치, 16개 사유와 research R-04 예시 표 전부, 권한·표시 유형 전부, 스키마 재사용, 이미지 규칙, 완전 삭제가 SC 합격 기준대로 판정된다.
  - 파일: `tests/integration/registration/conftest.py`(수정: 실제 앱+PostgreSQL 하네스 fixture, 합성 표본), `tests/integration/registration/test_acceptance_reproduction.py`(H-01), `test_acceptance_rejections.py`(H-02), `test_acceptance_permissions.py`(H-03), `test_acceptance_schemas.py`(H-04), `test_acceptance_images.py`(H-05), `test_acceptance_deletion.py`(H-06)
  - 배정 테스트: `H-01` `H-02` `H-03` `H-04` `H-05` `H-06`
  - 규칙: 표본은 DR-18 승인 규모(SC-001 Excel 2·글자 층 PDF 2·이미지 묶음 2·텍스트 2(`.txt` 1, `.md` 1), SC-002 사유별 대표 1 + 경계 1 + R-04 예시 표 전부, SC-005 후속 10회 + 재시작 1회, SC-006 이미지 묶음 2·전체 스캔 PDF 1·그림 포함 Excel 1·그림 포함 PDF 1·일부 스캔 PDF 1). H-02의 SC-009 정성 검토 합격 예시는 "스키마 ID가 21자라 20자 이하로 줄여야 한다", 불합격 예시는 "등록 실패"만 있는 응답. 소요 시간은 측정·기록만 한다(DR-17).
  - TDD 예외(RED 선행 면제, 사용자 승인 2026-11-10, Q-04): 인수 하네스는 T001~T014에서 이미 구현된 동작을 SC 기준으로 판정하므로 구현 부재로 인한 RED가 생기지 않고, 실행 Agent는 PostgreSQL 컨테이너를 쓰지 않는다. 실행 Agent는 하네스를 작성하고 `ruff`와 수집(`--collect-only`)까지만 확인한다. 대신 검증 Agent가 컨테이너로 실행해 통과를 확인하고, 각 테스트가 가짜(stub)가 아니라 실제 HTTP 앱·서비스·PostgreSQL의 대상 동작을 호출하는지 코드로 확인한다. 의도적 고장·변이 단계는 두지 않는다.
  - 선행: T014 | 예상 줄 수: ~230 | 외부 의존: PostgreSQL(`compose.test.yaml`), 검증 Agent 실행
  - 실행 Agent 검증: `uv run ruff check src tests && uv run pytest tests/integration/registration --collect-only -q`
  - 검증 Agent 추가: `uv run pytest tests/integration/registration -k acceptance -q`, 각 테스트의 대상 동작 실호출 여부 코드 점검(Q-04)

- [ ] T016 MVP 격리·보안 하네스 H-07·H-08·I-REG-06과 저장 위치 점검(quickstart 4절)을 `tests/integration/registration/`에 작성·수행한다(plan P10 중 격리·보안)
  - 의도: 등록 전후 같은 색인·고정 질의의 검색 결과가 같고 MVP 테이블 행 수·해시가 그대로이며, 응답·로그에 비밀값·원본 표식이 없고 외부 연결 시도가 0건이며, 모든 저장이 플랫폼 PostgreSQL 안이고 업로드 임시 파일이 남지 않는다.
  - 파일: `tests/integration/registration/test_search_regression.py`(H-07, 기존 통합 테스트의 소형 MVP 색인 fixture 사용), `tests/integration/registration/test_acceptance_secrets.py`(H-08), `tests/integration/registration/test_mvp_isolation.py`(I-REG-06)
  - 배정 테스트: `H-07` `H-08` `I-REG-06`
  - 저장 위치 점검(FR-032): quickstart 4절 표 3항목(저장 위치, DB 밖 잔여 파일, 외부 전송)을 수행하고 결과를 보고에 적는다. 테스트 ID가 없는 점검 항목이다.
  - 규칙: H-08은 성공·거부 응답 각 16건 이상과 그 로그 전체, socket 차단, 원본 바이트와 파일 안 값(시트 이름·셀 값·제목)에 표식 문자열(원본 읽기 권한 경로 제외). I-REG-06 대상 테이블은 `index_runs`·`chunks`·`chunk_embeddings`·`client_source_grants`·`search_policy_manifests`.
  - TDD 예외(RED 선행 면제, 사용자 승인 2026-11-10, Q-04): 격리·보안 하네스는 이미 구현된 동작과 기존 MVP 검색의 불변을 확인하므로 구현 부재로 인한 RED가 생기지 않고, 실행 Agent는 PostgreSQL 컨테이너를 쓰지 않는다. 실행 Agent는 작성과 `ruff`·수집까지만 확인한다. 대신 검증 Agent가 컨테이너로 실행해 통과를 확인하고, 각 테스트가 가짜(stub)가 아니라 실제 검색·등록 경로, 실제 로그, 실제 MVP 테이블을 호출·조회하는지 코드로 확인한다. 의도적 고장·변이 단계는 두지 않는다.
  - 선행: T015, T014(H-07·H-08은 실제 HTTP 앱+PostgreSQL에서 응답·로그를 판정하므로 T015가 `tests/integration/registration/conftest.py`에 만드는 하네스 fixture(실제 앱, 합성 표본)를 그대로 쓴다. 같은 fixture를 따로 만들면 중복되고 conftest.py가 겹쳐 `[P]`가 아니다) | 예상 줄 수: ~150 | 외부 의존: PostgreSQL, 검증 Agent 실행
  - 실행 Agent 검증: `uv run ruff check src tests && uv run pytest tests/integration/registration --collect-only -q`
  - 검증 Agent 추가: `uv run pytest tests/integration/registration -k "search_regression or secrets or mvp_isolation" -q`, 대상 동작 실호출 여부 코드 점검(Q-04), quickstart 4절 점검, 최종 전체 `uv run pytest tests/unit tests/contract tests/integration -q`

---

## Dependencies & Execution Order

### 작업 의존 그래프

```text
T001 ──> T002 ──> T003 ──> T004 ─────────────────────────────────────────────> T014
                  T003 ──> T006 ──> T007 ──> T008 ──> T009 ──> T010 ──> T011 ──> T012 ──> T013 ──> T014 ──> T015 ──> T016
         T002 ──> T005 ──────────────────────────────> T009
                  T003 ──> T005
                  T003 ──────────────────────────────────────> T010
                  T003 ──────────────────────────────────────────────────────> T012
```

선행 관계 목록(본문 각 작업의 "선행"과 같다):

| 작업 | 선행 |
|---|---|
| T001 | — |
| T002 | T001 |
| T003 | T002 |
| T004 | T003 |
| T005 | T002, T003 |
| T006 | T001, T003 |
| T007 | T006 |
| T008 | T007 |
| T009 | T008, T005 |
| T010 | T009, T003 |
| T011 | T010 |
| T012 | T011, T003 |
| T013 | T012 |
| T014 | T013, T004 |
| T015 | T014 |
| T016 | T015, T014 |

- 기본 실행 순서(한 번에 한 작업): T001 → T002 → T003 → T004 → T005 → T006 → T007 → T008 → T009 → T010 → T011 → T012 → T013 → T014 → T015 → T016. plan의 Phase 순서(P1, P2a, P3, P2b, P4a, P4b, P5, P6, P7, P8, P9, P10)와 같다.
- T010이 T003을 선행으로 두는 것은 등록 서비스가 `access.py`의 쓰기 판정을 쓰기 때문이다. plan 순서상 P3이 P6보다 먼저라 순서 변화는 없다.

### User Story Dependencies

- US2(관리 경로, T003·T004)는 Foundational 뒤 자료 테이블 없이 독립 검증된다.
- US1(T010~T013)은 내용 확인기·스키마(T006~T009)와 접근 정책(T003)에 의존한다. 계층형 분할이라 US1 이전 단계가 US3·US4·US5의 판정 부품을 먼저 만든다.
- US3·US4(T008·T009)의 HTTP 수준 동작은 T011·T013 뒤에 확인된다(H-04, H-01 텍스트 표본).
- US5의 판정 부품인 T007(Foundational 단계라 스토리 라벨 없음)의 HTTP 수준 동작은 T011 뒤 H-05로 확인된다.

### Parallel Opportunities

- `[P]` 표시 작업: 없음. T005는 T003과 `tests/integration/database/test_registration_triggers.py`가 겹쳐 T003 뒤에 실행한다.
- T016은 T015의 하네스 fixture를 써서 `[P]`가 아니다.
- T006은 T001만 필요하지만 `ports.py`가 T003과 겹쳐 `[P]`가 아니다.
- 같은 테스트 파일을 고치는 작업(`test_registration_api.py`: T011·T013, `test_admin_commands.py`: T004·T014, `test_registration_migrations.py`: T002·T005, `test_registration_triggers.py`: T002·T003·T005)은 병렬로 돌리지 않는다.

## Parallel Example

현재 `[P]` 작업이 없어 병렬 예시가 없다. 모든 작업은 `Dependencies & Execution Order`의 기본 실행 순서대로 한 번에 하나씩 실행한다.

## Implementation Strategy

1. Foundational(T001·T002) → 관리 경로(T003·T004)로 인증 영역을 먼저 독립 검증한다.
2. 자료 저장·내용 확인·스키마(T005~T009)를 계층 순서로 만든다.
3. US1 MVP 증분: T010·T011로 등록, T012·T013으로 되찾기를 완성하면 "등록하고 원본을 그대로 되찾는다"를 HTTP로 시연할 수 있다.
4. T014로 완전 삭제, T015·T016으로 SC 인수와 MVP 격리·보안을 판정한다.
5. 각 작업 뒤 실행 Agent 보고와 별도 검증 Agent의 전체 검증을 거친다. 범위 변경·새 의사결정·검증 실패가 없으면 다음 작업으로 진행한다(`AGENTS.md`).

## 범위 밖(작업 없음)

- spec US2 시나리오 6 문구 정리(DR-13의 spec 수정)는 별도 작업이다(plan 범위 밖).
- 공유 서버 배포, 운영 미정 항목(DR-17), 색인·검색 반영(`006`), 파서(`003`)는 001 작업이 아니다.
- `.specify/feature.json`, `AGENTS.md`, `CLAUDE.md`는 이 작업 목록이 바꾸지 않는다.

---

## 추적표

### FR → 작업

| FR | 작업 | 근거 테스트(배정 작업) |
|---|---|---|
| FR-001 | T011, T015 | C-API-02(T011), H-01(T015) |
| FR-002 | T001, T010, T011 | U-SVC-01(T010), C-API-04(T011) |
| FR-003 | T006 | U-INSP-01(T006) |
| FR-004 | T006 | U-INSP-01(T006) |
| FR-005 | T006 | U-INSP-02(T006) |
| FR-006 | T011, T015 | U-HTTP-01(T011), C-API-02(T011) |
| FR-007 | T010, T015 | I-REG-01(T010), H-02(T015) |
| FR-008 | T001, T011, T015 | U-RULE-04(T001), C-API-02(T011), H-02(T015) |
| FR-009 | T001, T010 | U-RULE-03(T001) |
| FR-010 | T008 | U-SCH-01(T008) |
| FR-011 | T001, T005 | U-RULE-01(T001), I-DB-03(T005) |
| FR-012 | T001, T010 | U-RULE-03(T001) |
| FR-013 | T008 | U-SCH-02(T008) |
| FR-014 | T001, T009, T010 | U-RULE-03(T001), U-SCH-04(T009) |
| FR-015 | T002, T005, T009 | U-SCH-03(T009), I-DB-02(T002, T005 확장) |
| FR-016 | T003, T013 | U-ACC-02(T003), C-API-08(T013) |
| FR-017 | T007 | U-INSP-06(T007) |
| FR-018 | T001, T010 | U-COMP-02(T001), U-ORDER-01(T010) |
| FR-019 | T001, T007, T010 | U-INSP-04(T007), U-COMP-02(T001) |
| FR-020 | T007, T010, T015 | U-INSP-05(T007), H-05(T015) |
| FR-021 | T001, T010, T015 | U-COMP-02(T001), H-05(T015) |
| FR-022 | T005, T010 | U-SVC-02(T010), I-DB-02(T002, T005 확장) |
| FR-023 | T010, T012, T013, T015 | I-REG-03(T010), H-01(T015) |
| FR-024 | T010, T011, T013, T015 | C-API-02(T011), H-01(T015) |
| FR-025 | T005, T010, T015 | I-REG-03(T010), H-01(T015) |
| FR-026 | T014, T015 | I-REG-05(T014), C-CLI-04(T014), H-06(T015) |
| FR-027 | T011, T013 | C-API-02(T011) |
| FR-028 | T002, T003, T004 | U-ACC-01(T003), C-CLI-02(T004) |
| FR-029 | T003, T011, T015 | U-ACC-01(T003), H-03(T015) |
| FR-030 | T003, T015 | U-ADM-02(T003), H-03(T015) |
| FR-031 | T003, T013 | U-ACC-01(T003), U-ACC-02(T003), C-API-03(T013) |
| FR-032 | T016 | quickstart 4절 점검(T016) |
| FR-033 | T010, T016 | U-SVC-03(T010), H-08(T016) |
| FR-034 | T001, T011, T013, T016 | U-RULE-04(T001), C-API-10(T013), H-08(T016) |
| FR-035 | T011, T013, T016 | C-API-06(T011, T013 확장), C-API-11(T011, T013 확장), H-07(T016) |
| FR-036 | T002, T005, T016 | I-REG-06(T016), H-07(T016) |
| FR-037 | T001 | U-RULE-02(T001) |
| FR-038 | T001, T010, T015 | U-HASH-01(T001), U-SVC-02(T010), H-01(T015) |
| FR-039 | T012, T013, T015 | U-RET-01(T012), H-01(T015) |
| FR-040 | T003, T004, T011 | C-CLI-01(T004), C-API-04(T011) |
| FR-041 | T003, T004 | U-ADM-01(T003), U-ADM-02(T003), C-CLI-02(T004) |
| FR-042 | T010, T011, T013 | U-SVC-01(T010), U-ORDER-01(T010), C-API-09(T011, T013 확장) |
| FR-043 | T012, T013, T015 | C-API-03(T013), H-03(T015) |
| FR-044 | T001 | U-COMP-01(T001), U-COMP-02(T001) |
| FR-045 | T002, T003, T013 | U-ADM-03(T003), C-API-07(T013) |
| FR-046 | T002, T003, T015 | U-ADM-01(T003), I-DB-04(T002, T003 확장), H-03(T015) |
| FR-047 | T003, T012, T015 | U-ADM-03(T003), I-REG-04(T012), H-03(T015) |

### SC → 작업

| SC | 작업 | 테스트 |
|---|---|---|
| SC-001 | T015 | H-01 |
| SC-002 | T015 | H-02 |
| SC-003 | T015 | H-03 |
| SC-004 | T015 | H-04 |
| SC-005 | T015 | H-01 |
| SC-006 | T015 | H-05 |
| SC-007 | T016 | H-07 |
| SC-008 | T016 | H-08 |
| SC-009 | T015 | H-02(정성 검토) |
| SC-010 | T015 | H-01 |
| SC-011 | T015 | H-06 |
| SC-012 | T015 | H-03 |
| SC-013 | T015 | H-03 |

### 사용자 스토리 → 작업

스토리 라벨은 스토리 단계의 작업에만 붙인다. Foundational 단계 작업(T001·T002·T005·T006·T007)은 라벨이 없으며 기여한 스토리를 여기에 적는다.

| 스토리 | 라벨 작업 | 기여하는 Foundational 작업 | 인수 하네스 |
|---|---|---|---|
| US1 | T010, T011, T012, T013 | T001, T005, T006 | H-01, H-02, H-07(T015, T016) |
| US2 | T003, T004, T014 | T002 | H-03, H-06, H-08(T015, T016) |
| US3 | T008, T009 | T001, T005 | H-04(T015) |
| US4 | (T009가 기본 스키마 포함) | T006 | H-01 텍스트 표본(T015) |
| US5 | — | T007, T001 | H-05(T015) |

### 테스트 ID → 배정 작업(65건)

| 테스트 | 작업 | 테스트 | 작업 | 테스트 | 작업 |
|---|---|---|---|---|---|
| U-RULE-01 | T001 | U-SCH-01 | T008 | C-API-06 | T011 |
| U-RULE-02 | T001 | U-SCH-02 | T008 | C-API-07 | T013 |
| U-RULE-03 | T001 | U-SCH-03 | T009 | C-API-08 | T013 |
| U-RULE-04 | T001 | U-SCH-04 | T009 | C-API-09 | T011 |
| U-COMP-01 | T001 | U-ACC-01 | T003 | C-API-10 | T013 |
| U-COMP-02 | T001 | U-ACC-02 | T003 | C-API-11 | T011 |
| U-ORDER-01 | T010 | U-SVC-01 | T010 | C-CLI-01 | T004 |
| U-HASH-01 | T001 | U-SVC-02 | T010 | C-CLI-02 | T004 |
| U-INSP-01 | T006 | U-SVC-03 | T010 | C-CLI-03 | T004 |
| U-INSP-02 | T006 | U-RET-01 | T012 | C-CLI-04 | T014 |
| U-INSP-03 | T006 | U-ADM-01 | T003 | C-CLI-05 | T004 |
| U-INSP-04 | T007 | U-ADM-02 | T003 | I-DB-01 | T002 |
| U-INSP-05 | T007 | U-ADM-03 | T003 | I-DB-02 | T002 |
| U-INSP-06 | T007 | U-HTTP-01 | T011 | I-DB-03 | T005 |
| U-MIG-01 | T002 | C-API-01 | T011 | I-DB-04 | T002 |
| U-MIG-02 | T005 | C-API-02 | T011 | I-REG-01 | T010 |
| U-ARCH-01 | T001 | C-API-03 | T013 | I-REG-02 | T010 |
| H-01 | T015 | C-API-04 | T011 | I-REG-03 | T010 |
| H-02 | T015 | C-API-05 | T013 | I-REG-04 | T012 |
| H-03 | T015 | H-06 | T015 | I-REG-05 | T014 |
| H-04 | T015 | H-07 | T016 | I-REG-06 | T016 |
| H-05 | T015 | H-08 | T016 | | |

확장(배정 아님): U-RULE-03 → T010, C-API-01·C-API-09·C-API-06·C-API-11 → T013, I-DB-04 → T003, I-DB-01·I-DB-02 → T005, C-CLI-05 → T014, U-ARCH-01 → T003·T008·T010·T012(Q-01·Q-02·Q-09·Q-10·Q-11).

## Notes

- 작업 체크박스는 검증 Agent의 독립 검증 PASS 뒤에 표시한다. 실행 Agent는 합격 판정을 하지 않는다.
- 각 작업 뒤 `git diff`가 그 작업의 파일 목록 밖을 바꾸지 않았는지 검증 Agent가 확인한다.
- `docs/reports/아카이브.zip`과 OMF 저장소는 어떤 작업도 건드리지 않는다.
