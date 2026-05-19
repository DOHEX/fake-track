"""Web UI pages (NiceGUI)."""

from typing import Any

from nicegui import ui

from fake_track.core.config_service import list_accounts
from fake_track.web.components import (
    account_card,
    config_section_tab,
    open_account_dialog,
    open_delete_dialog,
    option_badges,
)
from fake_track.web.utils import load_accounts

# -- nav ----------------------------------------------------------------


def _build_nav() -> None:
    with ui.tabs().classes("w-full mb-4 shadow-sm") as tabs:
        ui.tab("/", label="Dashboard", icon="dashboard")
        ui.tab("/accounts", label="Accounts", icon="people")
        ui.tab("/config", label="Config", icon="settings")
    tabs.on("update:model-value", lambda e: ui.navigate.to(e.args))


# -- pages -------------------------------------------------------------


@ui.page("/")
def dashboard() -> None:
    accounts, settings_list, error = load_accounts()

    with ui.column().classes("w-full max-w-3xl mx-auto gap-4"):
        _build_nav()
        ui.label("Dashboard").classes("text-2xl font-semibold")

        from fake_track.core.scheduler import scheduler as _sched

        async def _toggle_scheduler(e: Any) -> None:
            await _sched.set_enabled(e.value)

        with ui.row().classes("items-center gap-3"):
            toggle = ui.switch(
                "Auto-Run Scheduler",
                value=_sched.enabled,
                on_change=_toggle_scheduler,
            )
            ui.label().bind_text_from(
                toggle,
                "value",
                backward=lambda v: "Active — checking every 30s" if v else "Inactive",
            ).classes("text-xs text-slate-500")

        if error:
            with ui.card().classes("bg-red-50 border border-red-200"):
                ui.label(error).classes("text-sm text-red-700")

        if not accounts:
            ui.label(
                "No accounts configured. "
                "Add accounts in fake-track.toml or set environment variables."
            ).classes("text-sm text-slate-500 mt-4")

        for account, settings in zip(accounts, settings_list, strict=True):
            account_card(account, settings)


@ui.page("/accounts")
def accounts_page() -> None:
    with ui.column().classes("w-full max-w-3xl mx-auto gap-4"):
        _build_nav()
        with ui.row().classes("items-center justify-between w-full"):
            ui.label("Account Management").classes("text-2xl font-semibold")
            ui.button(
                "Add Account",
                icon="add",
                on_click=lambda: open_account_dialog("add", None, _render_cards),
            )

        cards_container = ui.column().classes("w-full gap-2")

        def _render_cards() -> None:
            cards_container.clear()
            with cards_container:
                for a in list_accounts():
                    with ui.card().classes("w-full"):
                        with ui.row().classes("w-full items-center justify-between"):
                            with ui.column().classes("gap-0.5"):
                                with ui.row().classes("items-center gap-2"):
                                    ui.label(
                                        a.name or f"account #{a.index + 1}"
                                    ).classes("text-base font-semibold text-slate-800")
                                    ui.label(f"#{a.index + 1}").classes(
                                        "text-xs text-slate-400"
                                    )
                                ui.label(a.phone).classes("text-xs text-slate-500")
                                track_hints: list[str] = []
                                if a.start_lat and a.start_lng:
                                    track_hints.append(
                                        f"📍{a.start_lat:.4f},{a.start_lng:.4f}"
                                    )
                                if a.target_distance_km:
                                    track_hints.append(f"🏃{a.target_distance_km}km")
                                if a.target_pace_min_per_km:
                                    track_hints.append(
                                        f"⏱{a.target_pace_min_per_km}min/km"
                                    )
                                if track_hints:
                                    ui.label(" · ".join(track_hints)).classes(
                                        "text-xs text-slate-400"
                                    )
                                with ui.row().classes("gap-1 mt-1"):
                                    option_badges(a)
                            with ui.row().classes("gap-1 ml-auto"):
                                ui.button(
                                    icon="edit",
                                    on_click=lambda _, a=a: open_account_dialog(
                                        "edit", a, _render_cards
                                    ),
                                ).props("flat dense")
                                ui.button(
                                    icon="delete",
                                    color="red",
                                    on_click=lambda _, a=a: open_delete_dialog(
                                        a, _render_cards
                                    ),
                                ).props("flat dense")

        _render_cards()


