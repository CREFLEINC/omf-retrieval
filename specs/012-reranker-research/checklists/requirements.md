# Specification Quality Checklist: 리랭커 연구 — 하이브리드 후보 재정렬의 근거 순위 개선 검증(reranker-research)

**Purpose**: Validate specification completeness and quality before proceeding to planning
**Created**: 2026-11-10
**Revised**: 2026-11-10 (독립 검증 발견 M1·M2·L1~L7 반영, 재검증 N1(RRF 동점 서술 정정)·L-a(`002` Dependencies의 `009` 선행 조건)·L-b(FR-030 지연 기준 제거) 반영, 승인권자 확인 대상 세 항목 사용자 승인(2026-11-10) 반영, 검증 Low(FR-011 통과 모듈 제외, 시나리오 범위 표기) 반영 후 재평가)
**Feature**: [spec.md](../spec.md)

## Content Quality

- [x] No implementation details (languages, frameworks, APIs)
- [x] Focused on user value and business needs
- [ ] Written for non-technical stakeholders
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

- [ ] All functional requirements have clear acceptance criteria
- [x] User scenarios cover primary flows
- [x] Feature meets measurable outcomes defined in Success Criteria
- [x] No implementation details leak into specification

## Notes

- Items marked incomplete require spec updates before `/speckit-clarify` or `/speckit-plan`
- 미충족 처리 방침: `002` 체크리스트와 같다. 미충족 항목은 명세 결함이 아니라 계획 단계나 `009`에서 승인권자 승인으로 정할 값·정의에 의존하는 항목이다. 명세에 값을 지어 넣지 않고 아래 "계획 단계 이월"로 넘기며, 정해질 때까지 통과로 표시하지 않는다.
- 미충족 "Written for non-technical stakeholders": 이 명세의 독자는 연구자·검토자·승인권자이며 검색 순위·식별값을 다루므로 비전문가가 그대로 읽기에는 도메인 용어가 많다. 약어와 다른 문서에서 가져온 키워드를 모두 최상단 용어표에 두어 완화했지만(아래), 비전문가 독자 기준을 충족한다고 표시하지 않는다.
- 미충족 "Requirements are testable and unambiguous": 시간 초과 한도 값(FR-020), 리랭크 점수 허용 범위 값(FR-028), 후보 모델 목록(FR-006), 후보 깊이 비교 값의 범위(FR-018), 통과 모듈일 때의 리랭크 적용 상태 값(Key Entities), FR-016 뒤 세 항목의 배치(FR-016a), 근거 항목 대표 점수·순위와 일치 조각 순서의 정의(FR-013a, `009`)가 정해져야 판정 기준이 완성된다. `002`에서 이월된 운영 GPU 메모리 용량도 FR-027 판정에 필요하다.
- 미충족 "All functional requirements have clear acceptance criteria": FR-013a의 근거 항목 대표 점수·순위는 `009` 정의 전까지 수용 기준이 없다(순서 규칙과 응답 근거 수 자르기는 User Story 3 시나리오 10~13으로 확인). FR-022 감사 필드와 FR-030 적용 전 확인은 `009` 구현과 O-13 절차가 있어야 실제로 확인할 수 있다. FR-009a는 수용 시나리오 없이 계획 단계 점검으로만 확인한다.
- 마커는 0개다. 마커 후보였던 항목은 합리적 기본값이 있어 Assumptions에 기본값으로 적었다: 원격 사내 모델 서버 채점 제외(서비스 경계 유지), GPU 메모리를 질의 경로 구성 전체로 측정, 리랭크 점수 하한선 미도입(O-7), 같은 자원 내 순위 완전 동일 요구(O-8), 미세 조정 제외, 후보 깊이 비교 값 3개 이상, 현재 근거 묶기 규칙 유지(통과 모듈이면 동점 처리까지 동일, 리랭크된 경우의 정확한 식은 조건을 붙여 `009`로). 설계서를 넘는 세 항목(FR-016 식별값 추가, FR-009a, FR-030 필수화)은 마커 대신 Assumptions의 "승인권자 확인 대상"으로 명시했고, 2026-11-10 사용자 승인으로 확정되어 Assumptions에 "확정(사용자 승인 2026-11-10)"으로 옮겼다.
- M1 반영: FR-016에 설계서 7.1과 겹치는 항목과 추가 항목(입력 구성·자르기 규칙·시간 초과 한도)을 나누고, 추가가 사용자 승인 사항임을 적었다. 배치(기술자 설정 해시 안 또는 별도 정책 필드)는 정하지 않고 FR-016a로 `012` 계획·`009` 승인에 넘겼다. FR-016b에 관문 통과 뒤 시간 초과 한도를 운영 하드웨어에 맞춰 바꾸면 새 후보이고 관문 시도 1회를 쓴다는 결과를 적고, FR-020에 첫 관문 시도 전 고정을 더했다. Dependencies의 `009` 항목을 갱신했다.
- M2 반영: 리랭커 순서를 근거 묶기 전 후보 목록 기준으로 다시 썼다(FR-010·FR-011·FR-013, User Story 3 시나리오 1·2, SC-002). FR-013a에 근거 묶기와 응답 근거 수 자르기가 리랭커 뒤에 오고, 근거 항목 순서 규칙의 조건 ①~④를 적었다(재검증 N1 반영으로 위치 기준을 철회하고 현재 규칙 유지로 바꿈). 근거 항목 대표 점수·순위와 일치 조각 순서는 결정성·순서 규칙 조건을 붙여 `009`에 넘겼다. FR-017은 일치 조각 단위의 리랭크 점수·순위와 "리랭크되지 않음" 표시로 고쳤다. 시나리오 7을 고치고 10·11·13(당시 번호 12)을 더했다(시나리오 12는 뒤의 재검증 N1 반영에서 끼워 넣었다). 용어표에 후보 목록, 근거 묶기, 근거 항목, 일치 조각, 응답 근거 수를 더했다.
- L1 반영: Dependencies에 `009`의 검색 정책 새 형식·등록 경로·마이그레이션이 FR-025 등록의 선행 조건이라 기록이 남는 개발셋 실험도 그 전에는 시작할 수 없다는 점, 평가 경로에서도 리랭커 실행 위치가 필요하다는 점(D-04)을 적었다.
- L2 반영: FR-009a에 평가 실행기가 후보 정책마다 명시적 목록에서 리랭커 구현을 고르고(D-08) 여러 모델은 평가 실행기에서만 올린다는 D-05 원칙의 적용을 적었다. 설계서 D-05가 임베딩에 대해 정한 것이므로 승인권자 확인 대상으로 표시했다.
- L3 반영: FR-030에 합격 기준 두 가지(기존 smoke 합격 기준, 리랭커가 불리는 정상 질의 5개의 리랭크 적용 상태가 모두 "적용")를 두고, 설계서 15절이 권고로 둔 확인을 필수로 하는 것을 승인권자 확인 대상으로 표시했다. User Story 5 시나리오 5가 이 기준을 가리킨다. 용어표에 허용 근거를 더했다.
- L4 반영: FR-010 입력 정의에 `strict` 모드의 교집합 필터가 RRF 뒤·리랭커 앞에 있음과 O-10을 적고, 실험 기록에 사용 여부를 남기며 두 구성에 같은 값을 쓰게 했다. 용어표에 `strict` 모드와 O-10을 더했다.
- L5 반영: Out of Scope의 미세 조정 항목을 "승인 목록의 모델을 사내에 보관한 고정 revision 그대로"(FR-005·FR-006)로 맞췄다.
- L6 반영: 용어표에 AI, Agent, 호출 Agent, 근거 패키지를 더하고 본문에 쓰지 않는 RAG를 뺐다. User Story 3의 "(검색을 요청하는 AI)" 괄호는 용어표 정의로 대신했다.
- L7 반영: 위 두 항목을 미충족으로 바꾸고 사유를 적었다.
- 재검증 N1 반영: FR-013a의 현재 동작 서술을 바로잡았다. 현재 근거 묶기는 (가장 높은 RRF 점수 내림차순, 상위 절 식별값)으로 근거 항목 순서를 정하고, 후보 목록은 (RRF 점수 내림차순, 청크 식별값)이라 RRF 동점에서 "가장 앞선 위치" 순서와 다를 수 있다. 현재 규칙을 바꾸지 않는 안을 택해(새 승인 항목 없음) 통과 모듈이면 동점 처리까지 지금과 같아야 한다는 조건 ②와, 리랭크된 경우 이 규칙으로 돌아가는 결정적 규칙 조건 ①·③·④를 두고 정확한 식은 `009`에 넘겼다. Assumptions의 근거 묶기 기본값, Edge Cases 첫 항목, User Story 3 시나리오 10·11(조건 참조로 수정)·12(통과 모듈 동점 사례를 새로 끼워 넣고 기존 12를 13으로 밀어냄), SC-003(통과 모듈 순서 동일, 동점 표본 포함)을 맞췄다.
- 재검증 L-a 반영: Dependencies `002` 항목에 기록이 남는 개발셋 실험의 `009` 선행 조건(검색 정책 새 형식·등록 경로, 리랭커 실행 위치)을 더해 `009` 항목과 맞췄다.
- 재검증 L-b 반영: FR-030의 지연 기준 ③을 뺐다. 시간 초과는 ②의 "리랭크 미적용(시간 초과)"으로 드러난다. ②는 리랭커가 불리는 정상 질의 5개로 한정하고, 리랭커를 부르지 않는 문서에 없는 질의는 ①의 `no_evidence`만 본다. 리랭크 소요 시간은 기록만 한다.
- 사용자 승인 반영(2026-11-10): 승인권자 확인 대상 세 항목(① FR-016 뒤 세 항목의 검색 정책 식별값 포함, ② FR-009a의 D-05 원칙 리랭커 적용, ③ FR-030 적용 전 확인 필수화와 합격 기준)이 적힌 그대로 승인되었다. Assumptions 항목을 "확정(사용자 승인 2026-11-10)"으로 바꾸고 FR-009a·FR-016·FR-030과 설계서 v0.3 Dependencies 항목의 "확인 대상" 서술을 확정으로 고쳤다. FR-016a의 배치는 계속 `012` 계획·`009` 승인 사항이다. 위 L2·L3 항목의 "확인 대상" 표시는 당시 이력이다.
- 검증 Low 반영: FR-011에 통과 모듈은 리랭크 점수를 내지 않으므로 점수·순위 요구에서 빠지고 후보 집합 보존 요구는 그대로 적용됨을 적었다(FR-013a와 일치). 위 미충족 사유의 시나리오 범위를 10~13으로 고쳤다.
- 구현 세부 점검: `config_hash`, 검색 정책 등록 기록(설계서의 manifest), 모듈 기술자, 청크 식별값, RRF, 하한선, `strict` 모드, `limit`은 승인된 설계서 v0.3과 `AGENTS.md`의 기존 결정·요청 항목이므로 명세에 적었다. 라이브러리, 모델 이름, 저장 기술, 코드 위치, HTTP 상태 코드는 쓰지 않았다.
- 성공 기준 점검: SC-001은 "관문 판정을 얻음"을 측정하며 "적용 권고"를 요구하지 않는다(FR-002). SC-011은 정성 기준과 합격·불합격 예시를 함께 두었다.
- FR 대응 점검: FR-001~004는 User Story 2와 SC-001·SC-009·SC-011, FR-005~009는 User Story 1 시나리오 1~3·7과 SC-008, FR-009a는 계획 단계 점검, FR-010~017(FR-013a·016a·016b 포함)은 User Story 3과 SC-002·SC-003·SC-007, FR-018은 User Story 1 시나리오 4와 SC-010, FR-019~022는 User Story 4와 SC-005, FR-023~029는 User Story 1 시나리오 5·6, User Story 2, User Story 5 시나리오 1~3과 SC-001·SC-004·SC-006·SC-007, FR-030~031은 User Story 2 시나리오 7, User Story 5 시나리오 5와 SC-009로 확인한다.
- 계획 단계 이월: 위 미충족 항목의 값·정의, FR-016a 배치, 연구 자원의 구체 선택, 후보 모델 라이선스 확인, 연구 결과 요약의 `crefle-doc` 작성 정보(작성자·작성일시·버전·열람 대상).
- 이 체크리스트는 실행 Agent의 자체 점검이며 최종 합격 판정이 아니다.
