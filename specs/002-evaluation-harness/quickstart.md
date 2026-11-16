# Quickstart: 평가 하네스 검증 안내(spec 002)

**상태**: 결정 요청 DR-01~DR-25 승인(사용자 2026-11-10, 모두 권장안. DR-02·DR-03은 권장 A). 값 입력 대기: DR-03 연구 자원 구체 목록, DR-04 운영 GPU 총 메모리 실측값(5절 점검에서 넣으며, 그 전에는 실험 시작·관문 시도가 fail-closed로 거부됨). 갱신(사용자 결정 2026-11-13): 운영 서버 값(호스트·GPU UUID·총 메모리)은 첫 접속 때 실측해 기록하고, 운영 개시 전에는 운영 서버를 예외 표시와 함께 허용 목록에 둔다(spec FR-033). 구현 전 문서이며, 구현 단계가 끝난 뒤 이 순서로 기능을 검증한다. 명령 이름·경로는 [plan.md](./plan.md)의 구조와 [contracts/](./contracts/) 기준이다. `DR-xx` 결정이 바뀌면 해당 단계를 고친다.

**개정(2026-11-16, `012` 연동 ①~⑧)**: 3절에 5~7단계(개발셋 역할·문서에 없는 질의 합성·단일 구성 진단·확인 실험과 관문 앞 확인)를, 6절에 리랭커 트랙 순서를 더했다. 결정 요청 DR-26~DR-28(사용자 승인 2026-11-16, DR-28은 리랭커 교체 건 포함·확인 횟수 시험셋 세대별 변경 포함)에 의존한다. 이 단계의 명령은 해당 Phase 구현 뒤에 생긴다. 같은 날 2절 `test_acceptance_resources`의 운영 개시 전 예외 사례를 보완했다.

## 용어

| 용어 | 뜻 |
|---|---|
| 로컬 검증 | 개발 PC에서 `compose.test.yaml`의 PostgreSQL만 써서 하는 검증. GPU·실제 임베딩 모델·외부 네트워크가 필요 없다. |
| fixture 색인 | 테스트가 만드는 작은 git 저장소와 결정적 가짜 임베딩 제공자(테스트 전용 레지스트리 항목)로 만든 run. |
| 인수 하네스 | SC-001~011을 실제 CLI와 PostgreSQL로 판정하는 테스트 묶음(`tests/integration/evaluation/test_acceptance_*.py`). |
| 평가 DB | 평가 실행기가 쓰는 DB. 로컬 검증에서는 테스트 DB, 연구 자원에서는 별도 평가 PostgreSQL이다[DR-02]. |
| 연구 자원 | 운영과 분리된 실험 실행 장비. 허용 목록에 등록한다[DR-03]. 예외: 운영 개시 전까지는 운영 서버도 연구 자원으로 쓸 수 있다(spec FR-033 "운영 개시 전 예외", 사용자 결정 2026-11-13). |
| 탐색·확인 개발셋, 확인 실험, 확인 소진, Recall@N(후보 목록), 참고 지표 | research.md 용어표와 같다(`012` 연동 개정). |
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
| `test_acceptance_resources` | SC-011 | 모든 실험 기록에 실행 자원이 있음. 운영 GPU UUID 환경에서 예외 표시 없는 허용 항목으로 실행 → `resource_not_allowed`. 운영 개시 전 예외 사례(spec FR-033, research DR-03): 운영 서버 항목에 `pre_launch_exception: true`가 있으면 운영 목록과 겹쳐도 실행되고 기록의 `resource_name`이 "운영 서버(운영 개시 전 예외)"(표시 누락 0건). 같은 설정에서 표시만 지움 → `resource_not_allowed`. 허용 목록 빈 목록·운영 목록 빈 목록(각각) → `resource_not_allowed` |

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

   기대: exit 3 `test_set_scale`, 트랙 ② 사용 횟수 0(시도로 세지 않음). 개정(DR-28) 뒤에는 트랙 ②가 확인이 필요한 트랙이라, 요청 파일에 `holdout`이 없으면 먼저 사전 점검 2a에서 exit 3 `gate_without_holdout_check`(`detail.rule = holdout_input_missing`)가 나온다. 이 단계의 규모 확인은 확인이 필요 없는 트랙(예: `track-3-embedding`)으로 하거나 7단계 뒤에 한다.

