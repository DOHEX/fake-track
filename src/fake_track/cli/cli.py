import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import NoReturn, TypedDict

import typer

from fake_track.core.client import ApiError, ensure_authenticated_client
from fake_track.core.config import ConfigError, CryptoSettings, Settings
from fake_track.core.crypto import aes_encrypt
from fake_track.core.payloads import build_counts_payload
from fake_track.core.records import annotate_records, filter_records
from fake_track.core.utils import default_track_image_path, mask_phone
from fake_track.core.workflow import RunExecutionOptions, RunReport, RunWorkflow
from fake_track.web import serve as run_web_server

from .output import (
    _build_multi_report,
    _error_report,
    _print_counts,
    _print_error_report,
    _print_multi_run_summary,
    _print_record_list,
    _print_report,
    _try_write_error_report,
    _write_multi_report_file,
    _write_report_file,
    console,
    error_console,
)

app = typer.Typer(help="fake-track campus run API test tool")


@dataclass(frozen=True)
class AccountContext:
    index: int
    settings: Settings
    label: str
    slug: str


@dataclass(frozen=True)
class AccountRunResult:
    account: AccountContext
    report: RunReport


def _with_account_suffix(path: Path, slug: str) -> Path:
    if not slug:
        return path
    return path.with_name(f"{path.stem}-{slug}{path.suffix}")


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", value).strip("-").lower()
    return slug


def _account_display_label(settings: Settings, index: int) -> str:
    name = settings.account_name or f"account-{index}"
    return f"{name} ({mask_phone(settings.phone)})"


def _account_slug(settings: Settings, index: int) -> str:
    base = settings.account_name or f"account-{index}"
    slug = _slugify(base)
    if not slug:
        slug = f"account-{index}"
    digits = re.sub(r"\D", "", settings.phone)
    suffix = digits[-4:] if digits else ""
    return f"{slug}-{suffix}" if suffix else slug


def _build_account_contexts(settings_list: list[Settings]) -> list[AccountContext]:
    contexts: list[AccountContext] = []
    for index, settings in enumerate(settings_list, start=1):
        contexts.append(
            AccountContext(
                index=index,
                settings=settings,
                label=_account_display_label(settings, index),
                slug=_account_slug(settings, index),
            )
        )
    return contexts


def _select_accounts(
    contexts: list[AccountContext],
    selectors: list[str] | None,
) -> list[AccountContext]:
    if not selectors:
        return contexts

    selected: list[AccountContext] = []
    missing: list[str] = []
    for selector in selectors:
        selector_text = selector.strip()
        if not selector_text:
            continue

        match: AccountContext | None = None
        if selector_text.isdigit():
            index = int(selector_text)
            match = next((item for item in contexts if item.index == index), None)
        else:
            matches = [
                item
                for item in contexts
                if item.settings.account_name
                and item.settings.account_name.lower() == selector_text.lower()
            ]
            if len(matches) > 1:
                raise typer.BadParameter(
                    f"Account name '{selector_text}' is not unique."
                )
            match = matches[0] if matches else None

        if match is None:
            missing.append(selector_text)
            continue

        if match not in selected:
            selected.append(match)

    if missing:
        available = ", ".join(
            item.settings.account_name or str(item.index) for item in contexts
        )
        raise typer.BadParameter(
            f"Unknown account: {', '.join(missing)}. Available: {available}"
        )

    return selected


