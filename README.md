# BloombergBridge

BloombergBridge is a local Windows control plane for registered Bloomberg work engines. It keeps GitHub transport, command validation, job execution state, and engine adapters separate. Bloomberg API calls and financial-data handling belong to a worker such as BloombergSearchAgent, not to this project.

## Current scope

- SQLite-backed command idempotency and execution status.
- Fixed GitHub Issue schema for `PING`, `HEALTH_CHECK`, and `GET_JOB_STATUS`.
- A local-only `MockEngineAdapter` with success, long-running, timeout, unavailable, and failure paths.
- A fixed local `SearchAgentAdapter` for the three metadata-only worker 1.0 operations; it is started only by an explicit local CLI command.
- GitHub REST polling and safe status comments, gated by `REMOTE_COMMANDS_ENABLED=false`.
- A separate-PC GitHub relay is implemented behind independent `REMOTE_SEARCHAGENT_SUBMIT_ENABLED=false` and `REMOTE_SEARCHAGENT_JOBS_ENABLED=false` flags. `REMOTE_COMMANDS_ENABLED=false` remains independent and does not start SearchAgent jobs.

The GitHub REST API version header uses `2026-03-10`, the current supported version at the time this project was prepared. GitHub documents supported versions and conditional request behavior in its [API version guide](https://docs.github.com/en/rest/about-the-rest-api/api-versions) and [REST API best practices](https://docs.github.com/en/rest/using-the-rest-api/best-practices-for-using-the-rest-api).

## Setup on Windows

Use a Python 3.11+ interpreter. The Bridge does not share the SearchAgent environment.

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pytest -q
```

If the `py` launcher is not installed, create the environment using the full path to the intended Python interpreter. Copy `.env.example` to `.env` for local settings. The default database is `data/bridge.sqlite3`; it and `.env` are ignored by Git.

To enable the local SearchAgent worker adapter, set `SEARCHAGENT_PYTHON` to its virtual-environment Python and `SEARCHAGENT_ROOT` to the SearchAgent project directory. Bridge uses the fixed command `python main.py worker`; it does not accept an executable or project path from an Issue. These paths are not shown in health or execution output. Without both settings, `bloomberg_search` stays unavailable.

## Local use

```powershell
.\.venv\Scripts\python.exe main.py --health
.\.venv\Scripts\python.exe main.py --run-mock-job mock.success
.\.venv\Scripts\python.exe main.py --run-mock-job mock.fail
```

Run SearchAgent metadata jobs only from the local CLI:

```powershell
.\.venv\Scripts\python.exe main.py --run-searchagent-job searchagent.plan_query --searchagent-question "Find government bonds"
```

The other fixed job IDs are `searchagent.search_instruments` and `searchagent.search_fields`. The latter requires `--searchagent-product-type` or `--searchagent-field-type`. Bridge stores only execution state and allowlisted status codes; detailed SearchAgent results remain in its own local storage.

The local adapter runs on the same PC as its configured SearchAgent. The separate-PC GitHub relay submits only encrypted fixed metadata jobs and returns safe status only; it does not return candidate lists or detailed results to the control PC. Relay deployment still requires per-PC key and least-privilege token setup, staged verification, and explicit operational authorization.

`--run-mock-job` reports the final execution status. It exits with code `0` only for `SUCCEEDED`; failed and rejected jobs return a nonzero exit code.

With `REMOTE_COMMANDS_ENABLED=false`, default startup and `--once` do not poll GitHub. `--check-github` diagnoses authentication and repository access only; it does not receive or process Issues. Both require a configured token and `GITHUB_REPOSITORY=owner/repository`.

## GitHub setup and command format

The current `bridge-command` channel accepts only its existing control/status commands. The separate SearchAgent relay uses the dedicated `bridge-searchagent-job` label, encrypted Issue body, author allowlist, signature verification, and its own role flag. Setup steps are in the [cross-computer relay runbook](docs/integration/cross-computer-relay-runbook.md). Keep every flag off until staged checks and go-live authorization.

Generate role keys locally on each PC (use separate directories and never copy private-key files):

```powershell
.\.venv\Scripts\python.exe main.py --generate-relay-keys worker --key-directory C:\BridgeKeys
.\.venv\Scripts\python.exe main.py --generate-relay-keys control --key-directory C:\BridgeKeys
```

The control PC needs the worker encryption **public** key and its own signing **private** key. The Bloomberg Desktop PC needs its own encryption **private** key and the submitter signing **public** key. Transfer only public keys using a trusted channel, then set the absolute key paths and role-specific token in that PC's protected `.env`. Keep `REMOTE_SEARCHAGENT_SUBMIT_ENABLED` and `REMOTE_SEARCHAGENT_JOBS_ENABLED` false during setup. Never place a question in command arguments; submit with hidden prompt or piped stdin:

```powershell
Get-Content -Raw .\question.txt | .\.venv\Scripts\python.exe main.py --submit-searchagent-job plan_query
```

Use `main.py --searchagent-status <REQUEST_ID>` on the control PC. Use `main.py --once-searchagent-jobs` for a bounded manual poll on the Bloomberg Desktop PC; the continuous mode is `--poll-searchagent-jobs`.

Each Issue body must contain a JSON object with exactly these keys:

```json
{
  "schema_version": 1,
  "command_id": "69f1d5d3-64b6-4d9d-8c84-3d721c6493a6",
  "command": "PING",
  "issued_at": "2026-10-27T03:00:00Z",
  "expires_at": "2026-10-27T03:05:00Z",
  "parameters": {}
}
```

`GET_JOB_STATUS` takes exactly `{"execution_id": "<UUID>"}` as parameters. Issue authors outside `GITHUB_ALLOWED_USERS` are ignored without a response. Commands must be unexpired, issued no more than five minutes in the future, and expire within 24 hours of issuance. Rejected, duplicate, and completed commands are persisted so polling cannot execute them again. Response comments contain only the v1 status allowlist.

## Scheduling

Run the program with Windows Task Scheduler at a 60-second interval, using the project `.venv` interpreter and working directory. A continuously running process also polls every 60 seconds. Only one Bridge process should run against a database at once. The GitHub Issues API is a low-frequency command channel, not a real-time transport.

## Project map

- `bridge/config.py`: validated environment settings.
- `bridge/github_client.py`: bounded REST calls, ETag reads, status comments, diagnostics.
- `bridge/command_processor.py`: strict schema validation, idempotent command processing, polling.
- `bridge/job_registry.py`, `bridge/job_runner.py`: fixed local job allowlist and execution state.
- `bridge/engine_adapter.py`: v1 adapter contract and Mock/SearchAgent boundaries.
- `bridge/searchagent_request.py`: pure validation and serialization for fixed SearchAgent worker requests.
- `bridge/searchagent_protocol.py`: strict validation of SearchAgent worker 1.0 responses.
- `bridge/searchagent_worker.py`: fixed local subprocess boundary with input/output limits, timeout, and process-tree termination.
- `bridge/proposed_worker_protocol.py`: isolated parser for the historical, non-connected Bridge proposal.
- `bridge/storage.py`: local SQLite schema and restart recovery.
- `docs/README.md`: documentation index and folder map.
