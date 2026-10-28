# 문서 안내

문서는 주제에 따라 최상위와 `integration/` 아래로 나눕니다. 일반 문서 파일명은 소문자 kebab-case로 정리했으며, 표준 `README.md`와 저장소 지침에서 지정한 `TEST_REPORT.md` 이름은 그대로 유지합니다.

## 폴더 구조

```text
docs/
├── README.md
├── system-architecture.md
├── TEST_REPORT.md
└── integration/
    ├── plan.md
    ├── searchagent-improvement-plan.md
    ├── status.md
    ├── bridge-worker-contract-proposal.md
    ├── github-remote-execution-review.md
    ├── cross-computer-github-relay-plan.md
    └── cross-computer-relay-runbook.md
```

## 문서

- [시스템 아키텍처](system-architecture.md): Bridge의 책임 경계와 실행 흐름
- [테스트 보고서](TEST_REPORT.md): 오프라인 테스트와 검증 기록
- [통합 계획](integration/plan.md): Bridge와 SearchAgent의 역할, 정책 경계, 단계별 연동 계획
- [SearchAgent 실행·검색 개선 제안](integration/searchagent-improvement-plan.md): 요청 안정성, 후보 순위, 평가·승격 제안
- [통합 현황](integration/status.md): 양 프로젝트의 현재 상태와 확인된 계약 차이
- [Bridge worker 계약 제안](integration/bridge-worker-contract-proposal.md): 기존 Bridge 측 계약 제안. SearchAgent 계약과 합의되거나 실행 연동된 문서가 아님
- [GitHub 원격 실행 검토](integration/github-remote-execution-review.md): 현재 실행 경계와 조건부 relay 정책
- [별도 PC 간 GitHub relay 계획](integration/cross-computer-github-relay-plan.md): v1 제품 범위(요청 제출·안전 상태 확인), 두 PC 배치, 암호화 요청 흐름
- [별도 PC relay 실행 순서](integration/cross-computer-relay-runbook.md): 사용자별 사전 확인, 설치, 단계별 완료 기준과 복구 절차

BloombergSearchAgent 문서는 SearchAgent 저장소에 남겨 둡니다. 이 저장소는 해당 프로젝트의 코드·환경·문서를 소유하거나 수정하지 않습니다.