def _progress_printer(
    enabled: bool,
    prefix: str | None = None,
    lock: threading.Lock | None = None,
):
    if not enabled:
        return None

    def progress(message: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        text = f"[cyan][{stamp}][/cyan] {message}"
        if prefix:
            text = f"[cyan][{stamp}][/cyan] [{prefix}] {message}"
        if lock:
            with lock:
                console.print(text)
        else:
            console.print(text)

    return progress


def _override_output_report_path(
    settings: Settings,
    report_path: str | None,
) -> Settings:
    if settings.output.report_path == report_path:
        return settings
    output = settings.output.model_copy(update={"report_path": report_path})
    return settings.model_copy(update={"output": output})


def _resolve_track_image_path(
    track_image: bool,
    track_image_path: Path | None,
    account: AccountContext,
    multi_run: bool,
) -> str | None:
    if track_image_path is None and not track_image:
        return None

    base = track_image_path or default_track_image_path()
    if multi_run:
        base = _with_account_suffix(base, account.slug)
    return str(base)


def _handle_command_error(command: str, exc: Exception, json_output: bool) -> NoReturn:
    message = str(exc)
    if json_output:
        _print_report(_error_report(message), json_output=True, title="")
    else:
        _print_error_report(
            _error_report(message),
            title=f"{command} failed",
            output_console=error_console,
        )
    raise typer.Exit(1) from exc


def _load_accounts(
    command: str,
    json_output: bool,
    report_path: Path | None = None,
    selectors: list[str] | None = None,
) -> list[AccountContext]:
    try:
        settings_list = Settings.load_all()
    except ConfigError as exc:
        _try_write_error_report(report_path, str(exc))
        _handle_command_error(command, exc, json_output)

    contexts = _build_account_contexts(settings_list)
    return _select_accounts(contexts, selectors)


@app.command()
def encrypt(
    text: str = typer.Argument(..., help="Plain text to encrypt"),
) -> None:
    """Encrypt text with the same iv:cipher format used by the mini-app."""
    try:
        settings = CryptoSettings.from_env()
    except ConfigError as exc:
        error_console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc

    console.print(aes_encrypt(text, settings.run_key))


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", "--host", help="Bind address."),
    port: int = typer.Option(8000, "--port", help="Port to listen on."),
    reload: bool = typer.Option(False, "--reload", help="Enable auto-reload."),
    log_level: str = typer.Option("info", "--log-level", help="Uvicorn log level."),
    on_air: str | None = typer.Option(
        None,
        "--on-air",
        help="Share via NiceGUI On Air. Pass a token for a fixed URL, or 'true' for a random 1-hour URL.",
    ),
) -> None:
    """Start the local web UI."""
    on_air_value: str | bool | None = (
        True if on_air and on_air.lower() == "true" else on_air
    )
    run_web_server(
        host=host,
        port=port,
        reload=reload,
        log_level=log_level,
        on_air=on_air_value,
    )


