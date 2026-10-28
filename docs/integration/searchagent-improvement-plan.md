# BloombergSearchAgent 실행·검색 개선 제안

**상태:** 6단계 구현 완료 — holdout 승격 gate는 provisional로 남음  
**기준일:** 2026-10-27  
**목표:** Bloomberg API 요청의 실패 경계와 검색 후보의 순위를 개선하되, 기존의 보수적인 확정·출력 게이트를 유지한다.

## 1. 범위와 소유권

- Bloomberg API 세션, 요청 실행, 결과 해석, 검색 알고리즘은 BloombergSearchAgent 소유다.
- 사용자의 실행 지시에 따라 SearchAgent 코드와 offline 테스트를 수정했다. 수정 위치는 SearchAgent의 `bloomberg/session.py`, `main.py`, 검색 ranker/evaluator 및 해당 테스트·보고서다.
- 일반 CLI metadata 요청 2회와 단일 Historical workflow 1회가 SearchAgent 로컬 이력에 기록됐다. workbook은 만들거나 수정하지 않았다. Phase 1 active probe와 SearchAgent worker는 실행하지 않았다.
- SearchAgent worker 1.0은 현재 metadata 후보 검색용이다. Reference/Historical 데이터 조회와 Excel 내보내기를 Bridge worker로 확장하는 제안은 포함하지 않는다.
- Bridge에는 metadata-only worker 1.0을 explicit local CLI에서 실행하는 adapter가 구현됐다. 별도 PC relay는 Bridge 통합 계획에서 관리하며 이 SearchAgent 코드 개선 작업에는 포함하지 않는다.

## 2. 현재 기준선과 확인된 개선점

| 영역 | 현재 기준선 | 개선할 점 |
|---|---|---|
| BLPAPI 요청 수명 | 동기 요청 직렬화, correlation ID, partial/final response, timeout을 처리한다. | 최종 response 전에 종료되면 해당 ID를 취소하도록 구현했다. cancellation failure는 원래의 안전한 상태 코드를 유지한다. |
| Historical 데이터 전처리 | canonical security, FieldInfo, freshness postflight를 사용한다. | HistoricalData 전에 단일 security-field ReferenceData를 보내 entitlement와 VALUE point를 확인한다. 실패·timeout·missing 값이면 HistoricalData를 건너뛴다. |
| 종목 후보 순위 | 기본 ranker는 결정적인 `bounded_lexical_v1`, 검토된 ranking-only alias, Yellow Key 충돌 hard exclusion을 사용한다. | offline 전용 `bounded_evidence_v2` 실험 ranker를 추가했다. validation 지표가 v1과 동률이라 기본 동작은 v1로 유지한다. |
| 필드 후보 순위 | 기본 ranker는 mnemonic, name, description, type/category 근거를 사용한다. | evidence 가중치 후보를 offline evaluator에 추가했다. Field Search 결과는 계속 CANDIDATE/AMBIGUOUS이며 FieldInfo만 mnemonic을 확인한다. |
| 다중 질의 | 계획 검색은 최대 5회 metadata 검색을 수행하고, 질의별 근거를 보존하며 서로 다른 질의 점수를 더하지 않는다. | offline 검증은 있으나 bounded multi-query의 실제 Bloomberg 결과는 아직 검증되지 않았다. |
| 승격 평가 | baseline/candidate, target/similar/unrelated 사례와 보호 지표가 있다. | 검색·freshness holdout이 provisional이므로 일반 정책 승격 판정은 아직 INCONCLUSIVE다. |

## 3. 개선 순서

### 1단계 — 요청 시간 초과 정리: 완료

1. 요청 deadline에 도달하면 해당 `CorrelationId`를 명시적으로 취소한다.
2. 취소가 실패해도 SDK 원문 예외를 출력하지 않고, 제한된 오류 코드와 session 상태만 남긴다.
3. 지연된 partial/final event가 후속 요청의 결과로 해석되지 않도록 correlation filtering과 요청 종료 상태를 검증한다.
4. 재시도는 자동으로 추가하지 않는다. timeout 이후의 재요청 정책은 요청 종류별 부작용·비용·중복 가능성을 검토한 뒤 정한다.

