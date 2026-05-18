"""Reusable NiceGUI components."""

from datetime import datetime
from pathlib import Path
from typing import Any

from nicegui import run, ui

from fake_track.core.client import CampusRunClient, get_authenticated_client
from fake_track.core.config import Settings
from fake_track.core.config_service import (
    add_account,
    delete_account,
    get_section,
    update_account,
    update_section,
)
from fake_track.core.models import RunType, classify_run_type, semester_for
from fake_track.core.utils import (
    annotate_records,
    build_counts_payload,
    default_track_image_path,
    parse_record_dt,
)
from fake_track.core.workflow import RunExecutionOptions, RunWorkflow
from fake_track.web.utils import OPTION_BADGES

# -- option badges ------------------------------------------------------


def option_badges(account: dict[str, Any]) -> None:
    """Render skip_wait / force_submit / ignore_target_met badges for an account."""
    for key, color in OPTION_BADGES:
        if account.get(key):
            ui.badge(key, color=color).classes("text-xs")


# -- counts display -----------------------------------------------------


def render_counts(
    counts: dict[str, int | bool],
    container: ui.element,
    effective_label: ui.label | None = None,
    target_met_label: ui.label | None = None,
    today_status: dict[str, bool] | None = None,
) -> None:
    container.clear()
    morning = counts["morning"]
    normal = counts["normal"]
    effective = counts["effective"]
    target = counts["target_effective"]
    target_met = counts["target_met"]

    if effective_label is not None:
        effective_label.set_text(f"有效: {effective}")
        effective_label.set_visibility(True)
    if target_met_label is not None:
        met_text = "达标" if target_met else "未达标"
        met_color = "emerald" if target_met else "red"
        target_met_label.set_text(met_text)
        target_met_label.classes(f"text-xs font-medium text-{met_color}-600")
        target_met_label.set_visibility(True)

    with container:
        if target > 0:
            for label, done, key in [
                ("晨跑", morning, "morning"),
                ("普通", normal, "normal"),
            ]:
                met = done >= target
                today_done = (today_status or {}).get(key, False)
                with ui.row().classes("items-center gap-2"):
                    ui.label(label).classes("text-xs text-slate-500")
                    ui.linear_progress(
                        value=min(done / target, 1.0),
                        size="20px",
                        show_value=False,
                        color="emerald" if met else "blue",
                    ).classes("w-56")
                    ui.label(f"{done}/{target}").classes(
                        "text-xs text-slate-600 tabular-nums"
                    )
                    if today_done:
                        ui.avatar("done", color="green", text_color="white", size="xs")
        else:
            if effective_label is not None:
                effective_label.set_text(f"有效: {effective}")
                effective_label.set_visibility(True)
            if target_met_label is not None:
                target_met_label.set_visibility(False)
            with ui.row().classes("gap-6"):
                ui.label(f"晨跑: {morning}").classes("text-sm text-slate-600")
                ui.label(f"普通: {normal}").classes("text-sm text-slate-600")


# -- result / error -----------------------------------------------------


def render_result(container: ui.element, report: dict[str, Any]) -> None:
    container.clear()
    success = report.get("success", False)
    color = "emerald" if success else "red"
    label = "OK" if success else "FAILED"

    with container:
        with ui.card().classes(f"bg-{color}-50 border border-{color}-200"):
            with ui.row().classes("gap-2 items-center"):
                ui.badge(label, color=color)
                ui.label("run").classes("text-xs text-slate-500")
                if report.get("record_id"):
                    ui.label(f"#{report['record_id']}").classes(
                        "text-xs text-slate-400"
                    )

            if report.get("warning"):
                ui.label(report["warning"]).classes("text-xs text-amber-700 mt-2")

            summary = report.get("summary")
            if isinstance(summary, dict) and summary:
                with ui.row().classes("gap-x-4 gap-y-1 mt-2 flex-wrap"):
                    for key, val in summary.items():
                        ui.label(f"{key}: {val}").classes("text-xs text-slate-600")

            with ui.expansion("Raw JSON", icon="code").classes("mt-2"):
                ui.code(str(report), language="json")


