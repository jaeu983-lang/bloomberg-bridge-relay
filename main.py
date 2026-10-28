"""BloombergBridge command-line entry point."""

from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
import time
from pathlib import Path
from typing import Sequence

from bridge.command_processor import CommandPoller, CommandProcessor
from bridge.config import ConfigurationError, Settings
from bridge.engine_adapter import EngineError, MockEngineAdapter, SearchAgentAdapter
from bridge.github_client import GithubApiError, GithubClient
from bridge.job_registry import JobNotRegistered, JobRegistry
from bridge.job_runner import DuplicateExecution, JobRunner
from bridge.storage import Storage
from bridge.remote_job_crypto import RelayCryptoError, generate_key_files
from bridge.remote_job_protocol import RelayProtocolError, create_envelope
from bridge.remote_job_processor import RemoteJobProcessor
from bridge.remote_job_poller import RemoteJobPoller


def load_env_file(path: Path = Path(".env")) -> None:
    """Load simple KEY=VALUE entries without overriding process environment."""
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        if key:
            os.environ.setdefault(key, value)


def _json_line(payload: dict[str, object]) -> None:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def build_runtime(settings: Settings) -> tuple[Storage, dict[str, object], JobRunner, CommandProcessor]:
    storage = Storage(settings.database_path)
    mock = MockEngineAdapter()
    search_agent = SearchAgentAdapter(
        python_executable=settings.searchagent_python,
        project_root=settings.searchagent_root,
    )
    adapters = {
        mock.ENGINE_ID: mock,
        search_agent.ENGINE_ID: search_agent,
    }
    registry = JobRegistry()
    runner = JobRunner(registry=registry, adapters=adapters, storage=storage)
    processor = CommandProcessor(
        storage=storage,
        allowed_users=settings.github_allowed_users,
        remote_commands_enabled=settings.remote_commands_enabled,
        job_runner=runner,
        adapters=adapters,
    )
    return storage, adapters, runner, processor


def _make_github_client(settings: Settings) -> GithubClient | None:
    if not settings.github_configured:
        return None
    return GithubClient(
        token=settings.github_token,
        repository=settings.github_repository,
        api_url=settings.github_api_url,
        api_version=settings.github_api_version,
        timeout_seconds=settings.request_timeout_seconds,
    )


