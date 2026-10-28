# BloombergSearchAgent 통합 현황

**기준일:** 2026-10-28  
**범위:** 저장소와 런타임은 분리되어 있다. Bridge에는 고정 SearchAgent worker 1.0 local adapter와 기본 비활성화된 별도 PC relay가 구현됐다. 실제 GitHub Issue 및 Bloomberg worker staged 검증은 대기 중이다.

## 현재 확인된 상태

### BloombergBridge

- Bridge는 명령 검증, 실행 상태, GitHub 상태 projection을 담당한다.
- 등록된 기본 작업은 `mock.*` 세 개와 `searchagent.plan_query`, `searchagent.search_instruments`, `searchagent.search_fields`다.
- 지원 Issue 명령은 `PING`, `HEALTH_CHECK`, `GET_JOB_STATUS`이며 검색 작업을 시작하지 않는다.
- `SearchAgentAdapter`는 worker Python과 root를 local configuration에서 받으며, 둘 다 유효할 때만 실행 가능하다. `bridge/proposed_worker_protocol.py`는 과거 Bridge proposal 전용이고 현재 adapter는 SearchAgent contract 1.0을 사용한다.
- `bridge/searchagent_protocol.py`는 SearchAgent worker 1.0 응답과 exit code를 검사한다. `bridge/searchagent_worker.py`는 fixed argv, `shell=False`, bounded JSON stdin/stdout, 제한 환경, timeout 및 프로세스 트리 종료를 담당한다.
- `bridge/searchagent_request.py`는 고정된 worker operation과 입력 schema를 검사하고 UTF-8 JSON 요청을 만든다.
- `REMOTE_COMMANDS_ENABLED` 기본값은 `false`다.
- 현재 세 metadata job은 explicit local CLI로 시작한다. Issue parser는 `PING`, `HEALTH_CHECK`, `GET_JOB_STATUS`만 받고 worker를 시작하지 않는다. `REMOTE_COMMANDS_ENABLED` 기본값은 `false`이며 Bridge에 Bloomberg API client는 없다.
- 별도 relay는 `REMOTE_SEARCHAGENT_SUBMIT_ENABLED`와 `REMOTE_SEARCHAGENT_JOBS_ENABLED`로 각각 제어하며 기본값은 `false`다. SealedBox 암호화, Ed25519 서명, 작성자 allowlist, 고정 operation 검증, 중복 ledger와 safe status CLI가 구현됐다.
- relay 코드는 fake keys와 fake worker 중심으로 오프라인 검증했다. 실제 token, production keys, GitHub Issue, SearchAgent worker/Bloomberg API는 사용하지 않았다. PC별 배포와 staging, 명시적 go-live 결정은 남아 있다.

### 별도 PC relay 목표 제품과 사전 확인

- 목표 제품 v1은 Control PC에서 고정 metadata 작업을 제출하고 안전 상태만 확인하는 CLI/worker 도구다. Control PC에는 검색 후보·상세 SearchAgent 결과·Bloomberg 값이 돌아오지 않으며, 해당 데이터는 Bloomberg Desktop PC의 SearchAgent 로컬 저장소에 남는다.
- 사용자가 제공한 결과 기준 Control PC와 Bloomberg Desktop PC 모두 `github.com:443`, `api.github.com:443` TCP 연결에 성공했다. 이는 TCP 연결 확인이며 relay API 자격 증명은 아직 검증하지 않았다.
- `jaeu983-lang/bloomberg-bridge-relay`는 public이고 Issues가 활성화되어 있다. 초기 제출자 allowlist는 owner `jaeu983-lang` 단독, 미시작 요청 만료는 10분, terminal Issue 삭제는 완료 30일 뒤 owner가 수동 수행하도록 정했다. 키 방식은 PyNaCl/libsodium SealedBox와 별도 Ed25519 서명 키이며, production key는 각 PC에서 `--generate-relay-keys`로 생성해야 한다. GitHub description의 `Private` 문구는 사용자가 나중에 수정할 예정이다.

