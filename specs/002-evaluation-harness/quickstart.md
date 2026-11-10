# Quickstart: 평가 하네스 검증 안내(spec 002)

**상태**: 결정 요청 DR-01~DR-25 승인(사용자 2026-11-10, 모두 권장안. DR-02·DR-03은 권장 A). 값 입력 대기: DR-03 연구 자원 구체 목록, DR-04 운영 GPU 총 메모리 실측값(5절 점검에서 넣으며, 그 전에는 실험 시작·관문 시도가 fail-closed로 거부됨). 구현 전 문서이며, 구현 단계가 끝난 뒤 이 순서로 기능을 검증한다. 명령 이름·경로는 [plan.md](./plan.md)의 구조와 [contracts/](./contracts/) 기준이다. `DR-xx` 결정이 바뀌면 해당 단계를 고친다.

## 용어

| 용어 | 뜻 |
|---|---|
| 로컬 검증 | 개발 PC에서 `compose.test.yaml`의 PostgreSQL만 써서 하는 검증. GPU·실제 임베딩 모델·외부 네트워크가 필요 없다. |
| fixture 색인 | 테스트가 만드는 작은 git 저장소와 결정적 가짜 임베딩 제공자(테스트 전용 레지스트리 항목)로 만든 run. |
| 인수 하네스 | SC-001~011을 실제 CLI와 PostgreSQL로 판정하는 테스트 묶음(`tests/integration/evaluation/test_acceptance_*.py`). |
| 평가 DB | 평가 실행기가 쓰는 DB. 로컬 검증에서는 테스트 DB, 연구 자원에서는 별도 평가 PostgreSQL이다[DR-02]. |
| 연구 자원 | 운영과 분리된 실험 실행 장비. 허용 목록에 등록한다[DR-03]. |
| DR, SC | [research.md](./research.md) 3절의 결정 요청과 spec의 성공 기준. |

## 1. 준비(로컬 검증)

1. 테스트 DB를 띄우고 연결을 지정한다(README 1단계와 같다).

   ```bash
   docker compose -f compose.test.yaml up -d db
   export OMF_RETRIEVAL_DATABASE_URL='postgresql+psycopg://omf_retrieval_test:omf_retrieval_test@127.0.0.1:55432/omf_retrieval_test'
   export OMF_RETRIEVAL_TEST_DATABASE_URL="$OMF_RETRIEVAL_DATABASE_URL"
   export OMF_EVAL_DATABASE_URL="$OMF_RETRIEVAL_DATABASE_URL"
   uv run alembic upgrade head
   ```

   기대: 평가 마이그레이션 두 개(계획 이름 `0006_evaluation_datasets`, `0007_evaluation_experiments`)까지 적용된다. 기존 `0001`~`0005` 테이블은 바뀌지 않는다. `eval_tracks`에 연구 트랙 3행이 있다.

2. 자동 테스트를 계층별로 실행한다.

   ```bash
   uv run ruff check src tests
   uv run pytest tests/unit -q
   uv run pytest tests/contract -q
   uv run pytest tests/integration -q
   ```

   기대: 모두 통과. 기존 검색 계약·단위 테스트(`tests/contract/api/test_search_api.py`, `tests/unit/search/`)는 수정 없이 통과한다(C-SRCH-01·02). `tests/server`(GPU)는 002 로컬 검증에 필요하지 않다.

## 2. 인수 하네스

```bash
uv run pytest tests/integration/evaluation -q
```

| 하네스 | 판정 | 기대 결과 |
|---|---|---|
| `test_acceptance_reproduction` | SC-001 | 표본 실험 3건 재현에서 질의별 결과·품질 지표·필수 검사 불일치 0건 |
| `test_acceptance_recompute` | SC-002 | 표본 관문 기록 전부의 재계산 판정·사유 일치 100%, 재실행 0건 |
| `test_acceptance_attempt_limits` | SC-004 | 네 번째 시도·후보 2개 성공 0건, 다른 트랙 남은 횟수 불변 |
| `test_acceptance_scale` | SC-005 | 조건 하나씩 어긴 시험셋 3개 모두 `test_set_scale`, 판정 0건 |
| `test_acceptance_dev_exclusion` | SC-006 | 시험셋·개발셋에 같은 정규화 질의문 0건 |
| `test_acceptance_boundaries` | SC-007 | 경계값 표본 6종 판정이 data-model 4.4대로 |
| `test_acceptance_isolation` | SC-008, SC-009 | 실험 전후 고정 질의 결과 차이 0건, 활성 색인 변경 0건, `적용 권고` 뒤 운영 설정·포인터 변경 0건 |
| `test_acceptance_data_boundary` | SC-010 | socket 감시 아래 외부 연결 0건, 합성 질의 표식이 청크·검색 응답에 0건 |
| `test_acceptance_resources` | SC-011 | 모든 실험 기록에 실행 자원, 운영 GPU UUID 환경 실행 거부 |

