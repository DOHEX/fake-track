import json
from datetime import datetime
from pathlib import Path

import typer
from rich import box
from rich.console import Console
from rich.table import Table

from .config import ConfigError, Settings
from .crypto import aes_encrypt
from .workflow import RunDebugOptions, RunWorkflow

app = typer.Typer(help="fack-track campus run API test tool")
console = Console()


@app.command()
def encrypt(
    text: str = typer.Argument(..., help="Plain text to encrypt"),
) -> None:
    """Encrypt text with the same iv:cipher format used by the mini-app."""
    settings = Settings.from_env()
    console.print(aes_encrypt(text, settings.run_key))


@app.command("run")
def run_once(
    mode: str = typer.Option("full", help="full or connectivity"),
    force: bool = typer.Option(
        False,
        "--force",
        help="Continue even when checkRecord reports status=0.",
    ),
    quiet: bool = typer.Option(
        False,
        "--quiet",
        help="Disable stage/progress logs and print final JSON only.",
    ),
    json_output: bool = typer.Option(
        False,
        "--json-output",
        help="Print full JSON report instead of concise summary.",
    ),
    debug: bool = typer.Option(
        False,
        "--debug",
        help=(
            "Enable debug mode (skip submit wait and save debug track image to "
            "dev/debug-images)."
        ),
    ),
) -> None:
    """Run one test cycle."""
    try:
        settings = Settings.from_env()
    except ConfigError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc

    debug_image_path: str | None = None
    if debug:
        default_name = datetime.now().strftime("track-overlay-%Y%m%d-%H%M%S.png")
        debug_image_path = str(Path("dev") / "debug-images" / default_name)

    workflow = RunWorkflow(settings)
    debug_options = RunDebugOptions(
        enabled=debug,
        track_image_path=debug_image_path,
    )

    def progress(message: str) -> None:
        if quiet:
            return
        stamp = datetime.now().strftime("%H:%M:%S")
        console.print(f"[cyan][{stamp}][/cyan] {message}")

    try:
        if mode == "connectivity":
            report = workflow.run_connectivity(progress=progress)
        else:
            report = workflow.run_full(
                force=force,
                progress=progress,
                debug=debug_options,
            )
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]run failed:[/red] {exc}")
        raise typer.Exit(1) from exc

    report_dict = report.to_dict()
    if json_output:
        console.print_json(json.dumps(report_dict, ensure_ascii=False, indent=2))
        return

    table = Table(title="Run Summary", box=box.ASCII)
    table.add_column("Field", style="cyan")
    table.add_column("Value", style="white")
    table.add_row("mode", str(report.mode))
    table.add_row("record_id", str(report.record_id))
    if report.mode == "full":
        summary = report.summary
        table.add_row("distance_km", str(summary.get("generated_distance_km", "-")))
        table.add_row("duration_sec", str(summary.get("generated_duration_sec", "-")))
        table.add_row(
            "pace_min_per_km", str(summary.get("generated_pace_min_per_km", "-"))
        )
        table.add_row("uploaded_batches", str(summary.get("uploaded_batches", "-")))
        if summary.get("generated_debug_track_image"):
            table.add_row(
                "debug_track_image", str(summary.get("generated_debug_track_image"))
            )

    console.print(table)
    if report.warning:
        console.print(f"[yellow]warning:[/yellow] {report.warning}")