### BloombergSearchAgent

- SearchAgent는 Bloomberg 세션, 종목·필드 검색, 데이터 검증·조회, 로컬 DB 및 Excel 결과를 소유한다.
- SearchAgent는 고정 JSON stdin 형식의 로컬 worker contract `1.0`을 구현했다.
- worker v1 작업은 `plan_query`, `search_instruments`, `search_fields`로 제한된다. Reference/Historical 조회, Excel 내보내기, feedback 변경은 worker v1에 포함되지 않는다.
- worker 응답은 후보 식별자, Bloomberg 값, 원시 응답, 예외 원문, 로컬 경로를 반환하지 않는다고 계약에 명시돼 있다.
- SearchAgent 최신 테스트 보고서에는 worker 계약과 SearchAgent 개선을 포함한 오프라인 테스트 **142 passed**가 기록되어 있다. 이 테스트 결과 자체는 Bridge 연동 증거가 아니며, Bridge 경유 worker smoke는 위에 별도로 기록했다.

### SearchAgent 개선 실행 결과

- timeout/final-response 미수신 시 해당 correlation ID를 취소하도록 했다. 취소 실패는 안전한 timeout/error 결과를 바꾸지 않는다.
- HistoricalData 전에 단일 security-field ReferenceData preflight를 추가했다. entitlement 확인과 정확한 VALUE point가 없으면 HistoricalData 요청을 중단한다.
- `bounded_evidence_v2`를 offline evaluator 전용 후보로 추가했다. synthetic validation 9개에서 v1과 지표가 동률이어서 런타임 기본 규칙은 `bounded_lexical_v1`로 유지한다. false exact, hard-conflict 누출, 금지 동작, 확인 gate 위반은 0건이다.
- 실험 ranker는 SearchAgent `search/ranking.py`에 분리했고 offline evaluator에서만 쓴다.
- data command flow, reference parser/types, evaluation fixture validation도 각 모듈로 분리했다.
- SearchAgent 전체 offline suite: **142 passed in 2.68s**. 테스트 보고서는 `C:\Projects\BloombergSearchAgent\docs\TEST_REPORT.md`에 갱신됐다.
- SearchAgent 고정 CLI로 metadata 검색 2회와 단일 security-field Historical workflow 1회를 제한해 실행했고 검색·요청 이력이 로컬 SQLite에 기록됐다. Historical preflight는 `PASSED`, 데이터 요청은 `SUCCEEDED`, freshness는 `FRESH`였다. active probe와 worker 프로세스는 실행하지 않았다. holdout은 provisional이라 검색 후보 정책 승격은 미결이다.

## 연동 가능성 판단

두 프로젝트는 별도 저장소와 런타임을 유지한다. 현재 adapter는 고정 worker 계약을 통해 local CLI에서만 실행되며, Bridge는 worker 코드를 import하지 않는다. GitHub relay의 목표와 코드 순서는 [별도 PC relay 계획](cross-computer-github-relay-plan.md)에 있다. 원격 기능은 구현·활성화되지 않았다.

또한 Bridge의 기존 proposal은 정수 contract version `1`, `execution_id`, `job_id`, `parameters` 중심이고, SearchAgent worker는 문자열 contract version `"1.0"`, `request_id`, `operation`, `arguments` 중심이다. 두 프로토콜은 그대로 호환되지 않는다.

## 다음 단계

단계별 통합 계획과 운영 경계는 [BloombergBridge + BloombergSearchAgent 통합 계획](plan.md), SearchAgent offline 개선과 남은 live/holdout 단계는 [SearchAgent 실행·검색 개선 계획](searchagent-improvement-plan.md)에서 관리한다. 기존 Bridge worker proposal은 역사 문서로 유지하며 현재 adapter는 SearchAgent worker contract 1.0을 사용한다.