SC-003은 자동 하네스가 아니라 3절의 정성 검토로 판정한다.

## 3. 수동 점검(로컬, fixture 색인)

아래는 fixture 저장소와 테스트 전용 레지스트리 항목을 쓰는 예다. 실제 OMF 저장소를 쓰는 연구 자원 절차는 5절이다.

1. 원본 자료 스냅숏을 만들고 목록을 본다(FR-003b, FR-010).

   ```bash
   uv run omf-retrieval eval source-snapshot create --source omf --commit "$FIXTURE_COMMIT" --actor tester
   uv run omf-retrieval eval source-snapshot list
   uv run omf-retrieval eval source-snapshot show SNAP-YYYYMMDD-01 --materials
   ```

   기대: 목록에 기본 선택 표시가 없다. `show`에 자료 ID(경로)·revision(commit SHA)·행 수가 나온다.

2. 시험셋을 만들고 질의 파일을 제출한다([contracts/query-authoring-format.md](./contracts/query-authoring-format.md)).

   ```bash
   uv run omf-retrieval eval set create test-local --kind test --actor tester
   uv run omf-retrieval eval query add --set test-local --file samples/good.json --actor tester
   uv run omf-retrieval eval query add --set test-local --file samples/bad.json --actor tester; echo "exit=$?"
   ```

   기대: 첫 제출은 질의 ID와 새 스냅숏 ID를 돌려준다. 둘째(`질의문` 키 없음, 의도 빈 칸, 정답 표시 모순, 목록 밖 스냅숏, 범위 밖 행, 작성자 빈 칸, 같은 파일 안 중복의 7가지 결함을 서로 다른 항목에 하나씩 넣은 파일)는 exit 3, 배열 위치별 사유 코드 7종(`missing_field`, `invalid_intent`, `invalid_answer_marking`, `unknown_source_snapshot`, `invalid_answer_coordinate`, `invalid_authoring_info`, `duplicate_query`)이 하나씩 나오고 아무것도 접수되지 않는다. 출력에 질의문이 없다.

3. 개발셋 실험을 실행한다(US2).

   ```bash
   uv run omf-retrieval eval set create dev-local --kind dev --actor tester
   uv run omf-retrieval eval dev build-from-template --set dev-local --template samples/template.json --actor tester
   uv run omf-retrieval eval experiment run --spec samples/dev-spec.json --actor tester
   ```

   기대: 두 구성의 Recall@5·MRR@20·근거 완전성·의도별 Recall@5·`no_evidence` 비율·지연 p95·GPU 메모리(`not_measured` 가능)·재색인(`not_measured`)·반환 근거 평균 길이와 필수 검사 5종이 출력된다. 실행 전후 `source_profiles.active_index_run_id`가 같다.

4. 관문 시도·재계산·재현을 확인한다(US3~US5). 로컬 검증용 시험셋 규모는 테스트 설정으로 낮출 수 없다. 규모 충족 시험셋은 인수 하네스가 합성하며, 수동 점검에서는 규모 미달 거부와 출력 형식만 확인한다.

   ```bash
   uv run omf-retrieval eval gate attempt --spec samples/gate-spec.json --track track-2-reranker --actor tester; echo "exit=$?"
   uv run omf-retrieval eval track list --set test-local
   ```

   기대: exit 3 `test_set_scale`, 트랙 ② 사용 횟수 0(시도로 세지 않음).

## 4. SC-003 정성 검토 절차

1. 작성자 2명 이상에게 spec(`specs/002-evaluation-harness/spec.md`)과 질의 작성 형식 계약서(`contracts/query-authoring-format.md`) 두 파일만 준다(사용자 결정 A-1, 2026-11-10: "다른 문서"에서 빠지는 것은 이 계약서 한 파일뿐).
2. 작성자마다 20건 이상(정답 하나, 정답 여러 개, 문서에 없는 질의 포함)을 작성·제출한다.
3. 검토자는 `eval_submissions` 기록으로 작성자별 형식 오류 거부 0건을 확인하고, plan.md `정성 평가 기준` 표 네 기준으로 판정한다.
4. 합격 예시: spec FR-003 예시 A·B처럼 작성자 항목 8개를 모두 갖춘 질의. 불합격 예시: 정답 위치를 "운영 매뉴얼 3장 근처"처럼 적거나 확인 기준 원본 자료 스냅숏을 빠뜨린 질의.

