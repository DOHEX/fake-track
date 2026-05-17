import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import typer
from rich import box
from rich.console import Console
from rich.table import Table

from fake_track.core.client import ApiError, CampusRunClient, get_authenticated_client
from fake_track.core.config import ConfigError, CryptoSettings, Settings
from fake_track.core.crypto import aes_encrypt
from fake_track.core.models import RunType, classify_run_type, semester_for
from fake_track.core.workflow import RunExecutionOptions, RunReport, RunWorkflow
from fake_track.web import serve as run_web_server

app = typer.Typer(help="fake-track campus run API test tool")
console = Console()
error_console = Console(stderr=True)


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


def _default_track_image_path() -> Path:
    default_name = datetime.now().strftime("track-overlay-%Y%m%d-%H%M%S.png")
    return Path(".local") / "debug-images" / default_name


def _with_account_suffix(path: Path, slug: str) -> Path:
    if not slug:
        return path
    return path.with_name(f"{path.stem}-{slug}{path.suffix}")


def _mask_phone(phone: str) -> str:
    text = phone.strip()
    if len(text) <= 4:
        return text
    return f"****{text[-4:]}"


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", value).strip("-").lower()
    return slug


def _account_display_label(settings: Settings, index: int) -> str:
    name = settings.account_name or f"account-{index}"
    return f"{name} ({_mask_phone(settings.phone)})"


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


def _print_report(report: RunReport, json_output: bool, title: str) -> None:
    report_dict = report.to_dict()
    if json_output:
        typer.echo(json.dumps(report_dict, ensure_ascii=False, indent=2))
        return

    if report.mode == "full":
        _print_full_report(report)
        return
    if report.mode == "skipped":
        _print_skipped_report(report)
        return
    if report.mode == "error":
        _print_error_report(report, title=title)
        return

    table = _new_summary_table(title)
    table.add_row("Status", _status_text(report.success))
    table.add_row("Mode", str(report.mode))
    table.add_row("Record ID", _display_value(report.record_id))
    if report.mode == "connectivity":
        summary = report.summary
        table.add_row("Student ID", _display_value(summary.get("student_id")))
        table.add_row("Pass Points", _display_value(summary.get("pass_point_count")))
    console.print(table)
    if report.warning:
        console.print(f"[yellow]warning:[/yellow] {report.warning}")


def _new_summary_table(title: str) -> Table:
    table = Table(title=title, box=box.ASCII)
    table.add_column("Field", style="cyan")
    table.add_column("Value", style="white")
    return table


def _status_text(success: bool) -> str:
    return "[green]success[/green]" if success else "[red]failed[/red]"


def _display_value(value: object, default: str = "-") -> str:
    if value is None or value == "":
        return default
    return str(value)


def _format_duration(seconds: object) -> str:
    if seconds is None:
        return "-"
    total = max(0, int(round(float(seconds))))
    minutes, secs = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def _format_distance_km(value: object) -> str:
    if value is None:
        return "-"
    return f"{float(value):.2f} km"


def _format_pace(value: object) -> str:
    if value is None:
        return "-"
    return f"{float(value):.2f} min/km"


def _print_full_report(report: RunReport) -> None:
    summary = report.summary
    table = _new_summary_table("Run Result")
    table.add_row("Status", _status_text(report.success))
    table.add_row("Record ID", _display_value(report.record_id))

    warning_treated_as_success = bool(summary.get("server_warning_treated_as_success"))
    if report.warning:
        server_status = (
            "accepted (warning treated as success)"
            if warning_treated_as_success
            else "warning"
        )
    else:
        server_status = "accepted"
    table.add_row("Server", server_status)
    if report.warning:
        table.add_row("Server Warning", report.warning)

    table.add_row(
        "Distance",
        _format_distance_km(summary.get("generated_distance_km")),
    )
    table.add_row(
        "Duration",
        _format_duration(summary.get("generated_duration_sec")),
    )
    table.add_row("Pace", _format_pace(summary.get("generated_pace_min_per_km")))
    table.add_row(
        "Uploaded",
        (
            f"{_display_value(summary.get('uploaded_point_count'), '0')} points / "
            f"{_display_value(summary.get('uploaded_batches'), '0')} batches"
        ),
    )

    if summary.get("generated_track_image"):
        table.add_row("Track Image", str(summary["generated_track_image"]))
    if summary.get("generated_ignored_target_skip_reason"):
        table.add_row(
            "Target Check",
            f"ignored: {summary['generated_ignored_target_skip_reason']}",
        )

    console.print(table)


