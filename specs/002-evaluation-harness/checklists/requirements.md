# Specification Quality Checklist: 평가 하네스 — 평가셋·지표·실험 기록·적용 관문(evaluation-harness)

**Purpose**: Validate specification completeness and quality before proceeding to planning
**Created**: 2026-11-09
**Revised**: 2026-11-09 (사용자 결정 반영 후 재평가; 독립 검증 발견 M1·M2·L1~L4 반영 후 재평가)
**Revised**: 2026-11-10 (계획 단계 사용자 결정 A-1·A-15의 spec 반영 기록)
**Feature**: [spec.md](../spec.md)

## Content Quality

- [x] No implementation details (languages, frameworks, APIs)
- [x] Focused on user value and business needs
- [x] Written for non-technical stakeholders
- [x] All mandatory sections completed

## Requirement Completeness

- [x] No [NEEDS CLARIFICATION] markers remain
- [ ] Requirements are testable and unambiguous
- [x] Success criteria are measurable
- [x] Success criteria are technology-agnostic (no implementation details)
- [x] All acceptance scenarios are defined
- [x] Edge cases are identified
- [x] Scope is clearly bounded
- [x] Dependencies and assumptions identified

## Feature Readiness

- [x] All functional requirements have clear acceptance criteria
- [x] User scenarios cover primary flows
- [x] Feature meets measurable outcomes defined in Success Criteria
- [x] No implementation details leak into specification

## Notes