Bloomberg Session 문서는 성공한 요청이 0개 이상의 partial response 뒤 하나의 최종 response로 끝나며, correlation ID가 최종 response 또는 명시적인 취소까지 유지된다고 설명한다. 따라서 timeout 처리에 취소 동작과 지연 응답 검증을 넣는 것이 우선이다. [BLPAPI Session 문서](https://bloomberg.github.io/blpapi-docs/python/3.26.9/_autosummary/blpapi.Session.html)

**완료 확인:** offline session 테스트가 timeout correlation ID 취소와 partial response timeout을 확인한다. 취소 예외는 진단에 노출되지 않고 final response가 온 성공 요청은 취소하지 않는다. 전체 테스트 142개 통과. 지연 event에 관한 live 검증은 수행하지 않았다.

### 2단계 — Historical 적용성 preflight: 완료

조회 흐름을 다음처럼 명확히 나눈다.

```text
canonical security 확인
  → 직접 FieldInfo 확인
  → security-field 적용성·요청별 entitlement preflight
  → HistoricalData 요청
  → 관측일 기반 freshness 검사
  → 승인된 로컬 출력
```

- preflight는 단일 종목·단일 필드·bounded request로 제한한다.
- 적용성 또는 entitlement를 확인하지 못하면 HistoricalData 요청을 보내지 않고 구체적인 상태 코드를 반환한다.
- preflight 응답의 시장 값은 로컬 요청 판정에만 쓰고 stdout, 로그, Bridge, GitHub로 내보내지 않는다.
- 실제 Historical 응답을 받은 뒤 관측일 기반 freshness 검사는 계속 수행한다. STALE/FRESHNESS_UNVERIFIED 값은 현재처럼 출력·Excel 단계 전에 제거한다.

**완료 확인:** mock 테스트에서 successful VALUE preflight 뒤에만 HistoricalData가 실행된다. field error, entitlement denial, timeout, missing value에서는 HistoricalData 호출이 0회다. stdout에는 preflight와 Historical 값이 나타나지 않는다.

### 3단계 — `bounded_evidence_v2` 후보 순위 실험: 구현·비교 완료, 미승격

현재 `bounded_lexical_v1`은 baseline으로 고정한다. 새 버전은 결정적 가중 점수만 후보 순위에 적용하고, 기존 정확성 게이트와 분리해 비교한다.

**종목 검색 특징 순서 제안:**

1. 전체 canonical security ID 완전 일치
2. ticker, bbgid, curveid 등 identifier 필드 일치
3. 정규화한 공식 이름·short name 구문 일치
4. 출처가 검토된 alias/확장어 일치
5. 이름의 토큰 coverage와 순서
6. 설명·issuer 등 보조 metadata 일치

**필드 검색 특징 순서 제안:**

1. 사용자가 제공한 mnemonic을 직접 FieldInfo로 확인한 결과
2. 공식 field name 구문 일치
3. mnemonic·name의 토큰 coverage와 순서
4. 검토된 alias 일치
5. description, category, data type 보조 일치

구현 시 점수와 함께 `matched_fields`, `matched_terms`, `matched_aliases`, `missing_constraints`, evidence source를 반환한다. 확인된 자산 유형·Yellow Key 충돌은 계속 hard exclusion으로 둔다. metadata 누락은 모순으로 간주하지 말고 “확인되지 않음”으로 표현한다.

**보호 규칙:** 단일 후보, 높은 점수, lexical·alias 일치만으로 `EXACT_MATCH`를 만들지 않는다. 종목은 전체 canonical ID 완전 일치, 필드는 직접 FieldInfo 확인 등 기존 근거가 있어야 확정한다. 점수 임계값을 confidence로 표시하려면 validation 데이터에서 먼저 보정한다.

### 4단계 — 검증 세트와 승격 기준: validation 완료, holdout 대기

- target, 비슷하지만 정답이 달라야 하는 사례, 무관 사례를 같은 family 안에 둔다.
- instrument: canonical ID, ticker, 회사명, 채권·curve, 통화, Yellow Key 충돌, 모호한 약칭을 포함한다.
- field: mnemonic, 공식명, 유사한 rate/price/yield/amount, product type, field type, security applicability를 포함한다.
- 검색 상태 정확도, Top-1, Recall@5, MRR, hard-conflict leakage, false exact, clarification gate 위반을 함께 본다.
- `bounded_lexical_v1`과 `bounded_evidence_v2`를 동일한 validation에서 비교한다. 독립 holdout은 validation 통과 뒤 봉인된 사례로 한 번만 평가한다.
- holdout 통과 전까지 새 ranker는 provisional이며 기존 동작을 유지한다.

현재 synthetic validation 비교에서 v1과 v2 모두 상태 정확도 9/9, Top-1 7/7, 후보 순서 9/9였고 보호 지표 위반은 모두 0건이었다. v2의 목표 지표 개선이 없으므로 기본 ranker로 승격하지 않았다. holdout은 provisional이다.

**완료 조건:** 목표 family의 개선과 보호 family의 무회귀를 확인하고, false exact·금지 후보 누출·clarification 우회가 0건이다. 결과에 분자·분모와 holdout 상태를 기록한다.

### 5단계 — bounded live 확인: 완료

- offline 테스트와 독립 평가 뒤, 운영자가 승인한 범위에서만 metadata 검색 live smoke를 수행한다. 한 요청당 fan-out은 5회 이하로 유지한다.
- 실제 반환 후보가 offline 가정과 다르면 정책을 승격하지 않고 fixture/검증 세트를 갱신한다.
- 2026-10-27에 SearchAgent 고정 CLI로 Instruments와 API Fields 검색을 각각 1회 실행했다. 둘 다 `SUCCEEDED`; 종목 결과는 `EXACT_MATCH`, 필드 결과는 `AMBIGUOUS`였다. 필드 검색은 176개 후보 중 표시 한도 5를 유지했고, 후보 원문은 보고서에 복사하지 않았다.
- Historical workflow는 단일 security-field, 2일 범위, export 없이 실행했다. preflight는 `PASSED`, Historical 요청은 `SUCCEEDED`, freshness는 `FRESH`였고 관측 2개를 반환했다.
- Phase 1 active probe와 SearchAgent worker는 실행하지 않았다.

Bloomberg가 제공하는 `blpapi.test.appendMessage()`는 schema-aware test message를 구성하는 데 쓸 수 있다. 현재 mock 기반 검증을 유지하면서, SDK schema를 확인해야 하는 offline fixture에 제한적으로 도입한다. [BLPAPI appendMessage 문서](https://bloomberg.github.io/blpapi-docs/python/3.26.9/_autosummary/blpapi.test.appendMessage.html)

### 6단계 — 주요 모듈 책임 분리: 완료

- `search/ranking.py`는 실험 ranker를 runtime ranker와 분리했다.
- `search/data_pipeline.py`는 `main.py`에서 Reference/Historical command flow를 분리한다. CLI는 고정 의존성을 전달하고 출력 형식은 유지한다.
- `bloomberg/reference_types.py`는 결과 타입·series 변환, `bloomberg/reference_parser.py`는 value-local payload parsing을 맡는다. `reference.py`에는 request builder와 client가 남는다.
- `search/evaluation_cases.py`는 bounded fixture validation을 맡고, `search/evaluation.py`는 scoring·비교·보고서 CLI를 맡는다.
- `blpapi==3.26.9.1` pin은 유지한다. SDK/Python/Terminal capability 변경은 별도 검증 후 반영한다. Bloomberg API Library에서 인증된 release와 Python 배포 정보를 확인한다. [Bloomberg API Library](https://professional.bloomberg.com/support/api-library/)

## 4. 남은 범위와 실행 경계

- 제한된 live metadata smoke 2회와 Historical/preflight 확인 1회는 완료했다. active probe는 지침에 따라 그대로 두었다.
- Bridge adapter가 구현됐지만, 제한된 실제 SearchAgent worker 확인은 별도 운영 단계로 남아 있다.
- Bridge에 Bloomberg API client나 원격 worker 명령은 이 SearchAgent 개선 작업에서 추가하지 않았다. local adapter는 fixed worker contract만 호출한다. 두 PC 간 relay의 구현 범위는 [Bridge 통합 계획](plan.md)에서 관리한다.
- holdout이 provisional인 상태에서 `bounded_evidence_v2`를 승격하지 않았다.

SearchAgent offline 구현, bounded metadata smoke, 1회 bounded Historical workflow, 주요 모듈 분리를 완료했다. Historical preflight와 조회는 성공했고 freshness는 FRESH였다. 검색 정책 holdout은 provisional이고 v2는 v1보다 validation 개선이 없어 승격하지 않았다. 전체 offline 결과는 SearchAgent `docs/TEST_REPORT.md`에 기록했다: **142 passed in 2.68s**. Phase 1 active probe는 실행하지 않았다.
