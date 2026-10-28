# BloombergBridge + BloombergSearchAgent 통합 계획

**상태:** 1–6회차 구현 완료; 두 PC staging 및 운영 go-live 결정 대기  
**기준일:** 2026-10-28  
**목표:** 두 프로젝트의 역할과 연결 단계를 정리하되, 데이터·실행 경계를 지킨다.

## 1. 결론

두 프로젝트를 **하나의 운영 흐름으로 정돈하는 것은 가능**하다. 코드와 저장소를 합치는 방식은 권장하지 않는다.

Bridge와 SearchAgent는 별도 저장소, Python 환경, SQLite 저장소를 유지한다. 고정된 SearchAgent worker 1.0을 Bridge의 명시적 local CLI에서 호출할 수 있고, 별도 PC용 GitHub relay 코드도 추가됐다. relay는 기본 비활성화 상태이며, Bridge는 Bloomberg API에 직접 접근하지 않는다.

relay는 암호화·인증된 고정 요청과 독립적인 default-off flags로 제한한다. 현재 legacy Issue parser는 worker를 시작하지 않는다. `REMOTE_COMMANDS_ENABLED=false` 기본값을 유지하고, submit/worker flags도 각각 false로 둔다.

## 2. 현재 상태

| 항목 | BloombergBridge | BloombergSearchAgent |
|---|---|---|
| 역할 | 명령 검증, 실행 상태 및 안전한 상태 보고를 위한 control plane | Bloomberg 메타데이터 검색, 데이터 검증·조회, 로컬 저장을 맡는 data plane |
| 현재 실행 대상 | 세 고정 Mock 작업과, 경로 설정 시 local CLI에서만 시작할 수 있는 세 metadata worker 작업 | 기존 사용자 CLI와 로컬 `worker` CLI |
| SearchAgent 연동 상태 | `SearchAgentAdapter`는 절대 경로 설정 시 local CLI로만 실행 가능; 세 worker operation 호출 성공 | worker contract 1.0이 구현되고 오프라인 테스트됨; Bridge local CLI 경유 호출 확인 |
| worker v1 범위 | Bridge의 제안 parser는 별도 존재 | `plan_query`, `search_instruments`, `search_fields`; Bloomberg 시장 데이터 조회와 Excel 내보내기는 제외 |
| 원격 동작 | 기존 Issue 명령은 `PING`, `HEALTH_CHECK`, `GET_JOB_STATUS`; 별도 relay 구현됐지만 기본 비활성화; 두 relay flags 기본 false | GitHub polling·원격 명령 기능 없음 |
| 소유 데이터 | 명령 및 실행 metadata | Bloomberg 세션, 검색·검증 로직, 로컬 DB와 Excel 결과 |

SearchAgent는 metadata 검색 worker를 이미 제공하지만, Bridge 문서의 기존 worker 제안과 형식이 다르다. 기존 Bridge proposal은 실행 연결이나 SearchAgent의 계약 수락을 뜻하지 않는다.

## 3. 양쪽 worker 계약의 차이

| 구분 | Bridge 제안 parser/계약 | SearchAgent 구현 계약 |
|---|---|---|
| 버전 | 정수 `1` | 문자열 `"1.0"` |
| 요청 식별자 | `execution_id` | `request_id` |
| 작업 지정 | 고정 `job_id`와 `parameters` | `operation`과 `arguments` |
| 입력 모델 | 초기 제안은 빈 parameters | 작업별 엄격한 arguments; 질문은 최대 512자 |
| 결과 | Bridge 실행 상태, 시각, 고정 오류 코드 | 검색 `status`, 제한 metadata, 안전 오류 코드 |
| 종료 코드 | 제안은 성공/실패/계약 오류를 구분 | `0` 유효 처리, `2` 입력 거부, `3` worker 오류 |
| 시간 제한 | 등록 작업별 Bridge timeout 제안 | worker 계약은 단일 end-to-end deadline을 보장하지 않으며 호출자가 제한해야 함 |

따라서 두 parser를 직접 연결하지 않는다. 4회차 adapter는 SearchAgent worker 1.0 요청·응답 validator를 사용하고, 과거 Bridge proposal parser와 구분한다. 양쪽이 서로 다른 버전 번호를 같은 계약인 것처럼 취급해서는 안 된다.

## 4. 목표 역할 분담

```text
현재:
  └─ BloombergBridge local CLI ── fixed worker ── BloombergSearchAgent

계획:
  Control PC ── GitHub relay ── Bloomberg Desktop PC의 Bridge worker/poller
                                      └─ BloombergSearchAgent ── Bloomberg Desktop API

Bridge는 SearchAgent 코드나 저장소를 공유하지 않는다. worker 요청은 stdin으로 전달하고, 결과 데이터는 SearchAgent 내부에 남긴다.
```

공통 원칙:

