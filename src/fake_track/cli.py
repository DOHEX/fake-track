import json
from datetime import datetime
from pathlib import Path

import typer
from rich import box
from rich.console import Console
from rich.table import Table

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

    table = Table(title=title, box=box.ASCII)
    table.add_column("Field", style="cyan")
    table.add_column("Value", style="white")
    table.add_row("mode", str(report.mode))
    table.add_row("record_id", str(report.record_id))

    summary = report.summary
    if report.mode == "full":
        table.add_row("distance_km", str(summary.get("generated_distance_km", "-")))
        table.add_row("duration_sec", str(summary.get("generated_duration_sec", "-")))
        table.add_row(
            "pace_min_per_km", str(summary.get("generated_pace_min_per_km", "-"))
        )
        table.add_row("uploaded_batches", str(summary.get("uploaded_batches", "-")))
        if summary.get("generated_track_image"):
            table.add_row("track_image", str(summary.get("generated_track_image")))
    if report.mode == "skipped":
        table.add_row("run_type", str(summary.get("run_type", "-")))
        if summary.get("morning") is not None:
            table.add_row("morning", str(summary["morning"]))
            table.add_row("normal", str(summary["normal"]))
            table.add_row("target_effective", str(summary["target_effective"]))
    if report.mode == "connectivity":
        table.add_row("student_id", str(summary.get("student_id", "-")))
        table.add_row("pass_point_count", str(summary.get("pass_point_count", "-")))

    console.print(table)
    if report.warning:
        console.print(f"[yellow]warning:[/yellow] {report.warning}")


def _error_report(message: str) -> RunReport:
    return RunReport(
        success=False,
        mode="error",
        record_id=None,
        summary={},
        server={},
        warning=message,
    )


def _exit_with_error(command: str, exc: Exception, json_output: bool) -> None:
    message = str(exc)
    if json_output:
        _print_report(_error_report(message), json_output=True, title="")
    else:
        error_console.print(f"[red]{command} failed:[/red] {message}")
    raise typer.Exit(1) from exc


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
    try:
        settings = Settings.load()
    except ConfigError as exc:
        _exit_with_error("run", exc, json_output)

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
        _exit_with_error("run", exc, json_output)

    _print_report(report, json_output=json_output, title="Run Summary")
    if not report.success:
        raise typer.Exit(1)


@app.command()
def doctor(
    json_output: bool = typer.Option(
        False,
        "--json-output",
        help="Print full JSON report instead of progress logs and summary table.",
    ),
) -> None:
    """Check login, route fetching, and createLine connectivity."""
    try:
        settings = Settings.load()
    except ConfigError as exc:
        _exit_with_error("doctor", exc, json_output)

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
