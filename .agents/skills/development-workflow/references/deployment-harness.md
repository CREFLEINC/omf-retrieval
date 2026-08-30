---
title: OMF Retrieval 공유 배포 하네스
author: Codex — 사용자 승인 반영
modified_at: 2026-08-30 19:11 KST
version: v1.1
audience: 프로젝트 관련자
---

# OMF Retrieval 공유 배포 하네스

이 문서는 OMF Retrieval 공유 서버 배포의 순서, 보존 좌표와 중단 조건을 한곳에 고정한다.
명령 모음이나 자동 실행 스크립트가 아니며, 사용자 승인과 실행·검증 Agent 분리를 대체하지 않는다.

## 용어

- **하네스**: 승인, 실행 순서, 검증 증거와 중단·복구 조건을 일관되게 적용하는 절차 경계다.
- **preflight**: 서버 상태를 바꾸기 전에 전제와 불변 조건을 읽기 전용으로 확인하는 사전 점검이다.
- **snapshot**: 변경 전 Git, container, DB, index와 설정 좌표를 비밀값 없이 기록한 상태 증거다.
- **active run**: API가 현재 검색 대상으로 사용하는 완료된 색인 실행이다.
- **search policy**: 질의 임베딩, 후보 수, RRF 결합과 유사도 하한선처럼 검색 시점의
  동작을 정하는 immutable manifest다. RRF는 여러 검색 순위를 합치는 Reciprocal Rank Fusion이다.
- **smoke**: 배포 기능이 최소 사용자 흐름을 만족하는지 확인하는 6개 대표 질의다.
- **publish**: 검증을 끝낸 API를 `192.168.1.185:9090` listener에 연결하는 단계다.

## 적용 경계

배포, 재배포, 장애 복구, rollback 또는 CUDA 검색 보정을 수행하는 실행형 요청에서
이 문서를 전부 읽는다. 현재 SHA, 서버 상태나 불변 좌표가 달라지면 실행을 멈춘다.

다음 near-miss는 하네스를 실행하는 요청이 아니다.

- 배포 상태 확인만 요청한 경우
- 임계값이나 calibration의 개념 설명만 요청한 경우
- 서버를 보지 않고 선택지만 비교해 달라는 경우
- 승인·반려 또는 기존 결과를 짧게 다시 말해 달라는 경우

## 배포 정본 좌표

| 항목 | 승인된 좌표 |
|---|---|
| SSH alias | `phoebe-onpremise-test` |
| 서비스 경로 | `/opt/omf-retrieval` |
| 공유 endpoint | `192.168.1.185:9090` |
| 호스트·GPU | Ubuntu 22.04 · NVIDIA GeForce RTX 4090 · `cuda:0` |
| 영구 보관 root | `/home/storage_disk3/omf-retrieval-disk` |
| OMF source | 서버 clone · commit `a8f46f23cd3fb9c5f7042e987dff8103d23f0fa2` |
| embedding model | `Qwen/Qwen3-Embedding-0.6B` · revision `97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3` · 1024차원 |
| 보존 active run | `427f2c4a-ab06-486a-9801-4bde3ef17d63` |
| 보존 counts | 문서 158 · section 4,202 · chunk 5,584 · embedding 5,584 |
| 공개 상태 | API 중단 · 외부 listener 중단 · publish 금지 |

위 값은 기대값이지 현재 사실을 대신하지 않는다. 매번 preflight로 다시 측정한다.

## 비밀정보 경계

정본화하는 것은 경로와 POSIX mode뿐이며 내용은 정본화하지 않는다.

- `/opt/omf-retrieval/.env`: mode `0600`
- `/opt/omf-retrieval/secrets/postgres_password`: mode `0600`
- `/opt/omf-retrieval/secrets/audit_hmac_key`: mode `0600`
- 기존 deployment token 파일: 배포 전 서버에서 승인된 절대경로를 확인하고 mode
  `0600`을 검증한다. 현재 문서에 경로를 추측해 추가하지 않는다.

비밀 파일은 존재 여부, regular file 여부, owner와 mode만 검사한다. 어떤 비밀도
값을 읽거나 출력하지 않는다. 로그에는 token, password, HMAC key, 전체 DB URL,
`.env` 내용과 command environment를 남기지 않는다.
검색 검증에서는 인증 성공 여부와 안전한 HTTP status·code만 증거로 남긴다.

## 정상 실행 순서

아래 번호는 gate 순서다. 앞 단계의 실행 증거와 별도 검증 Agent의 확인이 없으면
다음 단계로 넘어가지 않는다. 외부 변경 직전과 최종 공개 직전에는
단계별 사용자 확인을 받는다.

1. **승인 컨텍스트** — 오케스트레이터가 `AGENTS.md`, 승인 계획, 이 reference와
   v2.1 정본을 확인하고 실행 Agent와 검증 Agent를 별도 인스턴스로 배정한다.
2. **읽기 전용 preflight** — SSH 대상, 배포 SHA, clean tree, Ubuntu, NVIDIA runtime,
   GPU 0, 영구 mount, secret metadata, PostgreSQL health와 외부 listener 중단을
   변경 없이 확인한다.
3. **상태 snapshot** — image·container 상태, Alembic revision, active pointer,
   source commit, model cache, `local-agent`, deployment token metadata와 네 count를
   비밀값 없이 기록한다.
4. **서버 image build** — 승인 SHA에서 새 image를 서버에서 빌드하되 API와 외부
   listener는 계속 중단 상태로 둔다. 기존 model cache를 재사용한다.
