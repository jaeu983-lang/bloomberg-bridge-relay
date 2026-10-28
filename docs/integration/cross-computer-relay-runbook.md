# 별도 PC GitHub relay 실행 순서

**상태:** 코드 구현 완료; PC별 자격 증명·키 설정과 운영 검증 대기  
**목표 제품:** Control PC에서 고정 SearchAgent metadata 작업을 제출하고 안전한 실행 상태만 확인하는 CLI/worker 도구. 상세 검색 결과·후보·Bloomberg 값은 Bloomberg Desktop PC의 SearchAgent 로컬 저장소에 남으며 Control PC로 전달하지 않는다.

## 역할

| 역할 | 설치·보관할 항목 | 하지 않을 일 |
|---|---|---|
| Control PC | Bridge submit/status 도구, 제출 전용 GitHub 자격 증명, worker의 암호화 공개키, 제출자 서명 private key | Bloomberg API 접속, worker 복호화 private key 보관, 상세 SearchAgent 결과 수신, 검색어 평문을 GitHub에 기록 |
| Bloomberg Desktop PC | Bridge worker/poller와 자체 Python 환경, 별도 SearchAgent 프로젝트·Python 환경·SQLite DB, worker 전용 GitHub 자격 증명, 복호화 private key, 제출자 검증 public key, Bloomberg Desktop 세션 | Bridge 안에 SearchAgent 코드 import, private key를 Control PC로 복사 |
| Public GitHub repo | 누구나 볼 수 있는 암호문 요청, request/issue ID, 제출자 계정, 작업 ID, 시각, allowlist된 상태 metadata | 검색어 평문·후보·상세 결과·Bloomberg 값·결과 count·raw output·토큰·키 저장 |

두 PC는 서로 직접 연결하지 않는다. 둘 다 대상 GitHub 호스트로 outbound HTTPS가 가능해야 한다. Bloomberg Desktop PC는 Bloomberg Desktop API에도 로컬로 접근할 수 있어야 한다.

## 현재 구현 상태

Bridge에는 역할별 키 생성, 암호화된 고정 작업 제출, 서명·작성자 검증, 중복 방지, safe status 조회, 별도 worker poll CLI가 구현돼 있다. 세 원격 플래그는 모두 기본 `false`이며, 실제 GitHub Issue나 Bloomberg worker로 검증하지 않았다. 다음은 두 PC에서 키·토큰을 별도로 설정하고, go-live 승인 전 flags를 끈 채 staged verification을 하는 단계다. `REMOTE_COMMANDS_ENABLED`를 켜도 SearchAgent relay는 활성화되지 않는다.

### 사전 확인 현황