## 5. 연구 자원 배치 점검(SC-011, FR-033)

구현 뒤 실제 실험 전에 한 번, 그리고 자원이 바뀔 때마다 한다. 이 점검은 운영 서버를 바꾸지 않는다.

| 확인 | 방법 | 합격 |
|---|---|---|
| 운영 GPU 식별값 | 사용자가 운영 서버에서 `nvidia-smi --query-gpu=name,uuid,memory.total --format=csv`를 실행해 알려 준 값을 `OMF_EVAL_PRODUCTION_GPU_UUIDS`에 넣는다[DR-03·DR-04] | 값이 있고 관문 기준 `gpu_memory_limit_mib`이 승인된 값으로 정해짐 |
| 연구 자원 허용 목록 | `OMF_EVAL_RESOURCE_ALLOWLIST`에 승인된 연구 자원의 이름·호스트·GPU UUID | 목록에 운영 호스트·운영 GPU UUID가 없음 |
| 실행 자원 판정 | 연구 자원에서 `uv run omf-retrieval eval resource show` | `allowed: true`, `production_match: false` |
| 평가 DB 분리 | `OMF_EVAL_DATABASE_URL`이 연구 자원의 평가 DB이고 운영 DB 거부 목록(`OMF_EVAL_PRODUCTION_DATABASE_IDS`)에 없음[DR-02] | 아무 `eval` 명령이 `production_database_refused` 없이 시작 |
| 운영 GPU 거부 | 운영 GPU UUID를 흉내 낸 설정으로 `eval experiment run` | exit 3 `resource_not_allowed` |
| 연결 끊김 감지[DR-25] | 연구 자원의 별도 평가 PostgreSQL(DR-02)에서 `SHOW tcp_keepalives_idle; SHOW tcp_keepalives_interval; SHOW tcp_keepalives_count; SHOW idle_session_timeout;` | 30, 10, 3, 0(리눅스 기본 약 2시간 11분이면 호스트 정지 뒤 정리 가능 시점이 그만큼 늦어짐). 실행기 전용 연결의 클라이언트 keepalive(`keepalives_idle=30` 등)는 코드 기본값. 이 서버 설정은 평가 PostgreSQL에만 두며 운영 PostgreSQL 설정은 바꾸지 않는다 |
| 연결 풀러 없음[DR-25] | `OMF_EVAL_DATABASE_URL`의 호스트·포트가 평가 PostgreSQL 자체(또는 그 서버로 바로 이어지는 Docker 포트 매핑·SSH 터널)이고 PgBouncer 같은 트랜잭션 모드 풀러가 아님을 배치 설정에서 사람이 확인한다. 실행기는 시작 시 이를 검사하지 않는다 | 풀러 경유 없음이 배치 기록으로 확인됨. 실수로 풀러를 거치면 실행 중 `pg_backend_pid()` 변경으로 실험이 `liveness_lost`(exit 4)로 멈출 수 있다(모든 풀러 구성을 잡는 것은 아님) |

## 6. 연구 자원에서의 실제 실행 순서(참고)

1. 평가 DB에 마이그레이션을 적용한다.
2. OMF 고정 commit(`a8f46f23cd3fb9c5f7042e987dff8103d23f0fa2`)으로 원본 자료 스냅숏을 만든다(OMF 저장소는 읽기 전용).
3. 현재 구성 run을 평가 DB에서 운영과 같은 색인 식별값으로 만들고, 후보 run을 나란히 만든다(자동 활성화 없음, `006`의 "빌드만" 선택지 필요).
4. 관문 시도를 하려면 두 구성의 콜드 빌드 측정 run이 필요하다[DR-14].
5. 후보 정책은 `eval policy register`로 등록한 뒤 실험 요청 파일에 `config_hash`를 적는다.
6. 개발셋으로 튜닝하고, 최종 후보 하나만 `eval gate attempt`에 올린다(트랙별 3회).
7. `적용 권고`를 받아도 운영 적용은 승인권자 승인과 별도 운영 절차로만 한다(FR-021, spec Out of Scope).