5. **additive 0003 migration** — PostgreSQL backup·복구 가능성을 확인한 뒤 upgrade만
   적용하고 immutable search policy backfill 결과를 기록한다.
6. **active run/count invariants** — active run ID, source commit과
   158/4,202/5,584/5,584가 snapshot과 같은지 확인한다.
7. **CUDA raw calibration** — 보존된 embedding을 재사용해 GPU 0에서 6개 질의의
   lane별 raw score와 separation을 측정한다.
8. **search policy apply** — 검증된 CUDA floors와 calibrated status로 새 immutable
   policy를 선택한다. index identity와 embedding pipeline은 호출하지 않는다.
9. **internal validation** — 외부 listener를 열지 않은 격리된 내부 endpoint에서
   live, 인증된 ready와 search policy 좌표를 확인한다.
10. **6개 smoke** — 정상 5개 질의의 top 5 직접 근거와 문서에 없는 1개 질의의
    `no_evidence`를 원문 provenance와 대조한다.
11. **security** — 401, 403, 409, 422, 503 응답과 로그에 원문, token, DB URL 또는
    host path가 노출되지 않는지 확인한다.
12. **restart** — 같은 image와 policy 환경설정으로 API를 내부에서 재시작하고 ready,
    active run과 policy 좌표가 유지되는지 확인한다.
13. **publish** — 모든 gate와 독립 검증이 끝난 뒤에만 승인받아
    `192.168.1.185:9090`을 연다. 외부에서 live, ready와 대표 검색을 한 번 더 확인한다.

## Preflight 불변 조건과 중단

`SHA drift`, `GPU drift`, `secret mode drift`, `count drift` 중 하나라도 있으면 즉시
중단한다. read-only preflight에서 실패하면 server build, migration, API start와
publish를 실행하지 않는다. 기존 model cache, `local-agent`, deployment token,
active run과 embedding은 모든 단계에서 보존한다.

다음 동작은 이 하네스의 배포·복구 경로에서 금지한다.

- `omf-retrieval index`: 재색인과 embedding 재생성을 유발할 수 있다.
- `omf-retrieval client create`: 기존 client와 deployment token을 재발급하지 않는다.
- `alembic downgrade` 또는 DB downgrade: additive migration을 역전하지 않는다.
- `docker compose down -v`: 영구 volume을 제거할 수 있다.
- volume 삭제, data 삭제, PostgreSQL 초기화 또는 영구 mount 교체

migration 실패는 공개 중단 조건이다. `calibration separation 실패` 또는 smoke 실패가
발생하면 원인과 secret-safe 증거만 기록하고 publish 금지 상태를 유지한다. Migration,
calibration 또는 smoke의 재시도는 실패 원인과 승인 범위를 다시 확인한 뒤에만 한다.

## 복구

정책 적용이나 API 검증 실패 시 이전 image를 선택하고 이전 policy 환경설정을
복원한다. 복구 범위는 **API-only**이며 API만 재시작한다. active run과 embedding을 보존하고
DB downgrade, migration rollback, 재색인과 client 재생성을 수행하지 않는다.
복구한 내부 endpoint가 검증되기 전에는 외부 listener를 열지 않는다.

## 최초 bootstrap 경계

기본 경로는 기존 active run을 보존하는 redeploy다. Active run이나 인증 client가
없는 최초 bootstrap은 별도 계획과 명시적 승인이 필요하며 이 문서가 승인하지 않는다.
Bootstrap에서도 기존 model cache가 있으면 `model prepare`를 다시 실행하지 않고,
기존 deployment token이 있으면 `omf-retrieval client create`로 재발급하지 않는다.
`omf-retrieval index`와 신규 client 생성은 해당 자산이 없다는 read-only 증거와 별도
승인이 모두 있을 때만 bootstrap 절차에 포함한다.

## 현재 corrective gate 상태

| Gate | 상태 | 증거 범위 |
|---|---|---|
| calibration code/image packaging | PASS | 로컬 독립 검증 |
| Compose policy injection | PASS | 로컬 독립 검증 |
| server preflight | NOT RUN | 실제 서버 미접속 |
| server build | NOT RUN | 실제 서버 image 미생성 |
| server migration | NOT RUN | 실제 DB 미변경 |
| server CUDA calibration | NOT RUN | 실제 GPU 미실행 |
| server smoke | NOT RUN | 실제 API 미실행 |
| 공유 API | API 중단 | 기존 중단 상태 보존 |
| 공유 listener | 외부 listener 중단 | `192.168.1.185:9090` 미공개 |
| publish | publish 금지 | 모든 server gate 검증 전 |

두 로컬 PASS는 구현과 packaging의 정적·무외부 계약만 확인한 결과다. 실제 서버의
preflight, build, migration, CUDA calibration 또는 smoke를 수행했다는 뜻이 아니다.
서버 gate가 모두 실행·독립 검증되기 전까지 API와 listener 중단을 유지한다.

## 단계 증거

각 gate는 실행 명령의 종류와 exit code, 시작·종료 KST, 대상 commit/image digest,
secret-safe 관찰값, 기대값 비교, 실행 Agent와 검증 Agent 결과를 남긴다. 실패나
미실행은 PASS로 축약하지 않는다. 최종 보고에는 보존 active run과 네 count,
search policy ID·config hash, 6개 smoke 순위와 외부 endpoint 결과를 포함한다.