| 항목 | 상태 | 메모 |
|---|---|---|
| 사용자 PC(Control PC)의 `github.com:443` | 확인 완료 | `TcpTestSucceeded: True` |
| 사용자 PC(Control PC)의 `api.github.com:443` | 확인 완료 | `TcpTestSucceeded: True` |
| Bloomberg Desktop PC의 SearchAgent 위치 | 확인 완료 | SearchAgent는 Bloomberg Desktop PC에 있음 |
| Bloomberg Desktop PC의 `github.com:443` | 확인 완료 | `TcpTestSucceeded: True` |
| Bloomberg Desktop PC의 `api.github.com:443` | 확인 완료 | `TcpTestSucceeded: True` |
| Relay 저장소 | 확인 완료 | [`jaeu983-lang/bloomberg-bridge-relay`](https://github.com/jaeu983-lang/bloomberg-bridge-relay), public, Issues 활성화, 비어 있음 |
| 허용 제출자 | 설정 확정 | `jaeu983-lang` 단독 allowlist; 다른 계정은 별도 결정 전까지 불허 |
| 키 방식 | 설정 확정 | PyNaCl/libsodium SealedBox 암호화 + 별도 Ed25519 서명 키. production key는 구현된 key-generation 도구로 생성 |
| 만료·보존 | 설정 확정 | 미시작 요청 10분 만료; terminal Issue 즉시 close, 완료 30일 후 owner가 수동 삭제 |
| Repository description | 수정 필요 | 현재 설명에 `Private`가 남아 있어 공개 설정과 불일치 |

### 1단계 — PC 역할과 위치 정하기

사용자가 기록할 항목:

- `BLOOMBERG_PC`: Bloomberg Desktop 로그인과 API 세션이 있는 PC
- `CONTROL_PC`: 요청을 제출할 PC
- Bloomberg PC의 Windows 사용자 계정과 그 PC에서 SearchAgent 프로젝트를 둘 경로
- Control PC와 Bloomberg PC 모두 Windows인지 확인

두 역할이 같은 PC에 놓이면 GitHub relay는 필요하지 않고 기존 local CLI를 쓸 수 있다.

**완료 기준:** Bloomberg Desktop PC와 Control PC가 구분되고, SearchAgent를 어느 Bloomberg PC에서 실행할지 정해졌다.

### 2단계 — 두 PC에서 GitHub 연결 확인

각 PC의 PowerShell에서 실제 사용할 GitHub 호스트로 확인한다. GitHub.com을 쓰는 경우:

```powershell
Test-NetConnection github.com -Port 443
Test-NetConnection api.github.com -Port 443
```

`TcpTestSucceeded`가 두 호스트에서 모두 `True`여야 한다. GitHub Enterprise를 쓰면 회사의 실제 web/API host를 확인한다. DNS, proxy, firewall 차단이 있으면 해당 네트워크 관리자에게 outbound HTTPS 허용을 요청한다. PC 간 inbound 연결은 열지 않는다.

**완료 기준:** 양쪽 PC에서 각각 사용할 GitHub API host에 outbound HTTPS 연결이 된다.

### 3단계 — GitHub 저장소와 제출자 정하기

- 선택한 relay 저장소는 public이다. Issue 암호문, 계정명, 작업 ID, 요청 ID, 시각, 상태 comment가 공개된다는 점을 전제로 한다. 검색어 평문·결과·토큰·키는 저장하지 않는다.
- 초기 allowlist는 저장소 owner `jaeu983-lang` 단독이다. 다른 계정은 명시적으로 추가 승인하기 전까지 실행 권한이 없다.
- 미시작 요청은 10분 뒤 만료한다. terminal Issue는 즉시 닫고 완료 30일 뒤 owner가 수동 삭제한다. worker 자격 증명에는 repo 관리/삭제 권한을 주지 않는다. SearchAgent 상세 결과는 이 repo에 넣지 않는다.
- 암호화는 PyNaCl/libsodium SealedBox, 인증은 독립된 Ed25519 서명 키를 사용한다. `main.py --generate-relay-keys control|worker --key-directory <절대 경로>`로 각 PC에서 별도 생성한다.
- Repository description에서 `Private` 표현을 제거해 공개 설정과 일치시킨다.
- 저장소에 `bridge-searchagent-job` label을 만들고, submit·worker 역할의 최소 권한 자격 증명을 따로 발급한다. 토큰은 Issue에 기록하지 않는다.

**완료 기준:** 저장소, 초기 제출자, 키 방식, 만료·보존 정책이 정해졌다. 비밀값은 채팅에 보내지 않는다.

## 배포 준비와 staged verification

### 4단계 — Relay 코드 검토와 오프라인 검증

다음 기본값은 모두 `false`다.

- `REMOTE_COMMANDS_ENABLED`: 기존 PING/health/status Issue 채널
- `REMOTE_SEARCHAGENT_SUBMIT_ENABLED`: Control PC에서 암호화 요청 제출
- `REMOTE_SEARCHAGENT_JOBS_ENABLED`: Bloomberg Desktop PC에서 Issue poll/decrypt/worker 실행

오프라인 검증은 fake worker 및 암호화 프로토콜 테스트만 사용했다. 구현 이후 전체 pytest 결과는 `docs/TEST_REPORT.md`에 기록한다. 아래 PC 간 staging은 실제 GitHub Issue를 사용하므로 운영 자격 증명과 각 PC의 설정 완료 후 별도로 진행한다.

**완료 기준:** 암호문 변조, 서명 불일치, 미허가 사용자, 만료, 중복·재전송, 잘못된 operation, 과대 입력, worker timeout이 모두 안전하게 거부되고 테스트가 통과했다.

### 5단계 — Bloomberg Desktop PC 설치

배포 문서가 준비되면 Bloomberg Desktop PC에서:

1. Bridge 프로젝트를 배치하고 Bridge 전용 virtual environment를 만든다.
2. BloombergSearchAgent 프로젝트를 별도로 배치하고 SearchAgent 전용 virtual environment를 유지한다.
3. 로컬 Bridge 설정에서 `SEARCHAGENT_PYTHON`과 `SEARCHAGENT_ROOT`를 지정한다.
4. `main.py --generate-relay-keys worker --key-directory <절대 경로>`를 실행한다. worker private key는 이 PC에만 둔다. control PC에서 만든 signing public key만 이 PC로 옮긴다.
5. worker 역할 전용 GitHub 자격 증명을 로컬 비밀 저장소나 보호된 `.env`에 둔다. SearchAgent 프로세스에 GitHub 자격 증명을 전달하지 않는다.
6. remote worker flag는 계속 꺼둔 상태로 health와 local `plan_query`를 확인한다.

**완료 기준:** Bloomberg Desktop PC의 local Bridge adapter가 SearchAgent를 실행할 수 있고, worker key와 GitHub 자격 증명이 해당 PC 밖으로 복사되지 않았다.

### 6단계 — Control PC 설치

1. Bridge submit/status 도구와 별도 virtual environment를 설치한다. SearchAgent나 Bloomberg 환경은 설치하지 않는다.
2. `main.py --generate-relay-keys control --key-directory <절대 경로>`를 실행하고 control signing private key를 이 PC에만 둔다. Bloomberg PC의 encryption public key만 control PC로 옮긴다.
3. 제출 전용 GitHub 자격 증명을 설정한다. 권한 범위는 구현된 API와 배포 문서에서 지정한 relay repository로 제한한다.
4. 검색어는 숨김 prompt 또는 stdin으로 입력한다. 명령 인자·shell history·stdout·로그에 넣지 않는다.

**완료 기준:** Control PC는 암호화된 요청을 제출하고 issue/request ID만 확인할 수 있다.

### 7단계 — 암호화된 transport 및 상태 확인

1. Control PC에서 `plan_query` 요청을 한 번 제출한다.
2. Bloomberg PC worker를 one-shot poll로 한 번 실행한다.
3. GitHub Issue body에 plaintext 검색어가 없는지 확인한다.
4. 결과 comment가 request ID, 고정 job ID, lifecycle status, allowlisted outcome/error code, 시각만 포함하는지 확인한다. 결과 수나 SearchAgent 후보·상세 결과는 전달되지 않는다.
5. 동일 요청을 다시 전달해도 두 번째 worker가 시작되지 않는지 확인한다.

**완료 기준:** 요청과 결과가 GitHub relay를 통과하고 plaintext 및 중복 실행이 없다. 이 단계는 Bloomberg session/API를 열지 않는다.

### 8단계 — 제한된 metadata 검색과 운영 전환

- 사용자가 실행 시점에 승인한 뒤 `search_instruments` 또는 `search_fields` 중 하나를 낮은 limit으로 한 번 실행한다.
- Control PC에는 safe status만 돌아오고 SearchAgent 상세 결과가 Bloomberg PC의 SearchAgent 로컬 저장소에만 남는지 확인한다. V1에서는 Control PC에서 상세 결과를 볼 수 없다.
- Reference/Historical, market-data 조회, Excel, active probe는 이 runbook의 범위에 넣지 않는다.
- transport, redaction, idempotency, local Bloomberg session을 확인한 뒤 Bloomberg Desktop PC에만 Task Scheduler worker poller를 등록한다.
- go-live 때만 Control PC에서 `REMOTE_SEARCHAGENT_SUBMIT_ENABLED=true`, Bloomberg Desktop PC에서 `REMOTE_SEARCHAGENT_JOBS_ENABLED=true`를 각각 설정한다. `REMOTE_COMMANDS_ENABLED=false`는 유지한다.

**완료 기준:** 한 번의 허가된 metadata 요청이 실행되고 Control PC에서 안전 상태를 확인했으며, 상세 결과는 Bloomberg PC에만 남고 중지·복구 절차가 검증됐다. 기본 설정 파일과 다른 PC의 flag는 계속 off다.

## 중지·복구

- 잘못된 요청, plaintext 노출, duplicate start, 예상 밖 operation이 보이면 즉시 worker poller를 중지하고 `REMOTE_SEARCHAGENT_JOBS_ENABLED=false`로 되돌린다.
- 시작된 worker는 자동 재실행하지 않는다. 최종 status와 local execution ledger를 확인한 뒤 새 request ID로만 재시도한다.
- GitHub, API 응답 원문, Bloomberg 후보·값, 토큰, private key를 채팅이나 Issue comment에 붙이지 않는다. 공유할 것은 request ID와 허용된 status/error/outcome code뿐이다.

## 사용자 실행 후 공유할 확인값

배포 기본값은 저장소 owner 한 명, 10분 요청 만료, terminal issue 30일 후 수동 삭제다. 다음 작업은 사용자 측 PC에서 수행한다:

- 각 PC에서 생성한 public key만 상대 PC에 전달하고 설정했다는 확인 (private key 내용은 공유하지 않음)
- 각 PC의 role-specific GitHub token 발급과 `.env` 설정 완료 여부 (token 자체는 공유하지 않음)
- public repo label `bridge-searchagent-job` 생성 여부
- Bloomberg PC의 SearchAgent Python/root 경로가 정해졌는지 여부

토큰, 검색어, Bloomberg 값, key 파일 내용은 공유하지 않는다.