5. (개정, `012` 연동 ④⑥, DR-28·R-20) 탐색·확인 개발셋을 만들고 문서에 없는 질의를 합성한다. 로컬에서는 가짜 사내 모델 서버(테스트 허용 목록)를 쓴다.

   ```bash
   uv run omf-retrieval eval set create dev-explore --kind dev --role exploration --actor tester
   uv run omf-retrieval eval set create dev-holdout --kind dev --role holdout --actor tester
   uv run omf-retrieval eval dev synthesize --set dev-explore --source-snapshot SNAP-YYYYMMDD-01 --mode no_answer --per-intent 무엇=4,왜=3,어떻게=3 --seed 7 --actor tester
   uv run omf-retrieval eval dev synthesize --set dev-holdout --source-snapshot SNAP-YYYYMMDD-01 --mode no_answer --count 5 --seed 7 --actor tester
   ```

   기대: 첫 합성 출력의 `by_intent` 요청 수가 4·3·3이고 접수 질의는 모두 정답 구분 "정답 없음"·출처 구분 "합성", `review_sample`에 질의 ID가 최대 20개(질의문 없음). 가짜 서버가 받은 요청에는 제목 목록만 있고 본문 표식 문자열이 0건. 두 번째 합성은 탐색 개발셋과 같은 문을 `excluded_as_exploration_duplicate`로 뺀다. `eval set create dev-x --kind test --role holdout`은 exit 2.

6. (개정, `012` 연동 ①②⑦, DR-26·DR-27·R-18) 현재 구성 하나로 단일 구성 진단을 한다(요청 파일의 `current`와 `candidate`를 같게 둠).

   ```bash
   uv run omf-retrieval eval experiment run --spec samples/dev-single-config.json --actor tester
   ```

   기대: exit 0, 실험 수준 `metrics.single_config: true`, 구성별 `diagnostic.recall_at_n_candidates`에 `5`·`10`·`20`·`30`·`50`·`max` 여섯 키(분수 표기), `diagnostic.candidate_count`의 `max` ≤ 100, `reference.mrr_at_3`(분수)·`reference.ndcg_at_3`(`precision` 포함), 짝 차이 0. 질의별 결과(`eval experiment export`)에 후보 원문이 없다. 관문 판정 키(`judgment`, `reasons`)는 없다.

7. (개정, `012` 연동 ④, DR-28) 확인 실험과 관문 앞 확인을 확인한다. 리랭커 기술자가 있는 정책은 `009` 이후에만 있으므로, 로컬 검증은 인수·통합 테스트(U-HOLD-01~04, I-HOLD-01)의 가짜 정책 표본으로 한다. 수동으로는 키를 만들 수 없는 경우만 본다.

   ```bash
   uv run omf-retrieval eval experiment run --spec samples/holdout-spec.json --purpose holdout_check --track track-2-reranker --test-set test-local --actor tester; echo "exit=$?"
   uv run omf-retrieval eval experiment run --spec samples/holdout-spec.json --purpose tuning --actor tester; echo "exit=$?"
   ```

   기대: 첫째는 MVP 형식 정책이라 exit 3 `holdout_not_allowed`(`detail.rule = candidate_key_unavailable`), 실험 행 0건. 둘째는 확인 개발셋을 튜닝 목적에 써서 exit 3 `holdout_not_allowed`(`detail.rule = purpose_not_allowed`). `eval track list --set test-local`의 트랙 ② `holdout_checks_used`(현재 세대)는 0.

## 4. SC-003 정성 검토 절차

1. 작성자 2명 이상에게 spec(`specs/002-evaluation-harness/spec.md`)과 질의 작성 형식 계약서(`contracts/query-authoring-format.md`) 두 파일만 준다(사용자 결정 A-1, 2026-11-10: "다른 문서"에서 빠지는 것은 이 계약서 한 파일뿐).
2. 작성자마다 20건 이상(정답 하나, 정답 여러 개, 문서에 없는 질의 포함)을 작성·제출한다.
3. 검토자는 `eval_submissions` 기록으로 작성자별 형식 오류 거부 0건을 확인하고, plan.md `정성 평가 기준` 표 네 기준으로 판정한다.
4. 합격 예시: spec FR-003 예시 A·B처럼 작성자 항목 8개를 모두 갖춘 질의. 불합격 예시: 정답 위치를 "운영 매뉴얼 3장 근처"처럼 적거나 확인 기준 원본 자료 스냅숏을 빠뜨린 질의.

