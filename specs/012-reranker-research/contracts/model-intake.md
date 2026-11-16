# Contract: 리랭커 모델 반입·무결성 매니페스트·오프라인 로딩 — spec 012

**상태**: 승인(사용자 2026-11-13, DR-10 권장안). 승인 목록은 사용자 결정 2026-11-12. 운영 개시 전에는 반입·보관 장비가 운영 서버(`/home/phoebe-deploy/experiments` 아래)다(DR-12, 사용자 결정 2026-11-13). 운영 서버의 반입 작업은 배포 하네스에 따라 작업마다 승인받는다. 기존 임베딩 모델 준비 방식(`infrastructure/embedding/prepare.py`, `manifest.py`)을 리랭커용으로 일반화한다. 기존 임베딩 경로 코드는 바꾸지 않는다.

## 용어

| 용어 | 뜻 |
|---|---|
| 반입 | 승인 목록의 (모델 id, revision)을 Hugging Face에서 받아 사내 모델 저장소에 두는 일. 사내로 들이는 것이며 자료를 밖으로 보내는 일이 아니다. |
| 반입 지점 | 외부 다운로드가 허용된 사내 장비. 연구 장비 자체(운영 개시 전에는 운영 서버, DR-12)이거나 사용자가 정하는 반입용 장비다. |
| 모델 저장소 | 연구 장비의 사내 디렉터리(운영 개시 전에는 운영 서버 `/home/phoebe-deploy/experiments` 아래, 운영 배포 경로와 분리). 반입 담당자만 쓰기 권한을 가진다. |
| 무결성 매니페스트 | 파일마다 경로·크기·SHA-256을 적은 불변 JSON. 로딩 전에 대조한다. |
| LFS | Git Large File Storage. Hugging Face가 큰 파일(가중치)을 저장하는 방식이며 파일마다 SHA-256을 알려 준다. |
| `auto_map`, 원격 코드 | 모델 설정 파일이 저장소 안 Python 코드를 실행해 모델을 만들라고 지정하는 항목과 그 코드. 이 계약은 원격 코드를 실행하지 않는다. |
| 오프라인 로딩, fail-closed | research.md 용어표와 같다. |

## 1. 반입 절차(O1)

| 순서 | 확인 | 실패 시 |
|---|---|---|
| 1 | 요청한 (모델 id, revision)이 승인 목록 상수([data-model.md](../data-model.md) 1절)에 있다 | 거부, 다운로드 없음 |
| 2 | `snapshot_download(repo_id, revision=고정 SHA)`로 임시 디렉터리에 받는다. `trust_remote_code` 관련 동작 없음 | 임시 디렉터리 삭제 |
| 3 | 받은 스냅숏의 커밋 SHA = 승인 revision | 삭제, 거부 |
| 4 | 파일 확장자가 허용 목록(`.json`, `.model`, `.safetensors`, `.txt`) 안. 그 밖 파일(`.py`, `.bin`, `.jinja` 등)이 있으면 목록을 보고하고 멈춘다(필요하면 허용 목록 확장을 따로 승인) | 삭제, 보고 |
| 5 | `config.json` 등에 `auto_map`이 없다 | 삭제, `remote_code_required` 보고 |
| 6 | 파일마다 SHA-256 계산, 반입 시 Hugging Face API가 알려 준 LFS SHA-256과 대조 | 삭제, 거부 |
| 7 | 파일 수 ≤ 256, 파일당 ≤ 4 GiB, 합계 ≤ 모델별 `max_total_bytes` | 삭제, 보고 |
| 8 | 매니페스트를 정규화 JSON으로 쓰고, 스냅숏과 매니페스트를 모델 저장소에 원자적으로 옮긴다(기존 방식) | 부분 반입 없음 |

반입 기록(반입 시각·담당·장비, 대조 결과)을 매니페스트 옆에 남긴다. 비밀값(Hugging Face 토큰 등)은 쓰지 않거나, 쓰더라도 저장소·로그·매니페스트에 기록하지 않는다.

## 2. 매니페스트 형식

```json
{
  "schema": "omf-retrieval.reranker-model-manifest",
  "version": 1,
  "model_id": "nlpai-lab/KURE-Reranker-nano",
  "revision": "8514b9e113f3b27c441ed9c2b50f81d80388b2fd",
  "files": [
    {"path": "config.json", "size": 0, "sha256": "<64자 16진수>"},
    {"path": "model.safetensors", "size": 0, "sha256": "<64자 16진수>"}
  ],
  "hub_lfs_check": "matched",
  "remote_code_required": false
}
```

(크기·해시는 예시 자리 표시다.)

## 3. 로딩 규칙(P4·P5, 매 시작)

| 규칙 | 내용 |
|---|---|
| 승인 목록 대조 | 기술자의 (모델 id, revision) = 승인 목록 상수 = 매니페스트 값 |
| 파일 대조 | 매니페스트의 모든 파일이 있고 크기·SHA-256 일치, 여분 파일 없음 |
| 오프라인 | Hugging Face 오프라인 환경변수 설정, 로딩 인자 `local_files_only=True` |
| 원격 코드 | 로딩 인자 `trust_remote_code=False` |
| 역할 | `upper_bound_only` 모델은 평가 모드에서만 |
| 실패 | contracts/reranker-module.md 5절의 시작 거부 코드, 원문·경로 비밀 없음 |

## 4. 운영 반입(S4, 승인 뒤)

"적용 권고"와 사용자 승인을 받은 최종 후보 1개만 운영 배포 경로(운영 API가 읽는 모델 위치)로 옮긴다. 같은 매니페스트로 대조하고, 운영 배포 경로로는 네트워크 다운로드를 하지 않는다. 다른 후보·상한 확인 모델은 운영 배포 경로에 두지 않는다. 운영 개시 전 예외 기간에 운영 서버를 연구 장비로 쓰면 연구용 반입 모델(다른 후보·상한 확인 모델 포함)은 운영 서버의 `/home/phoebe-deploy/experiments` 아래 연구용 모델 저장소에 두고 운영 배포 경로와 섞지 않는다(`002` FR-033 "운영 개시 전 예외", 사용자 결정 2026-11-13, 조건 ⑤). 실제 반입·배포 절차는 `AGENTS.md`의 배포 하네스와 `009` 계획을 따른다.

## 5. 테스트 대응

U-REG-01~03, U-NET-01(plan `테스트 전략`). O1 실행 결과는 quickstart 2절로 검증 Agent가 다시 확인한다.
