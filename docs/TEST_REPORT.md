# BloombergBridge Test Report

Date: 2026-10-28 (Asia/Seoul)

## Offline test suite

- Command: `.venv\Scripts\python.exe -m pytest -q`
- Runtime: Python 3.12.10, pytest 9.1.1, requests 2.34.2, PyNaCl 1.6.2
- Result: **175 passed in 4.46s**
- Scope: existing offline suite plus relay role configuration, SealedBox/Ed25519 envelope round-trip, tamper/expiry/duplicate-key/author rejection, fixed-operation processor mapping, duplicate suppression, restart recovery without automatic rerun, and safe local relay ledger/status projection. Fake subprocess tests cover fixed argv, JSON stdin, environment isolation, invalid input/output, bounded stdout/stderr, timeout and process-tree termination, and local CLI result redaction. No production SearchAgent worker, live GitHub endpoint, production key/token, or Bloomberg API was contacted. The new GitHub relay REST calls were not live-tested.

## Local CLI smoke check

The recorded local mock CLI smoke check used `REMOTE_COMMANDS_ENABLED=false` and a disposable local SQLite path:

- `main.py --health` returned `OK`; `mock_search` was `READY`, `bloomberg_search` was `UNAVAILABLE` without local worker configuration, and the feature flag was `false`.
- `main.py --run-mock-job mock.success` and `mock.long` returned `SUCCEEDED`.
- `main.py --run-mock-job mock.fail` returned `FAILED`; an unregistered job returned `REJECTED / JOB_NOT_REGISTERED`.
- The mock CLI smoke checks made no Bloomberg API or GitHub requests. Separate smoke checks invoked the real SearchAgent `plan_query`, `search_instruments`, and `search_fields` workers through the Bridge local CLI. All three returned `SUCCEEDED / AMBIGUOUS`; Bridge output contained only execution metadata and allowlisted outcome codes. The search operations were metadata-only; market-data retrieval, Reference/Historical requests, and the active probe were not run. SearchAgent paths were supplied only to those processes and were not saved in `.env`; `REMOTE_COMMANDS_ENABLED=false` was set for the invocations.

The command-processor unit tests use in-memory issue data and fake clients. Relay processor tests use in-memory generated keys, fake worker/client objects, and SQLite memory storage. All relay feature flags remain false in tests; live relay calls were not made.

## GitHub connectivity

In the prior 2026-10-27 check, authentication and repository access were not checked: neither `GITHUB_TOKEN` nor `GITHUB_REPOSITORY` was configured in the process environment, and no `.env` file was present. `main.py --check-github` returned `SKIPPED / GITHUB_CONFIGURATION_MISSING` without making a request. The remote command feature flag remains false by default; no GitHub endpoint was contacted by the current test suite.

The configured REST API version, `2026-03-10`, is currently supported according to [GitHub's REST API version documentation](https://docs.github.com/en/rest/about-the-rest-api/api-versions).
