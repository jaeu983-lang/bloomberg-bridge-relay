# BloombergBridge architecture

## Responsibility boundary

BloombergBridge is the local control plane. It validates commands, stores deduplication and status records, coordinates fixed local jobs, and reports limited execution metadata. SearchAgent and future workers retain their own domain logic, Bloomberg sessions, local data stores, and result validation.

```text
GitHub Issues API
       │ labeled open Issues (optional; feature flag off by default)
       ▼
GithubClient ──► CommandPoller ──► CommandProcessor
                                     │
                         SQLite ◄────┤
                                     │ local-only job path
                                     ▼
                               JobRegistry
                                     ▼
                                JobRunner
                                     ▼
                     EngineAdapter v1 boundary
                      ├─ MockEngineAdapter
                      └─ SearchAgentAdapter ──► fixed local SearchAgent worker
                           (local CLI only; configured paths; no Issue launch)
```

`REMOTE_COMMANDS_ENABLED` defaults to `false`. In that state, the normal process does not poll Issues and the processor refuses to handle Issue commands. `--check-github` is an explicit diagnostic path that only checks `/user` and `/repos/{owner}/{repo}`; it does not consume commands.

## Command path

1. Fetch open Issues carrying the configured label with `state=open`, oldest first, 100 per page. Ignore pull requests returned by the shared Issues endpoint.
2. Apply a conditional request using the saved ETag for that page. Persist the next ETag only after the returned page is processed. A `304` page does not stop pagination when a later page already has a cached ETag; this lets the poller check changes beyond page 1 without re-reading unchanged pages.
3. Require a configured author allowlist, an exact v1 JSON object, UUID command ID, supported command, timezone-aware timestamps, a non-expired command, and command-specific fixed parameters.
4. Insert the Issue and command ID into SQLite before command handling. The unique command ID and Issue number prevent reprocessing across later polls.
5. Persist a safe response before writing an Issue comment. If comment delivery fails, the next poll retries the persisted response without rerunning the command.
6. Comments contain only command ID, outcome, timestamps, engine/job identifiers, and registered execution status.

The only remote operations are `PING`, `HEALTH_CHECK`, and `GET_JOB_STATUS`. `RUN_REGISTERED_JOB` and the SearchAgent task types are not exposed by the command parser. The local CLI can start fixed mock jobs and three fixed SearchAgent worker 1.0 metadata jobs. SearchAgent operation inputs are validated and sent as UTF-8 JSON on stdin.

## Persistence and recovery

SQLite stores command idempotency, status responses, execution identifiers, and execution states. Database files, WAL files, `.env`, and logs are ignored by Git. A process restart converts any remaining `RUNNING` execution to `INTERRUPTED` with `PROCESS_RESTARTED`; it never reissues that execution. A command left in `PROCESSING` becomes `FAILED / PROCESS_INTERRUPTED`. There is no automatic retry of an interrupted job.

Initial operation is one synchronous worker and one command at a time. The local mock runner also prevents simultaneous execution of the same job ID. A future multi-process scheduler would need a process-level singleton lock before recovery is run.

## GitHub behavior

- API calls use HTTPS, a fixed API version, explicit timeout, and no response-body logging.
- Issue-list reads use ETag and `If-None-Match`.
- Timeouts, connection failures, and server errors use a bounded retry count.
- `Retry-After` and rate-limit reset headers take priority for 403/429 responses. If the requested wait exceeds the run's configured wait window, the client defers instead of retrying early.
- Authentication and permission failures stop immediately.
- Status comment delivery can be ambiguous if the server accepts a POST but the network drops its response. Such a transport ambiguity may create a duplicate status comment on a later poll; it cannot re-execute the command.
- The initial implementation reads pages of up to 100 Issues and continues while the API returns a full page.

## Data protection

There is no Bloomberg API client in Bridge. The GitHub response projection is an allowlist; it never contains market data, engine output, issue body text, local file paths, access tokens, or raw exception text. SearchAgent worker paths come from local configuration, never an Issue. The worker receives a minimal OS environment without Bridge credentials; its output is size-limited and validated before Bridge stores safe status metadata. SearchAgent remains responsible for storing search and market-data results locally.

## Planned cross-computer relay (not implemented)

For the two-PC deployment, the Bloomberg Desktop PC will host Bridge's worker poller and the separate SearchAgent runtime. A control PC will submit fixed requests through the public GitHub repository `jaeu983-lang/bloomberg-bridge-relay`. Both PCs need outbound HTTPS access to GitHub; they do not need a direct route to each other. Repository Issues, encrypted request envelopes, account names, job identifiers, timestamps, and safe status metadata are publicly visible.

The planned V1 product is a CLI/worker job-submission and safe-status tool. It sends an authenticated encrypted request through GitHub and returns only request ID, fixed job ID, lifecycle status, allowlisted outcome/error code, and timestamps. It does not return candidate lists, detailed SearchAgent results, counts, or Bloomberg values to the control PC; those remain on the Bloomberg Desktop PC in SearchAgent's local storage. This is a design target only: the current Issue parser does not start jobs, and `REMOTE_COMMANDS_ENABLED` does not enable the relay. See [the cross-computer relay plan](integration/cross-computer-github-relay-plan.md).