## 5. 연구 자원 배치 점검(SC-011, FR-033)

구현 뒤 실제 실험 전에 한 번, 그리고 자원이 바뀔 때마다 한다. 이 점검은 운영 서버를 바꾸지 않는다.

| 확인 | 방법 | 합격 |
|---|---|---|
| 운영 GPU 식별값 | 운영 서버 첫 접속 때(별도 승인 작업) `nvidia-smi --query-gpu=name,uuid,memory.total --format=csv`로 읽어 기록·보고한 값을 `OMF_EVAL_PRODUCTION_GPU_UUIDS`에 넣는다[DR-03·DR-04](사용자 결정 2026-11-13) | 값이 있고 관문 기준 `gpu_memory_limit_mib`이 실측 총 메모리 −10%로 정해짐. 운영 개시 전 예외 기간에도 운영 서버 GPU UUID는 이 목록에 그대로 둔다(아래 비고) |
| 연구 자원 허용 목록 | `OMF_EVAL_RESOURCE_ALLOWLIST`에 승인된 연구 자원의 이름·호스트·GPU UUID | 목록이 비어 있지 않음. 운영 목록과 겹치는 항목은 운영 개시 전 예외 기간의 운영 서버 항목 하나뿐이며, 그 항목에만 `pre_launch_exception: true`가 있고 자원 이름이 "운영 서버(운영 개시 전 예외)"임. 운영 개시 뒤에는 `pre_launch_exception: true`인 항목이 없음 |
| 실행 자원 판정 | 연구 자원에서 `uv run omf-retrieval eval resource show` | `allowed: true`, `production_match: false`. 운영 개시 전 예외 기간의 운영 서버에서는 `allowed: true`, `production_match: true`이고 예외 표시 항목으로 허용됨 |
| 평가 DB 분리 | `OMF_EVAL_DATABASE_URL`이 연구 자원의 평가 DB이고 운영 DB 거부 목록(`OMF_EVAL_PRODUCTION_DATABASE_IDS`)에 없음[DR-02] | 아무 `eval` 명령이 `production_database_refused` 없이 시작 |
| 운영 GPU 거부 | 운영 GPU UUID를 흉내 낸 설정(허용 항목에 예외 표시 없음)으로 `eval experiment run`. 예외 기간에는 운영 서버 항목의 예외 표시만 지운 설정으로도 한 번 실행 | 둘 다 exit 3 `resource_not_allowed` |
| 연결 끊김 감지[DR-25] | 연구 자원의 별도 평가 PostgreSQL(DR-02)에서 `SHOW tcp_keepalives_idle; SHOW tcp_keepalives_interval; SHOW tcp_keepalives_count; SHOW idle_session_timeout;` | 30, 10, 3, 0(리눅스 기본 약 2시간 11분이면 호스트 정지 뒤 정리 가능 시점이 그만큼 늦어짐). 실행기 전용 연결의 클라이언트 keepalive(`keepalives_idle=30` 등)는 코드 기본값. 이 서버 설정은 평가 PostgreSQL에만 두며 운영 PostgreSQL 설정은 바꾸지 않는다 |
| 연결 풀러 없음[DR-25] | `OMF_EVAL_DATABASE_URL`의 호스트·포트가 평가 PostgreSQL 자체(또는 그 서버로 바로 이어지는 Docker 포트 매핑·SSH 터널)이고 PgBouncer 같은 트랜잭션 모드 풀러가 아님을 배치 설정에서 사람이 확인한다. 실행기는 시작 시 이를 검사하지 않는다 | 풀러 경유 없음이 배치 기록으로 확인됨. 실수로 풀러를 거치면 실행 중 `pg_backend_pid()` 변경으로 실험이 `liveness_lost`(exit 4)로 멈출 수 있다(모든 풀러 구성을 잡는 것은 아님) |

