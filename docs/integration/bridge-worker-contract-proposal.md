# Engine Integration Contract v1 — proposal

**Status:** Historical Bridge-side proposal, not the SearchAgent worker contract. SearchAgent implements worker contract `1.0`; the active Bridge adapter uses that contract and does not use this proposal's envelope. See [the current combined integration plan](plan.md).

## 1. Ownership boundary

BloombergBridge is the Control Plane. It owns command validation, the local job allowlist, execution IDs, execution status, deduplication, restart recovery, and the safe status projection.

BloombergSearchAgent is the Data Plane. It owns Bloomberg API sessions and requests, instrument/field validation, entitlement and freshness decisions, local SQLite data, and Excel generation. Bridge must not import SearchAgent modules, share its Python environment or SQLite file, read its workbook, or forward its result data to GitHub.

## 2. Engine identity and handshake

The initial proposed identity is `engine_id = "bloomberg_search"` and `contract_version = 1`. The worker reports a documented `engine_version` string. Bridge requires an exact engine ID and supported contract version before executing any job; an incompatible version fails closed with `ENGINE_CONTRACT_MISMATCH`.

The worker's machine-readable `capabilities` response should contain only:

```json
{
  "contract_version": 1,
  "engine_id": "bloomberg_search",
  "engine_version": "<worker release>",
  "state": "READY",
  "can_execute": true,
  "supported_jobs": [
    {
      "job_id": "search.saved.<registered_alias>",
      "job_type": "SAVED_QUERY",
      "input_schema_version": 1
    }
  ]
}
```

`state` is one of `READY`, `RUNNING`, `STOPPED`, `ERROR`, or `UNAVAILABLE`. A separate versioned status response should contain only:

```json
{
  "contract_version": 1,
  "engine_id": "bloomberg_search",
  "engine_version": "<worker release>",
  "state": "READY",
  "last_started_at": null,
  "last_finished_at": null,
  "last_error_code": null
}
```

Status timestamps are nullable ISO-8601 UTC values; a finish timestamp requires a start timestamp and cannot precede it. `last_error_code` is nullable and must use the finite error-code allowlist below. Neither response contains Bloomberg values, account data, file paths, or raw diagnostics.

SearchAgent already documents a user-facing `capabilities` CLI command. That does not by itself satisfy this handshake: its machine schema, contract version, worker version, and supported job registry still require explicit definition and tests.

## 3. Registered jobs and input schemas

Job IDs are fixed local identifiers matched against both a Bridge registry and a SearchAgent-owned registry. The example `search.saved.<registered_alias>` is a naming proposal, not a currently supported SearchAgent job.

For the initial v1 contract, jobs accept no dynamic parameters:

```json
{
  "contract_version": 1,
  "execution_id": "<Bridge-generated UUID>",
  "job_id": "search.saved.<registered_alias>",
  "parameters": {}
}
```

Search criteria, security/field selection, date ranges, freshness policy, and export behavior should initially reside in SearchAgent-owned local job configuration. An Issue cannot create or edit that configuration.

If a later job needs dynamic inputs, its owner must publish a versioned per-job schema before enabling it. The schema must reject additional properties and define exact types, required keys, enums, ranges, maximum string and payload sizes, and date constraints. Only that job's approved fields may be transmitted as JSON data. No schema may accept an executable, shell fragment, CLI option string, interpreter, working directory, or output path. A change to the v1 empty-parameter rule requires an explicit contract-version decision.

## 4. Execution request, response, and identifiers

Bridge creates and persists an execution UUID before launching a worker. The worker must echo that same `execution_id`, `job_id`, `engine_id`, and `contract_version`. A mismatch rejects the result with `ENGINE_INVALID_RESULT`.

The worker emits one JSON envelope and no result payload:

```json
{
  "contract_version": 1,
  "engine_id": "bloomberg_search",
  "engine_version": "<worker release>",
  "execution_id": "<same UUID>",
  "job_id": "search.saved.<registered_alias>",
  "execution_status": "SUCCEEDED",
  "started_at": "<ISO-8601 UTC>",
  "finished_at": "<ISO-8601 UTC>",
  "error_code": null,
  "outcome_code": null
}
```

