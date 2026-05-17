"""Web UI package (NiceGUI)."""

from __future__ import annotations

from fake_track.web import pages  # noqa: F401 — triggers @ui.page registration


def serve(
    host: str = "127.0.0.1",
    port: int = 8000,
    reload: bool = False,
    log_level: str = "info",
) -> None:
    """Start the NiceGUI web server."""
    from nicegui import ui

    ui.run(
        host=host,
        port=port,
        reload=reload,
        title="fake-track",
        uvicorn_logging_level=log_level,
    )
