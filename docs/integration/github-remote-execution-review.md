# GitHub 원격 실행 검토

**검토일:** 2026-10-28  
**결과:** cross-computer GitHub relay는 조건부 target architecture로 문서화. 현재 worker Issue 기동은 미구현·비활성  
**범위:** 코드·설정의 정적 검토와 안전 경계. GitHub API 호출, feature flag 활성화, worker 실행은 하지 않았다.

## 결론

초기 검토에서는 원격 worker 실행 요구가 확인되지 않아 Issue 기동을 승인하지 않았다. 사용자가 별도 PC 간 직접 연결 없이 GitHub를 relay로 쓰려는 요구를 추가로 밝혀, cross-computer 설계를 재개했다. 구현과 운영 활성화는 아직 이뤄지지 않았다.

현재 [`AGENTS.md`](../../AGENTS.md)는 향후 암호화·인증된 고정 relay에 한해 조건부 예외를 기술한다. 현재 runtime에는 Issue worker dispatch가 없으며, 구현 전에는 계획의 계정·host·key·보존 결정을 확정해야 한다. 상세 순서는 [별도 문서](cross-computer-github-relay-plan.md)에 정리했다.

## 현재 코드의 경계

- `bridge/command_processor.py`의 Issue 명령 allowlist는 `PING`, `HEALTH_CHECK`, `GET_JOB_STATUS`다.
- `GET_JOB_STATUS`는 기존 실행 기록만 읽는다. Issue 처리 경로는 `JobRunner`를 호출해 작업을 시작하지 않는다.
- SearchAgent 작업 세 개는 고정 registry에 있지만, 시작 진입점은 `main.py --run-searchagent-job`이다.
- `REMOTE_COMMANDS_ENABLED` 설정 기본값은 `false`다. 명시적으로 켜면 GitHub polling이 시작되지만, 허용 명령 집합에 SearchAgent 실행은 포함되지 않는다.
- GitHub 상태 응답은 실행 식별자·상태·시각과 allowlist된 오류·outcome 코드로 제한된다.

## 남은 위험과 결정

원격 polling 자체는 GitHub credential과 Issue 상태에 Bridge를 연결한다. 현재 기본값을 바꾸면 안 된다. SearchAgent worker를 Issue에서 실행하려면 별도 작업으로 정책을 다시 정하고, 새로운 원격 요청 계약과 권한, 입력 제한, 중복 실행·재시도, timeout·취소, 로그와 상태 응답의 데이터 최소화, 운영 감사를 검토해야 한다.

현재 상태는 **v1 제품 범위와 설계 문서화 완료, 코드 구현 및 운영 활성화 대기**다. relay repo `jaeu983-lang/bloomberg-bridge-relay`는 public이고 Issues가 활성화돼 있으며, 공개 암호문·계정명·job/request ID·시각·안전 상태 노출을 사용자가 허용했다. 제품 v1은 job 제출과 안전 상태 조회만 제공하며 후보 목록과 상세 SearchAgent 결과를 Control PC로 반환하지 않는다. 공개 저장소의 미허가 Issue는 worker 시작 없이 무시/거부하고 공개 답글을 달지 않는 설계다. 기존 GitHub 상태 명령은 SearchAgent job dispatch와 별도다. `REMOTE_COMMANDS_ENABLED`를 켜도 worker dispatch가 추가되지 않는다.

## 검토 근거

- `bridge/command_processor.py`: `SUPPORTED_COMMANDS`, Issue 명령 처리, status projection
- `bridge/config.py`: 원격 명령 기본값과 활성화 설정 검증
- `main.py`: local SearchAgent CLI 시작 경로와 비활성 polling 동작
- `bridge/job_registry.py`: 고정 worker 작업 등록
- `AGENTS.md`: future relay를 조건부·default-off로 제한하는 지침

검토는 읽기 전용으로 진행했다. GitHub, SearchAgent worker, Bloomberg API 및 active probe에 접속하지 않았다.
