import json
from datetime import datetime
from pathlib import Path

import typer
from rich import box
from rich.console import Console
from rich.table import Table

from .client import ApiError, CampusRunClient
from .config import ConfigError, CryptoSettings, Settings
from .crypto import aes_encrypt
from .workflow import RunExecutionOptions, RunReport, RunWorkflow

app = typer.Typer(help="fake-track campus run API test tool")
console = Console()
error_console = Console(stderr=True)


def _default_track_image_path() -> Path:
    default_name = datetime.now().strftime("track-overlay-%Y%m%d-%H%M%S.png")
    return Path(".local") / "debug-images" / default_name


def _progress_printer(enabled: bool):
    if not enabled:
        return None

    def progress(message: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        console.print(f"[cyan][{stamp}][/cyan] {message}")

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


def _print_counts(counts: dict[str, int | bool], json_output: bool) -> None:
    if json_output:
        typer.echo(json.dumps(counts, ensure_ascii=False, indent=2))
        return

    table = Table(title="Run Counts", box=box.ASCII)
    table.add_column("Field", style="cyan")
    table.add_column("Value", style="white")
    table.add_row("student_id", str(counts["student_id"]))
    table.add_row("morning", str(counts["morning"]))
    table.add_row("normal", str(counts["normal"]))
    table.add_row("effective", str(counts["effective"]))
    table.add_row("completed_target_count", str(counts["completed_target_count"]))
    table.add_row("target_effective", str(counts["target_effective"]))
    table.add_row("target_met", str(counts["target_met"]).lower())
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


def _load_settings(
    command: str,
    json_output: bool,
    report_path: Path | None = None,
) -> Settings:
    try:
        return Settings.load()
    except ConfigError as exc:
        _try_write_error_report(report_path, str(exc))
        _exit_with_error(command, exc, json_output)


def _extract_student_id(login_data: object) -> int:
    if not isinstance(login_data, dict):
        raise ApiError(
            f"Login response data is not a dict: {type(login_data).__name__}"
        )

    student_id = int(login_data.get("id", 0))
    if not student_id:
        raise ApiError("Login response missing student id")
    return student_id


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
    skip_wait: bool = typer.Option(
        False,
        "--skip-wait",
        help="Skip the simulated run-duration wait before submit.",
    ),
    force_submit: bool = typer.Option(
        False,
        "--force-submit",
        help="Continue update/upload even when checkRecord rejects the payload.",
    ),
    ignore_target_met: bool = typer.Option(
        False,
        "--ignore-target-met",
        help="Run even when the current target count is already met.",
    ),
) -> None:
    """Run one test cycle."""
    settings = _load_settings("run", json_output, report_path=report_path)

    image_path: str | None = None
    if track_image_path is not None:
        image_path = str(track_image_path)
    elif track_image:
        image_path = str(_default_track_image_path())

    workflow = RunWorkflow(settings)
    run_options = RunExecutionOptions(
        skip_submit_wait=skip_wait,
        force_submit=force_submit,
        ignore_target_met=ignore_target_met,
        track_image_path=image_path,
    )

    try:
        report = workflow.run_full(
            progress=_progress_printer(enabled=not json_output),
            options=run_options,
        )
    except Exception as exc:  # noqa: BLE001
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


@app.command()
def counts(
    json_output: bool = typer.Option(
        False,
        "--json-output",
        help="Print JSON instead of a summary table.",
    ),
) -> None:
    """Show current completed run target counts."""
    settings = _load_settings("counts", json_output)
    client = CampusRunClient(settings)

    try:
        login = client.authenticate_user()
        student_id = _extract_student_id(login.data)
        run_counts = client.fetch_run_counts(student_id)
        counts_payload = _build_counts_payload(student_id, run_counts.data)
    except Exception as exc:  # noqa: BLE001
        _exit_with_error("counts", exc, json_output)

    _print_counts(counts_payload, json_output=json_output)


@app.command()
def doctor(
    json_output: bool = typer.Option(
        False,
        "--json-output",
        help="Print full JSON report instead of progress logs and summary table.",
    ),
) -> None:
    """Check login, route fetching, and createLine connectivity."""
    settings = _load_settings("doctor", json_output)

    workflow = RunWorkflow(settings)
    try:
        report = workflow.run_connectivity(
            progress=_progress_printer(enabled=not json_output)
        )
    except Exception as exc:  # noqa: BLE001
        _exit_with_error("doctor", exc, json_output)

    _print_report(report, json_output=json_output, title="Doctor Summary")
    if not report.success:
        raise typer.Exit(1)
