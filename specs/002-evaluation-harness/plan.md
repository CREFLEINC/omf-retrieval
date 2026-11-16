# Implementation Plan: 평가 하네스 — 평가셋·지표·실험 기록·적용 관문(evaluation-harness)

**Branch**: `002-evaluation-harness`(명세 식별 이름. 별도 git 브랜치는 만들지 않았고 현재 `001-data-registration` 브랜치 작업 트리에서 작성했다) | **Date**: 2026-11-10 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/002-evaluation-harness/spec.md`

**Note**: This template is filled in by the `/speckit-plan` command; its definition describes the execution workflow.

**상태**: **결정 승인 완료(사용자 2026-11-10).** [research.md](./research.md) 3절의 결정 요청 DR-01~DR-25를 모두 권장안(DR-02·DR-03은 권장 A)으로 승인했고, spec 모호점 A-1·A-15도 권장안으로 해소했다(spec 반영). `[DR-xx]`는 그 승인 결정에 의존한다는 표시다. tasks 단계(`speckit-tasks`)로 넘어갈 수 있다. **값 입력 대기**(결정 미승인이 아니며 tasks를 막지 않음): DR-04 운영 GPU 총 메모리 실측값(그 전 관문 시도는 사전 점검 `gate_setting_missing`으로 거부), DR-03 승인된 연구 자원의 구체 호스트·GPU 목록(그 전 실험·관문 명령은 `resource_not_allowed`로 시작 거부, fail-closed). 갱신(사용자 결정 2026-11-13): spec FR-033 "운영 개시 전 예외"에 따라 운영 개시 전에는 운영 서버를 예외 표시(`pre_launch_exception: true`)를 붙여 허용 목록에 넣고 운영 목록은 그대로 둔다. 운영 서버의 호스트·GPU UUID와 운영 GPU 총 메모리는 첫 접속 때 `nvidia-smi`로 읽어 기록·보고하고, 총 메모리 −10%를 `gpu_memory_limit_mib`로 둔다([research.md](./research.md) DR-03·DR-04). 운영 개시 뒤 쓸 다른 연구 자원 목록만 사용자 입력 대기다.

**개정(2026-11-16, `012` 연동 ①~⑧)**: `012` 리랭커 연구의 변경 요청(사용자 승인 2026-11-13)을 별도 작업 단위로 반영했다(`012 연동 개정` 절). 결정 요청 DR-26~DR-28은 **사용자 승인(2026-11-16)**을 받았다(DR-26·DR-27 권장안, DR-28은 리랭커 교체 건 포함·확인 횟수 시험셋 세대별의 변경 포함). 그에 의존하는 새 Phase는 P4c, P14~P19다. 기존 Phase P1~P13과 DR-01~DR-25는 바뀌지 않았다. spec 충돌 후보 A-16은 같은 날 처리 결정(FR-026 그대로, 트랙별 사전 조건으로 읽음)으로 닫았다. ⑨는 다음 개정 단위다.

## 용어

| 용어 | 뜻 |
|---|---|
| FR, SC | spec의 기능 요구사항과 성공 기준. 이 계획의 추적 단위다. |
| DR | 결정 요청(Decision Request). [research.md](./research.md) 3절. DR-01~DR-25 모두 사용자 승인(2026-11-10, 권장안). |
| R-xx | research.md 2절의 기술 판단(새 의존성·정책 변경 없음, 계획 승인 시 함께 승인). |
| A-xx | research.md 4.2절의 spec 모호점. |
| 설계서 | 공통 모듈 구조 설계서 `docs/design/2026-11-09-common-module-architecture.md` v0.3(사용자 승인 2026-11-09). `D-xx`는 승인 결정, `O-xx`는 열린 항목. |
| 헌법 | `.specify/memory/constitution.md` v2.1.0(작업 트리). 원칙 I~VII. VII (3)은 평가·연구용 자료 생성(개발셋 질의 합성)을 사내 모델로만 하고 생성물을 색인·조회 응답·근거에 넣지 않게 한다. |
| MVP | 현재 운영 중인 최소 기능 제품(Git 고정 commit OMF 색인 + `POST /v1/search`). |
| RAG, LLM, GPU | spec 용어표와 같다(검색 기반 근거 조회, 대규모 언어 모델, 그래픽 처리 장치). |
| Recall@5, MRR, 근거 완전성, `no_evidence` 비율, p95, %p, 짝 비교, 불일치율, 부트스트랩, 시드, 신뢰구간 | spec 용어표와 같다. MRR은 이 계획에서 MRR@20(20위까지)으로 계산한다[DR-11]. |
| 평가 실행기 | 이 계획이 만드는 서버 측 CLI 묶음 `omf-retrieval eval ...`([contracts/eval-cli.md](./contracts/eval-cli.md)). 공개 HTTP API가 아니다. |
| 비공개 평가 경로 | 평가 실행기만 쓰는, run id를 지정해 활성이 아닌 색인 run에 질의하는 내부 포트(설계서 D-04, [contracts/private-evaluation-path.md](./contracts/private-evaluation-path.md)). |
| run | 색인 실행 1회와 결과물(`index_runs`). 상태 `building, ready, active, previous, archived, failed`. |
| 검색 구성 | (run, 검색 정책 manifest) 쌍. spec의 "구성". |
| 검색 정책 manifest | 질의 시점 설정의 불변 기록(`search_policy_manifests`), `config_hash`로 식별. |
| 원본 자료 스냅숏, 평가셋 스냅숏 | spec 용어표의 스냅숏 두 종류. 저장 구조는 [data-model.md](./data-model.md) 3.1·3.8. |
| 시험셋 세대 | 관문 시도 횟수를 세는 단위. 승인된 보충으로만 바뀐다[DR-08]. |
| 트랙 | 연구 트랙 ①~③과 모듈 교체 건을 함께 부르는 말. 시도 횟수의 주체다. |
| 사전 점검 | 실험·관문 시도 전 확인(data-model 4.2). 사전 점검 거부는 시도로 세지 않는다. |
| 콜드 빌드 | 산출물 재사용 없이 처음부터 만드는 색인 빌드. 재색인 시간 측정용[DR-14]. |
| 모델 레지스트리 | 평가 실행기만 쓰는, 질의 임베딩 모델을 코드 안 목록에서 고르는 장치(설계서 D-05, D-08). |
| 순위 결정 함수 | 하한선 → RRF(Reciprocal Rank Fusion, 순위 결합) → `strict` → parent 묶기 → 상위 `limit`을 하는 순수 함수(`rank_evidence`). 운영과 평가가 함께 쓴다[DR-18]. |
| 정확 산술 | 비율·차이를 분수(`fractions.Fraction`)로 계산해 경계값 비교 오차를 없애는 방식(R-01). |
| CLI | 명령줄 인터페이스(Command-Line Interface). 터미널에서 명령으로 실행하는 도구. |
| ORM | 객체–관계 매핑(Object-Relational Mapping). DB 테이블을 Python 클래스로 다루는 방식(SQLAlchemy). |
| CTE | 공통 테이블 식(Common Table Expression). SQL의 `WITH 이름 AS (...)`로 쿼리 안에 임시 결과에 이름을 붙이는 문법. 기존 검색 SQL의 `_AUTHORIZED_SCOPE`가 이것이다. |
| advisory lock | PostgreSQL의 응용 정의 잠금. 같은 키를 쥔 다른 트랜잭션을 기다리게 해 동시 실행을 직렬화한다. |
| 세션 잠금, 트랜잭션 잠금 | advisory lock의 두 종류. 세션 잠금은 연결이 살아 있는 동안(또는 풀 때까지), 트랜잭션 잠금은 트랜잭션이 끝날 때까지 유지된다. |
| 생존 잠금 | 실행 중인 실험이 쥐는 세션 잠금. 잠금이 없으면 실험이 중단된 것으로 본다[DR-25]. |
| TCP keepalive | 조용한 연결에 주기적으로 확인 신호를 보내 상대가 사라졌는지 알아내는 TCP 기능. |
| 연결 풀, 연결 풀러, `NullPool` | 연결 풀은 DB 연결을 재사용하는 장치(SQLAlchemy 기본 동작). 연결 풀러는 PgBouncer처럼 앞단에서 여러 클라이언트가 서버 연결을 나눠 쓰게 하는 별도 프로그램. `NullPool`은 재사용 없이 매번 새 연결을 쓰는 SQLAlchemy 설정. |
| KST | 한국 표준시(UTC+9). 스냅숏 ID의 날짜 기준이다. |
| NFC, NFD | 유니코드 정규화 형식. NFC는 한글 음절을 한 글자로 합친 형태, NFD는 자모로 나눈 형태다. 눈에 같은 글자가 바이트로는 다를 수 있어 비교 전에 NFC로 맞춘다. |
| append-only | INSERT만 허용하고 UPDATE·DELETE를 트리거로 거부하는 저장 방식. |
| TDD | 테스트 주도 개발. RED(구현 부재로 assertion 실패 확인) → GREEN(최소 구현) → REFACTOR(동작 유지 정리). |
| RED | 구현이 없어서 생기는 assertion 실패. import·문법·fixture 오류는 RED가 아니다. |
| 계약 테스트 | 공개 인터페이스(CLI 출력·exit code, 기존 HTTP 계약)가 계약 문서와 같은지 확인하는 테스트(`tests/contract`). |
| 통합 테스트 | 실제 PostgreSQL(`compose.test.yaml`)과 함께 저장·트리거·SQL을 확인하는 테스트(`tests/integration`). |
| 인수 하네스 | SC를 실제 CLI와 PostgreSQL(작은 fixture 색인)로 판정하는 테스트 묶음. [quickstart.md](./quickstart.md) 2절. |
| fixture 색인 | 테스트가 만드는 작은 git 저장소와 결정적 가짜 임베딩 제공자로 만든 테스트 전용 run. GPU·실제 모델 없음. |
| Phase | 이 계획의 구현 단계 묶음. 각 Phase는 하나의 의도와 독립 검증을 가진다. |
| `012` 연동 ①~⑨ | `012` 리랭커 연구의 `002` 변경 요청 항목 번호(`specs/012-reranker-research/contracts/eval-linkage-request.md`). 이 개정은 ①~⑧을 다룬다. |
| 후보 목록, 리랭커, 리랭크 적용 상태, 통과 모듈, 진단 지표, 참고 지표, Recall@N(후보 목록), 탐색·확인 개발셋, 확인 실험, 확인 소진, 후보 대조 키, binary64 | research.md 용어표와 같다(`012` 연동 개정 2026-11-16). |

## Summary

검색 품질 연구 3건(`012` 리랭커, `013` 파싱·영역 판별·청킹, `014` 임베딩)과 모듈 교체가 함께 쓰는 평가 체계를 만든다. 질의 작성자는 spec 항목 이름을 그대로 키로 쓰는 JSON 파일[DR-10]로 시험셋 질의를 제출하고, 시스템은 원본 자료 스냅숏 목록(FR-003b)을 보여 주고 질의 ID를 매기며 형식·좌표 규칙(FR-003~FR-007)을 검사해 파일 단위로 접수한다. 접수·제거마다 불변 평가셋 스냅숏이 생긴다. 연구자는 (run, 정책) 구성 두 개를 같은 평가셋 스냅숏·원본 자료 스냅숏으로 비공개 평가 경로(설계서 D-04)에서 비교해 Recall@5·MRR@20·근거 완전성·의도별 Recall@5·`no_evidence` 비율·지연 p95·재색인 시간·GPU 메모리·반환 근거 평균 길이와 필수 검사 5종을 얻는다. 관문 시도는 개선 폭 기준 v1.0의 다섯 조건과 짝 비교 백분위 부트스트랩[DR-05]으로 "적용 권고 / 불합격 / 유의한 차이 없음(현재 유지)"을 판정하고, 같은 시험셋 세대에서 트랙(건)별 3회로 제한한다[DR-08·DR-09]. 모든 스냅숏·질의·실험 기록은 DB 트리거로 불변이며 기록만으로 판정을 다시 계산할 수 있다.

개정(2026-11-16, `012` 연동 ①~⑧): 리랭커 연구를 위해 비공개 평가 경로가 근거 묶기 전 후보 목록(최대 100개)을 함께 돌려주고[DR-26], 실험 기록에 Recall@N(후보 목록) 진단과 참고 지표 MRR@3·nDCG@3, 질의별 리랭크 점수·시간·적용 상태를 남긴다[DR-27]. 진단·참고 지표는 관문 판정에 쓰지 않는다. 개발셋에 탐색·확인 역할을 두고, 리랭커 트랙의 관문 시도마다 같은 후보의 확인 실험 1회를 먼저 요구하며 확인 기록과 관문 시도를 1:1로 소진한다[DR-28]. 문서에 없는 질의 합성(R-20), 단일 구성 진단(R-18), 콜드 빌드 측정 run 공유(R-19), 평가 모드 리랭커 오류 코드(DR-26)를 더한다.

기술 접근은 기존 스택(Python 3.12, SQLAlchemy 2.0, Alembic, PostgreSQL, typer) 재사용이다. 새 외부 의존성은 두지 않는다(승인 2026-11-10. 부트스트랩 난수·정확 산술·JSON은 표준 라이브러리, GPU 측정은 `nvidia-smi` 호출, 합성 호출은 기존 httpx). 평가 데이터는 플랫폼 PostgreSQL 스키마의 새 `eval_` 테이블[DR-01]에 두고, 평가는 연구 자원의 별도 평가 DB에서 한다[DR-02]. 운영 검색 코드는 순위 결정 함수와 SQL 조각만 추출하며 운영 SQL 문자열과 공개 API는 그대로다[DR-18]. 관문은 운영 구성을 바꾸지 않는다(FR-021).

## Technical Context

**Language/Version**: Python `>=3.12,<3.13`(기존 `pyproject.toml`)

**Primary Dependencies**: 기존 SQLAlchemy `2.0.x`, Alembic `1.19.x`, psycopg 3, typer, pydantic-settings, httpx(합성 클라이언트), sentence-transformers·torch(모델 레지스트리의 질의 임베딩), markdown-it-py(필수 검사의 블록 경계). 새 의존성 없음(승인). 대안으로 numpy(DR-05 대안 2)·openpyxl(DR-10 대안 2)을 검토했고 채택하지 않았다(DR-05·DR-10 권장안 승인).

**Storage**: 플랫폼 PostgreSQL 스키마에 새 테이블 17개(`eval_` 접두사)[DR-01]. 새 Alembic revision 2개: P4a `0006_evaluation_datasets`, P4b `0007_evaluation_experiments`(001이 예약한 `0004`·`0005` 다음, 구현 시점 head에 맞춤 R-13). 개정(`012` 연동): P4c `0008_evaluation_research_linkage`가 `eval_` 테이블에 선택 열을 더하고 테이블 2개(확인 실험·확인 소진)를 만든다(합계 19개)[DR-27·DR-28]. 평가 실행 DB는 연구 자원의 별도 평가 PostgreSQL[DR-02].

**Testing**: pytest(unit, contract, integration), ruff. 통합 테스트는 `compose.test.yaml` PostgreSQL과 `OMF_RETRIEVAL_TEST_DATABASE_URL`. 색인이 필요한 테스트는 작은 git fixture 저장소와 결정적 가짜 임베딩 제공자(테스트 전용 레지스트리 항목)로 만든 fixture 색인을 쓴다. GPU 측정은 `nvidia-smi` 출력 표본을 주입해 단위 테스트한다.

**Target Platform**: 연구 자원(Linux, GPU 선택)의 서버 셸과 macOS 개발 환경. 운영 서버(`192.168.1.185`)에서는 실행하지 않는다[DR-03]. 예외: 사용자가 운영 개시를 알리기 전까지는 spec FR-033 "운영 개시 전 예외"에 따라 운영 서버에서 실행할 수 있다(평가 DB는 별도 컨테이너·포트·볼륨, 연구용 파일은 `/home/phoebe-deploy/experiments`, 서버 작업마다 배포 하네스 승인. 사용자 결정 2026-11-13).

**Project Type**: CLI(기존 typer 앱에 `eval` 하위 명령) + 내부 라이브러리(평가 domain·application). 공개 HTTP 경로 추가 없음.

**Performance Goals**: 승인된 수치 없음. 참고 목표(측정·기록만): 부트스트랩 10,000회 × 정답 있음 질의 400개 계산 60초 이내, 실험 1회(450질의 × 2구성 × 2회)는 질의 처리 시간에 비례.

**Constraints**: 외부 네트워크 연결 0건(평가 DB, 허용 목록의 사내 모델 서버 제외). 운영 DB 사용 0건. 운영 GPU 사용 0건(단, spec FR-033 "운영 개시 전 예외" 기간의 운영 서버 실행은 예외이며 조건 ①~⑥을 따른다, 사용자 결정 2026-11-13). 활성 포인터·운영 정책 선택·감사 테이블 쓰기 0건. 실험 기록 수정·삭제 0건. 출력·로그에 비밀값 0건, 시험셋 질의별 결과는 내보내기 명령으로만[DR-21].

**Scale/Scope**: 시험셋 450질의 이상(정답 있음 400 + 문서에 없음 50), 개발셋 수천 질의 가능. 원본 자료 스냅숏은 OMF 158문서(현재 고정 commit) 수준부터. CLI 명령 25개, 새 테이블 17개, 연구 트랙 3개. 개정(`012` 연동): 새 명령 없이 기존 명령에 선택 인자를 더하고(`--role`, `--purpose holdout_check`, `--track`, `--mode`, `--per-intent`, `--seed`), 테이블은 19개. 리랭커 트랙의 개발셋은 탐색 1,100질의·확인 550질의 규모(`012` 결정).

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| 원칙 | 점검 내용 | 판정(설계 전) | 근거·조건 |
|---|---|---|---|
| I. 계획 보고와 사용자 승인 게이트 | 데이터 모델·정책·계약·운영 방식·외부 의존성을 단독 확정하지 않는가 | 통과(DR 승인 2026-11-10) | 저장 위치, 평가 DB 배치, 자원 차단, GPU 한도, 부트스트랩, 길이 단위, 좌표 기준, 보충 규칙, 교체 건 단위, 작성 형식, 지표 세부, 스냅숏 정합, 필수 검사 세부, 재색인 측정, 관문 기준 보관, 합성 모델, CLI 형식, 비공개 경로 구현, 첫 전환, 버그 수정, 결과 노출, factory 의존, 개발셋 겹침 처리, 시도를 세는 시점, 중단된 실험 정리를 DR-01~DR-25로 올렸고 사용자가 2026-11-10 모두 권장안으로 승인했다. 키가 없을 때의 거부 사유 해석(A-15)과 SC-003의 "다른 문서" 범위(A-1)도 같은 날 사용자 결정으로 해소했다. DR-03·DR-04의 설정값은 입력 대기이며 그 전에는 fail-closed로 거부한다. |
| II. 작업 분할 우선순위 | Single Intent, 위험 격리, 독립 검증, 계층 분할, 400줄 휴리스틱 | 통과 | `구현 단계 묶음`에서 14개 Phase. 운영 검색 코드를 건드리는 P8을 따로 격리, 마이그레이션을 데이터셋(P4a)과 실험(P4b)으로 분리. 500줄 초과 예상 Phase 없음. |
| III. 실행과 독립 검증의 분리 | Phase마다 실행 Agent·검증 Agent 분리 가능한가 | 통과 | Phase마다 실행 Agent의 쉬운 검증(unit·contract·ruff)과 검증 Agent의 전체 재검증(integration·인수 하네스·범위 밖 diff 확인)을 나눠 적었다. |
| IV. TDD | 관찰할 동작, 정상·경계·실패 사례와 예상 결과를 담은 테스트 설계가 있는가 | 통과 | `테스트 전략`에 U·C·I·H 계층 사례와 예상 결과. 마이그레이션도 정적 단위 테스트로 RED 확인. |
| V. 근거 재현성 | 평가 결과의 재현 좌표, 정답 근거 좌표 | 통과 | 정답 근거는 (source, 자료 ID, revision, 위치 좌표)로 헌법 V 좌표와 같다. 실험 기록은 구성 식별값·스냅숏·시드·관문 기준 해시로 재현(FR-028~031). 이 기능은 운영 근거 패키지를 바꾸지 않는다. |
| VI. 보안·데이터 경계 | 사내 저장, 외부 전송 금지, 비밀값 비기록, 운영 자원 분리 | 통과 | 평가 DB·자료·기록은 사내(DR-01·DR-02). 외부 연결 차단 테스트(U-NET-01, H-10). 합성 토큰은 비밀 파일만(DR-16). 운영 GPU·DB 차단(DR-03, DR-02, 둘 다 권장 A 승인). 운영 개시 전에는 spec FR-033 예외로 운영 서버를 예외 표시와 함께 허용 목록에 두고 운영 목록·운영 DB 차단은 유지한다(사용자 결정 2026-11-13). 연구 자원 목록이 입력되기 전에는 실험이 시작하지 않는다(fail-closed). |
| VII. 조회 경로 모델 역할 경계와 LLM 사용 경계 | LLM 사용 범위, 생성물 격리, 조회 경로 모델 역할 | 통과 | LLM은 개발셋 합성(VII (3))에만, 사내 모델 허용 목록으로만(DR-16). 생성물은 `eval_` 테이블에만 있고 색인·검색 모듈이 import·조회하지 않음을 정적 테스트(U-ARCH-02)와 인수 검사(H-10)로 확인. 비공개 경로의 모델은 채점·정렬만 하는 운영과 같은 순위 결정 함수를 쓴다. 파서·청킹에 LLM 없음. |

게이트 결과: 위반 없음. Complexity Tracking 기록 대상 없음. I의 조건(DR 승인 전 의존 Phase 금지)은 2026-11-10 DR-01~DR-25 승인으로 풀렸다.

## 설계 개요

### 구성 요소

| 구성 요소 | 계층 | 책임 | DR |
|---|---|---|---|
| 질의 규칙(`evaluation/queries.py`) | domain | 질의 객체 검사(R-04 순서, 7개 사유), 정규화 질의문(R-02), 시험셋·개발셋 출처 구분 규칙 | DR-10 |
| 좌표·겹침(`evaluation/coordinates.py`) | domain | 행·페이지·셀 좌표 해석과 범위 확인, 좌표 종류로 파일 결정, 겹침 판정, MVP origin → (omf, 경로, commit) 변환 | DR-07, DR-11 |
| 지표(`evaluation/metrics.py`) | domain | Recall@5, MRR@20, 근거 완전성, 의도별 Recall@5, `no_evidence` 비율, p95, 반환 근거 평균 길이(정확 산술). 개정: Recall@N(후보 목록)·후보 수 통계(R-21), 참고 지표 MRR@3(분수)·nDCG@3(binary64 고정 할인 상수) | DR-06, DR-11, DR-27 |
| 확인 규칙(`evaluation/holdout.py`, 개정) | domain | 확인 규칙 상수 `holdout-v1`, 후보 대조 키·확인 입력 식별값 계산, 확인 번호·승인 판정, 소진 대조 | DR-28 |
| 부트스트랩(`evaluation/bootstrap.py`) | domain | 짝 차이, SHA-256 카운터 난수, 백분위 구간, 불일치율 | DR-05 |
| 관문(`evaluation/gate.py`) | domain | 관문 기준 상수(v1.0), 다섯 조건 판정, 사유, 규모 확인, 시도 횟수·세대·교체 건 규칙 | DR-04, DR-08, DR-09, DR-15 |
| 평가 포트(`application/evaluation/ports.py`) | application | 평가 저장소, 원본 읽기(`SourceSnapshotReader`), 비공개 평가 경로(`EvaluationSearchPort`), 색인 산출물 읽기, 자원 확인, GPU 측정, 합성 모델 포트 | DR-01, DR-18 |
| 데이터셋 서비스 | application | 원본 자료 스냅숏 생성·목록, 질의 접수(전부 또는 전무), 제거, 평가셋 스냅숏, 세대 열기, 개발셋 템플릿·합성 가져오기 | DR-08, DR-10, DR-12, DR-22 |
| 필수 검사 서비스 | application | 결정성·원문 커버리지·좌표 정확성·고아 청크·구조 경계 5종 | DR-13 |
| 실험 실행기 | application | 사전 점검, 시도를 세는 시점(DR-24), 교대 실행(R-16), 두 번 실행, 질의별 결과·지표·비용·검사·판정 기록, 재현, 재계산, 중단 실험 정리 | DR-14, DR-19, DR-20, DR-21, DR-24, DR-25 |
| 순위 결정 함수와 SQL 조각 | application/search, infrastructure/database | 운영과 평가가 함께 쓰는 `rank_evidence`, lane SQL 본문 + 범위 CTE 두 종류. 개정: `rank_evidence` = `fuse_candidates` + `group_evidence_items` 합성 | DR-18, DR-26 |
| 비공개 평가 경로 구현 | infrastructure/evaluation | run 지정 범위 저장소, 정책 resolve·대조, 모델 레지스트리(D-05). 개정: 후보 목록 반환, 평가 전용 리랭커 슬롯과 오류 코드, `012` 레지스트리 조립(P19) | DR-18, DR-26 |
| 원본 읽기 구현 | infrastructure/evaluation | OMF: 고정 commit git 객체 읽기(기존 `git_archive` 재사용, 읽기 전용). factory: 001 원본·내용 확인기 포트(P13) | DR-22 |
| 저장소 어댑터 | infrastructure/database | `eval_` 테이블 ORM, 트랜잭션, advisory lock(트랙별 시도 직렬화) | DR-01 |
| 자원 확인·GPU 측정 | infrastructure/evaluation | 호스트·GPU UUID 확인, `nvidia-smi` 표본 측정 | DR-03, DR-04 |
| 합성 클라이언트 | infrastructure/evaluation | 사내 모델 서버 호출(허용 목록, 비밀 파일 토큰). 개정: `no_answer` 모드(제목 목록만), 의도별 배분 | DR-16, R-20 |
| 평가 CLI | interfaces/cli | `eval` 하위 명령, 출력 규칙, exit code, 조립 지점 | DR-17, DR-21 |
| 평가 설정 | `evaluation_settings.py` | `OMF_EVAL_` 설정(기존 `Settings` 불변, R-06) | DR-02, DR-03, DR-16 |
| 마이그레이션 | migrations | 새 테이블·트리거·부분 인덱스·연구 트랙 3행. 개정: `0008` 추가 열·확인 테이블 2개 | DR-01, DR-27 |

계층 규칙: application 평가 모듈은 infrastructure와 `Settings`·`EvaluationSettings`를 import하지 않는다. 색인·검색·API 모듈은 평가 모듈을 import하지 않는다(R-12, U-ARCH-01·02). 조립은 `interfaces/cli/evaluation.py`에서만 한다.

### 실험 흐름

```text
eval experiment run / eval gate attempt --spec F.json
[사전 점검 — 번호는 data-model 4.2가 정본. 거부는 exit 3, 실험 행 없음, 시도로 세지 않음]
 1 자원 확인(허용 목록·운영 목록)[DR-03]                             → resource_not_allowed
 2 평가 DB 거부 목록[DR-02] (풀러는 시작 검사 없음: 설정 규칙 + 실행 중 pid 불변 확인[DR-25])
                                                                     → production_database_refused
 2a (gate, 개정) 확인이 필요한 트랙이면 확인 기록 대조 → 맞으면 소진 INSERT·커밋[DR-28]
    (이 뒤 어디서 끝나도 소진 유지)                                  → unknown_track / policy_not_found /
                                                                       gate_without_holdout_check / holdout_check_already_used
 3 요청 구성(후보 1개), 스냅숏 일치                                  → candidate_limit / comparison_mismatch
 4 모든 질의의 확인 기준 = 실험 원본 자료 스냅숏[DR-12]                → basis_snapshot_mismatch
 5 run 상태·스냅숏 일치(R-10)                                        → run_not_evaluable / run_snapshot_mismatch
 6 정책 resolve·정책–run 대조(D-02)                                  → policy_not_found / policy_index_incompatible
 7 모델 레지스트리·로컬 준비(D-05). 개정: 리랭커 준비(P19)[DR-26]      → model_not_registered / model_not_prepared / 리랭커 준비 7개
 7a (dev, 개정) 개발셋 역할–목적, 확인 횟수·승인[DR-28]                → holdout_not_allowed / unknown_track /
                                                                       holdout_check_limit_exceeded
 8 (gate) 시험셋·규모(FR-002), 개발셋과 질의문 겹침 없음(SC-006)        → not_test_set / test_set_scale / dev_test_overlap
 9 (gate) 트랙·시도 수(FR-025)·진행 중(FR-026)                        → unknown_track / attempt_limit / attempt_in_progress
   - 같은 트랙 running 행의 생존 잠금을 짧은 트랜잭션의 pg_try_advisory_xact_lock으로 시험:
     못 얻음 → attempt_in_progress, 얻음(잠금 없음) → stale_attempt_in_progress(정리 명령 안내)[DR-25]
