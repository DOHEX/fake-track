import json
from datetime import datetime
from pathlib import Path
from typing import Any

import typer
from rich import box
from rich.console import Console
from rich.table import Table

from fake_track.core.models import RunCounts
from fake_track.core.workflow import RunReport

console = Console()
error_console = Console(stderr=True)


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


def _format_duration(seconds: float | int | None) -> str:
    if seconds is None:
        return "-"
    total = max(0, round(float(seconds)))
    minutes, secs = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def _format_distance_km(value: float | int | None) -> str:
    if value is None:
        return "-"
    return f"{float(value):.2f} km"


def _format_pace(value: float | int | None) -> str:
    if value is None:
        return "-"
    return f"{float(value):.2f} min/km"


def _print_full_report(report: RunReport) -> None:
    s = report.summary
    table = _new_summary_table("Run Result")
    table.add_row("Status", _status_text(report.success))
    table.add_row("Record ID", _display_value(report.record_id))

    warning_treated_as_success = bool(s.server_warning_treated_as_success)
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

    table.add_row("Distance", _format_distance_km(s.generated_distance_km))
    table.add_row("Duration", _format_duration(s.generated_duration_sec))
    table.add_row("Pace", _format_pace(s.generated_pace_min_per_km))
    table.add_row(
        "Uploaded",
        (
            f"{_display_value(s.uploaded_point_count, '0')} points / "
            f"{_display_value(s.uploaded_batches, '0')} batches"
        ),
    )

    if s.generated_track_image:
        table.add_row("Track Image", str(s.generated_track_image))
    if s.generated_ignored_target_skip_reason:
        table.add_row(
            "Target Check",
            f"ignored: {s.generated_ignored_target_skip_reason}",
        )

    console.print(table)


def _print_skipped_report(report: RunReport) -> None:
    s = report.summary
    table = _new_summary_table("Run Result")
    table.add_row("Status", "[yellow]skipped[/yellow]")
    table.add_row("Reason", _display_value(s.skip_reason))
    table.add_row("Run Type", _display_value(s.run_type))

    effective = s.effective
    target = s.target_effective
    if effective is not None or target is not None:
        table.add_row(
            "Progress",
            f"{_display_value(effective, '0')} / {_display_value(target, '0')}",
        )
    if s.morning is not None:
        table.add_row("Morning", _display_value(s.morning))
        table.add_row("Normal", _display_value(s.normal))

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


def _print_report(report: RunReport, json_output: bool, title: str) -> None:
    if json_output:
        typer.echo(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
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
        s = report.summary
        table.add_row("Student ID", _display_value(getattr(s, "student_id", None)))
        table.add_row("Pass Points", _display_value(s.pass_point_count))
    console.print(table)
    if report.warning:
        console.print(f"[yellow]warning:[/yellow] {report.warning}")


def _print_counts(counts: RunCounts, json_output: bool, student_id: int = 0) -> None:
    payload = {
        "student_id": student_id,
        "morning": counts.morning,
        "normal": counts.normal,
        "effective": counts.effective,
        "completed_target_count": counts.effective,
        "target_effective": counts.target_effective,
        "target_met": counts.target_effective > 0
        and counts.effective >= counts.target_effective,
    }
    if json_output:
        typer.echo(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    table = Table(title="Run Counts", box=box.ASCII)
    table.add_column("Field", style="cyan")
    table.add_column("Value", style="white")
    for key, value in payload.items():
        table.add_row(
            key, str(value).lower() if isinstance(value, bool) else str(value)
        )
    console.print(table)


def _error_report(message: str) -> RunReport:
    from fake_track.core.models import RunSummary, ServerResponses

    return RunReport(
        success=False,
        mode="error",
        record_id=None,
        summary=RunSummary(),
        server=ServerResponses(),
        warning=message,
    )


def _build_multi_report(
    results: list[Any],
) -> dict[str, object]:
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "success": all(item.report.success for item in results),
        "mode": "multi",
        "accounts": [
            {
                "index": item.account.index,
                "name": item.account.settings.account_name,
                "phone": _mask_phone_static(item.account.settings.phone),
                "label": item.account.label,
                "report": item.report.to_dict(),
            }
            for item in results
        ],
    }


def _mask_phone_static(phone: str) -> str:
    from fake_track.core.utils import mask_phone

    return mask_phone(phone)


def _write_multi_report_file(report: dict[str, object], report_path: Path) -> None:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _print_multi_run_summary(results: list[Any]) -> None:
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
    from fake_track.core.report import write_report_file

    write_report_file(report, str(report_path))


def _try_write_error_report(report_path: Path | None, message: str) -> None:
    if report_path is None:
        return

    try:
        _write_report_file(_error_report(message), report_path)
    except OSError as exc:
        error_console.print(f"[yellow]warning:[/yellow] cannot write report: {exc}")


_RUN_TYPE_LABELS: dict[Any, str] = {}


# Populated lazily
def _get_run_type_labels() -> dict:
    from fake_track.core.models import RunType

    return {
        RunType.MORNING: "晨跑",
        RunType.NORMAL: "普跑",
        None: "—",
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
    run_type_labels = _get_run_type_labels()

    for r in records:
        mileage_m = float(r.get("mileage", 0) or 0)  # type: ignore[arg-type]
        status_code = int(r.get("status", 0))  # type: ignore[arg-type]
        run_type = r.get("_run_type")
        type_label = run_type_labels.get(run_type, "—")

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