`execution_status` is `SUCCEEDED`, `FAILED`, or `INTERRUPTED`. A completed search may have an allowlisted `outcome_code` such as `AMBIGUOUS`, `NOT_FOUND`, `ENTITLEMENT_DENIED`, `STALE`, `FRESHNESS_UNVERIFIED`, or `UNSUPPORTED_SCREENING`. Bridge treats that code as opaque metadata; SearchAgent remains responsible for its meaning.

Bridge retains only execution metadata locally. SearchAgent retains any request history and data in its own local store. Neither the worker envelope nor a GitHub comment contains Bloomberg values, candidate lists, workbook contents, or a workbook/database path.

## 5. Process, timeout, and exit codes

Bridge configuration, not Issue content, selects the SearchAgent interpreter and project working directory. Bridge invokes a fixed worker command with an argument vector and `shell=False`. It does not translate Issue text into CLI arguments. If a future schema permits dynamic data, Bridge sends the validated JSON request on stdin.

Every registered job has a local fixed timeout. SearchAgent keeps its own bounded DAPI request timeouts; Bridge enforces an outer process timeout and terminates the worker process tree if necessary. The timeout is not an Issue parameter.

Proposed exit codes:

| Exit code | Meaning |
|---:|---|
| `0` | Valid `SUCCEEDED` envelope |
| `10` | Request rejected or job not registered |
| `20` | Job failed with a stable error code |
| `30` | Contract or version mismatch |
| Bridge timeout | Bridge records `FAILED / TIMEOUT` after terminating the worker |

Exit code and envelope status must agree. Missing, oversized, malformed, or contradictory output becomes `WORKER_INVALID_RESPONSE`. Raw stdout/stderr, exception text, and stack traces are never copied to logs or GitHub. If Bridge restarts during execution, it marks the record `INTERRUPTED` and does not automatically rerun it.

## 6. Stable error and outcome codes

The worker may return only codes from a finite, reviewed allowlist. Proposed common execution errors are `JOB_NOT_REGISTERED`, `INVALID_REQUEST`, `ENGINE_UNAVAILABLE`, `ENGINE_BUSY`, `ENGINE_CONTRACT_MISMATCH`, `ENGINE_INVALID_RESULT`, `TIMEOUT`, `ENGINE_ERROR`, `WORKER_INVALID_RESPONSE`, and `PROCESS_RESTARTED`.

Search outcome codes are separate from execution errors. Their exact set and mapping must be agreed with the SearchAgent owner. Exception messages, raw API error text, stack traces, and data values are never error codes.

## 7. Issue boundary and safe projection

Current remote Issue commands remain only `PING`, `HEALTH_CHECK`, and `GET_JOB_STATUS`. A future job command requires separate operational authorization and a locally enabled job registry.

Before any future launch, Bridge must validate the author, exact command schema, UUID, expiry, registered job ID, and that job's input schema. Issue data cannot select an executable, path, interpreter, shell command, arbitrary argument list, or output destination. Free-form query input is not part of v1.

GitHub may receive only command/execution UUIDs, engine and registered job IDs, contract/engine versions, execution state, timestamps, and allowlisted error/outcome codes. Never send security/field results, query echoes, raw Bloomberg responses or values, Excel contents or paths, local database paths, environment values, tokens, worker output, or exception text.

## 8. Offline acceptance criteria

- Mock adapter success, long-running, failure, unavailable, timeout, duplicate, and restart-recovery behavior passes without GitHub or Bloomberg access.
- A fake worker tests capabilities handshake, contract mismatch, unknown job, invalid/extra parameters, malformed envelope, exit-code mismatch, and safe error-code allowlisting.
- Timeout tests verify process-tree termination and a `TIMEOUT` record. Restart tests verify `INTERRUPTED` and no automatic resubmission.
- Repeated execution IDs are rejected. Bridge and SearchAgent keep separate processes, environments, and SQLite files.
- Tests scan Bridge responses and logs for synthetic sentinel values, raw stdout/stderr, paths, tokens, and exception text.
- `REMOTE_COMMANDS_ENABLED` remains false by default and stays disabled in tests. No Issue can execute a SearchAgent job until a separately approved step.

## 9. Implementation status

The historical parser remains isolated and unconnected. The fixed registry now includes three local SearchAgent metadata jobs. `SearchAgentAdapter` invokes only the configured Python executable and `main.py worker`, with `shell=False`, bounded stdin/stdout, timeout termination, and contract 1.0 validation. Fake-worker tests validate the process boundary; no SearchAgent worker or Bloomberg request was run during adapter implementation. Issue commands still cannot start these jobs.