10 (gate) 관문 기준 설정값·시드·정책 calibrated                       → gate_setting_missing / seed_not_allowed / policy_not_calibrated
11 (gate) 콜드 빌드 측정[DR-14]                                      → cost_measurement_missing
   - dev는 2a·8~11을 하지 않고 gate는 7a를 하지 않는다
[실행 단계 — 아래 A~G는 이 흐름 안의 이름이며 사전 점검 번호와 다르다]
 A 필수 검사(색인 쪽): 커버리지·좌표·고아·구조 경계 + 색인 결정성(CPU 재청킹)[DR-13]. 결과는 메모리에 둔다
 B 두 구성의 모델 적재와 예열 질의 3건(평가셋 밖 고정 문장, R-16)
   - A·B에서 오류 → exit 4, 실험 행 없음, 시도로 세지 않음(attempt_consumed=false)
 C 실험 id(UUID)를 클라이언트에서 만들고, 풀 밖 전용 연결(keepalive 켬)에서 생존 잠금
   (EVAL_LIVENESS_CLASS, id 해시)을 pg_try_advisory_lock으로 먼저 획득(기다리지 않음).
   못 얻으면(objid 우연 충돌) 새 UUID로 다시, 최대 5회. 모두 실패 → 실험 행 없음, exit 4 liveness_lock_unavailable
   (실험 종류 무관, attempt_in_progress 아님). 얻으면 backend pid 기록 → 실험 행 INSERT(running, gate는 attempt_number NULL), 커밋.
   - INSERT가 23505이고 constraint_name = eval_experiments_running_track_uq(같은 트랙 동시 시도) → 생존 잠금 해제,
     exit 3 attempt_in_progress, 행 없음, 시도로 안 셈. 다른 제약의 23505 → 예기치 않은 오류(exit 4)
   첫 평가셋 질의 실행 직전. 잠금은 끝날 때까지 유지[DR-25]
 D 질의 실행 1회차: 질의 ID 순, 질의마다 두 구성 교대(R-16), limit 20. 질의마다 두 구성 결과를 한 트랜잭션으로 저장
   - 개정: 경로 호출은 include_candidates=True. 질의 처리 = fuse_candidates → (리랭커 있으면 실행 경계) → group_evidence_items.
     후보 목록에서 적중 위치·후보 수, 리랭크 상태·시간·조각 점수를 기록(목록 자체는 저장 안 함)[DR-26·DR-27].
     리랭커 오류·시간 초과·계약 위반은 대체 없이 실험 failed(R-14)
   - 트랜잭션 순서: 부모 실험 행 FOR NO KEY UPDATE → (gate 첫 결과만) 트랙 잠금·attempt_number → 결과 INSERT
     → 생존 잠금 자기 확인 → COMMIT(잠금 승격 없음)[DR-25]
   - 자기 확인(pg_locks: advisory, 현재 DB oid, classid·objid를 클라이언트에서 부호 없는 32비트로 바꿔 CAST(:x AS oid) 비교,
     objsubid=2, 자기 pid, granted, backend pid 불변). 상실 → 커밋하지 않고 멈춤, failed(liveness_lost) 시도, exit 4[DR-25]
   - 결과 가드 트리거: 부모가 running이 아니면 INSERT 거부[DR-25]
   - gate: 첫 질의 결과를 저장하는 트랜잭션이 attempt_number를 정함 → 이때부터 시도로 셈[DR-24]
 E 질의 실행 2회차(질의 결정성), 같은 방식으로 저장
   - D·E에서 오류 → failed, 대체 없음(R-14, D-06). 결과가 하나라도 저장됐으면 시도로 셈(attempt_consumed=true), 아니면 세지 않음(false)
 F 지표·비용·부트스트랩·(gate) 판정. 개정: 진단·참고 지표도 계산해 metrics에 넣되 판정 입력이 아님[DR-27]. 완료 트랜잭션: 부모 행 FOR NO KEY UPDATE → A의 필수 검사 결과와 함께 결과 행 INSERT
   → 생존 잠금 자기 확인(상실이면 커밋 없이 failed(liveness_lost), exit 4) → 실험 completed → COMMIT[DR-25]
 G 출력: dev는 전체, gate는 집계 출력만[DR-21]
 (개정) 확인 실험(dev, 목적 holdout_check)은 C의 실험 행 INSERT 트랜잭션에서 트랙 확인 잠금 아래 확인 번호를 정하고
        확인 기록(data-model 3.17)을 함께 INSERT한다[DR-28]
```

### 재계산 흐름(SC-002)

```text
eval gate recompute ID
 기록된 eval_query_results(1회차) + eval_experiments(시드·관문 기준 해시) + eval_gate_criteria.settings
 → 적중·지표·부트스트랩·비용(기록값)·필수 검사(기록값) → 판정·사유
 → 기록된 판정·사유와 비교(재실행 없음)
