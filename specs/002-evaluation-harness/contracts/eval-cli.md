# Contract: 평가 실행기 CLI(`omf-retrieval eval`) — spec 002

**상태**: 승인(사용자 2026-11-10). DR-17(typer `eval` 하위 명령 + JSON 요청 파일)과 DR-08·DR-09·DR-10·DR-14·DR-19·DR-20·DR-21·DR-23·DR-25 권장안 기준이다. 승인된 결정이 바뀌면 해당 명령을 고친다.

## 용어

| 용어 | 뜻 |
|---|---|
| 평가 실행기 | 서버 셸(연구 자원)에서 `uv run omf-retrieval eval ...`로 실행하는 명령 묶음. HTTP 경로가 아니다(설계서 D-04). |
| `--actor` | 실행자가 밝히는 이름. 기록에 `"<actor> (os:<user>)"`로 남는다. 인증된 신원이 아니며 서버 셸 접근 통제가 실체다(001 관리자 CLI와 같음). |
| 요청 파일(`--spec`) | 실험 요청을 담은 UTF-8 JSON 파일. 형식은 아래 6절. |
| 사전 점검 | 실험·관문 시도 전에 하는 확인(data-model 4.2). 사전 점검 거부는 관문 시도로 세지 않는다. |
| 집계 출력 | 지표·조건별 사유만 담고 질의별 결과를 담지 않는 출력[DR-21]. |
| 평가 DB | `OMF_EVAL_DATABASE_URL`(또는 비밀 파일)로 지정한 DB. 운영 DB 거부 목록에 있으면 모든 명령이 시작하지 않는다[DR-02]. |
| exit code | 명령 종료 코드. 아래 표. |

## 1. 공통 규칙

- 성공 출력은 표준 출력 한 줄 JSON(키 정렬, 공백 없는 구분자). 기존 `client create`와 001 관리자 CLI 관례와 같다.
- 실패는 표준 오류에 고정 문장 한 줄과 사유 코드(`{"code":"…","message":"…","detail":{…}}`). DB URL, 토큰, 질의문, 원문, 스택 추적을 내지 않는다. `detail`에는 배열 위치·개수·한도·식별값(질의 ID, 스냅숏 ID, run id, 실험 id), 고정 코드(`failure_code`, 세부 `rule`), 재시도 가능 여부(`retryable`), 관문 시도 소비 여부(`attempt_consumed`), 다음 조치 안내(`next_step`)만 넣는다.
- 변경 명령은 `--actor`(1~100자, 공백만 불가)를 필수로 받는다.
- 실행 자원 확인(DR-03)은 실험·관문 명령에서만 한다. 데이터 관리 명령은 어느 사내 자원에서도 쓸 수 있으나 평가 DB 거부 목록 확인(DR-02)은 모든 명령이 한다.
- 네트워크 연결은 평가 DB와(합성 명령만) 허용 목록의 사내 모델 서버뿐이다(research R-11).

| exit code | 뜻 |
|---|---|
| 0 | 성공 |
| 2 | 인자 오류(typer 기본), 요청 파일 JSON 해석 불가 |
| 3 | 규칙상 거부(질의 접수 거부, 사전 점검 거부 등). 아무것도 바뀌지 않았고 관문 시도로 세지 않는다 |
| 4 | 예기치 않은 실패. 트랜잭션은 되돌려졌다. 실험 행은 색인 쪽 필수 검사·모델 적재·예열을 끝낸 뒤 첫 평가셋 질의 직전에 기록되고, 관문 시도는 첫 질의별 결과가 저장될 때부터 센다[DR-24]. 결과 저장 전 오류는 시도로 세지 않고(`detail.attempt_consumed: false`), 그 뒤 오류는 실험이 `failed`로 남고 시도로 센다(`true`) |
| 5 | 대상 없음(스냅숏, 평가셋, 질의, 실험, 트랙, run, 정책) |

## 2. 원본 자료 스냅숏(FR-010, FR-003b)

