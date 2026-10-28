# Cross-computer GitHub relay plan

**Date:** 2026-10-28  
**Status:** Relay code implemented locally; two-PC GitHub staging and go-live remain pending
**Target:** Run BloombergSearchAgent on a Bloomberg Desktop PC and submit fixed metadata jobs from a separate control PC without a direct network path between them.

## Goal and topology

Both PCs make outbound HTTPS connections to the public GitHub repository `jaeu983-lang/bloomberg-bridge-relay`. They do not connect to each other directly. The user has accepted public visibility for the relay repository.

```text
Control PC                         GitHub                         Bloomberg Desktop PC
Bridge submit CLI  ── encrypted request ──▶ public queue  ◀── Bridge worker/poller
Bridge status view ◀── safe status only ─── public queue  ──▶ SearchAgent worker
                                                                   │
                                                                   └─ Bloomberg Desktop API
```

The Bloomberg Desktop PC owns the SearchAgent Python environment, local SearchAgent database, Bloomberg session, and the worker process. The control PC needs only the Bridge submit/status tools, outbound GitHub access, a submit-only GitHub credential, the worker's request-encryption public key, and the submitter's signing private key. If either PC cannot reach GitHub over HTTPS, this relay cannot carry jobs.

**Public repository consequence:** anyone can read the repository, Issues, encrypted request envelopes, request IDs, submitter account names, job identifiers, timestamps, and safe status comments. Search questions must remain encrypted end-to-end; candidates, detailed results, Bloomberg values, credentials, and keys must never be posted. Since any user may be able to open an Issue, the worker must reject unallowlisted authors or invalid signatures before decryption or execution and must not post status comments on rejected requests.

## V1 product boundary

The deliverable is a two-PC **job submission and safe-status tool**. The control PC can submit one of the three fixed SearchAgent metadata operations and check whether it was accepted, started, and completed. It receives only the request ID, fixed job ID, lifecycle status, allowlisted outcome/error code, and timestamps.

V1 does **not** send candidate lists, detailed SearchAgent results, Bloomberg values, or query text back to the control PC. Detailed SearchAgent data remains on the Bloomberg Desktop PC in SearchAgent's local storage. The GitHub Issue carries the encrypted request; GitHub comments and the control-PC status view carry only the safe status fields above. If control-PC access to detailed results becomes a requirement, that is a separate product/security decision and requires a separately reviewed encrypted result-return contract; it is not part of this implementation plan.

This is a CLI/worker deployment, not a web application. To inspect detailed results, the operator must use an existing SearchAgent local workflow on the Bloomberg Desktop PC; the relay itself does not provide a result viewer or result download.

## Current implementation

- The local adapter still runs SearchAgent only by explicit local CLI.
- A separate relay CLI now submits encrypted, signed requests for only the three worker 1.0 operations and reads safe status by request ID.
- A separate default-off worker CLI polls only the configured label and allowed authors, verifies signature before decryption, validates the fixed worker request, and calls the registered local JobRunner.
- The remote job ledger stores request ID, issue number, ciphertext digest and safe state only; duplicates and previously claimed work are not started again.
- `REMOTE_COMMANDS_ENABLED`, `REMOTE_SEARCHAGENT_SUBMIT_ENABLED`, and `REMOTE_SEARCHAGENT_JOBS_ENABLED` remain false by default. No real GitHub Issue, SearchAgent worker, or Bloomberg API probe has been run as part of implementation.
- The relay is not operationally enabled. Per-PC production key/token setup, a fake-to-live transport staging decision, and explicit go-live authorization remain pending. Raw search questions, SearchAgent results, local paths, tokens, and exception text remain prohibited in GitHub plaintext.

## Proposed job flow

1. The control PC validates one of the fixed operation IDs and its operation-specific arguments. It creates an expiring, uniquely identified request.
2. The control tool encrypts the full request payload for the Bloomberg Desktop PC. GitHub stores ciphertext and non-sensitive routing metadata only.
3. The Bloomberg Desktop PC polls the public repository. It checks the GitHub sender/allowlist, request signature, expiry, duplicate ID, exact schema, operation allowlist, and argument limits before decrypting and validating the request. Public Issues from untrusted authors are ignored/rejected without worker start or reply.
4. The local worker uses the existing fixed adapter command: configured SearchAgent Python plus `<SEARCHAGENT_ROOT>/main.py worker`, `shell=False`, validated JSON stdin, bounded output, and fixed timeouts.
5. SearchAgent records any detailed results only in its local storage on the Bloomberg Desktop PC. The relay returns only request ID, fixed job ID, lifecycle status, allowlisted outcome/error code, and timestamps. It never returns candidate values, counts, raw engine output, query text, local paths, tokens, or exception text.
6. The control PC reads the safe status only. The request is marked consumed on the worker PC so retries cannot execute it twice.