@app.command("run")
def run_once(
    json_output: bool = typer.Option(
        False,
        "--json-output",
        help="Print full JSON report instead of progress logs and summary table.",
    ),
    track_image: bool = typer.Option(
        False,
        "--track-image",
        help="Save generated track overlay image to .local/debug-images.",
    ),
    track_image_path: Path | None = typer.Option(
        None,
        "--track-image-path",
        help="Save generated track overlay image to a custom path.",
    ),
    report_path: Path | None = typer.Option(
        None,
        "--report-path",
        help="Write the full JSON report to a file while keeping console logs.",
    ),
    skip_wait: bool | None = typer.Option(
        None,
        "--skip-wait/--no-skip-wait",
        help="Skip the simulated run-duration wait before submit.",
    ),
    force_submit: bool | None = typer.Option(
        None,
        "--force-submit/--no-force-submit",
        help="Continue update/upload even when checkRecord rejects the payload.",
    ),
    ignore_target_met: bool | None = typer.Option(
        None,
        "--ignore-target-met/--no-ignore-target-met",
        help="Run even when the current target count is already met.",
    ),
    account: list[str] = typer.Option(
        None,
        "--account",
        help="Account name or index from fake-track.toml. Can be repeated.",
    ),
) -> None:
    """Run one test cycle."""
    accounts = _load_accounts(
        "run",
        json_output,
        report_path=report_path,
        selectors=account,
    )
    if not accounts:
        raise typer.BadParameter("No account selected.")

    multi_run = len(accounts) > 1
    if multi_run:
        lock = threading.Lock()
        results: list[AccountRunResult] = []

        def _run_account(context: AccountContext) -> AccountRunResult:
            settings = context.settings
            if settings.output.report_path:
                settings = _override_output_report_path(settings, None)

            image_path = _resolve_track_image_path(
                track_image=track_image,
                track_image_path=track_image_path,
                account=context,
                multi_run=True,
            )
            workflow = RunWorkflow(settings)
            run_options = RunExecutionOptions(
                skip_submit_wait=skip_wait
                if skip_wait is not None
                else settings.skip_wait,
                force_submit=force_submit
                if force_submit is not None
                else settings.force_submit,
                ignore_target_met=ignore_target_met
                if ignore_target_met is not None
                else settings.ignore_target_met,
                track_image_path=image_path,
                disable_progress=True,
            )
            progress = _progress_printer(
                enabled=not json_output,
                prefix=context.label,
                lock=lock,
            )
            try:
                report = workflow.run_full(progress=progress, options=run_options)
            except Exception as exc:  # pylint: disable=broad-except
                report = _error_report(str(exc))
            return AccountRunResult(account=context, report=report)

        max_workers = min(4, len(accounts))
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [executor.submit(_run_account, ctx) for ctx in accounts]
            for future in as_completed(futures):
                results.append(future.result())

        results.sort(key=lambda item: item.account.index)
        multi_report = _build_multi_report(results)

        if report_path is not None:
            try:
                _write_multi_report_file(multi_report, report_path)
            except OSError as exc:
                _handle_command_error("run", exc, json_output)

        if json_output:
            typer.echo(json.dumps(multi_report, ensure_ascii=False, indent=2))
        else:
            _print_multi_run_summary(results)

        if not multi_report.get("success", False):
            raise typer.Exit(1)
        return

    account_context = accounts[0]
    settings = account_context.settings
    image_path = _resolve_track_image_path(
        track_image=track_image,
        track_image_path=track_image_path,
        account=account_context,
        multi_run=False,
    )

    workflow = RunWorkflow(settings)
    run_options = RunExecutionOptions(
        skip_submit_wait=skip_wait if skip_wait is not None else settings.skip_wait,
        force_submit=force_submit
        if force_submit is not None
        else settings.force_submit,
        ignore_target_met=ignore_target_met
        if ignore_target_met is not None
        else settings.ignore_target_met,
        track_image_path=image_path,
        disable_progress=False,
    )

    try:
        report = workflow.run_full(
            progress=_progress_printer(enabled=not json_output),
            options=run_options,
        )
    except Exception as exc:  # pylint: disable=broad-except
        _try_write_error_report(report_path, str(exc))
        _handle_command_error("run", exc, json_output)

    if report_path is not None:
        try:
            _write_report_file(report, report_path)
        except OSError as exc:
            _handle_command_error("run", exc, json_output)

    _print_report(report, json_output=json_output, title="Run Summary")
    if not report.success:
        raise typer.Exit(1)


class _RecordListPayload(TypedDict):
    list: list[dict[str, object]]
    page: int
    total: int


def _build_record_list_payload(resp_data: object) -> _RecordListPayload:
    if not isinstance(resp_data, dict):
        raise ApiError(
            f"Record list response data is not a dict: {type(resp_data).__name__}"
        )
    raw = resp_data.get("list", [])
    records = annotate_records([dict(r) for r in raw])  # type: ignore[arg-type]
    return {
        "list": records,
        "page": int(resp_data.get("page", 1)),
        "total": int(resp_data.get("total", 0)),
    }