| 명령 | 동작 | 성공 출력 | 거부 사유 코드 | 대응 |
|---|---|---|---|---|
| `eval source-snapshot create --source omf --commit SHA --actor A [--description D]` | OMF 저장소의 고정 commit에서 source profile 포함·제외 패턴에 맞는 Markdown을 읽어(읽기 전용) 항목과 행 수·내용 해시를 기록한다. 같은 항목 집합이면 기존 ID를 돌려준다 | `{"created":true,"id":"SNAP-20261120-01","item_count":158,"revision_summary":{"omf":{"commit":"a8f4…","materials":158}}}` | `commit_not_found`, `source_not_supported`(factory는 P13 전까지) | FR-010 |
| `eval source-snapshot create --source factory --actor A [--material ID ...]` | (P13) 001 현재본 revision과 범위 정보를 고정한다. `--material` 없으면 현재본이 있는 모든 자료 | 같은 형식 | `no_current_revision`, `revision_deleted` | FR-010, DR-22 |
| `eval source-snapshot list` | 작성자가 고를 수 있는 스냅숏 목록. 기본 선택값 없음[DR-10] | `{"snapshots":[{"id":…,"created_at":…,"description":…,"item_count":…,"revision_summary":…}]}` | — | FR-003b |
| `eval source-snapshot show ID [--materials]` | 스냅숏 정보. `--materials`면 자료 ID·revision·구성(001 `composition`)·파일 번호별 형식·역할·행 수·페이지 수·시트 이름 | `{"id":…,"materials":[{"source":"omf","material_id":"design/wiki/…md","revision":"a8f4…","composition":"single","files":[{"file_index":0,"format":"markdown","line_count":212}]}]}` | 없음 → exit 5 | FR-003b, DR-07(OMF commit 값 제공 위치) |

## 3. 평가셋과 질의(FR-001~FR-009)

| 명령 | 동작 | 거부 사유 코드 | 대응 |
|---|---|---|---|
| `eval set create NAME --kind test\|dev --actor A` | 평가셋 생성. 시험셋이면 세대 1을 연다 | `set_exists`, `invalid_set_name` | FR-001 |
| `eval set list` | 평가셋, 종류, 최신 스냅숏, 시험셋이면 현재 세대 | — | 운영 확인 |
| `eval query add --set NAME --file F.json --actor A` | 사람 작성 질의 파일 접수(전부 또는 전무). 성공 시 질의 ID 목록과 새 평가셋 스냅숏 | 파일 형식 오류 exit 2. 질의 거부 exit 3 `query_rejected`, `detail.entries[]`에 배열 위치별 사유 코드 목록([query-authoring-format.md](./query-authoring-format.md) 5절). 개발셋에 사람 작성 파일 → `set_kind_mismatch` | FR-003~FR-007, US1 |
| `eval query remove --set NAME --query QID --reason author_withdrawn\|other [--note N] --actor A` | 질의 제거, 새 스냅숏 | 이미 제거 → exit 3 `already_removed` | FR-009 |
| `eval query show --set NAME --query QID [--include-text --reason R --actor A]` | 질의 정보. 개발셋은 질의문 포함. 시험셋은 작성 정보·의도·정답 근거 좌표만, `--include-text`면 질의문도 출력하고 `eval_access_log`에 `test_query_text` 1건 기록[DR-21] | `--include-text`에 `--reason`·`--actor` 없음 → exit 2. 없는 질의 → exit 5 | FR-003, FR-024 |
| `eval set snapshot list --set NAME` / `show SNAPSHOT_ID` | 스냅숏 목록·구성원 수·규모 충족 여부(`scale_ok`)·평가 불가 수 | — | FR-009, FR-002 |
| `eval set generation start --set NAME --approval-ref REF --actor A` | 시험셋 새 세대 열기[DR-08]. 보충 조건 미달이면 거부 | `replenish_insufficient`(`detail`에 부족 수), `not_test_set`, `test_set_scale` | FR-026 |