The initial remote job allowlist should contain only `plan_query`, `search_instruments`, and `search_fields`. It should enforce a small result limit, a single concurrent worker, bounded request frequency, a short expiry, and no automatic retry of a started job. Bloomberg market-data, Reference/Historical, Excel export, arbitrary command/path inputs, and active API probes remain excluded.

## Security and configuration decisions

- Use PyNaCl/libsodium. Encrypt the request to the Bloomberg PC with `SealedBox`; sign the canonical outer envelope with a separate Ed25519 key pair. SealedBox uses an ephemeral sender key but does not identify or authenticate that sender, so the worker must verify the detached Ed25519 signature and GitHub author allowlist before decryption. Use distinct encryption and signing key pairs. The control PC holds the worker's encryption public key and submitter signing private key; the Bloomberg PC holds the worker decryption private key and trusted submitter verification public key. Generate production keys only with the implemented role-specific key-generation tool. See [PyNaCl public-key encryption](https://pynacl.readthedocs.io/en/latest/public/) and [digital signatures](https://pynacl.readthedocs.io/en/latest/signing/).
- Use the selected public repository and separate least-privilege GitHub credentials for submission and worker polling. Repository metadata, ciphertext, and safe status are public; store tokens and private keys in protected local configuration, never in source files or Issues.
- Allow only the repository owner account `jaeu983-lang` as the initial submitter. Require both GitHub author allowlisting and a valid Ed25519 signature; add other accounts only after an explicit operator decision.
- Expire unstarted requests after 10 minutes. Close terminal Issues immediately; after 30 days the repository owner manually deletes them. For this personal-account repository, only the owner can delete Issues; the worker token must not have repository administration/delete permissions. The local execution ledger retains request IDs/digests/status needed for replay prevention; it never stores plaintext query or worker output. See [GitHub issue deletion guidance](https://docs.github.com/en/issues/tracking-your-work-with-issues/administering-issues/deleting-an-issue).
- Keep the current `REMOTE_COMMANDS_ENABLED=false` setting for the existing command channel. Add two independent default-off flags: `REMOTE_SEARCHAGENT_SUBMIT_ENABLED=false` on the control PC and `REMOTE_SEARCHAGENT_JOBS_ENABLED=false` on the Bloomberg worker PC. The submit flag only permits creating encrypted Issues; only the worker flag permits polling/decryption/execution. Neither flag enables the other role.
- The Bloomberg Desktop PC must be logged in and able to reach its local Bloomberg Desktop API. GitHub relays the job; it does not provide Bloomberg connectivity.
- `AGENTS.md` records the exact encrypted, fixed-job exception and V1 defaults. Preserve these limits during implementation; do not broaden them to arbitrary Issue commands.

## Implementation and deployment sequence

### A. Policy and protocol design

- Approve the two-PC topology, GitHub repository ownership, outbound HTTPS access, allowed operators, data retention, key lifecycle, and failure handling.
- Specify the encrypted request envelope, authentication/signature scheme, request ID, expiry, replay prevention, status schema, and repository permissions.
- Confirm the repository policy's conditional exception matches this fixed encrypted worker flow. Keep the default-off flags unchanged.

### B. Bridge implementation

- Add a submit command on the control PC that validates and encrypts fixed requests, then publishes them to GitHub.
- Add a worker/poller mode on the Bloomberg Desktop PC that fetches, authenticates, decrypts, validates, de-duplicates, and executes requests through the existing local adapter.
- Add safe status retrieval and acknowledgment. Keep queries and SearchAgent results out of GitHub plaintext.
- Add persistence for request identity and state so restart, duplicate delivery, and retries cannot cause a second execution.

#### Ordered code changes

Implement one layer at a time. Keep every remote-job feature disabled until the end-to-end staging checks pass.

| Order | Files | Change | Completion check |
|---:|---|---|---|
| 0 | `AGENTS.md`, this plan, `github-remote-execution-review.md` | Before code, lock the security/data decisions in section A and confirm the conditional AGENTS exception matches the final contract. Preserve the ban on arbitrary Issue commands and direct Bloomberg API access in Bridge. | The policy and contract name the exact allowed operations, request fields, response fields, host role, and separate default-off flag. |
| 1 | New `bridge/remote_job_crypto.py` | Wrap the chosen maintained crypto library. Provide key generation/import, request encryption for the worker, sender authentication/signature verification, and decryption. The rest of Bridge calls this module rather than implementing primitives. Reject unknown key IDs and malformed or oversized envelopes. | In-memory-key tests prove round-trip, tamper rejection, wrong-key rejection, and signature rejection; no key or plaintext is logged. |
| 2 | New `bridge/remote_job_protocol.py`; reuse `bridge/searchagent_request.py` | Define the exact outer GitHub envelope and inner request schema. Validate UUID, version, issue expiry, fixed job/operation mapping, exact argument keys, question/filter lengths, and result limit. Bind the visible request ID and expiry to the signed/encrypted payload. | Unit tests reject duplicate/unknown keys, replay metadata, expiry, unsupported operations, path/shell fields, booleans as limits, and oversized ciphertext. |
| 3 | `bridge/config.py`, `.env.example` | Add independent `REMOTE_SEARCHAGENT_SUBMIT_ENABLED=false` and `REMOTE_SEARCHAGENT_JOBS_ENABLED=false` flags, separate from `REMOTE_COMMANDS_ENABLED`. Add role-specific GitHub credentials and key configuration: submit role needs issue creation/read status; worker role needs issue read, safe comment write, and close permission. Require SearchAgent paths and worker keys only in worker role. | Config tests cover defaults, role isolation, missing/mismatched settings, relative paths, and prove all three remote flags stay false by default. |
| 4 | `bridge/github_client.py` | Add fixed methods to create a job Issue, list only the fixed job label with bounded pagination, and read/post safe job status. Use existing timeout/retry behavior. The submit path sets a fixed title and label; the worker path never trusts a label as authorization by itself. | Fake-session tests verify endpoints, headers, pagination, request size limits, no raw query in plaintext body, and safe comment projection. |
| 5 | `bridge/storage.py`, new storage tests | Add a schema migration and receipt table keyed by `request_id` with unique `issue_number`, payload digest, lifecycle state, and timestamps. Persist receipt before execution; mark accepted, running, and final states atomically. Do not store plaintext arguments or worker output. | In-memory and migration tests prove duplicate issue/request rejection and recovery of interrupted runs without automatic re-execution. |
| 6 | New `bridge/remote_job_processor.py` | Handle one fetched Issue: check label, open state, allowed author, signed envelope, expiry, digest/replay state, decrypt, validate, map only to the fixed local `JobRunner`, then persist the safe outcome. Reject unsupported data before creating a worker process. | Fake processor tests prove every rejection case avoids worker invocation and public reply, and every accepted request invokes only the registered job. |
| 7 | New `bridge/remote_job_poller.py` | Poll the dedicated job label, process bounded pages sequentially, and publish safe status comments. Implement one-shot and continuous modes with a fixed interval, single-worker lock, graceful shutdown, and bounded GitHub API use. Keep it separate from the current `CommandPoller`. | Fake GitHub tests cover disabled mode, duplicate pages, pagination, restart, comment retry, rate/error handling, and no concurrent execution. |
| 8 | `main.py`, optional new `bridge/submit_cli.py` | Add a control-PC submit/status CLI and a worker-PC poll CLI. The submit CLI reads the question from a concealed prompt or stdin, never a command-line argument; it validates, encrypts, posts, and prints only request/issue IDs. The worker CLI uses the separate flag and worker role settings. Keep `--run-searchagent-job` as the existing local path. Do not make `--once` or `REMOTE_COMMANDS_ENABLED` launch jobs. | CLI tests prove role separation, default-off behavior, stable exit codes, no question/candidate/path/token in argv, stdout, or logs, and no worker start from legacy Issue commands. |
| 9 | `README.md`, `docs/README.md`, `docs/TEST_REPORT.md`, integration docs | Document the two PC roles, local setup, key installation/rotation, GitHub permissions, Task Scheduler command, recovery, and operator runbook. Record the offline test result after all code changes. | A clean setup can be followed on both PCs without undocumented values or copying the SearchAgent environment/database into Bridge. |

#### Request and status contract

Use a dedicated label such as `bridge-searchagent-job`; do not overload `bridge-command`. Keep the Issue title to a request UUID. Store only routing metadata and an authenticated ciphertext envelope in the Issue body. The following inner request is ordinary JSON before encryption; GitHub receives it only as ciphertext:

```json
{
  "schema_version": 1,
  "request_id": "<UUID>",
  "issued_at": "<UTC timestamp>",
  "expires_at": "<UTC timestamp>",
  "job_id": "searchagent.search_instruments",
  "arguments": {
    "question": "<search question>",
    "asset_class": "equity",
    "limit": 3
  }
}
```

The outer envelope contains only protocol version, key ID, request ID, expiry, nonce/ciphertext, and signature. The worker checks GitHub author allowlist and signature before decrypting, then verifies the inner and outer identities match. GitHub status comments and the control-PC status view contain only request ID, lifecycle status, fixed job ID, outcome/error code, and timestamps. They never contain the question, candidate lists, result counts, detailed results, local path, token, or raw worker output. The control PC cannot retrieve SearchAgent's detailed local results in V1.

### C. Offline validation

- Use a fake GitHub transport and fake SearchAgent worker to test encryption/decryption, authentication, invalid schema, expired and replayed requests, unauthorized submitters, result redaction, timeout, output limits, restart recovery, and disabled-by-default behavior.
- Use generated test keys held in memory; never use production keys in tests.
- Run the repository's offline pytest suite and update `docs/TEST_REPORT.md`. Do not contact GitHub or Bloomberg during this phase.

### D. Bloomberg Desktop PC setup

- Install the Bridge repository and its own Python virtual environment on the Bloomberg Desktop PC. Keep it separate from the SearchAgent environment.
- Install the separate BloombergSearchAgent project and its own Python virtual environment on that PC; keep its SQLite database inside its project.
- Configure `SEARCHAGENT_PYTHON` and `SEARCHAGENT_ROOT` locally in Bridge. Do not import SearchAgent modules into Bridge.
- Install a least-privilege worker-side GitHub credential and the worker's protected decryption/authentication keys. Do not copy the worker's private key to the control PC.
- Configure the worker with remote execution disabled. Verify local health and the local adapter before scheduling the GitHub poller.
- Use Windows Task Scheduler only after staging succeeds. Start the worker poller from the Bridge working directory with the Bridge virtual-environment interpreter.

### E. Control PC setup

- Install the Bridge submit/status tool and its own Python environment. It does not need Bloomberg Desktop, the SearchAgent environment, or the SearchAgent database.
- Configure a submit-only GitHub credential and the public encryption/authentication key. Do not copy the Bloomberg Desktop PC's GitHub worker credential or private key to this PC.
- Verify that submitted Issue content contains no plaintext search question or worker result.
- Read the question through a concealed prompt or stdin so it does not appear in command history, process arguments, stdout, or logs.

### F. Staged operation

- First run an encrypted `plan_query` request through GitHub; it must not open a Bloomberg session or database. The control PC receives only safe status, not the generated query plan.
- Then, after explicit operational approval, run one low-limit metadata search through the same relay. Confirm that SearchAgent handles the local operation and GitHub receives only safe status metadata.
- Confirm that duplicate/replayed requests do not re-run, no raw queries/candidates appear in GitHub, and the worker returns to its idle state.
- After staged checks, enable the submit flag only on the control PC and the worker-job flag only on the designated Bloomberg Desktop PC. `REMOTE_COMMANDS_ENABLED` stays false. All flags remain false by default and in development/tests.

## Open decisions before code changes

1. Confirm both computers can make outbound HTTPS calls to the selected GitHub host. As of 2026-10-28, both the user's control PC and Bloomberg Desktop PC passed TCP 443 checks to `github.com` and `api.github.com`. This confirms TCP reachability only; repository authorization and API credential checks remain for staging.
2. Use the selected public repository `jaeu983-lang/bloomberg-bridge-relay`; its public visibility and Issues-enabled setting were verified on 2026-10-28. Initial submitter allowlist is the owner `jaeu983-lang` only.
3. Use PyNaCl/libsodium sealed-box request encryption and a distinct Ed25519 signing key. Generate production keys only after the role-safe key-generation tool is implemented.
4. Use a 10-minute request expiry and delete terminal public Issues after 30 days; the owner account performs deletion.
5. Confirm the conditional `AGENTS.md` exception matches the exact encrypted job flow before code is written.
6. Confirm the Bloomberg Desktop PC's local Bloomberg session and SearchAgent storage are the intended execution target.

## Acceptance criteria

- The control PC and Bloomberg Desktop PC communicate only through the configured public GitHub repository; no inbound firewall rule or peer-to-peer route is required.
- Public exposure of request ciphertext, account names, job IDs, request IDs, timestamps, and safe status is explicitly accepted. No plaintext search question or result data appears publicly.
- The control PC cannot run SearchAgent locally or access its private key. The Bloomberg Desktop PC never receives the submitter's GitHub credential.
- A request with modified ciphertext/signature, unknown author, unsupported operation, extra field, expired timestamp, or duplicate UUID is rejected without worker start.
- GitHub plaintext contains no search question or result data. Bridge logs and comments contain only allowlisted metadata.
- The control-PC product exposes job status only; SearchAgent candidate lists and detailed results remain local to the Bloomberg Desktop PC.
- SearchAgent is launched only by the fixed local worker adapter on the Bloomberg Desktop PC. Its private environment and SQLite data remain separate from Bridge.
- Both remote-job and legacy remote-command flags default to false. Local CLI behavior remains available when GitHub is unavailable.
- Offline tests pass before either real PC is connected. Staging records a successful encrypted `plan_query`, then one separately approved metadata search; no market-data/Reference/Historical request or active probe is used.

Until these decisions are recorded, keep the relay unimplemented and all remote execution flags disabled.