def _print_skipped_report(report: RunReport) -> None:
    summary = report.summary
    table = _new_summary_table("Run Result")
    table.add_row("Status", "[yellow]skipped[/yellow]")
    table.add_row("Reason", _display_value(summary.get("skip_reason")))
    table.add_row("Run Type", _display_value(summary.get("run_type")))

    effective = summary.get("effective")
    target = summary.get("target_effective")
    if effective is not None or target is not None:
        table.add_row(
            "Progress",
            f"{_display_value(effective, '0')} / {_display_value(target, '0')}",
        )
    if summary.get("morning") is not None:
        table.add_row("Morning", _display_value(summary.get("morning")))
        table.add_row("Normal", _display_value(summary.get("normal")))

    console.print(table)


def _print_error_report(
    report: RunReport,
    title: str = "Run Result",
    output_console: Console | None = None,
) -> None:
    table = _new_summary_table(title)
    table.add_row("Status", _status_text(False))
    table.add_row("Reason", _display_value(report.warning))
    (output_console or console).print(table)


def _print_counts(counts_payload: dict[str, int | bool], json_output: bool) -> None:
    if json_output:
        typer.echo(json.dumps(counts_payload, ensure_ascii=False, indent=2))
        return

    table = Table(title="Run Counts", box=box.ASCII)
    table.add_column("Field", style="cyan")
    table.add_column("Value", style="white")
    table.add_row("student_id", str(counts_payload["student_id"]))
    table.add_row("morning", str(counts_payload["morning"]))
    table.add_row("normal", str(counts_payload["normal"]))
    table.add_row("effective", str(counts_payload["effective"]))
    table.add_row(
        "completed_target_count", str(counts_payload["completed_target_count"])
    )
    table.add_row("target_effective", str(counts_payload["target_effective"]))
    table.add_row("target_met", str(counts_payload["target_met"]).lower())
    console.print(table)


def _error_report(message: str) -> RunReport:
    return RunReport(
        success=False,
        mode="error",
        record_id=None,
        summary={},
        server={},
        warning=message,
    )


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

    base = track_image_path or _default_track_image_path()
    if multi_run:
        base = _with_account_suffix(base, account.slug)
    return str(base)


def _build_multi_report(
    results: list[AccountRunResult],
) -> dict[str, object]:
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "success": all(item.report.success for item in results),
        "mode": "multi",
        "accounts": [
            {
                "index": item.account.index,
                "name": item.account.settings.account_name,
                "phone": _mask_phone(item.account.settings.phone),
                "label": item.account.label,
                "report": item.report.to_dict(),
            }
            for item in results
        ],
    }


def _write_multi_report_file(report: dict[str, object], report_path: Path) -> None:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _print_multi_run_summary(results: list[AccountRunResult]) -> None:
    table = Table(title="Multi Run Summary", box=box.ASCII)
    table.add_column("Account", style="cyan")
    table.add_column("Status", style="white")
    table.add_column("Mode", style="white")
    table.add_column("Record ID", style="white")
    table.add_column("Warning", style="yellow")

    for item in results:
        report = item.report
        table.add_row(
            item.account.label,
            _status_text(report.success),
            str(report.mode),
            _display_value(report.record_id),
            _display_value(report.warning),
        )
    console.print(table)


def _write_report_file(report: RunReport, report_path: Path) -> None:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _try_write_error_report(report_path: Path | None, message: str) -> None:
    if report_path is None:
        return

    try:
        _write_report_file(_error_report(message), report_path)
    except OSError as exc:
        error_console.print(f"[yellow]warning:[/yellow] cannot write report: {exc}")


def _exit_with_error(command: str, exc: Exception, json_output: bool) -> None:
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
        _exit_with_error(command, exc, json_output)

    contexts = _build_account_contexts(settings_list)
    return _select_accounts(contexts, selectors)


def _build_counts_payload(
    student_id: int, counts_data: object
) -> dict[str, int | bool]:
    if not isinstance(counts_data, dict):
        raise ApiError(
            f"Run counts response data is not a dict: {type(counts_data).__name__}"
        )

    morning = int(counts_data.get("morning", 0))
    normal = int(counts_data.get("universal", 0))
    effective = int(counts_data.get("effective", 0))
    target_effective = int(counts_data.get("target_effective", 0))
    return {
        "student_id": student_id,
        "morning": morning,
        "normal": normal,
        "effective": effective,
        "completed_target_count": effective,
        "target_effective": target_effective,
        "target_met": target_effective > 0 and effective >= target_effective,
    }


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
) -> None:
    """Start the local web UI."""
    run_web_server(host=host, port=port, reload=reload, log_level=log_level)


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
            except Exception as exc:  # noqa: BLE001  # pylint: disable=broad-except
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
                _exit_with_error("run", exc, json_output)

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
    except Exception as exc:  # noqa: BLE001  # pylint: disable=broad-except
        _try_write_error_report(report_path, str(exc))
        _exit_with_error("run", exc, json_output)

    if report_path is not None:
        try:
            _write_report_file(report, report_path)
        except OSError as exc:
            _exit_with_error("run", exc, json_output)

    _print_report(report, json_output=json_output, title="Run Summary")
    if not report.success:
        raise typer.Exit(1)