- `eval query add` 성공 출력 예: `{"accepted":3,"query_ids":["Q-000101","Q-000102","Q-000103"],"set":"test-main","snapshot_id":"test-main@14","scale_ok":false}`.
- `eval query show`의 열람 기록은 data-model 3.15 `eval_access_log`(`kind=test_query_text`, `experiment_id` NULL)이다. 개발셋 질의 열람은 기록하지 않는다.
- 시험셋 제출 질의가 어느 개발셋의 현재 구성원과 같은 정규화 질의문이면 `duplicate_query`(`in_dev_set`)로 거부한다(SC-006, data-model 4.1)[DR-23].

## 4. 개발셋 생성(FR-007, FR-008, FR-008a)

| 명령 | 동작 | 거부 사유 코드 | 대응 |
|---|---|---|---|
| `eval dev build-from-template --set DEV --template T.json --actor A` | 템플릿 확장으로 질의 생성([query-authoring-format.md](./query-authoring-format.md) 6절). 시험셋과 같은 정규화 질의문은 뺀다. 결과는 같은 접수 규칙으로 검사 | `query_rejected`, `set_kind_mismatch` | FR-007, FR-008 |
| `eval dev synthesize --set DEV --source-snapshot S --count N --actor A` | (P7, DR-16) 사내 모델 서버로 합성. 허용 목록 밖 호스트면 시작 거부 | `synthesis_host_not_allowed`, `synthesis_unavailable`, `query_rejected` | FR-008, FR-008a, 헌법 VII (3) |
| `eval dev import-synthesized --set DEV --file F.json --actor A` | 사내 모델로 만든 합성 결과 파일을 가져온다. 생성 정보(모델 이름, 서버 호스트)가 없거나 호스트가 허용 목록 밖이면 거부 | `provenance_missing`, `synthesis_host_not_allowed` | FR-008 |

- 출력에 `excluded_as_test_duplicate`(뺀 건수)를 넣는다(US4-6, SC-006).
- 합성 산출물은 평가 DB의 `eval_` 테이블에만 저장한다. 색인·검색 경로는 이 테이블을 읽지 않는다(R-12).

## 5. 정책과 트랙

| 명령 | 동작 | 거부 사유 코드 | 대응 |
|---|---|---|---|
| `eval policy register --file P.json --actor A` | 후보 검색 정책 snapshot을 불변 manifest로 등록(설계서 D-11, 기존 저장소 함수). 운영 정책 선택은 바꾸지 않는다. 출력 `config_hash`, `policy_id` | `invalid_policy` | FR-028, D-11 |
| `eval track list [--set NAME]` | 트랙과 교체 건, 시험셋 지정 시 현재 세대의 사용·남은 시도 수 | — | FR-025, SC-004 |
| `eval track register-swap --module-kind K --description D --approval-ref REF --actor A` | 모듈 교체 건 등록[DR-09] | `invalid_module_kind`, `module_kind_exhausted` | FR-023, FR-025 |

## 6. 실험 요청 파일(`--spec`)

```json
{
  "set_snapshot_id": "dev-main@3",
  "source_snapshot_id": "SNAP-20261120-01",
  "configs": {
    "current":   {"run_id": "…", "policy_config_hash": "…", "reindex_measurement_run_id": "…"},
    "candidate": {"run_id": "…", "policy_config_hash": "…", "reindex_measurement_run_id": "…"}
  },
  "seed": 20261108
}
```

- `configs`는 정확히 `current`·`candidate` 두 키. 후보가 둘 이상(배열·추가 키)이면 `candidate_limit`(FR-026, US4-2).
- `seed`는 개발셋 실험에서만 선택 항목(없으면 관문 기준 시드). 관문 시도에서 다른 값이면 `seed_not_allowed`[DR-05].
- `reindex_measurement_run_id`는 선택 항목. 관문 시도에서 없거나 콜드 빌드 조건(DR-14)을 만족하지 않으면 `cost_measurement_missing`.