- 저장소, Python 환경, 프로세스, SQLite DB를 각각 유지한다.
- Bloomberg 세션과 값·후보 목록·워크북은 SearchAgent 안에 둔다.
- Bridge와 GitHub에는 허용된 상태 metadata만 둔다. 질의 원문, 검색 후보, 원시 응답, 값, 로컬 경로, 토큰, 예외 문구는 보내지 않는다.
- 작업 이름과 허용 입력은 코드/로컬 설정에 고정한다. Issue 내용으로 executable, shell, 경로, 임의 인자, 출력 위치를 정하지 않는다.
- 기존 `REMOTE_COMMANDS_ENABLED` 기본값과 테스트 설정은 계속 `false`로 둔다. 향후 worker relay는 별도 default-off flag로 분리한다.
- Universe Screening, Reference/Historical 조회, Excel 생성은 SearchAgent worker v1 범위에 포함하지 않는다.

## 5. 여러 회차로 나눈 실행 순서

### 1회차 — 문서와 이름 정리: 완료

- `docs/integration/`에 통합 계획·현황·기존 계약 제안을 모았다.
- 일반 문서 파일명을 소문자 kebab-case로 정리하고, 지정 경로인 `TEST_REPORT.md`와 [문서 색인](../README.md)을 유지했다.
- 오래된 Bridge 제안과 SearchAgent의 실제 worker contract를 구분했다.

### 2회차 — 실행 없는 계약 검증: 완료

- SearchAgent worker v1 응답 envelope를 Bridge에서 **순수 데이터로만** 검증하는 parser를 추가했다.
- 성공, 입력 거부, worker 오류의 서로 다른 응답 형태와 종료 코드 `0/2/3`의 대응을 확인했다.
- 버전·요청 ID·작업 이름, 필드 allowlist, 값 범위, metadata 전용 제약을 검사하도록 했다.
- 합성 fixture로 offline pytest를 실행했다: **141 passed in 1.03s**. 결과는 [테스트 보고서](../TEST_REPORT.md)에 기록했다.
- adapter를 연결하거나 subprocess를 시작하지 않는다. job registry와 원격 명령도 변경하지 않는다.

### 3회차 — worker 요청 생성·검증: 완료

- SearchAgent worker 1.0의 세 metadata operation에 대해 exact key, 문자열 길이, enum, limit, request ID를 검사하고 UTF-8 JSON 요청을 만든다.
- 잘못된 operation, 경로·추가 인자, 제어문자, 길이 초과, 허용 범위 밖 값을 거부한다.
- 합성 입력만 사용한 전체 오프라인 테스트: **155 passed in 1.03s**. 결과는 [테스트 보고서](../TEST_REPORT.md)에 기록했다. 요청은 worker에 전달하지 않았다.

### 4회차 — 로컬 worker 실행 adapter: 완료

- `searchagent.plan_query`, `searchagent.search_instruments`, `searchagent.search_fields`만 고정 local job으로 등록했다.
- 실행 경로는 `SEARCHAGENT_PYTHON`과 `SEARCHAGENT_ROOT`의 절대 경로로 로컬 설정하며, 명령은 `<python> <root>/main.py worker`로 고정했다. `shell=False`, UTF-8 JSON stdin, SearchAgent worker 1.0 응답 검증을 적용한다.
- Bridge 프로세스의 GitHub 토큰 등은 worker 환경에서 제외한다. stdout/stderr는 크기 제한으로 읽고, timeout 또는 출력 초과 시 worker 프로세스 트리를 종료한다. 실행 metadata와 allowlisted outcome code만 Bridge에 남긴다.
- 실행 진입점은 명시적 `--run-searchagent-job` local CLI뿐이다. Issue 명령 집합과 `REMOTE_COMMANDS_ENABLED`는 바꾸지 않았다.
- fake worker로 성공, malformed/oversized 응답, timeout, 입력 거부, 환경 변수 격리를 오프라인 검증했다. 실제 SearchAgent worker와 Bloomberg API는 호출하지 않았다. 결과는 [테스트 보고서](../TEST_REPORT.md)에 기록한다.

### 5회차 — 제한된 실제 worker 확인: 완료

- Bridge의 명시적 local CLI로 실제 SearchAgent `plan_query`, `search_instruments`, `search_fields`를 각각 한 번 호출했다. 세 작업 모두 프로세스·계약 검증상 `SUCCEEDED`, worker outcome은 `AMBIGUOUS`였다.
- `plan_query`는 offline query planner를 사용한다. 두 검색 operation은 worker contract의 metadata-only 검색 범위에서만 실행했다. Bridge는 후보 값이나 원시 worker 출력을 기록·출력하지 않았다.
- SearchAgent 경로는 각 Bridge CLI 프로세스에만 임시로 전달했으며 `.env`에는 저장하지 않았다. 호출 때마다 `REMOTE_COMMANDS_ENABLED=false`였다.
- Bloomberg market-data 조회, Reference/Historical 조회, Excel 출력, active API probe는 실행하지 않았다. Issue-triggered worker 실행은 별도 PC relay 계획에 따라 아직 구현되지 않았다.

