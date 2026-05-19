"""Web UI package (NiceGUI)."""

from __future__ import annotations

from fake_track.web import pages  # noqa: F401 — triggers @ui.page registration


def _wire_scheduler() -> None:
    """Wire the auto-run scheduler to NiceGUI."""
    from nicegui import app, run, ui

    from fake_track.core.config import Settings
    from fake_track.core.config_service import AccountInfo
    from fake_track.core.scheduler import scheduler
    from fake_track.core.workflow import RunExecutionOptions, RunWorkflow

    async def _on_run(settings: Settings, run_type: str, raw: AccountInfo) -> None:
        """Called from APScheduler on the event loop when a schedule fires."""
        name = raw.name or "unknown"
        try:
            workflow = await run.io_bound(RunWorkflow, settings)
            options = RunExecutionOptions(
                skip_submit_wait=True,
                force_submit=settings.force_submit,
                ignore_target_met=settings.ignore_target_met,
                disable_progress=True,
            )
            report = await run.io_bound(workflow.run_full, options=options)
            if report.success:
                ui.notification(
                    f"Auto: {name} ({run_type}) OK #{report.record_id}",
                    type="positive",
                    position="bottom-right",
                )
            else:
                ui.notification(
                    f"Auto: {name} ({run_type}) skipped: {report.warning}",
                    type="warning",
                    position="bottom-right",
                )
        except Exception as exc:
            ui.notification(
                f"Auto: {name} ({run_type}) failed: {exc}",
                type="negative",
                position="bottom-right",
            )

    scheduler.set_on_run(_on_run)

    @app.on_startup
    async def _start_scheduler() -> None:
        await scheduler.start()


def serve(
    host: str = "127.0.0.1",
    port: int = 8000,
    reload: bool = False,
    log_level: str = "info",
) -> None:
    """Start the NiceGUI web server."""
    from nicegui import ui

    _wire_scheduler()

    ui.run(
        host=host,
        port=port,
        reload=reload,
        title="fake-track",
        uvicorn_logging_level=log_level,
    )