```

## 설계서 정합

| 설계서 항목 | 002의 처리 | 관계 |
|---|---|---|
| D-04 비공개 평가 경로 | run id 지정 내부 포트, 공개 API 불변. 구현 방식은 DR-18(공통 순위 결정 함수·SQL 조각, 운영 SQL 바이트 동일). 개정: 순위 결정 함수 두 함수 분할과 후보 목록 반환(DR-26, 승인 2026-11-16), 공개 API 불변 유지 | 일치(구체화) |
| D-05 모델 레지스트리는 평가 실행기만 | `infrastructure/evaluation/model_registry.py`, API 런타임은 조립하지 않음 | 일치 |
| D-06 평가에서 리랭커 실패는 오류 | 모든 조회 경로 오류를 대체 없이 실험 오류로(R-14). 개정: 평가 전용 리랭커 슬롯과 `rerank_failed`·`rerank_timeout`·`rerank_contract_violation`(DR-26), 실제 리랭커 조립은 `009` 뒤 P19 | 일치 |
| D-07 후보 깊이 | 정책 필드가 생기면(`009`) 실험 기록이 정책 snapshot으로 함께 펼침(R-08) | 일치 |
| D-11 후보 정책 manifest 등록 | `eval policy register`가 기존 정책 저장소 함수로 등록. 공개 policy 관리 CLI·active-policy pointer가 아니며 운영 선택을 바꾸지 않음 | 일치 |
| D-02 정책–색인 짝 확인 | 사전 점검 6(data-model 4.2 번호)에서 현재 규칙(모델·revision·차원·정규화)으로 대조. 새 형식의 호환 선언은 `009` 이후 같은 자리에서 | 일치 |
| 13절 입력 정보 | 결정성·커버리지·좌표·고아·경계는 색인 산출물에서, 적중·길이는 검색 결과에서 읽음. MVP 형식 run은 경계 기록이 없어 평가 실행기가 파서 블록으로 대신 계산[DR-13] | 보완 |
| 13절·O-11 다중 원본 적중 | 원본 중 하나라도 겹치면 적중[DR-11] | 설계서 권장 채택(DR-11 승인) |
| 13절 반환 근거 길이 단위 | 코드 포인트[DR-06] | 승인 결정 |
| O-8 GPU 결정성 허용 기준 | 질의 결과 정확 일치, 벡터는 비교 안 함[DR-13] | 승인 결정(002 몫) |
| O-13 ① 평가 DB 배치 | 연구 자원의 별도 평가 DB[DR-02, 권장 A 승인: 연구와 운영 분리]. ②(운영 적용 절차)는 범위 밖 | 승인 결정(일부) |
| 15절 공통 선행 조건 | 400개 전 개선 목적 교체 금지(규모 사전 점검), 트랙별 3회, 개발셋 튜닝, 분리 자원 | 일치 |
| 15절 적용 전 하한선 재확인·smoke | 하네스 범위 밖(값을 바꾸지 않는 운영 확인). 재보정은 새 후보로 새 관문 시도 | 범위 구분 |
| D-02의 "첫 전환의 관문 적용 여부는 `009`·`002` plan" | 전환 확인 실험(관문 아님)[DR-19] | 승인 결정 |
| 001 plan "좌표의 파일 지정은 `002`·`006`에서" | 작성자는 파일 번호를 적지 않고, 시스템이 원본 자료 스냅숏에 복사한 001 `composition`(`single`·`image_bundle`·`embedded_image_bundle`)과 역할(`primary`·`info`·`bundle_target`), 좌표 종류로 revision 안 파일을 하나로 정해 `file_index`로 저장[DR-07] | 보완 |

설계서와 **충돌**하는 판단은 없다.

## MVP 검색 기능 영향

- **002는 운영 검색 결과를 바꾸지 않는다**(FR-032, SC-008).
  - P8의 리팩터링은 `SearchService.search`의 순위 결정 부분을 순수 함수로 옮기고 lane SQL을 조각으로 조합할 뿐이다. 운영 SQL 문자열 바이트 동일(U-PRIV-02), 기존 검색 단위·계약 테스트 무수정 통과(C-SRCH-01·02), 운영 경로와 평가 경로의 동등성(I-PRIV-02)으로 증명한다.
  - `create_app`, `runtime.py`, `Settings`는 바꾸지 않는다. 평가 CLI만 평가 포트를 조립한다.
  - 마이그레이션은 새 테이블만 추가한다(U-MIG-01·02). 운영 DB에도 빈 `eval_` 테이블이 생기지만 운영 경로는 읽지 않는다[DR-01].
- **운영 영향 0의 구조적 보장**: 평가는 별도 평가 DB에서 한다[DR-02]. 평가 설정에 운영 DB 식별값을 넣으면 시작하지 않는다.
- **되돌리기**: 002 마이그레이션 downgrade는 `eval_` 테이블만 지운다. P8 리팩터링은 단일 커밋으로 되돌릴 수 있고 운영 SQL이 같으므로 데이터 영향이 없다. 공유 서버 배포는 002 범위 밖이다.

## 미해결 사항 처리

| 출처 | 항목 | 처리 |
|---|---|---|
| checklist, Assumptions | 운영 GPU 메모리 용량(FR-018 ⑤) | DR-04 |
| checklist, Assumptions | 부트스트랩 세부(FR-018 ③) | DR-05 |
| checklist | 반환 근거 길이 단위(FR-014a) | DR-06 |
| checklist N4 | 행 번호 기준, OMF commit 값 제공 위치, 이미지 묶음 정보 자료가 PDF일 때 좌표 | DR-07, R-15 |
| checklist, Assumptions | 시험셋 보충 조건·재사용 범위·횟수 재계산(FR-026) | DR-08 |
| checklist, Assumptions | 모듈 교체 건 묶음 기준(FR-025) | DR-09 |
| Assumptions | 스냅숏 목록 방식과 기본 선택값 | DR-10 |
| checklist | 버그 수정의 정의와 관문 적용 여부 | DR-20 |
| checklist | 연구 실행 자원 | DR-03(차단 방식). 자원 선택 자체는 spec Out of Scope, quickstart 5절 점검 |
| spec Edge Case "결과가 일부라도 산출"(A-3) | 관문 시도를 세는 시점 | DR-24 |
| US1-4·US1-5·US1-8 대 FR-006(A-15) | 키가 없을 때의 거부 사유 | A-15 해소(사용자 결정 2026-11-10: 시나리오 사유, 정답 없음의 `정답 근거 목록` 키 생략은 접수). spec FR-003·FR-006 비고 반영 |
| SC-003 "다른 문서"(A-1) | 작성 형식 계약서를 "다른 문서"로 셀지 | A-1 해소(사용자 결정 2026-11-10: 질의 작성 형식 계약서 한 파일만 제외). spec SC-003·User Story 1 반영 |
| DR-03·DR-04 설정값 | 연구 자원 구체 목록, 운영 GPU 총 메모리 실측값 | 값 입력 대기. 결정은 승인됨. 갱신(사용자 결정 2026-11-13): 운영 서버의 호스트·GPU UUID(운영 목록과 예외 표시 허용 항목에 넣음)와 운영 GPU 총 메모리(−10%가 한도)는 운영 서버 첫 접속 때 실측해 기록·보고한다(spec FR-033 예외·조건 ⑥). 운영 개시 뒤 쓸 다른 연구 자원 목록은 사용자 입력 대기. 그 전에는 실험 시작 거부(`resource_not_allowed`)·관문 시도 거부(`gate_setting_missing`)로 fail-closed |
| SC-006(개발셋이 먼저 있을 때) | 개발셋과 같은 시험 질의 처리 | DR-23 |
| 재검증 M-B | 프로세스 강제 종료로 `running`에 남은 시도가 트랙을 막음 | DR-25 |
| checklist N5 | 002 spec 검증 발견(체크리스트 고정 문구와 계획 이월 방침의 긴장), 실질 영향 없음 | A-13 해소, 계획할 항목 없음 |
| 설계서 O-8, O-11, O-13 ① | GPU 결정성, 다중 원본 적중, 평가 DB 배치 | DR-13, DR-11, DR-02 |
| 설계서 D-02 | 첫 새 형식 전환의 관문 적용 | DR-19 |
| 001 plan | 좌표의 파일 지정 | DR-07 |
| Assumptions 열린 항목 | 불일치율 10% 가정 검증 | 첫 관문 시도에 측정값 기록(data-model 3.14 `discordance_check`), 보고만 |
| Assumptions 열린 항목 | 트랙 ① 영역 판별 전용 관문 | 범위 밖. 공통 관문 적용 |
| `012` 연동 ①⑤, `012` C-13 | 후보 목록 반환과 리랭커 삽입 지점(순위 결정 함수 분할) | DR-26(승인, 사용자 2026-11-16) |
| `012` 연동 ②③ | 저장 위치·키 이름·nDCG 정밀도·마이그레이션 | DR-27(승인, 사용자 2026-11-16) |
| `012` 연동 ④, `012` 열린 항목 H-1 | 확인 개발셋 규칙, 소진 시점, 코드 이름 | DR-28(승인, 사용자 2026-11-16, 변경: 리랭커 교체 건 포함, 확인 횟수 시험셋 세대별). H-1은 사전 점검 1·2 뒤 2a에서 소진(1·2 거부는 소진 없음) |
| `012` 연동 ⑥⑦⑧ | 합성 인자, 같은 구성 실험, 측정 run 공유 | R-20, R-18, R-19 |
| 개정 중 발견 | FR-026 재시도 허용 대 확인 규칙 | A-16 처리 결정(사용자 2026-11-16, 선택지 (1)): FR-026 그대로, 재시도는 트랙(건)별 사전 조건을 따름 |

## `012` 연동 개정(2026-11-16)

`012` 리랭커 연구의 변경 요청서 `specs/012-reranker-research/contracts/eval-linkage-request.md`(사용자 승인 2026-11-13, `012` DR-11 권장 A)의 ①~⑧을 이 계획에 반영한 개정이다. 기존 승인 내용(DR-01~DR-25, Phase P1~P13)은 바꾸지 않고 추가했다. 사용자 승인이 필요한 선택은 DR-26~DR-28로 올렸고 사용자가 2026-11-16 결정했다(DR-28은 변경 포함). ⑨(후보 목록 고정 파일 생성과 오프라인 실험)는 다음 개정 단위이며, 이 개정은 확장 지점만 남긴다: 확인 입력 식별값의 종류(`holdout_identity_kind`에 `frozen_file` 추가, DR-28 5), 관문 요청 `holdout`의 파일 형식, 후보 목록 필드(DR-26, 고정 파일이 같은 필드를 쓴다).

### 012 요청 ①~⑧ → 002 위치

| `012` 요청 | 내용 | 002 위치(파일·절) | 결정·판단 | Phase | 테스트 |
|---|---|---|---|---|---|
| ① | 근거 묶기 전 후보 목록 반환(최대 100) | contracts/private-evaluation-path.md 1·2·3절, data-model 3.13 `candidate_count` | DR-26 1~3 | P14 | U-CAND-01, U-PRIV-05, I-PRIV-04 |
| ② | Recall@N(후보 목록) 진단, 참고 지표 MRR@3·nDCG@3 | data-model 3.13·3.14, research R-21, contracts/eval-cli.md 7절 | DR-27 3~5, R-21 | P15(계산), P16(기록), P4c(열) | U-CAND-02, U-CMET-01, U-RMET-01, C-CLI-11 |
| ③ | 질의별 리랭크 점수·순위·여부, 소요 시간, 적용 상태. 결정성 비교(`012` DR-03)의 점수 차이는 1·2회차 기록(`pass_number` 1·2)에 저장된 `rerank_score`를 같은 청크끼리 비교해 계산 | data-model 3.13(`rerank_status`, `rerank_ms`, 일치 조각 필드), 3.14 `costs.rerank_ms` | DR-27 2 | P4c, P16 | U-RREC-01, I-DB-04. 후보 목록 리랭크 순위 일치(`012` DR-03 `rank_identity`의 후보 목록 부분)는 후보 목록을 DB에 저장하지 않으므로(DR-27 2) 다음 개정 ⑨의 오프라인 실험(고정 파일)에서 확인한다(`012` U-FRZ-02) |
| ④ | 탐색·확인 개발셋 역할, 확인 횟수(시험셋 세대별), 관문 앞 확인, 1:1 소진, 소진 시점(H-1), 코드 이름, 리랭커 교체 건 포함 | data-model 3.3·3.12·3.17·3.18·4.2(2a·7a)·4.5, contracts/eval-cli.md 1·3·5·6·7절, quickstart 3절 7·6절 | DR-28(사용자 결정 2026-11-16 (a)~(d), 이전 세대 미소진 확인 넘겨 쓰기 허용 [결정 2026-11-16]), A-16(처리 결정) | P4c, P17 | U-HOLD-01~04, C-CLI-09, I-HOLD-01, I-DB-04 |
| ⑤ | 평가 경로의 리랭커 준비와 오류 코드 10개(대체 없음) | contracts/private-evaluation-path.md 1~3절, data-model 4.2 순서 7, contracts/eval-cli.md 7절 | DR-26 4·5 | P14(슬롯·질의 오류), P19(`012` 레지스트리 조립) | U-RERR-01·02 |
| ⑥ | 문서에 없는 질의 합성, 의도별 배분 | contracts/eval-cli.md 4절, research R-20, quickstart 3절 5 | R-20 | P18 | U-SYN-04, C-CLI-10 |
| ⑦ | 단일 구성 진단 | contracts/eval-cli.md 6절, research R-18 | R-18 | P16 | U-EXP-08, C-CLI-11 |
| ⑧ | 정책만 다른 후보의 콜드 빌드 측정 run 공유 | contracts/eval-cli.md 6절, research R-19, data-model 3.14 `costs` | R-19 | P16 | U-EXP-09 |
| (⑨) | 후보 목록 고정 파일, 오프라인 실험 | 다음 개정 | — | 다음 개정 | `012` U-FRZ-01·04(기대값) |

### 의존과 순서

- 권장 구현 순서: P4c → P14 → P15 → P16 → P18 → P17 → P19. `012` S1(첫 측정)에 필요한 것은 ①②⑥⑦(P4c·P14·P15·P16·P18)이고, ④(P17)는 `012` S2 끝, ⑤의 실제 리랭커 조립(P19)과 ⑧은 `012` S3(관문 시도)에 필요하다.
- P19는 `009`의 검색 정책 새 형식(리랭커 기술자·후보 깊이 필드)과 `012` P4(리랭커 레지스트리) 구현 뒤에만 한다. 그 전까지 평가 경로의 리랭커 슬롯은 테스트용 가짜 구현으로만 채운다.
- 트랙 ② 관문 시도의 후보 대조 키도 `009` 정책 새 형식 필드에서 읽는다. 그 전의 트랙 ② 관문 요청은 `gate_without_holdout_check`(`candidate_key_unavailable`)로 거부되며, 이는 트랙 ② 관문 시도가 어차피 `009`를 기다리는 것과 같다(`012` C-05).
- 운영 경로 영향: P14는 P8과 같이 운영 검색 코드(`rank_evidence`)를 건드리는 유일한 개정 Phase라 위험 격리 대상이다. 합격 조건은 운영 SQL 바이트 동일(U-PRIV-02), 기존 검색 테스트 무수정 통과(C-SRCH-01·02), 분할 동등성(U-PRIV-05)이다.
- 운영 메모(트랙 ②): `012`는 시험셋 질의별 내보내기를 하지 않기로 했다(`012` plan `진행 판단 규칙` 6, 사용자 승인 2026-11-13). 트랙의 운영 약속이며 시스템 규칙 FR-024·DR-21은 바꾸지 않는다.
- spec 충돌 후보 A-16(FR-026의 재시도 허용 대 확인 규칙)은 사용자 처리 결정(2026-11-16, 선택지 (1))으로 닫았다: FR-026은 그대로이고, 재시도는 트랙(건)별 사전 조건(리랭커는 확인 실험 1회, 세대의 3회를 넘으면 승인)을 따른다. 독립 검증 발견 F-1(FR-023의 같은 기준)은 리랭커 교체 건을 확인 대상에 넣은 사용자 결정 (a)로 해소했다. 그 밖에 승인된 spec 요구사항과 충돌하는 항목은 찾지 못했다(R-18의 같은 구성 개발셋 실험과 R-19의 측정 run 공유는 FR-014·FR-017·DR-14 안에서 성립).

## FR 추적표

"계약"은 [contracts/eval-cli.md](./contracts/eval-cli.md)(CLI), [contracts/query-authoring-format.md](./contracts/query-authoring-format.md)(작성 형식), [contracts/private-evaluation-path.md](./contracts/private-evaluation-path.md)(내부 포트), "모델"은 [data-model.md](./data-model.md) 절 번호, "테스트"는 아래 `테스트 전략`의 ID다.

| FR | 데이터 모델 | 계약 | 구성 요소 | 테스트 | Phase |
|---|---|---|---|---|---|
| FR-001 | 3.3, 3.5 PK | `eval set create`, `set_kind_mismatch` | 데이터셋 서비스 | U-SET-02, C-CLI-02 | P4a, P5 |
| FR-002 | 3.8 `counts`·`scale_ok`, 3.10 | `test_set_scale` | 관문(규모), 사전 점검 | U-ATT-04, H-05 | P3, P11 |
| FR-003 | 3.5, 3.6 | 작성 형식 2·3절, `eval query add` | 질의 규칙 | U-QRY-01~06, C-CLI-02 | P1, P5 |
| FR-003a | 3.5 `intent` NOT NULL | 작성 형식 2절 | 질의 규칙 | U-QRY-02 | P1 |
| FR-003b | 3.1 | `eval source-snapshot list`·`show` | 데이터셋 서비스 | U-SNAP-01·02, C-CLI-01 | P5 |
| FR-004 | 3.2 `extent`, 3.6 | 작성 형식 3절 위치 객체 | 좌표·겹침 | U-COORD-01~05, U-FCOORD-01 | P1, P5, P13 |
| FR-005 | 3.6 | `invalid_answer_marking` | 질의 규칙 | U-QRY-03 | P1 |
| FR-006 | 3.9 `rejected_reasons` | 작성 형식 5절 | 질의 규칙 | U-QRY-06, C-CLI-02 | P1, P5 |
| FR-007 | 3.5 `normalized_text`, 4.1 | `duplicate_query`(`in_set`·`in_file`·`in_dev_set`), `excluded_as_test_duplicate`, `dev_test_overlap` | 질의 규칙, 데이터셋 서비스, 사전 점검 | U-QRY-05, U-DEV-02, U-EXP-02, H-06 | P1, P5, P6, P10 |
| FR-008 | 3.5 `origin`·`provenance` | `eval dev ...` | 데이터셋 서비스, 합성 클라이언트 | U-DEV-01·03, U-SYN-01~03 | P6, P7 |
| FR-008a | `eval_` 테이블 격리(R-12) | 작성 형식 6절, CLI 4절 | 계층 규칙 | U-ARCH-02, H-10 | P7, P12 |
| FR-009 | 3.7, 3.8 | `eval query add`·`remove` | 데이터셋 서비스 | U-SET-01, I-DB-02 | P4a, P5 |
| FR-010 | 3.1, 3.2 | `eval source-snapshot create` | 데이터셋 서비스, 원본 읽기 | U-SNAP-01, I-DATA-01, U-FCOORD-01 | P5, P13 |
| FR-011 | 3.13 `hit_ranks` | 비공개 경로 1절 결과 | 좌표·겹침, 지표 | U-HIT-01~03, U-MET-01 | P1, P2 |
| FR-012 | 3.14 `metrics` | — | 지표 | U-MET-02~04 | P2 |
| FR-013 | 3.14 `metrics` | — | 지표 | U-MET-01, U-MET-04 | P2 |
| FR-014 | 3.14 `costs` | `eval experiment run` 출력 | 지표, 실험 실행기, GPU 측정 | U-MET-06, U-EXP-06 | P2, P10 |
| FR-014a | 3.13 `top5_length_cp`, 3.14 | `avg_evidence_length` 출력 | 지표 | U-MET-05, C-CLI-05 | P2, P10 |
| FR-015 | 3.13 | `eval experiment export` | 실험 실행기 | U-EXP-04, U-REC-01 | P3, P10 |
| FR-016 | 3.14 `mandatory_checks` | — | 필수 검사 서비스 | U-CHK-01~05 | P9 |
| FR-017 | 3.12 | `comparison_mismatch`, `run_snapshot_mismatch` | 사전 점검 | U-EXP-02 | P10 |
| FR-018 | 3.10 `settings` | `reasons` | 관문 | U-GATE-01~04, U-CRIT-01 | P3 |
| FR-019 | 3.14 `judgment` | `eval gate attempt` 출력 | 관문 | U-GATE-01~04 | P3 |
| FR-020 | 3.14 `reasons` | `reasons` 형식 | 관문 | U-GATE-04, C-CLI-06 | P3, P11 |
| FR-021 | 쓰기 경로 없음 | CLI 7절 마지막 규칙 | — | H-09 | P11, P12 |
| FR-022 | 3.10 | — | 관문 기준 상수 | U-CRIT-01 | P3 |
| FR-023 | 3.11 | `eval track ...` | 관문(트랙) | U-ATT-02, C-CLI-04 | P3, P11 |
| FR-024 | 3.15 `eval_access_log` | 집계 출력, `experiment export`, `query show --include-text` | 실험 실행기·데이터셋 서비스 출력 규칙 | U-OUT-01, C-CLI-08 | P5, P10, P11 |
| FR-025 | 3.12 유일 인덱스, 4.3 | `attempt_limit` | 관문(시도 횟수) | U-ATT-01, I-GATE-01, H-04 | P3, P4b, P11 |
| FR-026 | 3.4, 3.12 진행 중 유일 | `candidate_limit`, `attempt_in_progress`, `stale_attempt_in_progress`, `eval experiment abandon`, `eval set generation start` | 관문(세대), 사전 점검, 실험 실행기(정리) | U-ATT-01·03, U-EXP-02·07, U-LOCK-01, I-EXP-02·03, C-CLI-06 | P3, P10, P11 |
| FR-027 | 3.8 `scale_ok` | `test_set_scale` | 사전 점검 | U-ATT-04, H-05 | P3, P11 |
| FR-028 | 3.12, 3.14 | `eval experiment show` | 실험 실행기 | U-EXP-04, I-EXP-01 | P4b, P10 |
| FR-029 | 트리거(R-07) | — | 마이그레이션 | U-MIG-01·02, I-DB-02 | P4a, P4b |
| FR-030 | 3.14 `reproduction_match` | `eval experiment reproduce` | 실험 실행기 | U-EXP-05, H-01 | P10 |
| FR-031 | 3.10 `bootstrap` | — | 부트스트랩 | U-BOOT-01 | P2 |
| FR-032 | 읽기 전용 접근 | 비공개 경로 2절 | 비공개 평가 경로 | I-PRIV-01, H-08 | P8, P12 |
| FR-033 | 3.12 `resource_name`·`environment` | `eval resource show` | 자원 확인 | U-ENV-01, H-11 | P10, P12 |
| FR-034 | — | CLI 1절 네트워크 규칙 | 전체 | U-NET-01, H-10 | P7, P10, P12 |

SC 대응: SC-001 → H-01, SC-002 → H-02, SC-003 → H-03, SC-004 → H-04, SC-005 → H-05, SC-006 → H-06(+U-QRY-05·U-DEV-02·U-EXP-02), SC-007 → H-07(+U-GATE-02), SC-008 → H-08, SC-009 → H-09, SC-010 → H-10, SC-011 → H-11.

## 테스트 전략

### 원칙

- TDD(헌법 IV): 각 Phase는 아래 사례로 테스트를 먼저 쓰고, 구현 부재로 인한 assertion 실패(RED)를 확인한 뒤 최소 구현(GREEN), 정리(REFACTOR) 순서로 진행한다. 실행 Agent는 이 설계를 그대로 쓴다.
- 표본은 테스트 안에서 합성한다: 작은 git 저장소(Markdown 몇 개, 표·목록·긴 절 포함), 결정적 가짜 임베딩 제공자, 질의별 결과 표본. 대외비 자료·OMF 실제 저장소·네트워크·GPU를 쓰지 않는다.
- 비밀값·질의문 비노출 검사는 알려진 표식 문자열을 토큰·질의문에 심고 출력·로그·예외 문자열에서 찾는 방식으로 한다.
- 계층: 단위(U, DB·네트워크 없음) → 계약(C, CLI 러너·기존 API 계약) → 통합(I, PostgreSQL) → 인수 하네스(H, CLI+PostgreSQL+fixture 색인, SC 판정).

### 단위 테스트(U)

| ID | 관찰할 동작 | 정상 사례 → 예상 | 경계 사례 → 예상 | 실패 사례 → 예상 |
|---|---|---|---|---|
| U-QRY-01 | 항목 누락(FR-003, R-04 1, A-15 승인안) | spec 예시 A·B를 형식대로 옮긴 객체 → 사유 없음 | 정답 없음 + `정답 근거 목록: []` → 사유 없음. 정답 없음 + `정답 근거 목록` 키 없음 → 사유 없음(빈 목록으로 봄) | `질의문`·`확인 기준 원본 자료 스냅숏`·`출처 구분` 키 없음 → 각각 `missing_field`(`missing:<키>`). `질의 ID`·`파일 번호` 적음 → `missing_field`(`unknown_field`). 모르는 키 → 같음. `의도`·`작성자`·`작성일`·`정답 구분` 키 없음 → `missing_field`가 **아니라** 각 시나리오 사유(U-QRY-02·03·04) |
| U-QRY-02 | 의도(FR-003a, US1-4) | `무엇`·`왜`·`어떻게` → 통과. 문서에 없는 질의 + `왜` → 통과 | 앞뒤 공백 `" 왜 "` → `invalid_intent`(정확 일치만) | `의도` 키 없음 → `invalid_intent`(`missing`). 빈 문자열, null, `언제`, `what` → `invalid_intent` |
| U-QRY-03 | 정답 표시(FR-005, US1-5) | 정답 있음 + 근거 2개 → 통과 | 정답 있음 + 근거 1개 → 통과 | 정답 있음 + `[]` 또는 `정답 근거 목록` 키 없음 → `invalid_answer_marking`(`answer_without_evidence`)이고 `invalid_answer_coordinate`는 나오지 않음. 정답 없음 + 근거 1개 → `no_answer_with_evidence`. `정답 모름` 또는 `정답 구분` 키 없음 → `invalid_status` |
| U-QRY-04 | 작성 정보(US1-8) | `작성자` 1자, `작성일` `2026-11-20` → 통과 | 작성자 100자 → 통과, 101자 → `invalid_authoring_info` | `작성자` 키 없음·빈 문자열 → `author_missing`. `작성일` 키 없음·빈 문자열 → `date_missing`. `2026-02-30`·`20261120` → `date_invalid`. 시험셋 + `템플릿` → `origin_not_allowed`. 개발셋 + `사람 작성` → `origin_not_allowed` |
| U-QRY-05 | 질의문 같음(R-02, FR-007, SC-006) | 다른 문장 → 중복 아님 | `"A  B"`와 `"A B\n"` → 같음. NFD 한글과 NFC 한글 → 같음. 개발셋에서 제거된 질의와 같은 문의 시험셋 제출 → 중복 아님 | 평가셋 현재 구성원에 같은 정규화 문 → `duplicate_query`(`in_set`). 같은 파일 안 두 번 → 두 번째에 `in_file`. 제거된 질의와 같은 문 → 중복 아님. **시험셋 제출 질의가 어느 개발셋의 현재 구성원과 같은 정규화 문 → `duplicate_query`(`in_dev_set`)[DR-23]** |
| U-QRY-06 | 사유 전부·순서(R-04, FR-006) | 사유 하나 → 그 하나 | 의도·작성자 동시 위반 → `[invalid_intent, invalid_authoring_info]` 순서 | 확인 기준 스냅숏 목록 밖 + 좌표 범위 밖 → `unknown_source_snapshot`만(5단계 건너뜀). 응답 `detail`에 질의문 표식 0건 |
| U-COORD-01 | 행 범위(DR-07) | `{행 범위, 12, 30}`, 행 수 212 → 통과 | `시작=끝=1` → 통과. `끝=212`(=행 수) → 통과, 213 → `out_of_range` | `시작=0`, `시작>끝`, 정수 아님 → `out_of_range`. Markdown에 `셀 범위` → `kind_format_mismatch`(US1-7) |
| U-COORD-02 | 페이지·셀 범위 | PDF `{페이지,7,7}`, 페이지 수 10 → 통과. xlsx `{셀 범위,보증현황,B14:D14}` → 통과, 내부 `min_row=14,max_row=14,min_col=2,max_col=4` | `B14`(한 셀) → 통과. `D14:B14`(역순) → 정규화해 같은 범위 | `보증 현황`(시트 이름 다름) → `sheet_not_found`. `B0`, `14B`, `B14:` → `invalid_cell_range`. 사용 범위 밖 → `out_of_range` |
| U-COORD-03 | 파일 결정(DR-07, 001 `composition`·역할) | `single`(`primary` 1개) → `file_index` 0. `image_bundle`(`info` 1 + `bundle_target` PNG 3) + 행 범위 → `info` 파일의 `file_index` | `embedded_image_bundle`(`info` 1 + `bundle_target` PDF 1) + 페이지 → 그 PDF의 `file_index`, + 행 범위 → `info`. `embedded_image_bundle`(xlsx 대상) + 셀 범위 → 그 xlsx | **`image_bundle`(`info` 1 + `bundle_target` 전체 스캔 PDF 1개) + 페이지 → `kind_format_mismatch`**(형식·역할이 앞의 `embedded_image_bundle` PDF 사례와 같아도 `composition`으로 구별). `image_bundle` + 셀 범위 → 같음. `embedded_image_bundle`(PDF 대상) + 셀 범위 → `kind_format_mismatch`. 작성자가 `파일 번호`를 적음 → `missing_field`(`unknown_field`). 스냅숏 항목에 `composition`이 없거나 규칙으로 파일이 하나로 정해지지 않음(가짜 데이터) → `kind_format_mismatch`(`ambiguous_file`) |
| U-COORD-04 | 스냅숏 소속(US1-6) | 스냅숏 항목과 (source, 자료 ID, revision) 일치 → 통과 | OMF 자료 ID가 경로 대소문자만 다름 → `material_not_in_snapshot`(정확 비교) | 다른 commit SHA → `revision_mismatch`. 스냅숏에 없는 자료 → `material_not_in_snapshot`. factory 근거(P13 전) → `source_not_supported` |
| U-COORD-05 | 행 번호 기준이 기존 함수와 같음(DR-07, L-1) | 평가 행 분할 결과 = 기존 `application/indexing/ports.py`의 `split_physical_lines` 결과(같은 함수를 재사용하거나 표본 전부에서 같음). 표·목록이 든 Markdown fixture의 각 제목 행 번호 = `MarkdownItParser` 절 `line_start` | 마지막 줄에 줄 끝 있음·없음 → 마지막 빈 조각은 행 아님(`a\nb\n`·`a\nb` 모두 2행). 빈 파일 → 0행 | CRLF(`a\r\nb`) → 2행(`\r\n`이 줄 끝 하나). **CR만(`a\rb`) → 2행**(`\n`으로만 나누면 1행이 되어 실패). 섞인 줄 끝(`a\rb\nc\r\nd`) → 4행 |
| U-HIT-01 | 구간 겹침(FR-011) | [10,20]과 [15,25] → 겹침 | [10,20]과 [20,30] → 겹침(끝 행 공유). [10,20]과 [21,30] → 겹치지 않음 | 페이지 [7,7]과 행 [7,7] → 종류 달라 겹치지 않음 |
| U-HIT-02 | 식별값·셀 겹침 | 같은 시트 B14:D14와 C10:C20 → 겹침 | 같은 시트 B14와 B14 → 겹침. 같은 범위 다른 시트 → 아님 | revision 다름, 자료 ID 다름, 파일 번호 다름 → 겹치지 않음 |
| U-HIT-03 | 근거 항목 적중(DR-11) | 항목 match [50,60], origin 경로 X, run commit C; 정답 (omf, X, C, [55,70]) → 적중 | origin 두 개 중 두 번째만 정답과 같은 경로 → 적중(O-11 권장) | parent 절 범위는 겹치나 match 범위는 안 겹침 → 적중 아님. 정답 revision ≠ run commit → 아님 |
| U-MET-01 | Recall@5(FR-011·FR-013) | 정답 있음 4질의 중 3개가 상위 5에 적중 → 3/4 | 6위 적중만 → 미적중. 정답 근거 2개 중 하나만 적중 → 성공 | 문서에 없는 질의가 분모에 들어가면 실패(정답 있음 4 + 문서에 없음 2에서 분모 4) |
| U-MET-02 | MRR@20 | 첫 적중 순위 1·2·없음 → (1 + 1/2 + 0)/3 | 20위 적중 → 1/20, 21위는 없음 취급(결과가 20개뿐) | `no_evidence` 정답 있음 질의 → 0 |
| U-MET-03 | 근거 완전성(FR-012) | 정답 3개 중 상위 5에서 2개 → 2/3, 정답 2개 중 2 → 1, 평균 5/6 | 정답 1개 질의는 제외 | 정답 2개 이상 질의 0개 → `not_applicable`(0이 아님) |
| U-MET-04 | 의도별 Recall@5·`no_evidence` 비율 | 의도별 분모가 그 의도의 정답 있음 질의 | 의도 하나에 질의 0개 → 그 의도 `not_applicable` | 문서에 없는 질의 3개 중 `no_evidence` 2 → 2/3. 정답 있음 질의의 `no_evidence`는 비율에 안 들어감 |
| U-MET-05 | 반환 근거 평균 길이(DR-06) | 항목 길이 = match `excerpt` 코드 포인트 합. "가나다"=3 | 같은 `chunk_id` 두 번 → 한 번만. `no_evidence` 질의 → 기여 없음 | 바이트 수로 계산하면 실패(한글 표본 "가"=1) |
| U-MET-06 | p95(DR-11) | 지연 1..100 → 95 | N=1 → 그 값. N=20 → 19번째 값 | 보간값(예: 95.05)이 나오면 실패 |
| U-MET-07 | 정확 산술 경계(R-01) | 현재 120/400, 후보 132/400 → 차이 정확히 3%p | 0.1+0.2 유형 부동소수 표본에서도 3%p 경계 비교가 정확 | 결과 표기에 `num`·`den`·`decimal`이 없으면 실패 |
| U-BOOT-01 | 결정성(FR-031) | 같은 d 목록·시드 → 두 번 계산 구간 같음 | 질의 순서를 섞어 넣어도 질의 ID 정렬 후 같음 | 시드 다르면 구간이 다를 수 있음(같은 경우도 허용, 첫 추출값은 다름) |
| U-BOOT-02 | 모두 같음(Edge Case) | d 전부 0 → [0, 0], 하한 > 0 아님 | N=1 → 구간이 그 값 하나 | 빈 목록 → 오류(정답 있음 질의 0) |
| U-BOOT-03 | 난수 규칙(DR-05) | SHA-256 카운터 첫 10개 추출이 고정 기대값(테스트에 적은 벡터)과 같음 | N=3에서 거절 표본추출 경계값 주입 → 다시 뽑음 | 추출 인덱스가 [0, N) 밖이면 실패 |
| U-BOOT-04 | 분위수 위치 | B=10,000 → 정렬 250번째·9,750번째 | B=40 → 1번째·39번째 | B=0 → 오류 |
| U-GATE-01 | 적용 권고(US3-1) | 필수 5종 통과, +3.4%p, 하한 +0.6%p, 하락·비용 한도 안 → `적용 권고`, 조건 5개 모두 `pass`와 측정값·기준값 | 근거 완전성 `not_applicable`이고 다른 조건 모두 통과 → `적용 권고`, ④ 사유에 `not_applicable` 기록 | 사유에 측정값·기준값 중 하나라도 빠지면 실패(FR-020) |
| U-GATE-02 | 경계값(SC-007, Edge Case) | +3.0%p → ② 통과 | 하한 정확히 0 → ③ 미통과. 의도 하락 정확히 5.0%p → ④ 미통과. MRR 하락 정확히 2.0%p → ④ 미통과. p95 증가 정확히 200ms → ⑤ 통과. 재색인 정확히 28,800초 → ⑤ 통과. GPU 최대 = 한도 → ⑤ 통과 | +2.99…%p(분수 299/10000) → ② 미통과 |
| U-GATE-03 | 불합격(US3-2·5·6) | ①·④·⑤가 모두 통과한 후보 → `불합격`이 아님(판정은 ②·③으로 정해짐) | 근거 완전성 `not_applicable` → ④에서 통과로 보고 사유 `not_applicable` | 구조 경계 위반 1 → `불합격`, 사유 ① 검사 이름. 후보 검사 `not_run` → ①. `왜` 하락 5%p → ④, 의도 이름. p95 +201ms, 재색인 28,801초, GPU 한도+1MiB, `no_evidence` 비율 감소 각각 → ⑤와 넘은 항목 |
| U-GATE-04 | 유의한 차이 없음(US3-3·4) | +2.5%p, 다른 조건 통과 → `유의한 차이 없음(현재 유지)` | +3.2%p + 하한 0 이하 → 같음 | ①과 ② 동시 미통과 → `불합격`(① 우선) |
| U-GATE-05 | 현재 구성 위반(Edge Case) | 현재 구성 커버리지 위반 → 기록되고 판정에는 영향 없음 | 현재·후보 모두 같은 위반 → 후보 ① 미통과(회귀가 아니어도 5종 통과 요구) | 현재 위반을 이유로 후보 ①을 면제하면 실패 |
| U-ATT-01 | 시도 횟수(FR-025·FR-026, DR-24) | 트랙 ② `attempt_number` 있는 행 2개 → 남은 1회 | 트랙 ② 3개 → `attempt_limit`(기준 `max_attempts_per_track` 값으로 판정, 2로 바꾼 가짜 기준에서는 2개에서 거부), 트랙 ③ 0개 → 허용 | 결과가 저장된 뒤 `failed`가 된 시도는 셈. 결과 없이 `failed`(`attempt_number` NULL)는 안 셈. `reproduction`은 안 셈. 다른 세대의 시도는 안 셈 |
| U-ATT-02 | 교체 건(DR-09) | `chunking` 건 등록 → `swap-chunking-1` | 같은 종류 두 번째 건(첫 건 미소진) → 허용 | 같은 세대에서 소진된 종류로 새 건 → `module_kind_exhausted`. `approval_ref` 없음 → 인자 오류 |
| U-ATT-03 | 세대 열기(DR-08) | 새 정답 있음 100(의도별 34·33·33) + 문서에 없음 10 → 새 세대, 모든 트랙 0회 | 의도 하나 25 → 통과, 24 → `replenish_insufficient` | 새 정답 있음 99 → 거부, 승인 참조값 없음 → 인자 오류, 새 스냅숏 규모 미달 → `test_set_scale` |
| U-ATT-04 | 시험셋 규모(FR-002, US4-4·5, Edge Case) | 정답 있음 400(의도별 134·133·133) + 문서에 없음 50 → 충족 | 의도 하나 정확히 100 → 충족 | 420+30, 390+60, 의도 하나 99, 평가 불가 제외 후 399 → 미충족(사유별 `detail`) |
| U-CRIT-01 | 관문 기준 상수(FR-022, DR-15) | v1.0 설정값이 data-model 3.10 표와 같고 정규화 해시가 고정 기대값 | 키 순서를 바꿔도 해시 같음 | `gpu_memory_limit_mib` 미정 → 관문 사전 점검 `gate_setting_missing`. 기준 상수에 승인 참조값 없으면 실패 |
| U-REC-01 | 재계산(SC-002, FR-015) | 질의별 결과 표본·기준·시드 → 기록 판정·사유와 같음 | 비용·필수 검사는 기록값 그대로 사용 | 질의별 결과 하나를 바꾸면 불일치 보고 |
| U-SNAP-01 | OMF 원본 자료 스냅숏(FR-010) | 가짜 git 읽기(파일 5개, 제외 패턴 해당 1개) → 항목 4개, 행 수·내용 해시 | 같은 항목으로 다시 → 같은 ID(멱등) | 없는 commit → `commit_not_found`. 결과 `extent`에 원문 문자열 0건 |
| U-SNAP-02 | 스냅숏 ID(DR-10) | 2026-11-20 첫 생성 → `SNAP-20261120-01` | 같은 날 두 번째 → `-02` | 그날 100번째 → 오류(상한 99) |
| U-SET-01 | 평가셋 스냅숏(FR-009, US5-5) | 접수 3건 → 스냅숏 `@n+1`, 구성원 +3 | 제거 1건 → 새 스냅숏, 이전 스냅숏 구성원 불변 | 스냅숏 `counts`·`scale_ok`가 구성원과 다르면 실패 |
| U-SET-02 | 전부 또는 전무(DR-10) | 3건 모두 유효 → 3건 접수, ID 순번 | 0건 파일 → `invalid_file` | 3건 중 1건 거부 → 접수 0, 질의 ID 소비 없음, 모든 건 사유 |
| U-SET-03 | 평가 불가 표시(Edge Case, DR-12) | revision 삭제 표시가 없는 질의 → 평가 가능 | 정답 근거 2개 중 하나의 revision만 삭제 → 그 질의 전체 평가 불가(일부 근거만으로 채점하지 않음) | 정답 revision 삭제 → 다음 스냅숏에서 `evaluable=false`, 이전 스냅숏 불변, 규모 계산 제외 |
| U-DEV-01 | 템플릿 확장(FR-008) | 틀 1개 × 채움 3개 → 질의 3개, 출처 구분 `템플릿`, 같은 순서 | 채움 0개 → 0개 | 자리 이름 없음 → `missing_field`. 남는 자리 → 같음 |
| U-DEV-02 | 시험셋 제외(FR-007, US4-6) | 겹침 없음 → 전부 접수 | 시험셋 제거 질의와 같음 → 접수 | 시험셋 질의와 같은 정규화 문 → 제외, `excluded_as_test_duplicate=1` |
| U-DEV-03 | 합성 가져오기(FR-008) | 생성 정보 완비 + 허용 호스트 → 접수, 출처 구분 `합성` | 허용 호스트와 포트까지 같아야 함: 같은 IP 다른 포트 → `synthesis_host_not_allowed` | 생성 정보 없음 → `provenance_missing`. 허용 목록 밖 호스트 → `synthesis_host_not_allowed` |
| U-SYN-01 | 합성 클라이언트 경계(DR-16, R-11) | 허용 호스트 + 비밀 파일 토큰 → 요청 생성 | 토큰 파일 끝 줄바꿈 → 제거 후 사용. 허용 호스트로 가는 리다이렉트 응답이 다른 호스트를 가리킴 → 따라가지 않고 `synthesis_unavailable` | 허용 목록 밖·외부 도메인 → 시작 거부. 토큰이 로그·예외·출력에 나오면 실패. 토큰 파일 없음 → `synthesis_unavailable` |
| U-SYN-02 | 구간 선택·정답 좌표 | 고정 시드 → 같은 원문 구간 목록. 생성 질의의 정답 근거 = 그 구간 좌표 | 구간이 문서 끝 행 → 범위 안 | 모델이 좌표를 만들어 오면 무시하고 구간 좌표 사용 |
| U-SYN-03 | 생성 결과 검사 | 정상 응답 → 접수 규칙 통과 | 빈 질의 → 제외·건수 보고 | 비 JSON 응답·시간 초과 → 그 건 제외, 전체 실패 아님, 제외 건수 기록 |
| U-PRIV-01 | 순위 결정 함수(DR-18) | 기존 `test_service.py`의 후보 표본으로 `rank_evidence` 결과 = 리팩터링 전 `SearchService` 결과(고정 기대값) | `strict` 경로 → 기존과 같음 | 하한선 미만 후보 포함 → 기존처럼 제외 |
| U-PRIV-02 | SQL 조각(DR-18) | 운영 키워드·벡터·활성 run SQL 문자열 = 리팩터링 전 문자열(바이트 동일) | run 지정 SQL에 `:run_id` 바인드, 상태 조건 `ready·active·previous` | run 지정 SQL에 `client_source_grants`가 있거나 문자열 보간이 있으면 실패 |
| U-PRIV-03 | 앞부분 안정성(R-03) | 같은 후보로 `limit=20` 앞 5개 = `limit=5` 결과 | 후보 3개 → 둘 다 3개. 5위와 6위 parent 점수 동점 → parent id 순서로 같은 결과 | `limit=0`·`21` → 오류(평가 경로 범위 1~20) |
| U-PRIV-04 | 준비·대조·대체 없음(D-05·D-06) | 등록 모델 + 호환 run → 준비 성공 | 차원만 다름 → `policy_index_incompatible` | 레지스트리에 없음 → `model_not_registered`. 임베딩 예외 → `query_embedding_failed`(빈 결과·`no_evidence`로 바꾸면 실패). `archived` run → `run_not_evaluable` |
| U-CHK-01 | 결정성(DR-13) | 재청킹 해시 목록 = 저장 목록, 1·2회차 근거 (청크 id, 순위) 같음 → `pass` | 파서 버전이 run 기록과 다름 → `not_run` | 청크 하나 해시 다름 → `fail`(문서 경로·개수). 2회차 순위 하나 다름 → `fail`(질의 ID) |
| U-CHK-02 | 원문 커버리지 | 모든 비공백 행이 청크 범위 안 → `pass` | 공백만 있는 행이 빠짐 → `pass` | 비공백 행 하나 빠짐 → `fail`(자료 ID, 행 번호) |
| U-CHK-03 | 좌표 정확성 | `raw_text`가 [시작,끝] 행 문자열의 부분 문자열이고 첫 행에서 시작·끝 행에서 끝남 → `pass` | 행 중간에서 시작하는 child → `pass` | 행 범위가 한 줄 어긋남 → `fail` |
| U-CHK-04 | 고아 청크 | 모든 청크가 occurrence → 자료 ID·revision에 닿음 → `pass` | 같은 내용이 두 경로에 있는 청크(occurrence 2개) → 한 번만 세고 `pass` | 연결 끊긴 청크 1개 → `fail`(청크 id) |
| U-CHK-05 | 구조 경계(US2-5) | 표 전체가 한 청크 → `pass` | 목록 항목 경계에서 나뉨 → `pass` | 표 한 행이 두 청크로 갈림 → `fail`. 코드 펜스 안에서 갈림 → `fail` |
| U-EXP-01 | 실행 순서(R-16) | 질의 1: 현재→후보, 질의 2: 후보→현재 | 예열 질의 3건의 지연은 결과에 없음 | 한 구성만 먼저 전부 실행하면 실패 |
| U-EXP-02 | 사전 점검(data-model 4.2, 번호는 4.2가 정본) | 모두 통과 → 진행 | 개발셋 실험은 사전 점검 8~11 건너뜀. 개발셋에서 제거된 질의와만 겹침 → 통과 | 순서대로 각 거부 코드. 후보 2개 → `candidate_limit`, 시도 기록 0건(US4-2). 스냅숏 다름 → `comparison_mismatch`(US2-3). 시험셋 스냅숏 구성원과 개발셋 현재 구성원의 정규화 질의문이 1건이라도 같음(DB에 직접 넣은 표본) → `dev_test_overlap`, 시도 기록 0건. 같은 트랙 `running` 행 + 가짜 잠금 탐침이 "잡힘" → `attempt_in_progress`, "없음" → `stale_attempt_in_progress`(`detail.next_step`에 정리 명령), 둘 다 실험 행 0건[DR-25] |
| U-EXP-03 | 시도를 세는 시점(DR-24, spec Edge Case) | 사전 점검·색인 쪽 필수 검사·예열 통과 → 첫 질의 직전에 실험 행 1건(`attempt_number` NULL) → 첫 질의 결과 저장 트랜잭션에서 `attempt_number`=현재 수+1(가짜 저장소의 호출 순서로 확인) | 사전 점검 거부 → 실험 행 0건. 색인 쪽 필수 검사가 `fail`로 끝남(오류 아님) → 진행해 첫 결과 저장 때 시도 1건 | 색인 쪽 필수 검사(재청킹) 중 예외, 모델 적재·예열 중 예외 → exit 4, 실험 행 0건, `attempt_consumed=false`, 질의 호출 0회. **첫 평가셋 질의에서 예외(결과 0건) → 실험 `failed`, `attempt_number` NULL, 시도로 안 셈, `attempt_consumed=false`**. 첫 질의 결과 저장 뒤 두 번째 질의에서 예외 → `failed`, `attempt_number` 있음, 시도로 셈, `attempt_consumed=true` |
| U-EXP-04 | 질의별 결과(FR-015, FR-028) | 순위·적중 순위·상위 5 적중 정답 번호·길이·지연 기록 | `no_evidence` → 근거 0개 | 기록에 `excerpt` 원문이 있으면 실패 |
| U-EXP-05 | 재현 대조(FR-030) | 같은 결과 → 일치 | 지연만 다름 → 일치(비용 제외) | 순위 하나 다름 → 불일치 수 1, 시도 횟수 불변 |
| U-EXP-06 | 비용 측정(DR-04·DR-14) | `nvidia-smi` 표본 출력 → 프로세스 최대 MiB. 콜드 빌드 run(재사용 0) → 초 | 표본 한 번뿐 → 그 값 | 측정 run 재사용 기록 있음·식별값 다름 → `cost_measurement_missing`. `nvidia-smi` 실패 → 개발셋 `not_measured`, 관문 거부 |
| U-EXP-07 | 생존 잠금 순서와 상실 시 중단(DR-25) | 가짜 DB·잠금 포트의 호출 순서가 UUID 생성 → 생존 잠금 `try` 획득 → 실험 행 INSERT 커밋 → 질의. 결과 트랜잭션 안 호출 순서가 부모 행 `FOR NO KEY UPDATE` → (`gate` 첫 결과만) 트랙 잠금·`attempt_number` → 결과 INSERT → 자기 확인 → 커밋. 완료 트랜잭션은 부모 행 잠금 → 3.14 INSERT → 자기 확인 → `completed` UPDATE → 커밋 | 자기 확인이 모든 쓰기 트랜잭션 커밋 직전에 한 번씩 일어남(질의 3개 × 2회차 = 결과 트랜잭션 6개 + 완료 1개 → 확인 7회). `try` 획득이 처음 2번 `false` → 매번 새 UUID, 세 번째 UUID로 행 INSERT, 기다리는 잠금 호출 0회 | `try` 획득 5번 모두 `false` → 실험 행 0건, exit 4 `liveness_lock_unavailable`(`retryable: true`), `dev`·`gate` 모두 같은 코드(`attempt_in_progress`가 나오면 실패). 두 번째 질의 커밋 직전 확인에서 "잠금 없음"·연결 오류·backend pid 변경 → 그 결과 커밋 0회, 이후 질의 호출 0회, `failed`(`liveness_lost`) 전이 시도 1회, exit 4. 그 전이마저 실패해도 exit 4로 끝남. 완료 직전 확인에서 "잠금 없음" → 3.14 커밋 0회, `completed` 0회, `failed`(`liveness_lost`) 시도, exit 4 |
| U-LOCK-01 | 잠금 키 유도(DR-25·DR-24) | 같은 UUID → 같은 (class, objid). 같은 (평가셋, 세대, 트랙) → 같은 트랙 키 | 생존 class와 트랙 class가 다르고 둘 다 기존 색인 파이프라인 잠금(단일 bigint 키)과 겹치지 않음. objid가 int4 범위(부호 있는) 안 | 잠금 함수가 단일 bigint 형식이거나 두 용도가 같은 class를 쓰면 실패. 자기 확인 SQL에 `locktype = 'advisory'`, `database = (SELECT oid FROM pg_database WHERE datname = current_database())`, `classid = CAST(:classid_u AS oid) AND objid = CAST(:objid_u AS oid)`(바인드 변수에 `::oid`를 붙인 형태면 실패), `objsubid = 2`, `pid = pg_backend_pid()`, `granted` 조건 중 하나라도 없으면 실패. 바인드 값 변환: objid `-1` → `objid_u = 4294967295`, objid `-2147483648` → `2147483648`, objid `5` → `5`(classid도 같은 규칙 `& 0xFFFFFFFF`). 음수 키를 부호 있는 값 그대로 바인드하면 실패 |
| U-ENV-01 | 실행 자원(DR-03) | 허용 목록 호스트·GPU UUID → 허용. 운영 개시 전 예외 표시(`pre_launch_exception: true`)가 있는 허용 항목이 운영 목록과 겹침 → 허용, `resource_name` "운영 서버(운영 개시 전 예외)" 기록(사용자 결정 2026-11-13) | CPU 전용 허용 자원 → 허용, GPU 메모리 `not_measured` | 운영 GPU UUID 일치(표시 없는 항목) → `resource_not_allowed`. 같은 설정에서 예외 표시만 지움 → `resource_not_allowed`(운영 개시 뒤 자동 복귀). 허용 목록 비어 있음·운영 목록 비어 있음(각각)·GPU 읽기 실패 → 거부 |
| U-ENV-02 | 운영 DB 거부(DR-02) | 평가 DB URL → 허용 | 호스트 대소문자만 다름·포트 명시 여부만 다름 → 같은 운영 DB로 보고 거부 | 운영 DB 식별값(호스트·DB 이름) 목록과 일치 → `production_database_refused`. 비밀번호가 오류에 나오면 실패. URL 포트(55432)와 가짜 서버 `port` 설정(5432)이 다른 직접 연결(Docker 포트 매핑) → 거부 없이 진행(시작 시 풀러 판정 거부가 생기면 실패) |
| U-OUT-01 | 시험셋 출력 제한(DR-21) | 관문 시도 출력 = 집계·조건 사유 | 개발셋 실험 출력엔 질의별 결과 요약 허용 | 관문·`show` 출력에 질의 ID별 결과·질의문 표식이 있으면 실패 |
| U-NET-01 | 외부 연결 없음(FR-034) | 접수·실험(가짜 경로) 중 socket 연결 0회 | 합성은 허용 호스트만 | 그 밖 연결 시도 → 테스트 실패 |
| U-MIG-01 | `0006` 정적 검사(RED, 컨테이너 없음). 파일을 모듈로 읽고 `alembic.op`를 기록용 가짜로 바꿔 호출(기존 `test_policy_migration.py` 관례) | `revision == "0006_evaluation_datasets"`, `down_revision == "0005_material_registration"`(R-13). 3.1~3.9 테이블 10개(3.8은 두 테이블), CHECK·append-only 트리거·출처 구분 대조 트리거 SQL | downgrade가 그 10개만 지움 | 기존·001 테이블에 alter·drop·UPDATE·DELETE → 실패. 앱 코드 import → 실패. 파일 없음 → assertion 실패(RED) |
| U-MIG-02 | `0007` 정적 검사 | `revision == "0007_evaluation_experiments"`, `down_revision == "0006_evaluation_datasets"`. 3.10~3.16 테이블 7개(3.16은 DR-25), 상태 전이 트리거, 시도 유일·진행 중 유일 부분 인덱스(진행 중 유일 인덱스는 이름이 정확히 `eval_experiments_running_track_uq`, 열 (`set_name`, `generation`, `track_id`), 조건 `WHERE kind = 'gate' AND status = 'running'`인 UNIQUE 인덱스. 이름·열·조건 중 하나라도 다르면 실패), 트랙 3행 INSERT | downgrade가 그 7개만 지움 | 기존 테이블 변경, 앱 코드 import → 실패 |
| U-ARCH-01 | 계층 규칙(R-12) | `application/evaluation`·`domain/evaluation`이 infrastructure·`Settings`·`EvaluationSettings`를 import하지 않음 | 함수 안 지연 import·`importlib` 문자열 import도 검사 대상 | 위반 import → 실패 |
| U-ARCH-02 | 생성물 격리(FR-008a, R-12) | 색인·검색·API 모듈이 평가 모듈을 import하지 않음, 그 SQL 문자열에 `eval_` 없음, `runtime.py`가 평가 포트를 조립하지 않음 | `eval_`이 SQL 식별자가 아닌 일반 단어(예: 변수 `eval_count`)로만 쓰인 Python 코드는 대상 아님. 검사는 SQL 문자열 상수에 한정 | 위반 → 실패 |
| U-FCOORD-01 | factory 범위 정보(P13, DR-22) | 001 가짜 포트: PDF 10쪽, xlsx 시트 2개 → `extent` 기록, 좌표 검증 통과 | 묶음(파일 3) → 파일별 `extent`, 스냅숏 항목 `composition`이 001 revision 값(`image_bundle`·`embedded_image_bundle`)과 같음 | 삭제된 revision → `revision_deleted`. 시트 이름 다름 → `sheet_not_found` |
| U-FCOORD-02 | factory 원본 자료 스냅숏 | 현재본 있는 자료만 고정 | `--material` 지정 → 그 자료만 | 현재본 없음 → `no_current_revision` |
| U-CAND-01 | 후보 목록 반환(DR-26 2·3, `012` 연동 ①) | 가짜 후보 묶음(키워드 6·벡터 6, 겹침 2, 하한선 통과 10) + `include_candidates=True` → `candidates` 10개, `position` 1..10이 RRF 순서, 원소마다 청크 id·상위 절 id·행 범위·원본·lane 순위(없는 lane은 null)·RRF 순위·점수, 리랭크 필드 null·null·거짓, `rerank_status = passthrough`, `rerank_ms = 0`. 근거 항목은 `include_candidates=False` 결과와 같음 | RRF 점수 동점 두 청크 → 기존 RRF 함수의 청크 id 순. 키워드 lane만 하한선 통과한 청크 → 포함(`vector_rank` null). 후보 수 설정 50/50, 결합 100개 → 100개, `candidates_truncated: false`. 가짜 정책 80/80으로 결합 130개 → 앞 100개, `candidate_total = 130`, `candidates_truncated: true`, 근거 항목은 130개 전체로 만든 결과와 같음. 두 lane 모두 하한선 미만 → `[]`, `no_evidence`, `skipped_no_candidates` | 두 lane 모두 하한선 미만인 청크가 목록에 있으면 실패. `include_candidates=False`인데 `candidates` 키가 있으면 실패. 후보 원소에 `excerpt` 원문 표식이 있으면 실패. 후보 목록을 얻으려고 검색 저장소를 두 번 부르면 실패(호출 수 1) |
| U-CAND-02 | 후보 목록 적중 위치(R-21, DR-11 2·3) | 정답 (omf, X, C, [55,70]), 3위 후보 청크 [50,60]·origin X → `candidate_hit_position = 3`, `candidate_count` = 결합 후보 수 | 같은 내용 두 원본 중 두 번째만 정답 경로 → 적중. 정답 두 개가 5위·2위에서 각각 겹침 → 2(첫 위치). 리랭크 뒤 순서에서 1위가 된 청크만 겹침 → 1(그 구성이 쓴 순서). 결합 130개인 질의의 101위에만 겹침 → NULL, `candidate_count = 130` | 정답 없음 질의 → NULL. 상위 절 범위만 겹치고 청크 범위는 안 겹침 → NULL. 정답 revision ≠ run commit → NULL |
| U-PRIV-05 | 순위 결정 함수 분할(DR-26 1) | U-PRIV-01 표본에서 `group_evidence_items(fuse_candidates(b, p, default), limit, 기본 순서 키)` = 개정 전 `rank_evidence` 고정 기대값 = 개정 뒤 `rank_evidence` | `strict` 경로 표본 → 같음. 후보 0개 → 둘 다 빈 결과 | 운영 `SearchService`가 `fuse_candidates`를 따로 부르거나 리랭커 슬롯을 가지면 실패. 운영 SQL 문자열이 바뀌면 실패(U-PRIV-02 재실행). 두 함수가 시계·I/O·난수를 쓰면 실패(정적 검사) |
| U-CMET-01 | Recall@N(후보 목록)·후보 수 통계(R-21, DR-27 3) | 정답 있는 질의 4개의 적중 위치 1·7·NULL·40 → `5`=1/4, `10`=2/4, `20`=2/4, `30`=2/4, `50`=3/4, `max`=3/4, `upper_gap_pp` = (3/4 − 1/4) × 100 = 50 | 후보 3개뿐인 질의가 3위 적중 → 모든 N에서 적중. 후보 수 [0, 3, 10, 100] → `min` 0, `median` 13/2(분수), `max` 100. [5, 1, 9] → `median` 5. `candidate_count` 130인 질의 1개 → `truncated_queries = 1` | 문서에 없는 질의가 분모에 들어가면 실패(정답 있음 4 + 문서에 없음 2에서 분모 4). 정답 있는 질의 0개 → Recall@N·`upper_gap_pp` 모두 `not_applicable`(0 나누기 오류 없음). 관문 판정 함수 입력이나 관문 기준 `settings`·해시에 `diagnostic` 키가 들어가면 실패(U-CRIT-01 기대 해시 불변) |
| U-RMET-01 | 참고 지표 MRR@3·nDCG@3(DR-27 4·5, `012` R-16. 기대값은 `012` plan U-RMET-01 그대로) | 정답 하나인 질의 3개의 첫 적중이 1위·3위·없음 → MRR@3 = 4/9(`num` 4, `den` 9), nDCG@3 = 0.5(허용 오차 1e-9) | 적중이 정확히 3위 → 셈(1/3, 이득 0.5). 4위만 적중 → 0. 정답 두 개가 1·2위에서 적중 → nDCG@3 = 1. 1위 근거 항목 하나가 정답 두 개와 모두 겹치고 다른 적중 없음 → 한 번만 셈: nDCG@3 = 1/(1 + 0.6309297535714574) ≈ 0.613147, MRR@3 = 1. 같은 질의별 결과를 순서만 바꿔 넣음 → 같은 binary64 값(`fsum`) | 정상 표본에 문서에 없는 질의 2개를 더해도 4/9·0.5 그대로. 정답 있는 질의 0개 → 둘 다 `not_applicable`. `decimal`이 소수점 6자리 문자열이 아니거나 `precision`이 없으면 실패. 할인값을 실행 때 `math.log2`로 계산하면 실패(상수 사용 정적 확인). 관문 판정 입력에 두 지표가 들어가면 실패 |
| U-RREC-01 | 리랭크 기록(DR-27 2, `012` 연동 ③) | 리랭커 없는 정책 → 질의별 `rerank_status = passthrough`, `rerank_ms = 0`, 모든 일치 조각 `rerank_score` null·`rerank_rank` null·`reranked` 거짓 | 결정적 가짜 리랭커(후보 깊이 3, 후보 5) → `applied`, 상위 3개 조각 `reranked` 참·점수 0~1·순위 1~3, 나머지 2개 거짓·null. 가짜 경계가 2.1ms를 보고 → `rerank_ms = 3`(올림). 문서에 없는 질의 → `skipped_no_candidates`, 0. 결과 요약 `costs.rerank_ms`의 `p95`·`p99`가 nearest-rank 값. 결정성 점수 차이(`012` DR-03): 2회차에서 가짜 리랭커가 모든 점수에 +0.0001 → 저장된 1·2회차 `rerank_score`를 같은 (질의, 구성, 청크)끼리 비교한 최대 차이가 0.0001로 계산됨(기록만으로 계산, 재실행 없음) | 평가 실행기가 `not_applied_error`·`not_applied_timeout`을 기록하면 실패. 기록에 원문 표식이 있으면 실패. `rerank_ms` 음수면 저장 거부 |
| U-RERR-01 | 평가 모드 리랭커 질의 오류(DR-26 4, D-06, R-14). 경로 사례는 P14, 실행기 사례(실험 상태·`attempt_consumed`)는 P16에서 실행(독립 검증 발견 F-8) | (P14) 가짜 리랭커 정상 → 근거 항목이 `012` 제안 식 순서 | (P14) 가짜 리랭커 예외 → `EvaluationPathError(rerank_failed)`, 시간 초과 → `rerank_timeout`, 입력과 다른 청크 집합 반환 → `rerank_contract_violation`. 어느 경우든 결과 객체 없음(RRF 순서 대체 0회). (P16) 두 번째 질의에서 그 오류 → 실험 `failed`, 그 질의 결과 저장 0건, 이후 질의 호출 0회 | (P14) RRF 순서 결과나 `no_evidence`를 돌려주면 실패. 오류 메시지에 질의문·원문 표식이 있으면 실패. (P16) 관문 시도에서 첫 결과 저장 뒤 오류 → `attempt_consumed: true`, 저장 전 → `false`(DR-24) |
| U-RERR-02 | 평가 모드 리랭커 준비 오류(DR-26 4·5) | 가짜 `012` 레지스트리가 승인 모델 기술자로 리랭커를 만듦 → 준비 성공, 슬롯 채움 | 정책에 리랭커 기술자 없음 → 레지스트리 호출 0회, 통과 모듈 | 가짜 레지스트리가 준비 코드 7개를 하나씩 냄 → 각각 사전 점검 7 거부 exit 3, 그 코드, 실험 행 0건, `attempt_consumed: false`, 질의 호출 0회 |
| U-HOLD-01 | 개발셋 역할과 확인 개발셋 사용 제한(DR-28 1·2·3·11) | `--role holdout` 개발셋 → `dev_role = holdout`. `--role` 생략 → `exploration`. 확인 개발셋 + 목적 `holdout_check` + 트랙 ② + `--test-set` 시험셋 + 키를 만들 수 있는 가짜 정책 → 사전 점검 7a 통과. 리랭커 교체 건 `swap-reranker-1`로 같은 요청 → 통과(사용자 결정 2026-11-16 (a)) | 확인 실험의 재현(`reproduction`) → 허용, 확인 횟수 불변. 탐색 개발셋 구성원과 같은 정규화 문을 확인 개발셋에 넣음 → 자동 제외, `excluded_as_exploration_duplicate=1`(반대 방향은 `excluded_as_holdout_duplicate`) | 확인 개발셋 + 목적 `tuning`·`bugfix_check`·`transition` → 각각 `holdout_not_allowed`(`purpose_not_allowed`). 탐색 개발셋 + `holdout_check` → `holdout_not_allowed`(`set_role_mismatch`). MVP 형식 정책(리랭커 기술자 없음) → `holdout_not_allowed`(`candidate_key_unavailable`). 등록됐지만 확인 규칙에 없는 트랙 ③·`swap-chunking-1` + `holdout_check` → `holdout_not_allowed`(`track_not_in_holdout_rules`). 등록되지 않은 id `track-9` → `unknown_track`. `--test-set`이 개발셋 → `not_test_set`. 시험셋 + `--role` → 인자 오류. 확인 실험이 아닌 개발셋 실험 요청에 `holdout` → `holdout_not_allowed`(`holdout_input_unexpected`). 모든 거부에서 실험 행 0건 |
| U-HOLD-02 | 확인 횟수, 시험셋 세대별(DR-28 4, 사용자 결정 2026-11-16 (c)) | 트랙 ②, 시험셋 T 세대 1에서 확인 1·2·3회 → `check_number` 1·2·3, 승인 참조 없이 허용 | 세대 1에서 4회째 + `--approval-ref` → 허용, `check_number` 4, 승인 참조 기록. `failed`로 끝난 확인도 셈(다음 번호 증가). 다른 확인 개발셋이어도 같은 (트랙, T, 세대)면 이어서 셈. 트랙 ②와 `swap-reranker-1`은 따로 셈. **새 세대 2가 열림 → 승인 참조 없이 확인 → `check_number` 1(0부터 다시), 세대 2의 3회까지 승인 없이 허용** | 세대 1에서 4회째 승인 참조 없음 → `holdout_check_limit_exceeded`, 실험 행 0건, 번호 소비 없음. 가짜 저장소에서 두 확인이 같은 세대의 3번을 동시에 노림 → 잠금 아래 하나만 3, 다른 하나는 승인 없으면 거부 |
| U-HOLD-03 | 관문 앞 확인 대조와 1:1(DR-28 5·6·9·10, `012` U-FRZ-04의 일반 사례) | 후보 X로 확인(`completed`, 시험셋 T 세대 1) → X로 T 세대 1 관문 요청(현재 구성·확인 개발셋 스냅숏 같음) → 2a 통과, 3.18 소진 1건(`generation` 1), 실험 행 `gate_request_id` = 소진 행 값이고 `generation` 1, 연결 시도 차수 = 그 행 `attempt_number`. 교체 건 `swap-reranker-1`도 같은 흐름 | X의 미소진 확인 두 건(1·2번) → 1번을 소진. **넘겨 쓰기[결정 2026-11-16]: 정상: 세대 1에서 X로 한 미소진 확인(키 일치)이 있고 세대 2가 열림 → 세대 2 관문 요청이 그 기록을 소진(3.18 `generation` 2, 3.17 `generation` 1), 세대 2의 확인 횟수는 0 그대로(새 세대 횟수에 더하지 않음). 경계: 같은 상황에서 세대 2 전에 색인이 바뀌어 확인 입력 식별값(⑨ 뒤 파일 B SHA-256)이 다름 → `gate_without_holdout_check`(`no_matching_check`), 세대 1 기록은 미소진으로 남음**. 확인이 필요 없는 트랙 ③ 관문 요청 → 2a에서 아무것도 하지 않음(소진 0건) | X 확인이 이미 소진된 뒤 X로 다시 요청 → `holdout_check_already_used`. 확인 없는 후보 Z → `gate_without_holdout_check`(`no_matching_check`). 대조 키 네 성분(현재 정책 해시, 리랭커 설정 해시, 후보 깊이, 확인 입력 식별값) 중 하나씩만 다른 요청 4가지 → 각각 `gate_without_holdout_check`. 확인 뒤 현재 구성 run이 바뀜 → 확인 입력 식별값 불일치로 같은 거부. `failed` 확인만 있음 → 같은 거부. **넘겨 쓰기[결정 2026-11-16]: 실패: 세대 1에서 X로 한 확인이 이미 세대 1 관문 시도에 소진됨 → 세대 2가 열린 뒤 X로 관문 요청 → `holdout_check_already_used`(넘겨 쓸 미소진 기록 없음)**. 다른 시험셋을 `--test-set`으로 한 확인만 있음 → `gate_without_holdout_check`(`no_matching_check`). 요청 파일에 `holdout` 없음 → `holdout_input_missing`. 트랙 ③ 요청에 `holdout` 있음 → `holdout_not_allowed`(`holdout_input_unexpected`). 후보 정책이 MVP 형식 → `candidate_key_unavailable`. 모든 거부에서 실험 행 0건, 시도로 안 셈 |
| U-HOLD-04 | 소진 시점(H-1, DR-28 7·8) | 가짜 저장소 호출 순서: 사전 점검 1 → 2 → 2a(소진 INSERT·커밋) → 3~11 | 사전 점검 1(`resource_not_allowed`)·2(`production_database_refused`) 거부 → 소진 0건. 2a 뒤 사전 점검 8 `test_set_scale` 거부 → exit 3, 소진 1건 유지, `detail.holdout_check_consumed: true`, 시도 수 불변. 2a 뒤 9 `attempt_in_progress`, 10 `gate_setting_missing`, 11 `cost_measurement_missing` → 같음. 실험 행 기록 뒤 첫 결과 전 오류 → `attempt_number` NULL, 소진 유지 | 소진 뒤 거부에서 3.18 행이 사라지거나 `holdout_check_consumed`가 없으면 실패. 2a보다 앞에서 DB에 쓰면 실패 |
| U-SYN-04 | 문서에 없는 질의 합성(R-20, `012` 연동 ⑥) | 가짜 사내 서버, `--mode no_answer --per-intent 무엇=34,왜=33,어떻게=33` → 의도별 요청 34·33·33, 접수 질의 모두 "정답 없음"·근거 빈 배열·출처 "합성", `provenance`에 모드·의도·`no-answer-v1`·프롬프트 해시·제목 목록 해시. 요청 본문에 제목 계층만 있고 본문 표식 0건 | 같은 `--seed` 두 번 → 같은 `review_sample`. 접수 12개 → `review_sample` 12개 전부. 모델이 정답 좌표를 붙여 옴 → 무시하고 빈 목록. `--mode answerable --per-intent` → 지금 합성을 의도별 수로. 시험셋과 같은 문 → 제외 | 검색 포트 호출이 1회라도 있으면 실패(검색 결과로 거르지 않음). `--count`와 `--per-intent` 동시·둘 다 없음·`무엇=-1`·`언제=3` → exit 2. 비 JSON 응답 → 그 건 제외, `shortfall_by_intent`에 부족 수. 허용 목록 밖 호스트 → `synthesis_host_not_allowed` |
| U-EXP-08 | 단일 구성 진단(R-18, `012` 연동 ⑦) | `current` = `candidate`(같은 run·정책) 개발셋 실험 → 사전 점검 통과, 두 역할 교대 실행, `single_config: true`, 짝 차이 모두 0, 구성별 `diagnostic` 둘 다 기록 | 같은 run·다른 정책 → `single_config: false` | 같은 구성 관문 시도 → 특별 거부 없음(사전 점검 3 통과). 같은 구성인데 `candidate_limit`·`comparison_mismatch`가 나오면 실패 |
| U-EXP-09 | 콜드 빌드 측정 run 공유(R-19, `012` 연동 ⑧) | 두 구성 같은 run, 같은 측정 run id → 사전 점검 11 두 구성 모두 통과, 두 구성 재색인 초 같음, `reindex_measurement_shared: true` | 다른 run이지만 색인 식별값 같음 + 같은 측정 run → 통과, 공유 표시 | 색인 식별값이 다른 두 구성에 같은 측정 run → `cost_measurement_missing`(한쪽 조건 위반). 측정 run 없음 → `cost_measurement_missing` |
| U-MIG-03 | `0008` 정적 검사(DR-27 1) | `revision == "0008_evaluation_research_linkage"`, `down_revision == "0007_evaluation_experiments"`. 3.3 `dev_role`·3.12 `gate_request_id`·3.13 열 4개 추가, 목적 CHECK에 `holdout_check`, `rerank_status` CHECK 다섯 값, 3.9 `excluded_counts`(NULL 허용 jsonb, 기본값 없음), 3.17(`test_set_name`·`generation` 포함, UNIQUE 네 열)·3.18 생성·append-only·승인 참조·트랙·세대 일치 트리거, 3.18 PK 이름 `eval_holdout_consumptions_pkey` | downgrade가 추가한 열·제약·트리거·두 테이블만 지움. `dev_role` 열은 기본값 없이 NULL 허용으로 더하고, 새 `kind = dev` 행의 NOT NULL은 BEFORE INSERT 트리거로만 강제(독립 검증 발견 F-4) | `eval_` 아닌 테이블(기존·001)에 ALTER·DROP·UPDATE·DELETE → 실패. `0006`·`0007` 파일 내용 변경 → 실패. 앱 코드 import → 실패. `eval_sets`·`eval_submissions`에 UPDATE문, `DISABLE TRIGGER`, `excluded_counts`의 `DEFAULT`·`NOT NULL`, 또는 `dev_role`의 `DEFAULT`·`NOT NULL` 열 제약(기존 시험셋 행까지 값이 생김)이 있으면 실패 |

### 계약 테스트(C)

| ID | 관찰할 동작 | 예상 결과 |
|---|---|---|
| C-CLI-01 | `eval source-snapshot create·list·show` | 성공 출력 키 집합 고정, 목록에 기본 선택 표시 없음, `show --materials`에 행 수·revision, 없는 ID → exit 5 |
| C-CLI-02 | `eval set create·list`, `eval query add·remove`, `set snapshot list·show` | 접수 성공 출력(질의 ID, 스냅숏 ID, `scale_ok`), 거부 exit 3 `query_rejected`와 `detail.entries[].reasons[]`(질의문 표식 0건), JSON 아님 exit 2, 이미 제거 exit 3 |
| C-CLI-03 | `eval dev build-from-template·import-synthesized·synthesize` | `excluded_as_test_duplicate` 출력, 사람 작성 파일을 개발셋에 → `set_kind_mismatch`, 허용 밖 호스트 → exit 3 |
| C-CLI-04 | `eval policy register`, `eval track list·register-swap` | `config_hash` 출력, 운영 정책 설정 불변, 남은 시도 수 표시, `module_kind_exhausted` exit 3 |
| C-CLI-05 | `eval experiment run·show·export` | dev 출력 키(지표·필수 검사·비용·평균 길이·짝 비교), `export`는 기록 1건 생성, 시험셋 `show`는 집계 출력 |
| C-CLI-06 | `eval gate attempt·recompute`, `eval experiment reproduce` | 관문 출력 키(`judgment`, `reasons` 5개, `attempt_number`, `remaining_attempts`), `attempt_limit`이면 `detail.next_step`, 재계산 `judgment_match`, 재현 `match`. `eval experiment abandon`[DR-25]: 성공 출력 키(`status`, `failure_code`, `attempt_counted`, `remaining_attempts`), 살아 있음 exit 3 `experiment_alive`, `running` 아님 exit 3 `not_running`, 가짜 저장소가 교착(SQLSTATE `40P01`)을 내면 exit 4 `lock_conflict`·`detail.retryable: true`·3.16 기록 0건·상태 불변, 인자 누락 exit 2, 없음 exit 5 |
| C-CLI-07 | 공통 규칙 | 모든 명령의 표준 출력·오류에 DB 비밀번호·합성 토큰·질의문 표식 0건, `--actor` 누락 exit 2, `eval resource show` 출력 키 |
| C-CLI-08 | 시험셋 내용 열람 기록(DR-21, data-model 3.15) | `eval query show --set 시험셋 --query Q`는 질의문 없이 작성 정보·좌표만 출력하고 기록 0건. `--include-text --reason R --actor A`면 질의문 출력과 `eval_access_log`에 `kind=test_query_text`, `set_name`·`query_id`·`reason` 1건(`experiment_id` NULL). `--reason`·`--actor` 없이 `--include-text` → exit 2, 기록 0건. 개발셋 질의는 옵션 없이 질의문 출력·기록 0건. `experiment export`는 `kind=per_query_export`·`experiment_id` 1건 |
| C-CLI-09 | 확인 규칙 CLI(DR-28, 개정) | `eval set create --role` 성공 출력에 `dev_role`, 시험셋 + `--role` exit 2. `eval experiment run --purpose holdout_check` 출력에 `holdout_check_number`·`generation`, `--track`·`--test-set` 없음 exit 2. `eval gate attempt` 거부 코드(`gate_without_holdout_check`, `holdout_check_already_used`) exit 3와 `detail.rule`, 2a 뒤 거부의 `detail.holdout_check_consumed: true`·`holdout_experiment_id`. 개발셋 실험 요청이나 확인이 필요 없는 트랙의 관문 요청에 `holdout` → exit 3 `holdout_not_allowed`(`holdout_input_unexpected`, 독립 검증 발견 F-6), 아무것도 바뀌지 않음. `eval track list --set T` 트랙 ②·리랭커 교체 건 키 `holdout_checks_used`·`holdout_checks_free_remaining`·`holdout_checks_used`·`holdout_checks_free_remaining`(현재 세대 기준), `holdout_checks_unconsumed`(그 시험셋의 모든 세대, 넘겨 쓰기 대상 포함[결정 2026-11-16]). 출력에 질의문 표식 0건 |
| C-CLI-10 | 문서에 없는 질의 합성 CLI(R-20, 개정) | `--mode no_answer --per-intent` 성공 출력 키(`mode`, `by_intent`, `review_sample`, `shortfall_by_intent`, `excluded_as_test_duplicate`), `review_sample`에 질의문 없음, 인자 조합 오류 exit 2, 기본 모드 출력은 개정 전과 같은 키 집합 + `mode` |
| C-CLI-11 | 실험 출력의 진단·참고 키(DR-27, R-18·R-19, 개정) | `eval experiment run` 출력의 구성별 `metrics.diagnostic`·`metrics.reference`, `reference.delta`, `costs.rerank_ms`, `single_config`, `reindex_measurement_shared`. 관문 시도 집계 출력에 `reference` 집계는 있고 질의별 값은 없음(DR-21). `eval gate recompute` 출력 키는 개정 전과 같음 |
| C-SRCH-01 | 공개 API 불변(D-04) | 기존 `tests/contract/api/test_search_api.py`(경로 3개 테스트 포함)·`test_web_delivery.py`가 수정 없이 통과. `create_app` 경로 집합 불변 |
| C-SRCH-02 | 운영 검색 단위 동작 불변 | 기존 `tests/unit/search/*`가 수정 없이 통과(P8 전후 같은 결과) |

### 통합 테스트(I, PostgreSQL)

| ID | 관찰할 동작 | 예상 결과 |
|---|---|---|
| I-DB-01 | 마이그레이션 | upgrade 뒤 새 테이블 17개와 제약·트리거 존재, 기존 테이블 스키마 기대값 불변, downgrade 뒤 새 테이블만 사라짐, ORM 대응 통과 |
| I-DB-02 | 불변 트리거(FR-029, US5-3) | 스냅숏·질의·정답 근거·제거·접수·기준·트랙·질의별 결과·결과·열람 기록 UPDATE·DELETE 거부. 열람 기록 `kind`별 필수 컬럼 CHECK(`per_query_export`는 `experiment_id`, `test_query_text`는 `set_name`·`query_id`). 실험은 `running→completed\|failed` 한 번만, `running` 중 `attempt_number` NULL→값 한 번만(값→다른 값, 값→NULL, 종료 뒤 지정은 거부), 그 밖 UPDATE·DELETE 거부. 결과 가드: 부모 실험이 `completed`·`failed`인 3.13·3.14 INSERT 거부, `running`이면 허용[DR-25] |
| I-DB-03 | CHECK·유일 | 시험셋에 `템플릿` 질의 INSERT 거부, 같은 (평가셋, 세대, 트랙, 차수) 두 번째 거부(차수가 NULL인 행 여러 개는 허용), 같은 트랙 `running` 두 개 거부(오류 SQLSTATE `23505`, 위반 제약 이름 `eval_experiments_running_track_uq`), `gate` 시드가 기준과 다르면 거부, `attempt_number`를 기준 행 `settings.max_attempts_per_track`(3)보다 큰 값으로 정하면 트리거 거부·`0` 이하면 CHECK 거부 |
| I-DATA-01 | OMF 스냅숏(git fixture) | fixture 저장소의 고정 commit에서 스냅숏 생성, 행 수가 실제 파일과 같음, OMF 작업 트리 불변 |
| I-PRIV-01 | run 지정 질의(FR-032) | 활성이 아닌 `ready` run에 질의해 결과를 받음. 전후 활성 포인터·run 상태·감사 행 수 불변 |
| I-PRIV-02 | 운영–평가 동등성(DR-18) | 활성 run에 대해 같은 정책·질의 10개로 운영 `SearchService` 근거 목록 = 평가 경로 근거 목록 |
| I-PRIV-03 | 거부 상태 | `archived`·`failed`·`building` run → `run_not_evaluable` |
| I-EXP-01 | 개발셋 실험 끝까지(US2-1) | fixture 색인 run 두 개(청킹 설정만 다름)로 실험 → 기록에 모든 지표·필수 검사 5종·비용(재색인 `not_measured` 허용)·평균 길이·환경, 상태 `completed` |
| I-EXP-02 | 동시 관문 시도(FR-026) | 같은 트랙 두 프로세스 → 하나 진행, 하나 `attempt_in_progress`(시도로 안 셈). 두 프로세스가 사전 점검 9를 함께 통과하도록 맞춘 경우에도 뒤쪽 실험 행 INSERT의 유일성 위반(`23505`, 위반 제약 이름 `eval_experiments_running_track_uq`)이 exit 3 `attempt_in_progress`로 바뀌고, 그 프로세스의 실험 행 0건·생존 잠금 해제 확인. 같은 실험 id로 두 번 INSERT해 PK에서 `23505`가 나면 `attempt_in_progress`가 아니라 exit 4 |
| I-EXP-03 | 중단된 시도 정리(DR-25, DR-24, FR-026) | (a) 관문 시도 프로세스를 첫 결과 저장 **전**에 강제 종료(SIGKILL, 연결 끊김) → 행은 `running`·`attempt_number` NULL로 남음. 같은 트랙 새 시도 → `stale_attempt_in_progress`(시도로 안 셈). (b) 다른 세션이 그 실험 id 잠금을 쥔 상태에서 `abandon` → exit 3 `experiment_alive`, 행 불변. (c) 잠금 해제 뒤 `abandon` → `failed`(`abandoned`), 3.16 기록 1건, 사용 시도 수 불변(0). (d) 이어서 같은 트랙 시도 → 진행 가능(트랙 해제). (e) 첫 결과 저장 **뒤** 강제 종료한 시도를 정리 → 시도로 셈(남은 횟수 1 감소). (f) `abandoned` 전이를 3.16 행 없이 직접 UPDATE → 트리거 거부. (g) 시작 경쟁: 실행기를 실험 행 INSERT 커밋 직후·첫 질의 전에 멈춰 두고 `abandon` → exit 3 `experiment_alive`(잠금이 INSERT 전에 잡혀 있음), 행 `running` 유지. (h) 좀비 쓰기: 정리(`abandoned`) 커밋 뒤 이전 실행기 경로로 3.13 INSERT → 결과 가드가 거부, 새 시도는 같은 트랙에서 진행 가능. (i) 잠금 상실: 실행 중 전용 연결의 백엔드만 `pg_terminate_backend`로 끊음(주 연결은 살아 있음) → 실행기가 다음 커밋 전에 멈추고 `failed`(`liveness_lost`), 그 뒤 3.13 행 증가 0건, exit 4 |
| I-DB-04 | `0008` 제약·트리거(DR-27·DR-28, 개정) | `0008` 전에 넣은 `kind = dev` 행이 있는 DB에 upgrade → 성공, 그 행 `dev_role` NULL(유효 역할 `exploration`), `kind = test` 행 NULL. upgrade 뒤 `kind = dev` 새 행에 `dev_role` NULL INSERT 거부, 시험셋 행에 값 INSERT 거부. 기존 3.9 행 `excluded_counts` NULL, 새 3.9 행에 `{"test_duplicate":0,"exploration_duplicate":2,"holdout_duplicate":0}` INSERT 허용, 음수·모르는 키 거부, 3.9 UPDATE는 여전히 거부. 목적 `holdout_check`가 `dev`·확인 개발셋이 아니면 거부, `rerank_status` 다섯 값 밖 거부, 3.17 같은 (`track_id`, `test_set_name`, `generation`, `check_number`) 두 번째 거부(다른 세대의 같은 번호는 허용), 승인 없이 허용된 수를 넘는 번호 + 승인 참조 NULL 거부, 3.17·3.18 UPDATE·DELETE 거부, 3.18 같은 확인 두 번째 INSERT `23505`(제약 이름 `eval_holdout_consumptions_pkey`), 3.18과 3.17의 트랙·시험셋이 다르거나 3.18 `generation`이 3.17 `generation`보다 작으면 거부(더 크면 넘겨 쓰기로 허용[결정 2026-11-16]), 새 열이 든 3.13 INSERT도 결과 가드(부모 `running`)를 따름, 기존 테이블 스키마 기대값 불변 |
| I-HOLD-01 | 동시 소진(DR-28 9, 개정) | 같은 확인 기록을 노리는 관문 요청 두 프로세스 → 하나만 소진, 다른 하나 exit 3 `holdout_check_already_used`, 소진 행 1건. 다른 제약의 `23505`(같은 `gate_request_id` 재사용 주입) → exit 4 |
| I-PRIV-04 | 후보 목록 동등성(DR-26, 개정) | fixture 색인 활성 run에서 질의 10개로 `include_candidates=True`·`False`의 평가 경로 근거 목록이 서로 같고 운영 `SearchService` 근거 목록과 같음(I-PRIV-02와 같은 기준). 후보 목록 길이 ≤ 100, 전후 활성 포인터·감사 행 수 불변 |
| I-GATE-01 | 시도 한도 영속(US4-1·3) | 트랙 ② 3회 기록 뒤 네 번째 거부, 트랙 ③ 허용, 불합격 뒤 재시도는 2회차·남은 1회 |

### 인수 하네스(H)

| ID | SC | 내용 | 합격 |
|---|---|---|---|
| H-01 | SC-001 | 표본 실험 3건(개발셋 2, 관문 1)을 `reproduce` | 질의별 결과·품질 지표·필수 검사 불일치 0건 |
| H-02 | SC-002 | 표본 관문 기록 전부(적용 권고·불합격·유의한 차이 없음 각 1 이상)를 `recompute` | 판정·사유 일치 100%, 재실행 0건 |
| H-03 | SC-003 | 정성 검토(아래 기준). 작성자 2명 이상이 이 spec과 질의 작성 형식 계약서 한 파일(A-1 승인)만 보고 각 20건 이상(정답 하나·여럿·문서에 없음 포함) 작성·제출 | 형식 오류 거부 0건, 검토자 판정 합격 |
| H-04 | SC-004 | 한 시험셋에서 트랙 ②의 네 번째 시도, 후보 2개 요청, 트랙 ③ 시도 | 네 번째·후보 2개 성공 0건, 트랙 ③ 남은 횟수 불변 |
| H-05 | SC-005 | 조건 하나씩만 어긴 시험셋 3개(정답 있음 399, 의도 하나 99, 문서에 없음 49)로 관문 시도 | 판정 0건(모두 `test_set_scale`) |
| H-06 | SC-006 | (a) 시험셋과 겹치는 질의문이 든 템플릿·합성 결과로 개발셋 생성, (b) 개발셋이 생긴 **뒤** 개발셋 질의와 같은 문을 시험셋에 제출, (c) 겹침을 DB에 직접 넣은 뒤 관문 시도 | (a) 제외 보고, (b) `duplicate_query`(`in_dev_set`) 거부[DR-23 승인안], (c) `dev_test_overlap` 거부. 모든 시점에서 두 셋의 현재 구성원에 같은 정규화 질의문 0건 |
| H-07 | SC-007 | 경계값 표본 6종을 질의별 결과로 합성해 판정(+3.0%p, 하한 0, 의도 5.0%p, 보조 2.0%p, 200ms, 8시간) | 전부 data-model 4.4대로 |
| H-08 | SC-008 | fixture DB에서 실험 전후 운영 `SearchService`로 고정 질의 결과 비교, 활성 포인터 비교 | 결과 차이 0건, 활성 색인 변경 0건 |
| H-09 | SC-009 | `적용 권고` 판정 뒤 운영 정책 설정·활성 포인터·정책 manifest 선택 확인, 평가 코드에서 포인터·설정 쓰기 경로 정적 검색 | 변경 0건, 쓰기 경로 0개 |
| H-10 | SC-010 | 접수·합성(가짜 사내 서버)·실험 전체를 socket 감시 아래 실행, 합성 질의문 표식을 `chunks.raw_text`·`search_text`와 검색 응답에서 검색 | 외부 연결 0건, 허용 밖 모델 0건, 표식 발견 0건 |
| H-11 | SC-011 | 표본 실험 기록 전부의 `resource_name`·`environment`, 운영 GPU UUID로 꾸민 환경에서 실행 시도 | 실행 자원 누락 0건, 운영 GPU 실행 0건(운영 목록에 든 GPU 기준. 운영 개시 전 예외 기간의 운영 서버 실행은 "운영 서버(운영 개시 전 예외)" 표시 누락 0건으로 본다, spec SC-011). 자원 배치 점검은 quickstart 5절 |

### 정성 평가 기준(H-03, SC-003)

| 기준 | 합격 | 불합격 |
|---|---|---|
| 항목 완비 | spec 예시 A·B처럼 작성자 항목 8개가 모두 있고 값이 spec 표기 그대로 | 정답 위치를 "운영 매뉴얼 3장 근처"처럼 revision·좌표 없이 적음 |
| 정답 표시 | 정답 있음이면 근거마다 (source, 자료 ID, revision, 위치)가 있고 스냅숏 목록에서 확인한 값 | 확인 기준 원본 자료 스냅숏을 빠뜨리거나 목록에 없는 값 |
| 문서에 없는 질의 | 의도가 있고 `정답 근거 목록`이 빈 배열 | 의도 없음, 또는 "정답 없음"인데 근거를 적음 |
| 다른 문서 의존 | 작성자가 spec과 작성 형식 계약 외 문서를 묻지 않았다(검토자 면담 기록) | 키 이름·좌표 표기를 다른 사람에게 물어서 작성 |

검토자는 작성자별 표본 20건(설정값)을 위 네 기준으로 판정하고, 하나라도 불합격이면 SC-003 불합격으로 기록한다.

## 구현 단계 묶음(Phase)

각 Phase는 하나의 의도와 독립 검증을 가진다. 줄 수는 테스트 포함 예상치다. 분할 순서는 헌법 II(Single Intent → 위험 격리 → 독립 검증 → 계층 분할 → 줄 수)를 따랐다. UI 작업이 없으므로 컴포넌트 순서 규칙은 해당 없다.

| Phase | 의도(관찰 가능한 결과) | 주요 테스트 | 예상 줄 수 | 선행 | 필요한 DR |
|---|---|---|---|---|---|
| P1 | 질의 규칙·좌표·겹침 순수 함수 | U-QRY-01~06, U-COORD-01~05, U-HIT-01~03, U-ARCH-01 | ~400 | — | DR-07, DR-10, DR-11, A-15 |
| P2 | 지표·부트스트랩 순수 함수 | U-MET-01~07, U-BOOT-01~04 | ~380 | P1 | DR-05, DR-06, DR-11 |
| P3 | 관문 기준 상수·판정·규모·시도 횟수·세대·교체 건 규칙과 기록 기반 재계산(순수) | U-GATE-01~05, U-ATT-01~04, U-CRIT-01, U-REC-01 | ~400 | P2 | DR-04(값 없이 구조만), DR-08, DR-09, DR-15 |
| P4a | `0006` 평가셋·스냅숏 테이블 마이그레이션 | RED·GREEN: U-MIG-01. 검증 Agent: I-DB-01~03(해당 테이블) | ~350 | P1, 001 `0005` 병합(R-13) | DR-01, DR-10, DR-12 |
| P4b | `0007` 실험·관문 테이블 마이그레이션 | RED·GREEN: U-MIG-02. 검증 Agent: I-DB-01~03 | ~350 | P4a | DR-01, DR-21, DR-24, DR-25 |
| P5 | OMF 원본 자료 스냅숏·질의 접수(개발셋 겹침 거부 포함)·평가셋 스냅숏·세대 열기·질의 열람과 CLI | U-SNAP-01·02, U-SET-01~03, C-CLI-01·02·08(`query show`), I-DATA-01 | ~400 | P4a, P1 | DR-02, DR-08, DR-10, DR-12, DR-17, DR-23 |
| P6 | 개발셋 템플릿 확장·합성 결과 가져오기·시험셋 제외 | U-DEV-01~03, C-CLI-03(템플릿·가져오기) | ~300 | P5 | DR-10, DR-16(가져오기 규칙) |
| P7 | 사내 모델 합성 클라이언트 | U-SYN-01~03, U-NET-01(합성), C-CLI-03(synthesize) | ~280 | P6 | DR-16 |
| P8 | 비공개 평가 경로: 순위 결정 함수·SQL 조각 추출, run 지정 저장소, 모델 레지스트리, 정책–run 대조(위험 격리: 운영 검색 코드) | U-PRIV-01~04, C-SRCH-01·02, I-PRIV-01~03 | ~400 | P1 | DR-18 |
| P9 | 필수 검사 5종 | U-CHK-01~05 | ~400 | P8, P5 | DR-13 |
| P10 | 개발셋 실험 실행기: 사전 점검, 교대 실행, 두 번 실행, 비용 측정, 자원 확인, 기록, 재현, 출력 규칙, 정책 등록 | U-EXP-01~07, U-LOCK-01, U-ENV-01·02, U-OUT-01, U-NET-01(실험), C-CLI-04·05·08(`export`), I-EXP-01 | ~400 | P3, P4b, P8, P9 | DR-02, DR-03, DR-04, DR-14, DR-17, DR-19, DR-20, DR-21, DR-24, DR-25 |
| P11 | 관문 시도·재계산 CLI·트랙 등록·세대 연동 | C-CLI-06·07, I-EXP-02·03, I-GATE-01 | ~300 | P10 | DR-08, DR-09, DR-15, DR-21, DR-24, DR-25 |
| P12 | 인수 하네스·격리 검사 | H-01~11, U-ARCH-02 | ~350 | P11, P7 | — |
| P13 | factory 원본 자료 스냅숏·좌표 검증 | U-FCOORD-01·02 | ~250 | P5, 001 구현 완료 | DR-22 |
| P4c | (개정) `0008` 연구 연동 마이그레이션: `eval_` 테이블 추가 열, 확인 실험·확인 소진 테이블 | RED·GREEN: U-MIG-03. 검증 Agent: I-DB-04, I-DB-01~03 재실행 | ~250 | P4b | DR-27, DR-28 |
| P14 | (개정) 비공개 평가 경로의 후보 목록 반환, 순위 결정 함수 분할, 평가 전용 리랭커 슬롯·질의 오류 코드(위험 격리: 운영 검색 코드) | U-CAND-01, U-PRIV-05, U-RERR-01(경로 사례), U-PRIV-01~03 재실행, C-SRCH-01·02, I-PRIV-04 | ~380 | P8 | DR-26 |
| P15 | (개정) 진단·참고 지표 순수 함수(Recall@N(후보 목록), 후보 적중 위치, MRR@3, nDCG@3) | U-CAND-02, U-CMET-01, U-RMET-01, U-CRIT-01 재실행 | ~250 | P1, P2 | DR-27 |
| P16 | (개정) 실험 실행기의 연동 기록(후보 적중 위치·리랭크 기록·진단·참고 지표 저장과 출력), 단일 구성 진단, 측정 run 공유 | U-RREC-01, U-RERR-01(실행기 사례), U-EXP-08·09, C-CLI-11 | ~350 | P10, P4c, P14, P15 | DR-27, R-18, R-19 |
| P17 | (개정) 개발셋 역할, 확인 실험, 확인 횟수, 관문 앞 확인 대조·소진(사전 점검 2a·7a), 확인 규칙 CLI | U-HOLD-01~04, C-CLI-09, I-HOLD-01 | ~400 | P11, P16, P4c | DR-28 |
| P18 | (개정) 문서에 없는 질의 합성과 의도별 배분 | U-SYN-04, C-CLI-10, U-NET-01(합성) 재실행 | ~250 | P7 | R-20, DR-16 |
| P19 | (개정) 평가 경로의 `012` 리랭커 레지스트리 조립(정책 리랭커 기술자 → 리랭커 생성, 준비 오류 코드 7개) | U-RERR-02, I-PRIV-04 재실행 | ~200 | P14, `009` 정책 새 형식, `012` P4 | DR-26 |

- P4를 둘로 나눈 근거: 평가셋·스냅숏(작성 흐름)과 실험·관문(판정 흐름)은 의도가 다르고, P5·P6를 실험 테이블 없이 먼저 독립 검증할 수 있다(헌법 II-3). 둘 다 400줄 이하.
- P8을 분리한 근거: 운영 검색 코드를 건드리는 유일한 Phase라 위험 격리 대상이다(헌법 II-2). 운영 SQL 바이트 동일·기존 테스트 무수정 통과를 이 Phase의 합격 조건으로 둔다.
- P6·P7을 나눈 근거: 합성 클라이언트(P7)는 외부 모델 호출과 DR-16에 의존하므로, DR-16이 대안 1(합성 후속)로 결정되면 P7만 빼면 된다.
- P13은 001 구현 완료에 의존하므로 마지막에 두고, 그 전까지 factory 정답 근거는 `source_not_supported`로 거부된다.
- 개정 Phase(P4c, P14~P19)의 분할 근거: `012` 연동 항목을 의도별로 나눴다. 마이그레이션(P4c)은 P4a·P4b처럼 따로 두어 승인된 `0006`·`0007`을 바꾸지 않는다(DR-27 1). P14는 운영 검색 코드를 건드리므로 P8처럼 격리한다. 지표 순수 함수(P15)와 실행기 기록(P16)을 나눠 순수 계산을 DB 없이 먼저 검증한다. 확인 규칙(P17)은 관문 사전 점검과 소진 기록이 얽혀 위험이 가장 커 따로 둔다. 합성(P18)은 P7만 의존한다. 실제 리랭커 조립(P19)은 `009`·`012`를 기다리므로 마지막이다. 모두 400줄 이하 예상이며 P14·P17은 400줄 근처라 실행 중 초과가 보이면 멈추고 재분할을 보고한다. 개정 Phase의 실행 Agent 쉬운 검증과 검증 Agent 전체 검증 규칙은 아래 두 항목과 같다.
- ⑨(후보 목록 고정 파일·오프라인 실험)는 다음 개정에서 P19 뒤 번호로 더한다.
- 500줄 초과 예상 Phase는 없다. P1·P3·P5·P8·P9·P10은 400줄 근처라 실행 중 초과가 보이면 멈추고 재분할을 보고한다.
- 마이그레이션 Phase(P4a·P4b)의 TDD: RED는 컨테이너 없이 도는 정적 단위 테스트 U-MIG-01·02로 확인한다. 실제 PostgreSQL 적용·트리거·CHECK 확인(I-DB-01~03)은 검증 Agent 단계의 계획된 전체 검증이다.
- 각 Phase의 실행 Agent 쉬운 검증: `uv run ruff check src tests`와 해당 Phase의 `tests/unit`·`tests/contract` 테스트(외부 의존 없음). 통합 테스트(PostgreSQL 컨테이너 필요)와 인수 하네스는 검증 Agent가 다시 실행한다.
- 각 Phase의 검증 Agent 전체 검증: ruff, 전체 `tests/unit`·`tests/contract`, 해당 Phase까지의 `tests/integration`(compose.test.yaml), 기존 검색 회귀(C-SRCH-01·02), `git diff`가 그 Phase 범위 밖 파일을 바꾸지 않았는지.

## Project Structure

### Documentation (this feature)

```text
specs/002-evaluation-harness/
├── spec.md
├── checklists/requirements.md
├── plan.md                                   # 이 파일
├── research.md                               # Phase 0: 사실, 판단(R-xx), 결정 요청(DR-xx), 모호점(A-xx)
├── data-model.md                             # Phase 1: 새 테이블 17개
├── quickstart.md                             # Phase 1: 검증 안내
├── contracts/
│   ├── eval-cli.md                           # 평가 실행기 CLI 계약
│   ├── query-authoring-format.md             # 질의·템플릿 작성 형식 계약
│   └── private-evaluation-path.md            # 비공개 평가 경로 내부 포트 계약(개정: 후보 목록·리랭커 슬롯)
└── tasks.md                                  # 다음 단계(speckit-tasks), 이번에 만들지 않음
```

### Source Code (repository root)

새 파일과 바꿀 파일의 계획이다. 이 계획 작업에서는 만들지 않는다.

```text
src/omf_retrieval/
├── domain/
│   └── evaluation/                               # 신규 패키지
│       ├── queries.py                            # P1 질의 규칙·사유·정규화
│       ├── coordinates.py                        # P1 좌표·겹침·origin 변환
│       ├── metrics.py                            # P2 지표(정확 산술)
│       ├── bootstrap.py                          # P2 부트스트랩·난수
│       ├── gate.py                               # P3 관문 기준 상수·판정·시도·세대·교체 건
│       └── holdout.py                            # P17 확인 규칙 상수·대조 키·확인 입력 식별값(개정)
├── application/
│   ├── evaluation/                               # 기존 빈 패키지 사용
│   │   ├── ports.py                              # 저장소·원본 읽기·평가 경로·산출물·자원·GPU·합성 포트
│   │   ├── datasets.py                           # P5·P6 스냅숏·접수·평가셋 스냅숏·세대·개발셋
│   │   ├── checks.py                             # P9 필수 검사
│   │   ├── runner.py                             # P10·P11 사전 점검·실행·기록·재현·관문
│   │   └── recompute.py                          # P3 기록 기반 재계산(순수 조합)
│   └── search/
│       ├── ranking.py                            # P8 rank_evidence(신규), P14 fuse_candidates·group_evidence_items 분할(개정)
│       └── service.py                            # P8 rank_evidence 호출로 정리(수정, 동작 불변)
├── infrastructure/
│   ├── evaluation/                               # 신규 패키지
│   │   ├── search_path.py                        # P8 run 지정 저장소·정책 resolve·대조
│   │   ├── model_registry.py                     # P8 D-05 레지스트리
│   │   ├── source_reader.py                      # P5 OMF git 읽기, P13 factory 읽기
│   │   ├── artifacts.py                          # P9 청크·occurrence·블록 읽기
│   │   ├── resources.py                          # P10 호스트·GPU 확인, nvidia-smi 측정
│   │   └── synthesis_client.py                   # P7 사내 모델 합성
│   └── database/
│       ├── search.py                             # P8 SQL 조각 조합(수정, 운영 SQL 바이트 불변)
│       ├── models_evaluation.py                  # P4a·P4b ORM(신규, 기존 models.py 불변)
│       └── repository_evaluation.py              # P5·P10·P11 저장소
├── interfaces/cli/
│   ├── evaluation.py                             # P5~P11 eval 하위 명령·조립(신규)
│   └── main.py                                   # eval 하위 명령 등록(수정)
└── evaluation_settings.py                        # OMF_EVAL_ 설정(신규, 기존 settings.py 불변)

migrations/versions/
├── 0006_evaluation_datasets.py                   # P4a
├── 0007_evaluation_experiments.py                # P4b
└── 0008_evaluation_research_linkage.py           # P4c(개정)

tests/
├── unit/evaluation/                              # U-*(U-MIG-01·02는 test_evaluation_migrations.py)
├── unit/search/                                  # U-PRIV-01~03 추가(기존 파일 무수정)
├── contract/cli/test_eval_commands.py            # C-CLI-*
├── integration/database/                         # I-DB-*(기존 기대값 파일에 새 테이블 추가)
└── integration/evaluation/                       # I-DATA·I-PRIV·I-EXP·I-GATE, H-*(fixture 색인)
```

**Structure Decision**: 기존 단일 프로젝트의 계층형 구조(domain → application → infrastructure → interfaces)를 그대로 쓴다. 새 기능은 `evaluation` 하위 패키지로 모으고 기존 빈 `application/evaluation/` 패키지를 쓴다. 기존 `models.py`·`settings.py`를 고치지 않고 새 ORM·설정 파일을 둔다. 운영 코드 수정은 P8의 `application/search/service.py`·`infrastructure/database/search.py` 두 파일로 한정하고 동작 불변을 테스트로 고정한다. 의존성 추가는 없다(승인안 기준).

## Constitution Check 재평가(설계 후)

| 원칙 | 판정 | 설계 후 확인 |
|---|---|---|
| I | 통과 | DR 25건 승인(사용자 2026-11-10, 권장안), A-1·A-15 해소(spec 반영). 개정(2026-11-16): DR-26~DR-28 사용자 승인(DR-28은 변경 포함), A-16 처리 결정. Phase별 필요한 DR을 단계표에 적었다. DR-03 연구 자원 목록과 DR-04 운영 GPU 실측값은 값 입력 대기(fail-closed). 갱신(사용자 결정 2026-11-13): 운영 서버 값은 첫 접속 때 실측해 기록하고, 운영 개시 전에는 운영 서버를 예외 표시와 함께 허용 목록에 둔다. 새 의존성 없음(승인). |
| II | 통과 | Phase 21개(기존 14개 + 개정 P4c·P14~P19), 각 400줄 이하 예상, 분할 근거 기록. 운영 코드 수정 Phase(P8, 개정 P14) 격리. |
| III | 통과 | Phase별 쉬운 검증과 검증 Agent 전체 검증을 분리했다. |
| IV | 통과 | U 74건·C 10건·I 11건·H 11건 설계(정상·경계·실패와 예상 결과). 마이그레이션도 정적 단위 테스트로 RED. 개정(2026-11-16): U 16건(U-CAND-01·02, U-PRIV-05, U-CMET-01, U-RMET-01, U-RREC-01, U-RERR-01·02, U-HOLD-01~04, U-SYN-04, U-EXP-08·09, U-MIG-03)·C 3건(C-CLI-09~11)·I 3건(I-DB-04, I-HOLD-01, I-PRIV-04)을 더해 U 90·C 13·I 14·H 11. |
| V | 통과 | 정답 근거 좌표가 헌법 V 좌표와 같고, 실험 기록이 구성·스냅숏·시드·기준 해시로 재현 가능. 운영 근거 패키지 불변. |
| VI | 통과 | 사내 저장·외부 연결 차단 테스트·비밀 파일 토큰·운영 GPU·DB 차단(DR-02·DR-03 권장 A 승인, 연구와 운영 분리. 운영 개시 전 예외는 spec FR-033, 사용자 결정 2026-11-13). |
| VII | 통과 | LLM은 개발셋 합성에만, 사내 허용 목록 모델로만. 생성물 격리를 정적 테스트와 인수 검사로 확인. 평가 경로의 순위 결정은 운영과 같은 함수. |

## Complexity Tracking

> 헌법 위반이 없어 기록할 항목이 없다.

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| — | — | — |

## 스킬 단계 처리 기록

- `speckit-plan` Setup: `.specify/scripts/bash/setup-plan.sh`는 실행하지 않았다. 다른 Agent가 001·012를 병렬 작업 중이라 `.specify/feature.json`을 바꾸지 말라는 지시에 따라, 산출물을 `specs/002-evaluation-harness/`에 직접 만들었다. git 브랜치는 만들거나 바꾸지 않았다.
- `.specify/extensions.yml`이 없어 before/after plan hook은 없다.
- Agent context 갱신(`CLAUDE.md`·`AGENTS.md` 반영) 단계는 하지 않았다. 두 파일은 사용자가 직접 적용하는 대상이다.
- Phase 0의 "research agent 배포"는 하지 않았다. 조사는 spec, 체크리스트, 헌법, 설계서, `AGENTS.md`, 001 계획 산출물, 저장소 코드(읽기 전용) 확인으로 한정했다. 외부 자료 조사와 운영 서버 확인(GPU 실측 등)은 하지 않았고 DR-04의 값 입력 대기 항목(사용자가 실측값 제공)으로 남겼다. 2026-11-13 갱신: 사용자 결정으로 이 값은 운영 서버 첫 접속 때 실측해 기록·보고한다(research DR-04).
- spec의 계획 단계 이월 항목은 모두 결정 요청(DR)으로 올렸고 2026-11-10 사용자가 모두 권장안으로 승인했다. tasks 단계로 넘어갈 수 있다.
- 2026-11-10 사용자 결정 반영: DR-01~DR-25 승인, A-1·A-15 해소(spec SC-003·User Story 1·FR-003·FR-006 비고), 직전 독립 검증의 Low 발견 L-o~L-u 반영(새 실험 잠금의 `try` 획득과 UUID 재생성, 쓰기 트랜잭션 안 순서와 `abandon` 교착 처리, 완료 직전 생존 확인, 풀러 처리 방법, `pg_locks` 확인 조건, 표 구분자, 평가 PostgreSQL 전용 서버 설정). 테스트 ID는 새로 늘리지 않고 기존 U-EXP-07·U-LOCK-01·U-ENV-02·C-CLI-06 사례를 보강했다(U 74·C 10·I 11·H 11 그대로).
- 같은 날 재검증 발견 반영: M-D(시작 시 포트 비교로 풀러를 거부하던 검사와 `pooler_not_supported` 코드 제거. 테스트 DB 포트 매핑·터널을 막았기 때문. 설정 규칙 + 실행 중 `pg_backend_pid()` 불변 확인만 남김), M-E(spec FR-006 비고에 `정답 근거 목록` 포함), L-v(`pg_locks` 바인드를 클라이언트 부호 없는 변환으로 고정), L-w(CLI `detail` 허용 항목), L-x(작성 형식 계약서의 내부 식별자 정리), L-y(진행 중 유일 인덱스 위반 → `attempt_in_progress`), L-z(사전 점검 번호는 data-model 4.2가 정본, 실험 흐름 정렬), L-aa(spec 용어표), 이어서 L-bb(작성 형식 계약서 용어·줄 끝 설명), L-cc(체크리스트 FR-006 비고), L-dd(`CAST(:x AS oid)` 바인드 형식), L-ee(`23505`는 진행 중 유일 인덱스 이름일 때만 `attempt_in_progress`), L-ff(인덱스 이름을 U-MIG-02·I-DB-03·data-model 5절에 고정). 테스트 ID는 늘리지 않고 U-ENV-02·U-LOCK-01·I-EXP-02 사례를 고쳤다(U 74·C 10·I 11·H 11 그대로).
- 2026-11-13 사용자 결정 반영: spec FR-033 "운영 개시 전 예외"에 맞춰 Target Platform, Constraints, 헌법 VI 점검, 값 입력 대기, U-ENV-01(예외 표시 허용·표시 없는 겹침 거부·표시 제거 뒤 거부·두 목록 각각 비면 거부), H-11 문구를 갱신했다. DR-03은 허용 항목의 예외 표시 하나만 더하고 운영 목록은 유지하며, DR-03·DR-04의 운영 서버 값은 첫 접속 때 실측해 기록한다.
- 2026-11-16 `012` 연동 개정(작업 단위 3a): `012` 변경 요청서 ①~⑧을 반영했다. 사용자 승인이 필요한 선택은 DR-26(후보 목록 반환·순위 결정 함수 분할·리랭커 슬롯), DR-27(저장 형식·지표 키·nDCG 정밀도·`0008` 마이그레이션), DR-28(확인 개발셋 규칙·소진 시점 H-1·코드 이름)로 올렸고 승인 대기다. 기술 판단 R-18~R-21을 더했다. spec 충돌 후보 A-16을 기록만 했다. 새 Phase P4c·P14~P19, 테스트 U 16·C 3·I 3건. spec.md는 바꾸지 않았다. ⑨는 다음 개정 단위다. 같은 날 작업 단위 1의 남은 지적 두 건(eval-cli `resource show` 예외 출력 예, quickstart `test_acceptance_resources` 예외 사례)을 고쳤다.
- 2026-11-16 작업 단위 3a 보완(사용자 결정 2026-11-16, 독립 검증 Low 발견 F-1~F-10): DR-26·DR-27을 권장안으로, DR-28을 변경 두 가지((a) 확인 대상에 리랭커 교체 건 `swap-reranker-*` 포함, F-1 해소. (c) 확인 횟수를 (트랙 또는 교체 건, 시험셋, 세대)별로 세고 새 세대에서 0부터, 이전 세대 확인은 새 세대 관문 시도에 쓰지 않음. 이 부분은 뒤의 F-11 사용자 결정으로 넘겨 쓰기 허용으로 바뀜)와 함께 승인 상태로 바꿨다. (b) 소진 시점은 사전 점검 1·2 뒤 2a, (d) 역할 사이 겹침 자동 제외. A-16은 처리 결정(FR-026 그대로). 확인 실험 요청에 `--test-set`을 더했다(세대를 정하려면 시험셋이 필요해서). 기록한 발견 수정: F-3(③ 결정성 점수 차이는 1·2회차 기록으로, 후보 목록 순위 일치는 ⑨), F-4(`dev_role` 기존 행 처리와 U-MIG-03·I-DB-04 사례), F-5(Phase 21개), F-6(잘못 붙인 `holdout`은 exit 3), F-7(`single_config`는 실험 수준 키), F-8(U-RERR-01 실행기 사례를 P16으로), F-9(MRR@3·nDCG@3 정의를 DR-27 3a에), F-10: R-20은 기존 `eval dev synthesize`에 CLI 인자 `--mode`, `--per-intent`, `--seed`를 더한다(새 명령은 없음). 테스트 ID는 늘리지 않고 U-RREC-01, U-RERR-01, U-HOLD-01~03, U-MIG-03, C-CLI-09, I-DB-04 사례를 고쳤다(U 90·C 13·I 14·H 11 그대로). `012` 문서도 이 결정에 맞춰 고쳤다.
- 2026-11-16 작업 단위 3a 재검증 수정(F-11~F-14): F-12 결정 (d)의 제외 수를 저장할 3.9 `excluded_counts`(`0008`, NULL 허용 jsonb)를 더하고 U-MIG-03·I-DB-04에 반영. F-13 `eval experiment run` 출력의 `single_config`를 실험 수준 키로. F-14 등록됐지만 확인 규칙에 없는 트랙·교체 건은 `holdout_not_allowed`(`track_not_in_holdout_rules`), `unknown_track`은 등록되지 않은 id에만. F-11 "이전 세대의 미소진 확인은 새 세대 관문 시도에 쓸 수 없다"는 사용자 결정 (c)에 들어 있지 않으므로 사용자 답을 받을 때까지 보류 표시를 붙였다(아래 F-11 결정 기록으로 해소). `--test-set`은 (c) 범위로 유지.
- 2026-11-16 F-11 사용자 결정(선택지 (1) 넘겨 쓰기 허용) 반영: 이전 시험셋 세대에 만들어졌고 아직 소진되지 않은 확인 기록은 대조 키(확인 입력 식별값, ⑨ 뒤 파일 B SHA-256 포함)가 맞으면 새 세대 관문 시도에 쓸 수 있다. 세대는 확인 횟수를 세는 범위로만 쓰고, 넘겨 쓴 기록은 새 세대 횟수에 다시 더하지 않는다. 보류 표시를 모두 [결정 2026-11-16]으로 바꾸고 research DR-28 5, data-model 3.17·3.18·4.2(2a)·4.5, U-HOLD-03(정상·경계·실패), I-DB-04, C-CLI-09, contracts/eval-cli.md, quickstart를 고쳤다. 확인 기록이 관문 요청과 같은 시험셋이어야 한다는 기존 조건은 그대로 두었다.
