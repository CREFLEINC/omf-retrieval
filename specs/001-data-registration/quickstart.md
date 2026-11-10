# Quickstart: 문서 자료 등록 검증 안내(spec 001)

**상태**: DR-01~DR-24 승인(2026-11-10) 기준. 구현 전 문서이며, 구현 단계가 끝난 뒤 이 순서로 기능을 검증한다. 명령 이름·경로는 [plan.md](./plan.md)의 구조와 [contracts/](./contracts/) 기준이다. `DR-xx` 결정이 바뀌면 해당 단계를 고친다.

## 용어

| 용어 | 뜻 |
|---|---|
| 로컬 검증 | 개발 PC에서 `compose.test.yaml`의 PostgreSQL만 써서 하는 검증. GPU·임베딩 모델·외부 네트워크가 필요 없다. |
| 합성 표본 | 테스트가 실행 중에 만드는 Excel·PDF·이미지·텍스트 파일. 대외비 자료를 쓰지 않는다(DR-18). |
| `$TOKEN_W` 등 | 아래에서 만드는 호출자 토큰을 담은 셸 변수. 토큰은 파일이나 명령 기록에 남기지 않는다. |
| 인수 하네스 | SC-001~013을 실제 HTTP 앱과 PostgreSQL로 판정하는 테스트 묶음(`tests/integration/registration/test_acceptance_*.py`). |
| DR | 결정 요청. [research.md](./research.md) 3절. |

## 1. 준비

1. 테스트 DB를 띄우고 연결을 지정한다(README 1단계와 같다).

   ```bash
   docker compose -f compose.test.yaml up -d db
   export OMF_RETRIEVAL_DATABASE_URL='postgresql+psycopg://omf_retrieval_test:omf_retrieval_test@127.0.0.1:55432/omf_retrieval_test'
   export OMF_RETRIEVAL_TEST_DATABASE_URL="$OMF_RETRIEVAL_DATABASE_URL"
   uv run alembic upgrade head
   ```

   기대: 등록 마이그레이션 두 개(`0004`, `0005`)까지 적용되어 `0005`가 head다. 기존 `0001`~`0003` 테이블은 바뀌지 않는다.

2. 자동 테스트를 계층별로 실행한다.

   ```bash
   uv run ruff check src tests
   uv run pytest tests/unit -q
   uv run pytest tests/contract -q
   uv run pytest tests/integration -q
   ```

   기대: 모두 통과. `tests/server`(GPU)는 001 검증에 필요하지 않다.

## 2. 인수 하네스

```bash
uv run pytest tests/integration/registration -q
```

디렉터리 전체를 실행한다. 인수 하네스(`test_acceptance_*`)와 SC-007 회귀(`test_search_regression`), I-REG 통합 테스트가 모두 포함된다. 하네스만 따로 보려면 `-k 'acceptance or search_regression'`을 쓴다(SC-007을 빠뜨리지 않기 위해 `-k acceptance`만 쓰지 않는다).

| 하네스 | 판정 | 기대 결과 |
|---|---|---|
| `test_acceptance_reproduction` | SC-001, SC-005, SC-010 | 형식별 표본 등록 성공, 되찾은 바이트 불일치 0건, 후속 10회·재시작 1회 뒤 변경 0건, 현재본 규칙 위반 0건 |
| `test_acceptance_rejections` | SC-002, SC-009 | 16개 사유 표본과 research R-04 예시 표 전부가 표의 기대 코드, 거부 뒤 새 행 0건, 메시지·`detail`로 고칠 내용 판단 가능 |
| `test_acceptance_permissions` | SC-003, SC-012, SC-013 | 권한 없는 쓰기·되찾기 성공 0건, 외부·미표시 호출자 부여 거부 100%, `내부` 호출자 부여 성공, 호출자 자기 표시 변경 시도 성공 0건, 표시 변경 뒤 되찾기 404 |
| `test_acceptance_schemas` | SC-004 | 두 번째 등록부터 스키마 파일 없이 성공, 다른 내용 시도로 사전 변경 0건 |
| `test_acceptance_images` | SC-006 | 정보 자료 없는 이미지·전체 스캔 PDF 성공 0건, 묶음 표시 확인, 그림 포함 표본 전부 관계 정보 있음 |
| `test_acceptance_deletion` | SC-011 | 삭제 뒤 되찾기 성공 0건, 삭제 기록 있음, 호출자 삭제 성공 0건, 스키마 삭제 0건 |
| `test_acceptance_secrets` | SC-008 | 응답·로그 표본에서 토큰·DB 비밀번호·원본 바이트 조각·파일 안 값(시트 이름·셀 값·제목) 노출 0건(원본 읽기 권한 경로 제외), 외부 호출 0건 |
| `test_search_regression` | SC-007 | 등록 전후 같은 색인·고정 질의의 검색 결과 동일, 기존 검색 계약 테스트 통과 |

## 3. 수동 점검(선택, 로컬 서버)

**등록 안내(DR-15·DR-03 승인 결정)**