## 7. 실험·관문(FR-011~FR-031)

| 명령 | 동작 | 성공 출력 | 거부 사유 코드 | 대응 |
|---|---|---|---|---|
| `eval experiment run --spec F.json --actor A [--purpose tuning\|bugfix_check\|transition --approval-ref REF]` | 개발셋 실험(`dev`) 또는 전환 확인(`transition_check`, DR-19). 횟수 제한 없음, 관문 시도로 세지 않음 | `{"experiment_id":…,"kind":"dev","status":"completed","metrics":{…},"mandatory_checks":{…},"costs":{…},"avg_evidence_length":{…},"paired":{…}}` | data-model 4.2의 1~7 | US2, FR-024, DR-20 |
| `eval gate attempt --spec F.json --track T --actor A` | 관문 시도(`gate`). 사전 점검 1~11 → 색인 쪽 필수 검사·모델 적재·예열(여기까지 오류는 시도로 안 셈) → 실험 행 기록 → 시험셋 두 번 실행(첫 질의별 결과 저장 때 시도 차수 확정, DR-24) → 판정 | 집계 출력: `{"experiment_id":…,"track":"track-2-reranker","generation":1,"attempt_number":2,"remaining_attempts":1,"judgment":"불합격","reasons":{"①":{…},"②":{…},…}}` | data-model 4.2의 1~11. `attempt_limit`이면 `detail.next_step="replenish_test_set"`. `stale_attempt_in_progress`이면 `detail.next_step="eval experiment abandon <id>"` | US3, US4, FR-017~FR-027 |
| `eval experiment show ID` | 실험 기록 요약. 시험셋 실험은 집계 출력만[DR-21] | 위와 같은 형식 + 구성 펼침·측정 환경 | 없음 → exit 5 | FR-028 |
| `eval experiment export ID --per-query --reason R --actor A` | 질의별 결과 출력. 시험셋이면 `eval_access_log`에 `per_query_export` 1건 기록 | 한 줄 JSON 배열(질의 ID, 구성별 순위·적중·지연·길이) | — | FR-015, DR-21 |
| `eval experiment reproduce ID --actor A` | 같은 구성·스냅숏·시드로 다시 실행(`reproduction`)하고 원 기록과 대조. 관문 시도로 세지 않음 | `{"experiment_id":…,"reproduces":ID,"match":{"per_query":true,"quality_metrics":true,"mandatory_checks":true,"mismatch_count":0}}` | 원 실험 `failed` → exit 3 `not_reproducible` | FR-025, FR-030, US4-7, US5-2 |
| `eval experiment abandon ID --reason R --actor A` | [DR-25] `running`으로 남은 중단된 실험 정리. 그 실험의 생존 잠금을 얻을 수 있을 때만(살아 있는 실행 프로세스 없음. 실행기는 행을 만들기 전에 잠금을 얻으므로 시작 직후에도 거부됨) `running → failed`(`abandoned`)로 바꾸고 중단 기록을 남김. 시도 횟수는 DR-24대로(`attempt_number`가 있었으면 셈). 트랜잭션 순서: 생존 키 `try` 트랜잭션 잠금 → 실험 행 `FOR NO KEY UPDATE` → 중단 기록 INSERT → 상태 UPDATE(research DR-25) | `{"experiment_id":…,"status":"failed","failure_code":"abandoned","attempt_counted":false,"remaining_attempts":3}` | 살아 있음 → exit 3 `experiment_alive`. `running`이 아님 → exit 3 `not_running`. PostgreSQL 교착(SQLSTATE `40P01`) → exit 4 `lock_conflict`, `detail.retryable: true`(트랜잭션 되돌림, 상태 변화·중단 기록 없음, 같은 명령을 다시 실행). `--reason`·`--actor` 없음 → exit 2. 없는 실험 → exit 5 | FR-026, FR-029 |
| `eval gate recompute ID` | 기록된 질의별 결과·관문 기준만으로 지표·부트스트랩·판정·사유를 다시 계산해 대조. 실험을 재실행하지 않는다 | `{"experiment_id":…,"judgment_match":true,"reasons_match":true,"recomputed":{"judgment":…,"reasons":{…}}}` | `gate`가 아님 → exit 3 `not_gate_experiment` | FR-015, US5-1, SC-002 |
| `eval resource show` | 현재 실행 자원(호스트, GPU UUID)과 허용 여부 | `{"resource_name":…,"allowed":true,"production_match":false,"gpus":[…]}` | — | FR-033, SC-011 |

