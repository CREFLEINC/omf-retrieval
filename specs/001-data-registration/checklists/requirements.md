# Specification Quality Checklist: 문서 자료 등록·원본 보관·불변 revision·권한·스키마 사전(data-registration)

**Purpose**: Validate specification completeness and quality before proceeding to planning
**Created**: 2026-11-03
**Revised**: 2026-11-09 (2026-11-06: `AGENTS.md` 방향 개정 반영 후 전 항목 재평가; 2026-11-09: 평가 하네스 명세 `002` 삽입에 따라 명세 번호 참조를 한 칸씩 이동)
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
- 마커는 0개다. FR-020, FR-026, FR-029와 A~E 결정은 사용자 결정으로 확정했다. FR-026의 완전 삭제 때 등록 기록 처리도 2026-11-04 사용자 승인으로 확정했다.
- 미충족 "Requirements are testable and unambiguous": 다음 FR은 계획 단계 승인값이 정해져야 판정 기준이 완성된다. FR-006(크기 한도 값), FR-037(자료 ID 길이·문자 규칙), FR-010(분할 규칙의 정형 판정 기준), FR-020(관계 정보 단위). 값이 없는 상태로 통과 표시하지 않았다.
- 정본 충돌 해소: `AGENTS.md`의 이미지 항목은 이제 "전체 스캔 PDF는 이미지 규칙을 적용하고, Excel·PDF 안의 그림과 일부 페이지에만 글자 층이 없는 PDF는 거부하지 않고 관계 정보만 만든다"로 FR-020과 같다.
- FR 대응 점검: FR-001~044(개정 전 범위)를 인수 시나리오 또는 성공 기준과 대조했다. FR-008은 사유별 시나리오와 SC-002로 확인한다. FR-032는 등록 호출로 관찰할 수 없어 계획 단계의 저장 위치 점검으로 인수한다.
- `REST API`, `Bearer 토큰`, source 이름 `factory`는 구현 선택이 아니라 확정된 결정이므로 명세에 적었다. 서버 관리 경로의 구체 형태와 그 밖의 언어, 프레임워크, 저장 기술은 쓰지 않았다.
- 계획 단계 이월: 위 미충족 항목의 값, 정확한 허용 확장자와 매크로 포함 Excel 허용 여부, 스키마 파일 형식, 기본 스키마 내용과 갱신 방식, 관리자 경로와 서버 관리 경로의 형태, 묶음 세부 제한, FR-032 저장 위치 점검, 성공 기준 표본 규모. 삭제의 후속 색인 전파는 `006`에 의존한다(2026-11-06 개정 전 번호 `003`).
- SC-009는 객관 지표로 정하기 어려워 정성 기준과 합격·불합격 예시를 두었다.
- 2026-11-06 개정 재평가: 등록 대상을 RDB에 보관되지 않는 문서 자료로 한정하고 MES DB 데이터 제외를 명시했다(Input, 용어, Assumptions). MES REST 수집을 삭제하고 후속 명세 번호를 `AGENTS.md`의 새 순서 `002`~`010`으로 맞췄다(2026-11-09에 평가 하네스 삽입으로 `003`~`011`로 다시 이동). 외부 LLM 사용 표시(FR-045)와 원본 읽기 권한 제한(FR-046, FR-047, FR-028·FR-031·FR-041 보완)을 추가하고 User Story 2 시나리오 18~22, SC-012, SC-013으로 인수 기준을 두었다. 문서 분석 Agent의 분할 규칙 생성은 `003`(2026-11-09 기준 번호) 범위로 Out of Scope에만 적었다. 각 항목을 다시 점검했고 표시 상태는 바뀌지 않았다.
- FR 대응 점검(개정): FR-045는 User Story 2 시나리오 19·20(표시 없음 간주, 관리자 표시 변경)과 시나리오 22·SC-012(호출자 자기 표시 변경 거부), FR-046은 시나리오 18·19와 SC-012, FR-047은 시나리오 21과 SC-013으로 확인한다. FR-023·FR-039는 FR-031과 같이 "효력을 가진 원본 읽기 권한"으로 맞췄다. `005`와의 관계는 범위 주장이 아니라 의존 메모로만 적었다.
- 마커는 개정 뒤에도 0개다. 표시 변경 직후 진행 중이던 되찾기의 처리, 표시 복귀 시 기존 권한의 효력, 효력을 잃은 권한 기록 처리, 표시 변경 기록과 관리자 경로 형태는 승인 범위를 넘으므로 정하지 않고 Assumptions의 미결 항목으로 넘겼다.
- 계획 단계 이월(이전 검토 미해결, spec Assumptions에 기록): M1 완전 삭제 때 등록 기록의 내용 해시 제거가 암시만 됨, M2 삭제 후 남는 등록 기록 항목을 볼 관리자 경로 필요, M3 시나리오 6 문구.
- 이 체크리스트는 실행 Agent의 자체 점검이며 최종 합격 판정이 아니다.
