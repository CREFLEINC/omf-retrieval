# Contract: 서버 관리 경로(관리자 CLI) — spec 001

**상태**: DR-10(서버에서 실행하는 typer CLI)과 DR-11(a)~(d) 승인(2026-11-10) 기준이다.

## 용어

| 용어 | 뜻 |
|---|---|
| 관리자 CLI | 서버 셸에서 `uv run omf-retrieval admin ...`(컨테이너에서는 `omf-retrieval admin ...`)로 실행하는 명령. 기존 `client create`와 같은 방식이다. |
| `--actor` | 실행자가 밝히는 관리자 이름. 기록에 남는다. 인증된 신원이 아니며 서버 셸 접근 통제가 관리자 인증의 실체다(DR-10). |
| key_id | 기존 호출자 토큰의 공개 식별 부분(16자 16진). 호출자를 지정할 때 쓴다. 토큰 비밀값은 받지 않는다. |
| 효력 있는 원본 읽기 권한 | 원본 읽기 권한 기록이 있고 외부 LLM 사용 표시가 `internal`인 상태. |
| exit code | 명령 종료 코드. 0 성공. 실패는 아래 표. |

## 공통 규칙

- 출력은 성공 시 표준 출력 한 줄 JSON(키 정렬, 공백 없는 구분자)이다. 기존 `client create` 관례와 같다.
- 실패 시 표준 오류에 고정 문장 한 줄과 사유 코드만 낸다. DB URL, 토큰, 파일 이름, 원본 내용, 스택 추적을 내지 않는다(FR-034).
- 변경 명령은 `--actor`(1~100자, 공백만 불가)를 필수로 받는다. 기록에는 `actor`와 운영체제 사용자 이름을 `"<actor> (os:<user>)"` 형식으로 남긴다.
- DB 연결은 기존 `OMF_RETRIEVAL_DATABASE_URL`(또는 비밀 파일 설정)을 쓴다.

| exit code | 뜻 |
|---|---|
| 0 | 성공 |
| 2 | 인자 오류(typer 기본) |
| 3 | 규칙상 거부(예: 이미 있는 source, 외부 LLM 사용 표시 사유). 아무것도 바뀌지 않았다. |
| 4 | 예기치 않은 실패. 트랜잭션은 되돌려졌다. |
| 5 | 대상 없음(호출자 key_id, revision이 없음) |

## 명령

### source

| 명령 | 동작 | 성공 출력 | 거부(exit 3) 사유 코드 | 대응 |
|---|---|---|---|---|
| `admin source create factory --actor A` | `source_profiles`에 `factory` 행과 `registration_sources` 표시를 만든다. | `{"source":"factory","registration_enabled":true}` | `source_exists`(이미 있음, 기존 source 불변), `source_name_not_allowed`(`factory` 외, DR-19) | FR-040, US2-13, Edge Case |

### permission

| 명령 | 동작 | 거부 사유 코드 | 대응 |
|---|---|---|---|
| `admin permission grant --client KEY_ID --source factory --kind write --actor A` | 활성 쓰기 권한 생성. 이미 활성이면 변경 없이 성공(`"changed":false`). | `source_not_registrable`(REST 등록 대상 아님, `omf` 포함) | FR-029, FR-041, US2-17 |
| `admin permission grant ... --kind read-original --actor A` | 표시가 `internal`일 때만 활성 원본 읽기 권한 생성. 표시 행을 공유 잠금으로 확인한다. | `llm_usage_not_internal`(표시가 `external`이거나 없음, 행 생성 없음, 다른 권한 불변), `source_not_registrable` | FR-046, US2-18~20, SC-012 |
| `admin permission revoke --client KEY_ID --source factory --kind write|read-original --actor A` | 활성 권한을 `admin_revoked`로 회수. 활성 권한이 없으면 `"changed":false`. | — | FR-030, US2-4, US2-9 |
| `admin permission list --client KEY_ID` | 활성·회수 권한과 검색 권한(source grant) 존재 여부, 효력 여부를 보인다. | — | 운영 확인 |

- 쓰기·원본 읽기 권한 명령은 검색 권한(`client_source_grants`)을 읽거나 바꾸지 않는다(FR-028).
- 성공 출력 예: `{"changed":true,"client_key_id":"0123456789abcdef","kind":"write","source":"factory","state":"active"}`.

### llm-usage

| 명령 | 동작 | 대응 |
|---|---|---|
| `admin llm-usage set --client KEY_ID --value internal|external --actor A` | 표시를 정한다. `internal`→`external`이면 같은 트랜잭션에서 활성 원본 읽기 권한 전부를 `llm_usage_changed`로 회수한다. 변경 기록을 남긴다. | FR-045, FR-047, DR-11(b)(d) |
| `admin llm-usage unset --client KEY_ID --actor A` | 표시를 없앤다(= `external` 간주). `internal`이었으면 위와 같이 회수한다. | FR-045, FR-047 |
| `admin llm-usage show --client KEY_ID` | 현재 표시(`internal`, `external`, `unset`)와 최근 변경 기록. | 운영 확인 |

- 성공 출력 예: `{"client_key_id":"0123456789abcdef","previous":"internal","current":"external","revoked_read_permissions":1}`.
- 진행 중 되찾기와의 순서(DR-11(a)): 표시 변경 트랜잭션은 되찾기 트랜잭션의 공유 잠금이 풀린 뒤 커밋된다. CLI는 잠금 대기 상한(설정값, 기본 30초)을 넘으면 exit 4로 끝나고 아무것도 바꾸지 않는다.

### revision

| 명령 | 동작 | 거부·없음 | 대응 |
|---|---|---|---|
| `admin revision delete REVISION_ID --actor A --confirm` | 완전 삭제. 원본 바이트·정보 자료·묶음·포함 이미지 관계 정보 삭제, 등록 기록의 파일 이름·내용 해시와 `manifest_hash` 제거(DR-12 M1), 삭제 기록 생성, 현재본이면 현재본 없음. 스키마는 그대로. `--confirm` 없으면 exit 2. | 없는 revision exit 5, 이미 삭제됨 exit 3 `already_deleted` | FR-026, US2-14, SC-011 |
| `admin revision show REVISION_ID` | 삭제 여부와 관계없이 남은 등록 기록 항목(source, 자료 ID, revision, sequence, 등록자, 등록 일시, 스키마 ID, 상태)과 삭제 기록을 보인다. 원본 내용은 보이지 않는다. | 없는 revision exit 5 | M2(DR-12) |
| `admin deletion-record list [--source factory] [--since ISO8601]` | 삭제 기록 목록(삭제자, 삭제 일시, source, 자료 ID, revision, 내용 해시). | — | FR-026 |

- 삭제 성공 출력 예: `{"deleted":true,"revision_id":"…","source":"factory","material_id":"…","was_current":true,"deletion_record_id":"…"}`.
- 호출자 HTTP 경로에는 삭제·삭제 기록 조회가 없다(US2-15).

## 계약 테스트 대응

`tests/contract/cli/test_admin_commands.py`(신규)가 각 명령의 인자, 성공 출력 키 집합, exit code, 표준 오류에 비밀값·내용이 없음을 확인한다. 세부 사례는 [plan.md](../plan.md) `테스트 전략` 표의 C-CLI 항목을 따른다.