- 관문 시도 출력의 `reasons`는 조건마다 `{"pass":bool,"measured":…,"threshold":…,"note":…}`. 근거 완전성 해당 없음은 `note: "not_applicable"`(spec Edge Case).
- 실행기가 생존 잠금 연결을 잃으면 다음 결과를 커밋하지 않고 멈춘 뒤 자기 실험을 `failed`(`liveness_lost`)로 바꾸려 시도하고 exit 4로 끝난다[DR-25]. 평가 DB 연결은 트랜잭션 모드 연결 풀러를 거치면 안 된다. 이는 설정 규칙(`OMF_EVAL_DATABASE_URL`은 평가 PostgreSQL 직접 주소, quickstart 5절)이며 시작 시 거부 검사는 없다. 실행 중 전용 연결의 `pg_backend_pid()`가 잠금 획득 때와 달라지면 잠금 상실(`liveness_lost`, exit 4)로 끝낸다(research DR-25).
- 새 실험의 생존 잠금은 기다리지 않고(`pg_try_advisory_lock`) 얻는다. 다른 세션이 같은 키를 쥐고 있으면 새 실험 id로 다시 시도하고, 5회 모두 실패하면 실험 행 없이 exit 4 `liveness_lock_unavailable`(`detail.retryable: true`, 관문이면 `attempt_consumed: false`)로 끝난다. 실험 종류와 무관한 코드이며 `attempt_in_progress`(같은 트랙의 진행 중 관문 시도, 사전 점검 9)와 다르다[DR-25].
- 사전 점검 9를 함께 통과한 같은 트랙의 두 관문 시도 중 뒤쪽 실험 행 INSERT가 유일성 위반(SQLSTATE `23505`)으로 실패하고 위반 제약 이름이 진행 중 유일 인덱스 `eval_experiments_running_track_uq`이면 exit 3 `attempt_in_progress`로 끝난다. 다른 제약의 `23505`는 예기치 않은 실패(exit 4)다. 실험 행은 남지 않고 시도로 세지 않으며(`attempt_consumed: false`) 자기 생존 잠금은 푼다(data-model 3.12).
- 실행기는 질의별 결과와 완료 기록을 커밋하기 직전마다 생존 잠금을 확인한다. 완료 직전 확인에서 잠금이 없으면 완료를 커밋하지 않고 `liveness_lost`로 끝난다[DR-25].
- 실험 중 질의 하나라도 비공개 평가 경로 오류가 나면 대체하지 않고 실험을 `failed`로 끝낸다(R-14). 출력 exit 4, `detail.failure_code`, 관문 시도면 `detail.attempt_consumed`(질의별 결과가 하나라도 저장됐으면 `true`).
- 어떤 `eval` 명령도 활성 포인터, 운영 정책 설정, `source_profiles`를 바꾸지 않는다(FR-021, FR-032).

## 8. 계약 테스트 대응

`tests/contract/cli/test_eval_commands.py`(신규)가 각 명령의 인자, 성공 출력 키 집합, exit code, 표준 오류에 비밀값·질의문·원문이 없음을 확인한다. 시험셋 열람 기록은 C-CLI-08이 확인한다. 세부 사례는 [plan.md](../plan.md) `테스트 전략`의 C-CLI 항목을 따른다.