- Items marked incomplete require spec updates before `/speckit-clarify` or `/speckit-plan`
- 미충족 항목 처리 방침: 이 체크리스트의 미충족 항목은 명세 결함이 아니라 계획 단계에서 정할 값에 의존하는 항목이다. 명세에 값을 지어 넣지 않고 계획 단계로 넘겨 거기서 사용자 승인으로 정한다. 아래 "계획 단계 이월"이 그 목록이며, 값이 정해질 때까지 해당 항목을 통과로 표시하지 않는다.
- 미충족 "Requirements are testable and unambiguous": 운영 GPU 메모리 용량(FR-018 ⑤), 부트스트랩 신뢰구간 세부 방식(FR-018 ③), 반환 근거 길이 단위(FR-014a), 시험셋 보충 조건과 보충 뒤 시도 횟수 재계산 기준(FR-026), 모듈 교체 건을 묶는 기준(FR-025)이 정해져야 판정 기준이 완성된다.
- 마커는 0개다. 시험셋 규모(FR-002)와 적중 판정(FR-011)은 사용자 결정(2026-11-09)으로 해소했다.
- M1 반영: 헌법을 v2.1.0으로 개정해 원칙 VII에 세 번째 LLM 용도 "(3) 평가·연구용 자료 생성"을 추가했고(사용자 승인), FR-008과 용어표가 이 조항을 근거로 참조한다. 합성 생성물이 색인·조회 응답·근거에 들어가지 않는다는 요구를 FR-008a로 두고 SC-010으로 확인한다. "사내 모델"을 특정 서버가 아니라 사내에서 운영하는 모델로 정의했다.
- M2 반영: 용어표에 source와 자료 ID를 `001` 정의에 따라 두고 출처를 밝혔다. 위치 좌표에 Markdown을 포함한 텍스트 파일의 행 범위를 명시했다(FR-004). `006` 전 OMF Markdown의 자료 식별은 `001`·`006`에서 정해지지 않았으므로 Assumptions에 임시 규칙(source `omf`, 자료 ID = 저장소 기준 파일 경로, revision = 고정 commit SHA, 행 범위)을 두고 User Story 1 시나리오 10으로 확인한다. 질의 ID는 시스템이 매기고(FR-003), 확인 기준 원본 자료 스냅숏은 시스템이 보여 주는 목록에서 고른다(FR-003b, 시나리오 11). 문서에 없는 질의의 의도는 필수이며 이유와 "분포 규칙 없음"을 FR-003a에 적었다.
- L1 반영: FR-003 아래에 작성자가 채우는 항목을 모두 갖춘 질의 예시 A(의도 무엇, 정답 2개)와 예시 B(문서에 없는 질의)를 표로 두고, User Story 1 시나리오 1·3과 SC-003 합격 예시가 이를 가리킨다.
- L2 반영: 미충족 처리 방침을 위와 같이 하나로 통일했다. FR-033은 User Story 2 시나리오 8과 SC-011로 수용 기준을 두었다(실험 기록의 실행 자원 확인과 계획 단계 자원 배치 점검). FR-009·FR-010의 스냅숏 불변은 User Story 5 시나리오 5·6으로 수용 기준을 두었다.
- L3 반영: smoke, 헌법, 헌법 원칙 V, 헌법 원칙 VII (3), 질의 템플릿, 토큰을 용어표에 정의했다.
- L4 반영: SC-005가 정답이 있는 질의 400개, 의도별 100개, 문서에 없는 질의 50개 세 조건을 모두 측정하며, 조건 하나씩만 어긴 표본 3개를 포함한다.
- 사용자 결정 반영(이전 회차): 관문 시도 한도는 같은 시험셋에서 연구 트랙(또는 모듈 교체 건)별 3회(FR-025, FR-026), 반환 근거 평균 길이 기록(FR-014a)과 길이 증가 한도의 `013` 이관, 연구 명세 번호 `012` 리랭커·`013` 파싱·영역 판별·청킹·`014` 임베딩. 원문자 트랙 번호는 `AGENTS.md` 순서이며 명세 번호와 다름을 용어표에 적었다.
- 유지한 해석: 평가 대상을 등록 자료 원문 근거로 한정, 영역 판별 교체는 전용 기준 전까지 공통 관문 적용, 판정 3종, 경계값 처리, %p 환산(0~1 지표 × 100), SC-003 검토 표본 20건. 경계값 처리와 표본 20건은 설정 가능한 기본값이다.
- 마커로 올리지 않은 열린 항목: 불일치율 10% 가정 검증, 트랙 ① 영역 판별 전용 관문 기준(Assumptions).
- FR 대응 점검: FR-001~010(FR-003a·003b·008a 포함)은 User Story 1·5와 SC-003·SC-006·SC-010, FR-011~016(FR-014a 포함)은 User Story 2, FR-017~023은 User Story 3과 SC-007·SC-009, FR-024~027은 User Story 4와 SC-004~006, FR-028~031은 User Story 5와 SC-001·SC-002, FR-032~034는 User Story 2 시나리오 2·8과 SC-008·SC-010·SC-011로 확인한다.
- SC-003은 객관 지표로 정하기 어려운 부분이 있어 정성 기준과 합격·불합격 예시를 함께 두었다.
- 계획 단계 사용자 결정 반영(2026-11-10): A-1에 따라 SC-003·User Story 1의 "다른 문서"에서 질의 작성 형식 계약서 `contracts/query-authoring-format.md` 한 파일만 제외했다. A-15에 따라 FR-006(빠진 의도·정답 구분·정답 근거 목록·작성자·작성일은 시나리오 4·5·8 사유. 정답 근거 목록이 빠진 정답 있음 질의는 정답 표시 사유, 항목 누락은 질의문·확인 기준 원본 자료 스냅숏·출처 구분과 알 수 없는 항목에만)과 FR-003(정답 없음 질의의 정답 근거 목록 생략은 빈 목록)에 비고를 더했다. 위 체크 항목의 판정은 바꾸지 않았다.
- `no_evidence`, 부트스트랩, Recall@5, MRR은 승인된 결정이나 기존 상태 값이므로 명세에 적었다. 라이브러리, 저장 기술, 파일 형식, 화면은 쓰지 않았다.
- 재검증 반영: N1 헌법 원칙 VII 근거 문단을 세 LLM 용도와 생성물 분리 이유로 고쳤다. N2 User Story 1 시나리오 2의 정답 근거에 source·자료 ID를 넣었다. N3 FR-006에 "확인 기준 스냅숏" 거부 사유를 추가하고 시나리오 11이 이 사유를 쓴다. N4·N5는 계획 단계로 넘긴다.
- 계획 단계 이월: 위 미충족 항목의 값, N4 좌표 세부(행 번호 기준: 1부터 세는지와 끝 행 포함 여부, OMF 고정 commit 값을 작성자에게 제공하는 위치, 이미지 묶음의 정보 자료가 PDF일 때의 좌표), N5, 원본 자료 스냅숏 목록을 보여 주는 방식과 기본 선택값, 새 시험셋·보충 질의의 이전 질의 재사용 범위, 버그 수정의 정의와 관문 적용 여부, 연구 실행 자원.
- 이 체크리스트는 실행 Agent의 자체 점검이며 최종 합격 판정이 아니다.