### 6회차 — 두 PC 간 GitHub relay 기획: 완료; 구현 전 결정 대기

- 요구 topology는 control PC와 Bloomberg Desktop PC가 GitHub에 outbound HTTPS로 접속하고, 서로 직접 연결하지 않는 store-and-forward 방식이다.
- 목표 제품 v1은 Control PC의 CLI submit/status와 Bloomberg Desktop PC의 Bridge worker/poller다. Control PC는 고정 작업을 제출하고 request ID, 고정 job ID, lifecycle status, allowlisted outcome/error code, 시각만 확인한다. 후보 목록·상세 SearchAgent 결과·Bloomberg 값은 반환하지 않으며 Bloomberg PC의 SearchAgent 로컬 저장소에 남는다.
- 검색 질의가 GitHub에 평문으로 저장되지 않도록 암호화 request를 기본 설계로 잡았다. 현재 Issue allowlist는 상태 명령뿐이며 원격 worker 실행은 구현되지 않았다.
- 선택한 relay 저장소는 public `jaeu983-lang/bloomberg-bridge-relay`이며 Issues가 활성화돼 있다. 공개 Issue 암호문, 계정명, 작업 ID, 요청 ID, 시각, 상태 노출을 사용자가 허용했다. 초기 제출자 allowlist는 owner `jaeu983-lang` 단독, 미시작 요청 만료는 10분, terminal Issue 삭제는 완료 후 30일로 정했다(저장소 owner가 수동 수행). 암호화는 PyNaCl/libsodium SealedBox, 서명은 별도 Ed25519 키 쌍을 사용하며 production key는 구현 뒤 생성한다. Control PC 제출 flag와 Bloomberg PC worker flag는 분리하며 모두 기본 off다. 저장소 설명에 남은 `Private` 문구는 사용자가 나중에 수정할 예정이다. 양 PC 모두 GitHub TCP 443에 성공했지만 API 자격 증명 권한은 staging에서 확인해야 한다.
- 배치·구현 순서와 Bloomberg Desktop PC 설치 방법은 [별도 PC 간 GitHub relay 계획](cross-computer-github-relay-plan.md)에 기록했다. 정적 검토 결과는 [원격 실행 검토](github-remote-execution-review.md)에 남겼다.
- 사용자가 직접 확인·설치할 단계와 완료 판정은 [별도 PC relay 실행 순서](cross-computer-relay-runbook.md)에 기록했다.

## 6. 위험과 대응

| 위험 | 대응 |
|---|---|
| 서로 다른 계약을 같은 v1로 오인 | SearchAgent 계약 1.0과 Bridge 제안 v1을 명시적으로 구분하고 mapping을 새로 승인 |
| 검색 질문이 Issue·로그로 노출 | 원문과 후보 목록은 응답·로그에 투영하지 않고, 입력 크기·형식을 작업별로 제한 |
| SearchAgent 응답/오류에 값이나 로컬 정보가 섞임 | 정확한 응답 스키마 allowlist, 크기 제한, 기본 거부 처리 |
| 프로세스 hang 또는 Bridge 재시작 | 고정 timeout, 프로세스 종료, `INTERRUPTED` 기록, 자동 재실행 금지 |
| 정책 경계가 흐려져 원격 실행으로 확장 | 로컬 연동과 원격 Issue 실행을 별도 단계·승인으로 분리 |
| 상태 코드만 보고 후보·상세 검색 결과를 확인했다고 오해 | relay v1은 상세 결과를 전송하지 않고 실행 상태만 반환; 상세 확인은 Bloomberg PC의 SearchAgent 로컬 경로에서 별도 수행 |

## 7. 이 계획의 범위와 현 문서

1–5회차의 local worker는 반영·확인됐다. 별도 PC 간 relay는 설계됐으며 conditional policy 범위도 `AGENTS.md`에 기록했다. 실제 코드 구현 전 repository/operator/network/key/retention 결정을 확정해야 한다. 두 remote execution flag는 기본 off로 유지한다. Phase 1 active probe와 Bloomberg market-data/Reference/Historical 요청은 실행하지 않았다.

관련 문서:

- [Bridge 아키텍처](../system-architecture.md)
- [Bridge 기존 worker 계약 제안](bridge-worker-contract-proposal.md)
- [SearchAgent API 실행·검색 개선 제안](searchagent-improvement-plan.md)
- [Bridge 테스트 보고서](../TEST_REPORT.md)
- [GitHub 원격 실행 검토](github-remote-execution-review.md)
- [별도 PC 간 GitHub relay 계획](cross-computer-github-relay-plan.md)
- SearchAgent worker contract: `C:\Projects\BloombergSearchAgent\docs\INTEGRATION_CONTRACT.md`
- SearchAgent 구현 계획: `C:\Projects\BloombergSearchAgent\docs\DEVELOPMENT_PLAN.md`