- 여러 쪽으로 된 문서(여러 쪽 스캔 등)는 이미지 여러 장으로 보내지 말고 **PDF 하나로 합쳐** 등록한다. 시스템은 이미지가 문서의 쪽인지 사진인지 구별할 수 없으므로 이는 거부 규칙이 아니라 안내다.
- 묶음의 이미지 여러 장은 순서 없는 집합으로 저장된다. 제출 순서는 기록되지 않고, 되찾기 때 파일 번호 순서에는 의미가 없다.
- `.zip` 등 압축 파일은 받지 않는다(`unsupported_file_format`). 여러 문서를 한 번에 등록하는 기능은 없으며 파일마다 요청을 보낸다.

API를 모델 없이 띄울 때 검색 경로는 503일 수 있다. 등록 경로는 모델과 무관하게 동작해야 한다(research DR-21).

**선행 조건(DR-24 권장안)**: 기존 `client create`는 항상 `omf` 검색 권한을 함께 부여하므로 `source_profiles`에 `omf` 행이 있어야 성공한다. 모델·색인 없이 점검하려고 **1절의 로컬 테스트 DB에만** `omf` 행 하나를 넣는다. 공유·운영 DB에서는 실행하지 않는다.

```bash
docker compose -f compose.test.yaml exec -T db \
  psql -U omf_retrieval_test -d omf_retrieval_test -c \
  "INSERT INTO source_profiles (id, source_key) VALUES (gen_random_uuid(), 'omf') ON CONFLICT (source_key) DO NOTHING;"
```

기대: `INSERT 0 1`(이미 있으면 `INSERT 0 0`). 이 DB의 검색은 활성 색인이 없어 실패하지만 등록 점검과 무관하다. 2절의 자동 테스트는 fixture로 같은 일을 하므로 이 단계가 필요 없다. README 1~4단계(모델 준비·OMF 작업 트리·전체 색인)는 필요하지 않다.

1. 관리자 준비(위 선행 조건 뒤, 순서대로).

   ```bash
   uv run omf-retrieval client create writer      # 출력의 token을 TOKEN_W로
   uv run omf-retrieval client create reader      # 출력의 token을 TOKEN_R로
   uv run omf-retrieval admin source create factory --actor local-admin
   uv run omf-retrieval admin permission grant --client <writer key_id> --source factory --kind write --actor local-admin
   uv run omf-retrieval admin permission grant --client <reader key_id> --source factory --kind read-original --actor local-admin
   ```

   기대: 마지막 명령은 exit 3 `llm_usage_not_internal`(표시 없음 = 외부, US2-19). 이어서:

   ```bash
   uv run omf-retrieval admin llm-usage set --client <reader key_id> --value internal --actor local-admin
   uv run omf-retrieval admin permission grant --client <reader key_id> --source factory --kind read-original --actor local-admin
   ```

   기대: 성공(US2-20).

2. 텍스트 등록과 되찾기.

   ```bash
   curl -sS -H "Authorization: Bearer $TOKEN_W" \
     -F 'file=@sample.md;type=text/markdown' \
     'http://127.0.0.1:8000/v1/registrations?source=factory&material_id=manual/ops-01'
   ```

   기대: 201, `schema_id` = `default-text-v1`, `files[0].detected_format` = `markdown`(DR-23), `files[0].content_hash` = `shasum -a 256 sample.md` 값. 응답에 파일 이름은 없다.

   ```bash
   curl -sS -H "Authorization: Bearer $TOKEN_R" -o out.md \
     "http://127.0.0.1:8000/v1/revisions/<revision_id>/files/0"
   cmp sample.md out.md
   ```

   기대: `cmp` 출력 없음(완전히 같음, SC-001). 같은 요청을 `$TOKEN_W`로 보내면 404 `revision_not_found`(US2-6, DR-13).

3. 거부 예시 하나(SC-009 정성 확인): `schema_id`를 21자로 보내 400 `invalid_schema_id`와 `detail.limit=20`, `detail.actual_length=21`을 확인한다.

4. 표시 변경(US2-21): `admin llm-usage set --client <reader key_id> --value external --actor local-admin` 뒤 2단계 되찾기가 404인지, `admin permission list`에 `llm_usage_changed` 회수가 보이는지 확인한다.

5. 완전 삭제(US2-14): `admin revision delete <revision_id> --actor local-admin --confirm` 뒤 되찾기 404, `admin revision show`에 파일 이름·내용 해시가 없고 `admin deletion-record list`에 내용 해시가 있는지 확인한다.

## 4. 저장 위치 점검(FR-032)

등록 호출로는 관찰할 수 없으므로 다음으로 인수한다(DR-01 승인안 기준).

| 점검 | 방법 | 합격 |
|---|---|---|
| 원본·정보 자료·스키마·등록 기록 저장 위치 | `OMF_RETRIEVAL_DATABASE_URL`의 호스트가 사내 PostgreSQL인지, 새 테이블(`revision_file_blobs`, `registration_schemas`, `registration_events`)에 행이 생기는지 확인 | 모든 저장이 플랫폼 PostgreSQL 안이다 |
| DB 밖 잔여 파일 | 등록 요청 전후 API 프로세스 임시 디렉터리의 파일 목록 비교 | 요청 종료 뒤 업로드 임시 파일 0개 |
| 외부 전송 | 인수 하네스가 네트워크 호출 차단 상태에서 통과하는지(`test_acceptance_secrets`) | 등록·되찾기 중 외부 연결 시도 0건 |

## 5. 정리

```bash
docker compose -f compose.test.yaml down
```

테스트 DB는 tmpfs라 내려가면 데이터가 남지 않는다.