_RUN_TYPE_LABELS: dict[RunType | None, str] = {
    RunType.MORNING: "晨跑",
    RunType.NORMAL: "普跑",
    None: "—",
}


def _annotate_record(record: dict[str, object]) -> dict[str, object]:
    start = str(record.get("start_time", "") or "")
    try:
        dt = datetime.strptime(start[:19], "%Y-%m-%d %H:%M:%S")
        record["_run_type"] = classify_run_type(dt)
        record["_semester"] = semester_for(dt)
    except ValueError:
        record["_run_type"] = None
        record["_semester"] = ""
    return record


def _filter_records(
    records: list[dict[str, object]],
    run_type: str | None,
    status: str | None,
    semester: str | None,
) -> list[dict[str, object]]:
    out: list[dict[str, object]] = []
    for r in records:
        if run_type:
            rt = r.get("_run_type")
            if run_type == "morning" and rt is not RunType.MORNING:
                continue
            if run_type == "normal" and rt is not RunType.NORMAL:
                continue
        if status:
            sc = int(r.get("status", 0))
            if status == "valid" and sc != 1:
                continue
            if status == "invalid" and sc != 2:
                continue
        if semester:
            if str(r.get("_semester", "")) != semester:
                continue
        out.append(r)
    return out


def _build_record_list_payload(resp_data: object) -> dict[str, object]:
    if not isinstance(resp_data, dict):
        raise ApiError(
            f"Record list response data is not a dict: {type(resp_data).__name__}"
        )
    raw = resp_data.get("list", [])
    records = [_annotate_record(dict(r)) for r in raw]  # type: ignore[arg-type]
    return {
        "list": records,
        "page": int(resp_data.get("page", 1)),
        "total": int(resp_data.get("total", 0)),
    }


def _print_record_list(
    records: list[dict[str, object]],
    page: int,
    total: int,
    json_output: bool,
    account_label: str | None = None,
    filter_tags: str = "",
) -> None:
    if json_output:
        typer.echo(
            json.dumps(
                {"records": records, "page": page, "total": total},
                ensure_ascii=False,
                indent=2,
                default=str,
            )
        )
        return

    if not records:
        console.print("[yellow]No records found.[/yellow]")
        return

    title = f"Records (page {page})"
    if filter_tags:
        title += f"  [{filter_tags}]"
    if account_label:
        title += f" -- {account_label}"

    table = Table(title=title, box=box.ASCII)
    table.add_column("ID", style="cyan", no_wrap=True)
    table.add_column("Start", style="white")
    table.add_column("End", style="white")
    table.add_column("Mileage", style="white", justify="right")
    table.add_column("Speed", style="white", justify="right")
    table.add_column("Status", style="white")
    table.add_column("Type", style="white")

    status_labels = {1: "达标", 2: "无效"}

    for r in records:
        mileage_m = float(r.get("mileage", 0) or 0)
        status_code = int(r.get("status", 0))
        run_type = r.get("_run_type")
        type_label = _RUN_TYPE_LABELS.get(run_type, "—")  # type: ignore[arg-type]

        table.add_row(
            str(r.get("id", "")),
            str(r.get("start_time", "")),
            str(r.get("end_time", "")),
            f"{mileage_m / 1000:.2f} km" if mileage_m else "-",
            str(r.get("speed", "-")),
            status_labels.get(status_code, str(status_code)),
            type_label,
        )

    console.print(table)
    if total > 0:
        console.print(f"[dim]Total records: {total}[/dim]")


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
            client, student_id = get_authenticated_client(context.settings)
            run_counts = client.fetch_run_counts(student_id)
            counts_payload = _build_counts_payload(student_id, run_counts.data)
        except Exception as exc:  # noqa: BLE001  # pylint: disable=broad-except
            failed = True
            if json_output:
                results.append({"account": context.label, "error": str(exc).strip()})
                continue
            error_console.print(f"[yellow]warning:[/yellow] {context.label}: {exc}")
            continue

        if json_output:
            results.append({"account": context.label, "counts": counts_payload})
        else:
            if len(accounts) > 1:
                console.print(f"Account: {context.label}")
            _print_counts(counts_payload, json_output=False)

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
            client, _ = get_authenticated_client(context.settings)
            resp = client.fetch_record_list(page=page, size=size)
            payload = _build_record_list_payload(resp.data)
        except Exception as exc:  # noqa: BLE001
            failed = True
            if json_output:
                results.append({"account": context.label, "error": str(exc).strip()})
                continue
            error_console.print(f"[yellow]warning:[/yellow] {context.label}: {exc}")
            continue

        filtered = _filter_records(
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