@ui.page("/config")
def parameters_page() -> None:
    with ui.column().classes("w-full max-w-3xl mx-auto gap-4"):
        _build_nav()
        ui.label("Simulation Parameters").classes("text-2xl font-semibold")

        tabs_config: list[
            tuple[str, str, list[tuple[str, str, str, type, str | None]]]
        ] = [
            (
                "Run",
                "run",
                [
                    ("start", "Start (GCJ02)", "coord", float, "°"),
                    ("target_distance_km", "Target Distance", "number", float, "km"),
                    (
                        "target_pace_min_per_km",
                        "Target Pace",
                        "number",
                        float,
                        "min/km",
                    ),
                    ("sample_interval_sec", "Sample Interval", "number", int, "sec"),
                    ("must_pass_radius_km", "Must-Pass Radius", "number", float, "km"),
                    ("distance_jitter_ratio", "Distance Jitter", "number", float, ""),
                    ("pace_jitter_ratio", "Pace Jitter", "number", float, ""),
                    ("point_accuracy_min", "Accuracy Min", "number", int, ""),
                    ("point_accuracy_max", "Accuracy Max", "number", int, ""),
                    ("point_jitter_m", "Coord Jitter", "number", float, "m"),
                    ("timestamp_jitter_ms", "Timestamp Jitter", "number", int, "ms"),
                ],
            ),
            (
                "Route",
                "route",
                [
                    ("road_routing_enabled", "Road Routing", "switch", bool, ""),
                    ("road_map_path", "Map Path", "text", str, ""),
                    ("road_snap_max_m", "Snap Max Distance", "number", float, "m"),
                ],
            ),
            (
                "Guard",
                "guard",
                [
                    ("max_speed_threshold_m_s", "Max Speed", "number", float, "m/s"),
                    ("max_jump_distance_km", "Max Jump", "number", float, "km"),
                    ("min_move_distance_m", "Min Move", "number", float, "m"),
                    ("min_move_speed_m_s", "Min Move Speed", "number", float, "m/s"),
                    (
                        "gps_accuracy_threshold_m",
                        "GPS Accuracy Threshold",
                        "number",
                        float,
                        "m",
                    ),
                    (
                        "primary_angle_threshold_deg",
                        "Primary Angle Thresh",
                        "number",
                        float,
                        "°",
                    ),
                    (
                        "secondary_angle_threshold_deg",
                        "Secondary Angle Thresh",
                        "number",
                        float,
                        "°",
                    ),
                    (
                        "distance_tolerance_ratio",
                        "Distance Tolerance",
                        "number",
                        float,
                        "",
                    ),
                ],
            ),
            (
                "Network",
                "network",
                [
                    ("base_url_xcxapi", "API Base URL", "text", str, ""),
                    ("base_url_root", "Root Base URL", "text", str, ""),
                    ("referer", "Referer", "text", str, ""),
                    ("user_agent", "User-Agent", "text", str, ""),
                    ("timeout_sec", "Timeout", "number", int, "sec"),
                    ("retry_count", "Retry Count", "number", int, ""),
                ],
            ),
        ]

        with ui.tabs().classes("w-full") as tabs:
            for label, _key, _fields in tabs_config:
                ui.tab(label, icon="settings")

        with ui.tab_panels(tabs, value=tabs_config[0][0]).classes("w-full"):
            for label, key, fields in tabs_config:
                with ui.tab_panel(label):
                    config_section_tab(key, fields)