def render_error(container: ui.element, message: str) -> None:
    container.clear()
    with container:
        with ui.card().classes("bg-red-50 border border-red-200"):
            ui.label(message).classes("text-sm text-red-700")


# -- account card (dashboard) -------------------------------------------


_RUN_TYPE_LABELS: dict[str, str] = {"morning": "晨跑", "normal": "普跑"}
_STATUS_LABELS: dict[int, str] = {1: "达标", 2: "无效"}


def _check_today_status(client: CampusRunClient) -> dict[str, bool]:
    today = datetime.now().strftime("%Y-%m-%d")
    resp = client.fetch_record_list(page=1, size=10)
    data = resp.data if isinstance(resp.data, dict) else {}
    done: dict[str, bool] = {"morning": False, "normal": False}
    for r in data.get("list", []):
        start = str(r.get("start_time", "") or "")
        if not start.startswith(today):
            continue
        if int(r.get("status", 0)) != 1:
            continue
        dt = parse_record_dt(r)
        rt = classify_run_type(dt) if dt else None
        if rt is RunType.MORNING:
            done["morning"] = True
        elif rt is RunType.NORMAL:
            done["normal"] = True
    return done


def _build_record_rows(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for r in records:
        mileage_m = float(r.get("mileage", 0) or 0)
        status_code = int(r.get("status", 0))
        rt = r.get("_run_type", "")
        rows.append(
            {
                "id": r.get("id", ""),
                "start_time": str(r.get("start_time", "")),
                "end_time": str(r.get("end_time", "")),
                "mileage_km": f"{mileage_m / 1000:.2f} km" if mileage_m else "-",
                "speed": str(r.get("speed", "-")),
                "status_label": _STATUS_LABELS.get(status_code, str(status_code)),
                "run_type": _RUN_TYPE_LABELS.get(rt, "—"),
            }
        )
    return rows


async def open_records_dialog(settings: Settings, dialog: ui.dialog) -> None:
    dialog.clear()
    with dialog, ui.card().classes("min-w-[750px] max-w-[950px]"):
        ui.spinner(size="sm")
        ui.label("Loading records...").classes("text-xs text-slate-400 ml-2")

    try:
        client, _ = await run.io_bound(get_authenticated_client, settings)

        BATCH_SIZE = 100
        all_records: list[dict[str, Any]] = []
        server_total = 0
        loading_more = False
        semesters: list[str] = []

        PAGE_SIZE = 10
        filter_state = {"run_type": "", "status": "", "semester": ""}
        page_state = {"page": 1}

        def _apply_filters() -> list[dict[str, Any]]:
            out: list[dict[str, Any]] = []
            for r in all_records:
                if filter_state["run_type"]:
                    if r.get("_run_type") != filter_state["run_type"]:
                        continue
                if filter_state["status"]:
                    sc = int(r.get("status", 0))
                    if filter_state["status"] == "valid" and sc != 1:
                        continue
                    if filter_state["status"] == "invalid" and sc != 2:
                        continue
                if filter_state["semester"]:
                    if r.get("_semester") != filter_state["semester"]:
                        continue
                out.append(r)
            return out

        def _render() -> None:
            nonlocal semesters
            filtered = _apply_filters()
            total_filtered = len(filtered)
            total_pages = max(1, (total_filtered + PAGE_SIZE - 1) // PAGE_SIZE)
            p = page_state["page"]
            if p > total_pages:
                p = total_pages
                page_state["page"] = p
            start = (p - 1) * PAGE_SIZE
            page_records = filtered[start : start + PAGE_SIZE]

            dialog.clear()
            with dialog, ui.card().classes("min-w-[750px] max-w-[950px]"):
                loaded_note = ""
                if loading_more:
                    loaded_note = " [loading more...]"
                elif server_total and len(all_records) < server_total:
                    loaded_note = f" ({len(all_records)} loaded)"
                header = f"Records — page {p}/{total_pages}  ({total_filtered} records"
                if server_total and total_filtered != server_total:
                    header += f" / {server_total} total"
                header += f"){loaded_note}"
                ui.label(header).classes("text-lg font-semibold")

                with ui.row().classes("gap-3 mt-2 items-center"):
                    ui.select(
                        options=[""] + list(_RUN_TYPE_LABELS.keys()),
                        label="Run Type",
                        value=filter_state["run_type"],
                        on_change=lambda e: _on_filter("run_type", e.value or ""),
                    ).classes("w-28")
                    ui.select(
                        options=["", "valid", "invalid"],
                        label="Status",
                        value=filter_state["status"],
                        on_change=lambda e: _on_filter("status", e.value or ""),
                    ).classes("w-28")
                    ui.select(
                        options=[""] + semesters,
                        label="Semester",
                        value=filter_state["semester"],
                        on_change=lambda e: _on_filter("semester", e.value or ""),
                    ).classes("w-52")

                if not page_records:
                    ui.label("No records match the filters.").classes(
                        "text-sm text-slate-500 mt-2"
                    )
                else:
                    columns = [
                        {"name": "id", "label": "ID", "field": "id", "align": "left"},
                        {
                            "name": "start",
                            "label": "Start",
                            "field": "start_time",
                            "align": "left",
                        },
                        {
                            "name": "end",
                            "label": "End",
                            "field": "end_time",
                            "align": "left",
                        },
                        {
                            "name": "mileage",
                            "label": "Distance",
                            "field": "mileage_km",
                            "align": "right",
                        },
                        {
                            "name": "speed",
                            "label": "Speed",
                            "field": "speed",
                            "align": "right",
                        },
                        {
                            "name": "status",
                            "label": "Status",
                            "field": "status_label",
                            "align": "left",
                        },
                        {
                            "name": "type",
                            "label": "Type",
                            "field": "run_type",
                            "align": "left",
                        },
                    ]
                    ui.table(
                        columns=columns,
                        rows=_build_record_rows(page_records),
                        row_key="id",
                    ).classes("w-full")

                with ui.row().classes("gap-2 mt-4 items-center"):
                    ui.button(
                        "Previous",
                        icon="chevron_left",
                        on_click=lambda: _on_page(page_state["page"] - 1),
                    ).bind_enabled_from(page_state, "page", backward=lambda pg: pg > 1)
                    ui.button(
                        "Next",
                        icon="chevron_right",
                        on_click=lambda: _on_page(page_state["page"] + 1),
                    ).bind_enabled_from(
                        page_state, "page", backward=lambda pg: pg < total_pages
                    )
                    ui.space()
                    ui.button("Close", on_click=dialog.close)

        def _on_filter(key: str, value: str) -> None:
            filter_state[key] = value
            page_state["page"] = 1
            _render()

        def _on_page(p: int) -> None:
            page_state["page"] = p
            _render()

        resp = await run.io_bound(client.fetch_record_list, 1, BATCH_SIZE)
        data = resp.data if isinstance(resp.data, dict) else {}
        all_records = annotate_records(list(data.get("list", [])))
        server_total = int(data.get("total", 0))
        semesters = sorted(
            {r.get("_semester", "") for r in all_records if r.get("_semester")},
            reverse=True,
        )
        _render()

        if server_total > BATCH_SIZE:
            loading_more = True
            remaining = server_total - BATCH_SIZE
            pages_needed = (remaining + BATCH_SIZE - 1) // BATCH_SIZE
            for pg in range(2, 2 + pages_needed):
                try:
                    resp = await run.io_bound(client.fetch_record_list, pg, BATCH_SIZE)
                    data = resp.data if isinstance(resp.data, dict) else {}
                    more = annotate_records(list(data.get("list", [])))
                    all_records.extend(more)
                    semesters = sorted(
                        {
                            r.get("_semester", "")
                            for r in all_records
                            if r.get("_semester")
                        },
                        reverse=True,
                    )
                    _render()
                except Exception:
                    pass
            loading_more = False
            _render()
    except Exception as exc:
        dialog.clear()
        with dialog, ui.card().classes("min-w-[750px] max-w-[950px]"):
            ui.label("Error loading records").classes("text-red-600 font-semibold")
            ui.label(str(exc)).classes("text-sm text-slate-600 mt-2")
            ui.button("Close", on_click=dialog.close).classes("mt-4")

    dialog.open()


# -- account card -------------------------------------------------------


def account_card(account: dict[str, Any], settings: Settings) -> None:
    """Dashboard card for a single account."""
    with ui.card().classes("w-full"):
        with ui.row().classes("items-center gap-2"):
            ui.label(account["label"]).classes("text-lg font-semibold text-slate-800")
            eff_label = ui.label("").classes("text-xs text-slate-500")
            eff_label.set_visibility(False)
            met_label = ui.label("").classes("text-xs font-medium")
            met_label.set_visibility(False)
            option_badges(account)

        counts_container = ui.column().classes("mt-1")
        with counts_container:
            ui.spinner(size="sm")
            ui.label("Loading counts...").classes("text-xs text-slate-400 ml-2")

        async def _fetch_counts(
            _settings: Settings = settings,
            _container: ui.element = counts_container,
            _eff: ui.label = eff_label,
            _met: ui.label = met_label,
        ) -> None:
            try:
                client, student_id = await run.io_bound(
                    get_authenticated_client, _settings
                )
                resp = await run.io_bound(client.fetch_run_counts, student_id)
                counts = build_counts_payload(student_id, resp.data)
                today = await run.io_bound(_check_today_status, client)
                render_counts(counts, _container, _eff, _met, today)
            except Exception as exc:
                _container.clear()
                with _container:
                    ui.label(str(exc)).classes("text-sm text-red-600")

        ui.timer(0.1, _fetch_counts, once=True)

        with ui.row().classes("gap-2 mt-1 items-center"):
            chk_skip = ui.checkbox("skip wait", value=account["skip_wait"])
            chk_force = ui.checkbox("force submit", value=account["force_submit"])
            chk_ignore = ui.checkbox(
                "ignore target", value=account["ignore_target_met"]
            )
            chk_image = ui.checkbox("image", value=False)

        progress_label = ui.label("").classes("text-xs text-slate-500 mt-1")
        result_container = ui.column().classes("mt-1")

        def _on_progress(msg: str) -> None:
            progress_label.set_text(msg)

        async def _run(
            _settings: Settings = settings,
            _container: ui.element = result_container,
        ) -> None:
            _container.clear()
            progress_label.set_text("Starting...")
            with _container:
                ui.spinner(size="sm")
            try:
                workflow = await run.io_bound(RunWorkflow, _settings)
                stamp = datetime.now().strftime("web-track-%Y%m%d-%H%M%S.png")
                image_path = (
                    str(Path(".local") / "debug-images" / stamp)
                    if chk_image.value
                    else None
                )
                options = RunExecutionOptions(
                    skip_submit_wait=chk_skip.value or _settings.skip_wait,
                    force_submit=chk_force.value or _settings.force_submit,
                    ignore_target_met=chk_ignore.value or _settings.ignore_target_met,
                    track_image_path=image_path,
                    disable_progress=False,
                )
                report = await run.io_bound(
                    workflow.run_full, progress=_on_progress, options=options
                )
                render_result(_container, report.to_dict())
                progress_label.set_text("")
                await _fetch_counts()
            except Exception as exc:
                render_error(_container, str(exc))
                progress_label.set_text("")

        with ui.row().classes("gap-2 mt-2"):
            ui.button("Refresh", icon="refresh", on_click=_fetch_counts)
            ui.button(
                "Records",
                icon="history",
                on_click=lambda d=ui.dialog(): open_records_dialog(settings, d),
            )
            ui.button("Run", icon="play_arrow", color="emerald", on_click=_run)


# -- account form dialog ------------------------------------------------


def _extract_account_form(
    name_inp: ui.input,
    phone_inp: ui.input,
    pwd_inp: ui.input,
    sw_skip: ui.switch,
    sw_force: ui.switch,
    sw_ignore: ui.switch,
    *,
    inp_lat: ui.number | None = None,
    inp_lng: ui.number | None = None,
    inp_dist: ui.number | None = None,
    inp_pace: ui.number | None = None,
) -> dict[str, Any]:
    data: dict[str, Any] = {
        "name": name_inp.value or None,
        "phone": phone_inp.value or "",
        "password": pwd_inp.value or "",
        "skip_wait": sw_skip.value if sw_skip.value else None,
        "force_submit": sw_force.value if sw_force.value else None,
        "ignore_target_met": sw_ignore.value if sw_ignore.value else None,
    }
    if inp_lat is not None:
        data["start_lat"] = inp_lat.value if inp_lat.value else None
    if inp_lng is not None:
        data["start_lng"] = inp_lng.value if inp_lng.value else None
    if inp_dist is not None:
        data["target_distance_km"] = inp_dist.value if inp_dist.value else None
    if inp_pace is not None:
        data["target_pace_min_per_km"] = inp_pace.value if inp_pace.value else None
    return data


async def _open_map_picker(lat_input: ui.number, lng_input: ui.number) -> None:
    """Open a Leaflet map dialog to pick coordinates."""
    cur_lat = lat_input.value or 30.83378
    cur_lng = lng_input.value or 121.504532

    dlg = ui.dialog()
    with dlg, ui.card().classes("min-w-[550px]"):
        ui.label("Pick a location — drag marker or pan map").classes(
            "text-lg font-semibold"
        )
        coord_label = ui.label(f"{cur_lat:.6f}, {cur_lng:.6f}").classes(
            "text-xs text-slate-500 mb-1"
        )

        m = ui.leaflet(center=(cur_lat, cur_lng), zoom=17).classes("w-full h-96")
        marker_obj = m.marker(latlng=(cur_lat, cur_lng))
        marker_obj.draggable()

        def _on_move(e: Any) -> None:
            center = e.args["center"]
            lat = float(center[0])
            lng = float(center[1])
            coord_label.set_text(f"{lat:.6f}, {lng:.6f}")

        m.on("map-moveend", _on_move)

        async def _confirm() -> None:
            resp = await marker_obj.run_method("getLatLng", timeout=3)
            if isinstance(resp, dict):
                lat_input.set_value(round(resp.get("lat", cur_lat), 6))
                lng_input.set_value(round(resp.get("lng", cur_lng), 6))
            else:
                lat_input.set_value(cur_lat)
                lng_input.set_value(cur_lng)
            dlg.close()

        with ui.row().classes("gap-2 mt-2"):
            ui.button("OK", icon="check", on_click=_confirm)
            ui.button("Cancel", on_click=dlg.close)

    dlg.open()


async def open_account_dialog(
    mode: str, account: dict[str, Any] | None, on_saved: Any
) -> None:
    """Unified add/edit account dialog. mode is 'add' or 'edit'."""
    is_edit = mode == "edit"
    title = f"Edit Account #{account['index'] + 1}" if is_edit else "Add Account"

    dlg = ui.dialog()
    with dlg, ui.card().classes("min-w-[420px]"):
        ui.label(title).classes("text-lg font-semibold")
        inp_name = ui.input(
            "Name", value=(account.get("name") or "") if is_edit else ""
        )
        inp_phone = ui.input("Phone", value=account.get("phone", "") if is_edit else "")
        inp_password = ui.input(
            "Password", value=account.get("password", "") if is_edit else ""
        )

        with ui.expansion("Personalized Track Params", icon="tune").classes("mt-1"):
            ui.label("Leave empty to use global defaults.").classes(
                "text-xs text-slate-400"
            )
            with ui.row().classes("gap-2 items-end"):
                inp_lat = ui.number(
                    "start_lat",
                    value=account.get("start_lat") if is_edit else None,
                    format="%.6f",
                ).classes("w-36")
                inp_lng = ui.number(
                    "start_lng",
                    value=account.get("start_lng") if is_edit else None,
                    format="%.6f",
                ).classes("w-36")
                ui.button(
                    icon="map", on_click=lambda: _open_map_picker(inp_lat, inp_lng)
                ).props("flat dense").tooltip("Pick from map")
            with ui.row().classes("gap-2"):
                inp_dist = ui.number(
                    "target_distance_km",
                    value=account.get("target_distance_km") if is_edit else None,
                    format="%.2f",
                ).classes("w-36")
                inp_pace = ui.number(
                    "target_pace_min_per_km",
                    value=account.get("target_pace_min_per_km") if is_edit else None,
                    format="%.2f",
                ).classes("w-36")

        sw_skip = ui.switch(
            "Skip Wait", value=bool(account.get("skip_wait")) if is_edit else False
        )
        sw_force = ui.switch(
            "Force Submit",
            value=bool(account.get("force_submit")) if is_edit else False,
        )
        sw_ignore = ui.switch(
            "Ignore Target Met",
            value=bool(account.get("ignore_target_met")) if is_edit else False,
        )

        async def _save() -> None:
            data = _extract_account_form(
                inp_name,
                inp_phone,
                inp_password,
                sw_skip,
                sw_force,
                sw_ignore,
                inp_lat=inp_lat,
                inp_lng=inp_lng,
                inp_dist=inp_dist,
                inp_pace=inp_pace,
            )
            if is_edit:
                err = await run.io_bound(update_account, account["index"], data)
            else:
                err = await run.io_bound(add_account, data)
            if err:
                ui.notification(err, type="negative", position="top")
            else:
                ui.notification(
                    "Account updated" if is_edit else "Account added",
                    type="positive",
                    position="top",
                )
                dlg.close()
                on_saved()

        with ui.row().classes("gap-2 mt-4"):
            ui.button("Save", icon="save", on_click=_save)
            ui.button("Cancel", on_click=dlg.close)
    dlg.open()


async def open_delete_dialog(account: dict[str, Any], on_deleted: Any) -> None:
    index = account["index"]
    name = account["name"] or f"account #{index + 1}"
    dlg = ui.dialog()
    with dlg, ui.card():
        ui.label(f"Delete {name}?").classes("text-lg font-semibold")
        ui.label(f"Phone: {account['phone']}").classes("text-sm text-slate-500")
        ui.label("This cannot be undone.").classes("text-sm text-red-500 mt-1")

        async def _confirm() -> None:
            err = await run.io_bound(delete_account, index)
            if err:
                ui.notification(err, type="negative", position="top")
            else:
                ui.notification("Account deleted", type="positive", position="top")
                dlg.close()
                on_deleted()

        with ui.row().classes("gap-2 mt-4"):
            ui.button("Delete", icon="delete", color="red", on_click=_confirm)
            ui.button("Cancel", on_click=dlg.close)
    dlg.open()


# -- config section tab -------------------------------------------------


def config_section_tab(
    key: str,
    fields: list[tuple[str, str, str, type, str | None]],
) -> None:
    """Render a single tab panel for a config section."""
    current = get_section(key) or {}
    widgets: dict[str, Any] = {}

    with ui.column().classes("gap-2"):
        for fname, flabel, ftype, _ft, _funit in fields:
            default = current.get(fname)
            unit_suffix = f" ({_funit})" if _funit else ""
            if ftype == "switch":
                widgets[fname] = ui.switch(flabel, value=bool(default))
            elif ftype == "number":
                decimals = 5 if isinstance(default, float) else 0
                widgets[fname] = ui.number(
                    flabel + unit_suffix,
                    value=float(default) if default else 0.0,
                    format=f"%.{decimals}f",
                )
            else:
                widgets[fname] = ui.input(
                    flabel + unit_suffix,
                    value=str(default) if default is not None else "",
                )

        async def _save(
            _key: str = key, _w: dict = widgets, _fields: list = fields
        ) -> None:
            data: dict[str, Any] = {}
            for fname, _flabel, ftype, _ft, _funit in _fields:
                if ftype == "switch":
                    data[fname] = _w[fname].value
                elif ftype == "number":
                    data[fname] = (
                        _ft(_w[fname].value)
                        if _w[fname].value is not None
                        else (_ft() if _ft is int else 0.0)
                    )
                else:
                    data[fname] = _w[fname].value
            err = await run.io_bound(update_section, _key, data)
            if err:
                ui.notification(err, type="negative", position="top")
            else:
                ui.notification(f"[{_key}] saved", type="positive", position="top")

        ui.button(f"Save [{key}]", icon="save", on_click=_save).classes("mt-2")