비고(사용자 결정 2026-11-13, spec FR-033 "운영 개시 전 예외"): 운영 개시는 사용자가 알려 주는 시점이다. 그 전에는 운영 서버를 연구 자원으로 쓸 수 있으며 이 점검을 운영 서버에서도 한다. 이때 추가로 확인한다: 평가 PostgreSQL이 운영 DB와 별도 컨테이너·포트·볼륨이고 운영 DB 주소 거부가 유지됨(조건 ①), 실험이 운영 컨테이너·운영 DB·활성 색인·운영 검색 정책을 읽거나 바꾸지 않음(조건 ②), 연구용 코드·자료가 `/home/phoebe-deploy/experiments` 아래에 있고 운영 배포 디렉터리와 섞이지 않음(조건 ⑤, 같은 OS 사용자라 권한 분리는 아님). 운영 서버의 호스트·GPU UUID와 총 메모리는 첫 접속 때 읽어 기록·보고한다[DR-03·DR-04]. 서버 작업마다 배포 하네스에 따른 별도 승인이 필요하다(조건 ⑥). 사용자가 운영 개시를 알리면 운영 서버 허용 항목의 `pre_launch_exception` 표시만 지운다. 운영 목록은 처음부터 그대로이므로 운영 서버는 겹침 거부로 자동 복귀한다. 통지 시점에 진행 중인 실험은 끝까지 마치고, 새 실험은 다른 연구 자원에서 시작한 뒤 이 점검을 그 자원에서 다시 한다(조건 ④).

## 6. 연구 자원에서의 실제 실행 순서(참고)

1. 평가 DB에 마이그레이션을 적용한다.
2. OMF 고정 commit(`a8f46f23cd3fb9c5f7042e987dff8103d23f0fa2`)으로 원본 자료 스냅숏을 만든다(OMF 저장소는 읽기 전용).
3. 현재 구성 run을 평가 DB에서 운영과 같은 색인 식별값으로 만들고, 후보 run을 나란히 만든다(자동 활성화 없음, `006`의 "빌드만" 선택지 필요).
4. 관문 시도를 하려면 두 구성의 콜드 빌드 측정 run이 필요하다[DR-14].
5. 후보 정책은 `eval policy register`로 등록한 뒤 실험 요청 파일에 `config_hash`를 적는다.
6. 개발셋으로 튜닝하고, 최종 후보 하나만 `eval gate attempt`에 올린다(트랙별 3회).
   - 개정(`012` 트랙 ② 순서, DR-28·R-18·R-19·R-20): 탐색(`exploration`)·확인(`holdout`) 개발셋을 따로 만들고 문서에 없는 질의를 `--mode no_answer --per-intent`로 합성한다(정답 있음 1,000 + 100, 500 + 50은 `012` 결정). 현재 구성 하나로 단일 구성 진단을 해 Recall@N(후보 목록)을 잰다. 튜닝은 탐색 개발셋으로만 하고, 관문 시도마다 그 시도에 보낼 후보로 확인 실험(`--purpose holdout_check --track track-2-reranker --test-set <시험셋>`)을 한 번 먼저 한다(확인은 시험셋 세대마다 승인 없이 3회, 4회째부터 `--approval-ref`. 새 세대가 열리면 0부터 다시 센다. 이전 세대의 미소진 확인은 대조 키가 맞으면 새 세대 관문 시도에도 쓸 수 있고 새 세대 횟수에 더하지 않는다[결정 2026-11-16]). 리랭커 교체 건(`swap-reranker-*`)도 같은 규칙이다. 관문 요청 파일에 `holdout`을 적고, 두 구성이 같은 run이면 같은 콜드 빌드 측정 run을 두 구성에 함께 적는다. 관문 요청이 사전 점검 1(실행 자원)·2(평가 DB)를 통과해 2a에 이르면 그 뒤 거부돼도 확인은 소진되므로(1·2에서 거부되면 소진되지 않음), 규모·측정 run·GPU 한도 설정을 먼저 확인한 뒤 요청한다.
   - 후보 목록 고정 파일(파일 A·B)과 오프라인 실험은 다음 개정(`012` 연동 ⑨)에서 이 순서에 더한다.
7. `적용 권고`를 받아도 운영 적용은 승인권자 승인과 별도 운영 절차로만 한다(FR-021, spec Out of Scope).