@app.command()
def counts(
    json_output: bool = typer.Option(
        False,
        "--json-output",
        help="Print JSON instead of a summary table.",
    ),
    account: list[str] = typer.Option(
        None,
        "--account",
        help="Account name or index from fake-track.toml. Can be repeated.",
    ),
) -> None:
    """Show current completed run target counts."""
    accounts = _load_accounts("counts", json_output, selectors=account)
    if not accounts:
        raise typer.BadParameter("No account selected.")

    failed = False
    results: list[dict[str, object]] = []
    for context in accounts:
        try:
            client, student_id = ensure_authenticated_client(context.settings)
            run_counts = client.fetch_run_counts(student_id)
            counts = build_counts_payload(student_id, run_counts.data)
        except Exception as exc:  # pylint: disable=broad-except
            failed = True
            if json_output:
                results.append({"account": context.label, "error": str(exc).strip()})
                continue
            error_console.print(f"[yellow]warning:[/yellow] {context.label}: {exc}")
            continue

        if json_output:
            results.append(
                {
                    "account": context.label,
                    "counts": {
                        "student_id": student_id,
                        "morning": counts.morning,
                        "normal": counts.normal,
                        "effective": counts.effective,
                        "completed_target_count": counts.effective,
                        "target_effective": counts.target_effective,
                        "target_met": counts.target_effective > 0
                        and counts.effective >= counts.target_effective,
                    },
                }
            )
        else:
            if len(accounts) > 1:
                console.print(f"Account: {context.label}")
            _print_counts(counts, json_output=False, student_id=student_id)

    if json_output and len(accounts) > 1:
        typer.echo(json.dumps(results, ensure_ascii=False, indent=2))
    elif json_output and len(accounts) == 1 and results:
        payload = results[0]
        if "counts" in payload:
            typer.echo(json.dumps(payload["counts"], ensure_ascii=False, indent=2))
        else:
            typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))

    if failed:
        raise typer.Exit(1)


@app.command()
def recordlist(
    page: int = typer.Option(1, "--page", help="Page number for pagination."),
    size: int = typer.Option(10, "--size", help="Number of records per page."),
    json_output: bool = typer.Option(
        False,
        "--json-output",
        help="Print JSON instead of a summary table.",
    ),
    run_type: str = typer.Option(
        "",
        "--run-type",
        help="Filter by run type: morning, normal.",
    ),
    status: str = typer.Option(
        "",
        "--status",
        help="Filter by validity: valid, invalid.",
    ),
    semester: str = typer.Option(
        "",
        "--semester",
        help='Filter by semester, e.g. "2025-2026 第二学期".',
    ),
    account: list[str] = typer.Option(
        None,
        "--account",
        help="Account name or index from fake-track.toml. Can be repeated.",
    ),
) -> None:
    """Show running records for one or more accounts."""
    accounts = _load_accounts("recordlist", json_output, selectors=account)
    if not accounts:
        raise typer.BadParameter("No account selected.")

    # build filter tag for display
    tags: list[str] = []
    if run_type:
        tags.append({"morning": "晨跑", "normal": "普跑"}.get(run_type, run_type))
    if status:
        tags.append({"valid": "达标", "invalid": "无效"}.get(status, status))
    if semester:
        tags.append(semester)
    filter_tags = " · ".join(tags)

    failed = False
    results: list[dict[str, object]] = []

    for context in accounts:
        try:
            client, _ = ensure_authenticated_client(context.settings)
            resp = client.fetch_record_list(page=page, size=size)
            payload = _build_record_list_payload(resp.data)
        except Exception as exc:  # pylint: disable=broad-except
            failed = True
            if json_output:
                results.append({"account": context.label, "error": str(exc).strip()})
                continue
            error_console.print(f"[yellow]warning:[/yellow] {context.label}: {exc}")
            continue

        filtered = filter_records(
            payload["list"], run_type or None, status or None, semester or None
        )
        filtered_count = len(filtered)

        if json_output:
            results.append(
                {
                    "account": context.label,
                    "page": payload["page"],
                    "total": payload["total"],
                    "filtered": filtered_count,
                    "records": filtered,
                }
            )
        else:
            if len(accounts) > 1:
                console.print(f"Account: {context.label}")
            _print_record_list(
                records=filtered,
                page=payload["page"],
                total=filtered_count,
                json_output=False,
                account_label=context.label if len(accounts) > 1 else None,
                filter_tags=filter_tags,
            )

    if json_output and len(accounts) > 1:
        typer.echo(json.dumps(results, ensure_ascii=False, indent=2, default=str))
    elif json_output and len(accounts) == 1 and results:
        typer.echo(json.dumps(results[0], ensure_ascii=False, indent=2, default=str))

    if failed:
        raise typer.Exit(1)