def main(argv: Sequence[str] | None = None) -> int:
    load_env_file()
    parser = argparse.ArgumentParser(description="BloombergBridge local control plane")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true", help="poll GitHub once when enabled")
    mode.add_argument("--check-github", action="store_true", help="diagnose auth and repository access")
    mode.add_argument("--health", action="store_true", help="show local bridge and adapter health")
    mode.add_argument("--run-mock-job", metavar="JOB_ID", help="run one locally registered mock job")
    mode.add_argument(
        "--run-searchagent-job",
        metavar="JOB_ID",
        help="run one fixed SearchAgent metadata job locally",
    )
    mode.add_argument("--generate-relay-keys", choices=("control", "worker"))
    mode.add_argument("--submit-searchagent-job", choices=("plan_query", "search_instruments", "search_fields"))
    mode.add_argument("--searchagent-status", metavar="REQUEST_ID")
    mode.add_argument("--once-searchagent-jobs", action="store_true")
    mode.add_argument("--poll-searchagent-jobs", action="store_true")
    parser.add_argument("--key-directory")
    parser.add_argument("--asset-class", choices=("equity", "government", "corporate", "index", "currency"))
    parser.add_argument("--product-type")
    parser.add_argument("--field-type")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--searchagent-question", help="local SearchAgent query text")
    parser.add_argument(
        "--searchagent-asset-class",
        choices=("equity", "government", "corporate", "index", "currency"),
    )
    parser.add_argument("--searchagent-product-type", help="local SearchAgent field filter")
    parser.add_argument("--searchagent-field-type", help="local SearchAgent field filter")
    parser.add_argument("--searchagent-limit", type=int)
    args = parser.parse_args(argv)

    if args.key_directory and not args.generate_relay_keys:
        _json_line({"status": "REJECTED", "error_code": "INVALID_REQUEST"})
        return 2
    if args.generate_relay_keys:
        try:
            directory = Path(args.key_directory) if args.key_directory else None
            if directory is None or not directory.is_absolute():
                raise RelayCryptoError("ABSOLUTE_KEY_DIRECTORY_REQUIRED")
            files = generate_key_files(directory, args.generate_relay_keys)
        except RelayCryptoError as exc:
            _json_line({"status": "FAILED", "error_code": str(exc)})
            return 2
        _json_line({"status": "OK", "role": args.generate_relay_keys, "key_files": files})
        return 0

    searchagent_flags = (
        args.searchagent_question,
        args.searchagent_asset_class,
        args.searchagent_product_type,
        args.searchagent_field_type,
        args.searchagent_limit,
    )
    if not args.run_searchagent_job and any(
        value is not None for value in searchagent_flags
    ):
        _json_line({"status": "REJECTED", "error_code": "INVALID_REQUEST"})
        return 2

    try:
        settings = Settings.from_env()
    except ConfigurationError as exc:
        _json_line({"status": "CONFIGURATION_ERROR", "error_code": str(exc)})
        return 2

    if args.submit_searchagent_job:
        if not settings.remote_searchagent_submit_enabled:
            _json_line({"status": "DISABLED", "remote_searchagent_submit_enabled": False})
            return 0
        try:
            question = getpass.getpass("Search question (hidden): ") if sys.stdin.isatty() else sys.stdin.readline().rstrip("\r\n")
            arguments: dict[str, object] = {"question": question}
            if args.asset_class is not None: arguments["asset_class"] = args.asset_class
            if args.product_type is not None: arguments["product_type"] = args.product_type
            if args.field_type is not None: arguments["field_type"] = args.field_type
            if args.limit is not None: arguments["limit"] = args.limit
            if args.submit_searchagent_job == "plan_query" and any((args.asset_class, args.product_type, args.field_type, args.limit is not None)):
                raise RelayProtocolError("INVALID_REQUEST")
            if args.submit_searchagent_job == "search_instruments" and any((args.product_type, args.field_type)):
                raise RelayProtocolError("INVALID_REQUEST")
            if args.submit_searchagent_job == "search_fields" and args.asset_class:
                raise RelayProtocolError("INVALID_REQUEST")
            if not settings.remote_searchagent_worker_public_key_path or not settings.remote_searchagent_signing_private_key_path:
                raise RelayProtocolError("CONFIGURATION_ERROR")
            request_id, body = create_envelope(operation=args.submit_searchagent_job, arguments=arguments,
                worker_key_id=settings.remote_searchagent_worker_key_id,
                signer_key_id=settings.remote_searchagent_signer_key_id,
                worker_public_key=settings.remote_searchagent_worker_public_key_path.read_bytes(),
                signing_private_key=settings.remote_searchagent_signing_private_key_path.read_bytes())
            relay_client = GithubClient(token=settings.remote_searchagent_submit_token,
                repository=settings.remote_searchagent_repository, api_url=settings.github_api_url,
                api_version=settings.github_api_version, timeout_seconds=settings.request_timeout_seconds)
            created = relay_client.create_relay_issue(title=f"Bridge request {request_id}", body=body,
                label=settings.remote_searchagent_label)
            _json_line({"status": "QUEUED", "request_id": request_id, "issue_number": created["number"]})
            return 0
        except (RelayProtocolError, RelayCryptoError, OSError, GithubApiError) as exc:
            if isinstance(exc, (RelayProtocolError, GithubApiError)):
                code = exc.error_code
            elif isinstance(exc, RelayCryptoError):
                code = str(exc)
            else:
                code = "LOCAL_KEY_UNAVAILABLE"
            _json_line({"status": "FAILED", "error_code": code})
            return 1

    if args.searchagent_status:
        try:
            relay_client = GithubClient(token=settings.remote_searchagent_submit_token,
                repository=settings.remote_searchagent_repository, api_url=settings.github_api_url,
                api_version=settings.github_api_version, timeout_seconds=settings.request_timeout_seconds)
            issue = relay_client.find_relay_issue(args.searchagent_status)
            if issue is None:
                _json_line({"status": "NOT_FOUND", "request_id": args.searchagent_status})
                return 1
            state = "QUEUED"
            if issue.get("state") == "closed":
                state = "REJECTED"
            issue_number = issue.get("number")
            if type(issue_number) is int:
                for comment in relay_client.list_issue_comments(issue_number):
                    body = comment.get("body")
                    if isinstance(body, str) and body.startswith("BloombergBridge relay status:"):
                        try:
                            parsed = json.loads(body.split("```json\n", 1)[1].split("\n```", 1)[0])
                            if parsed.get("request_id") == args.searchagent_status and parsed.get("status") in {"QUEUED", "RUNNING", "SUCCEEDED", "FAILED", "REJECTED"}:
                                state = parsed["status"]
                        except (ValueError, IndexError, AttributeError):
                            continue
            _json_line({"status": state, "request_id": args.searchagent_status})
            return 0
        except GithubApiError as exc:
            _json_line({"status": "FAILED", "error_code": exc.error_code})
            return 1

    try:
        storage, adapters, runner, processor = build_runtime(settings)
    except OSError:
        _json_line({"status": "STORAGE_ERROR", "error_code": "DATABASE_UNAVAILABLE"})
        return 2

    if args.once_searchagent_jobs or args.poll_searchagent_jobs:
        if not settings.remote_searchagent_jobs_enabled:
            _json_line({"status": "DISABLED", "remote_searchagent_jobs_enabled": False})
            return 0
        try:
            if settings.remote_searchagent_worker_private_key_path is None or settings.remote_searchagent_signing_public_key_path is None:
                raise OSError("worker key configuration missing")
            relay_client = GithubClient(token=settings.remote_searchagent_worker_token,
                repository=settings.remote_searchagent_repository, api_url=settings.github_api_url,
                api_version=settings.github_api_version, timeout_seconds=settings.request_timeout_seconds)
            remote_processor = RemoteJobProcessor(client=relay_client, storage=storage, runner=runner,
                allowed_users=settings.remote_searchagent_allowed_users,
                worker_key_id=settings.remote_searchagent_worker_key_id,
                signer_key_id=settings.remote_searchagent_signer_key_id,
                signing_public_key=settings.remote_searchagent_signing_public_key_path.read_bytes(),
                worker_private_key=settings.remote_searchagent_worker_private_key_path.read_bytes())
            remote_poller = RemoteJobPoller(client=relay_client, processor=remote_processor,
                label=settings.remote_searchagent_label, enabled=True)
            while True:
                _json_line(remote_poller.poll_once())
                if args.once_searchagent_jobs:
                    return 0
                time.sleep(settings.poll_interval_seconds)
        except (GithubApiError, OSError) as exc:
            _json_line({"status": "FAILED", "error_code": exc.error_code if isinstance(exc, GithubApiError) else "LOCAL_CONFIGURATION_ERROR"})
            return 1

    if args.health:
        _json_line(
            {
                "status": "OK",
                "storage": "OK",
                "remote_commands_enabled": settings.remote_commands_enabled,
                "engines": [
                    {"engine_id": engine_id, "state": adapter.get_status().state.value}
                    for engine_id, adapter in adapters.items()
                ],
            }
        )
        return 0

    if args.run_mock_job:
        try:
            execution = runner.run_registered_job(args.run_mock_job)
            status = execution.get("execution_status")
            if status not in {"SUCCEEDED", "FAILED", "INTERRUPTED"}:
                status = "FAILED"
            _json_line({"status": status, "execution": execution})
            return 0 if status == "SUCCEEDED" else 1
        except JobNotRegistered:
            _json_line({"status": "REJECTED", "error_code": "JOB_NOT_REGISTERED"})
            return 2
        except EngineError as exc:
            _json_line({"status": "FAILED", "error_code": exc.error_code})
            return 1
        except DuplicateExecution:
            _json_line({"status": "REJECTED", "error_code": "WORKER_BUSY"})
            return 1
        except Exception:
            _json_line({"status": "FAILED", "error_code": "JOB_EXECUTION_ERROR"})
            return 1

    if args.run_searchagent_job:
        job_id = args.run_searchagent_job
        adapter = adapters.get("bloomberg_search")
        job_definition = getattr(adapter, "JOBS", {}).get(job_id)
        operation = job_definition[1] if job_definition is not None else None
        if operation is None or not args.searchagent_question:
            _json_line({"status": "REJECTED", "error_code": "INVALID_REQUEST"})
            return 2

        arguments: dict[str, object] = {"question": args.searchagent_question}
        if args.searchagent_asset_class is not None:
            arguments["asset_class"] = args.searchagent_asset_class
        if args.searchagent_product_type is not None:
            arguments["product_type"] = args.searchagent_product_type
        if args.searchagent_field_type is not None:
            arguments["field_type"] = args.searchagent_field_type
        if args.searchagent_limit is not None:
            arguments["limit"] = args.searchagent_limit
        try:
            runner.registry.get(job_id)
            execution = runner.run_registered_job(job_id, arguments=arguments)
            status = execution.get("execution_status")
            if status not in {"SUCCEEDED", "FAILED", "INTERRUPTED"}:
                status = "FAILED"
            _json_line({"status": status, "execution": execution})
            return 0 if status == "SUCCEEDED" else 1
        except JobNotRegistered:
            _json_line({"status": "REJECTED", "error_code": "JOB_NOT_REGISTERED"})
            return 2
        except EngineError as exc:
            _json_line({"status": "FAILED", "error_code": exc.error_code})
            return 1
        except DuplicateExecution:
            _json_line({"status": "REJECTED", "error_code": "WORKER_BUSY"})
            return 1
        except Exception:
            _json_line({"status": "FAILED", "error_code": "JOB_EXECUTION_ERROR"})
            return 1

    client = _make_github_client(settings)
    if args.check_github:
        if client is None:
            _json_line({"status": "SKIPPED", "error_code": "GITHUB_CONFIGURATION_MISSING"})
            return 2
        try:
            result = client.diagnose()
        except GithubApiError as exc:
            _json_line({"status": "FAILED", "error_code": exc.error_code})
            return 1
        _json_line({"status": "OK", **result})
        return 0 if result["authentication"] == "OK" and result["repository_access"] == "OK" else 1

    if not settings.remote_commands_enabled:
        _json_line({"status": "DISABLED", "remote_commands_enabled": False})
        return 0
    if client is None:
        _json_line({"status": "CONFIGURATION_ERROR", "error_code": "GITHUB_CONFIGURATION_MISSING"})
        return 2

    poller = CommandPoller(
        client=client,
        processor=processor,
        storage=storage,
        label=settings.github_command_label,
        remote_commands_enabled=settings.remote_commands_enabled,
    )
    if args.once:
        try:
            _json_line(poller.poll_once())
            return 0
        except GithubApiError as exc:
            _json_line({"status": "FAILED", "error_code": exc.error_code})
            return 1

    while True:
        try:
            _json_line(poller.poll_once())
        except GithubApiError as exc:
            # The code is safe to log; never print response bodies or request headers.
            _json_line({"status": "DEFERRED", "error_code": exc.error_code})
        time.sleep(settings.poll_interval_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
