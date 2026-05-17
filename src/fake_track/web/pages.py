"""Web UI pages (NiceGUI)."""

from datetime import datetime
from pathlib import Path
from typing import Any

from nicegui import run, ui

from fake_track.core.client import ApiError, CampusRunClient, get_authenticated_client
from fake_track.core.config import CONFIG_PATH, ConfigError, Settings
from fake_track.core.models import RunType, classify_run_type, semester_for
from fake_track.core.workflow import RunExecutionOptions, RunWorkflow

# -- helpers -----------------------------------------------------------


def _mask_phone(phone: str) -> str:
    text = phone.strip()
    if len(text) <= 4:
        return text
    return f"****{text[-4:]}"


def _account_label(settings: Settings, index: int) -> str:
    name = settings.account_name or f"account-{index}"
    return f"{name} ({_mask_phone(settings.phone)})"


def _load_accounts() -> tuple[list[dict[str, Any]], list[Settings], str | None]:
    try:
        settings_list = Settings.load_all()
    except ConfigError as exc:
        return [], [], str(exc)
    accounts = [
        {
            "index": i,
            "label": _account_label(s, i),
            "name": s.account_name or f"account-{i}",
            "skip_wait": s.skip_wait,
            "force_submit": s.force_submit,
            "ignore_target_met": s.ignore_target_met,
        }
        for i, s in enumerate(settings_list, start=1)
    ]
    return accounts, settings_list, None


def _default_track_image_path() -> str:
    stamp = datetime.now().strftime("web-track-%Y%m%d-%H%M%S.png")
    return str(Path(".local") / "debug-images" / stamp)


def _read_config_text() -> tuple[str | None, str | None]:
    if not CONFIG_PATH.exists():
        return None, f"Config file not found: {CONFIG_PATH}"
    try:
        return CONFIG_PATH.read_text(encoding="utf-8"), None
    except OSError as exc:
        return None, str(exc)


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
        "target_effective": target_effective,
        "target_met": target_effective > 0 and effective >= target_effective,
    }


# -- display helpers ---------------------------------------------------


def _render_counts(
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

    # update header labels if provided
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
                with ui.row().classes("items-center gap-3"):
                    ui.label(label).classes("text-xs text-slate-500 w-10")
                    ui.linear_progress(
                        value=min(done / target, 1.0),
                        size="20px",
                        show_value=False,
                        color="emerald" if met else "blue",
                    ).classes("w-56")
                    ui.label(f"{done}/{target}").classes(
                        "text-xs text-slate-600 w-14 tabular-nums"
                    )
                    if met:
                        ui.badge("met", color="emerald")
                    if today_done:
                        ui.badge("今日已完成", color="green").classes("text-xs")
        else:
            if effective_label is not None:
                effective_label.set_text(f"有效: {effective}")
                effective_label.set_visibility(True)
            if target_met_label is not None:
                target_met_label.set_visibility(False)
            with ui.row().classes("gap-6"):
                ui.label(f"晨跑: {morning}").classes("text-sm text-slate-600")
                ui.label(f"普通: {normal}").classes("text-sm text-slate-600")


def _render_result(container: ui.element, report: dict[str, Any]) -> None:
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


def _render_error(container: ui.element, message: str) -> None:
    container.clear()
    with container:
        with ui.card().classes("bg-red-50 border border-red-200"):
            ui.label(message).classes("text-sm text-red-700")


# -- records dialog ----------------------------------------------------

_RUN_TYPE_LABELS: dict[Any, str] = {"morning": "晨跑", "normal": "普跑"}
_STATUS_LABELS: dict[int, str] = {1: "达标", 2: "无效"}


def _parse_record_dt(record: dict[str, Any]) -> datetime | None:
    start = str(record.get("start_time", "") or "")
    try:
        return datetime.strptime(start[:19], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def _annotate_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for r in records:
        dt = _parse_record_dt(r)
        rt = classify_run_type(dt) if dt else None
        r["_run_type"] = rt.value if rt else ""
        r["_semester"] = semester_for(dt) if dt else ""
    return records


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


async def _open_records_dialog(settings: Settings, dialog: ui.dialog) -> None:
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
                # header
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

                # filter bar
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
                    rows = _build_record_rows(page_records)
                    ui.table(columns=columns, rows=rows, row_key="id").classes("w-full")

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
            filter_state[key] = value  # type: ignore[literal-required]
            page_state["page"] = 1
            _render()

        def _on_page(p: int) -> None:
            page_state["page"] = p
            _render()

        # --- Phase 1: load first batch ---
        resp = await run.io_bound(client.fetch_record_list, 1, BATCH_SIZE)
        data = resp.data if isinstance(resp.data, dict) else {}
        all_records = _annotate_records(list(data.get("list", [])))
        server_total = int(data.get("total", 0))
        semesters = sorted(
            {r.get("_semester", "") for r in all_records if r.get("_semester")},
            reverse=True,
        )
        _render()

        # --- Phase 2: load remaining pages in background ---
        if server_total > BATCH_SIZE:
            loading_more = True
            remaining = server_total - BATCH_SIZE
            pages_needed = (remaining + BATCH_SIZE - 1) // BATCH_SIZE
            for pg in range(2, 2 + pages_needed):
                try:
                    resp = await run.io_bound(client.fetch_record_list, pg, BATCH_SIZE)
                    data = resp.data if isinstance(resp.data, dict) else {}
                    more = _annotate_records(list(data.get("list", [])))
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
                    pass  # keep what we have
            loading_more = False
            _render()
    except Exception as exc:
        dialog.clear()
        with dialog, ui.card().classes("min-w-[750px] max-w-[950px]"):
            ui.label("Error loading records").classes("text-red-600 font-semibold")
            ui.label(str(exc)).classes("text-sm text-slate-600 mt-2")
            ui.button("Close", on_click=dialog.close).classes("mt-4")

    dialog.open()


# -- account card ------------------------------------------------------


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
        dt = _parse_record_dt(r)
        rt = classify_run_type(dt) if dt else None
        if rt is RunType.MORNING:
            done["morning"] = True
        elif rt is RunType.NORMAL:
            done["normal"] = True
    return done


def _build_account_card(account: dict[str, Any], settings: Settings) -> None:
    with ui.card().classes("w-full"):
        # header: name + counts + option badges
        with ui.row().classes("items-center gap-2"):
            ui.label(account["label"]).classes("text-lg font-semibold text-slate-800")
            eff_label = ui.label("").classes("text-xs text-slate-500")
            eff_label.set_visibility(False)
            met_label = ui.label("").classes("text-xs font-medium")
            met_label.set_visibility(False)
            for opt, color in [
                ("skip_wait", "emerald"),
                ("force_submit", "amber"),
                ("ignore_target_met", "sky"),
            ]:
                if account[opt]:
                    ui.badge(opt, color=color).classes("text-xs")

        # counts
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
                counts = _build_counts_payload(student_id, resp.data)
                today = await run.io_bound(_check_today_status, client)
                _render_counts(counts, _container, _eff, _met, today)
            except Exception as exc:  # noqa: BLE001
                _container.clear()
                with _container:
                    ui.label(str(exc)).classes("text-sm text-red-600")

        ui.timer(0.1, _fetch_counts, once=True)

        # run options
        with ui.row().classes("gap-2 mt-1 items-center"):
            chk_skip = ui.checkbox("skip wait", value=account["skip_wait"])
            chk_force = ui.checkbox("force submit", value=account["force_submit"])
            chk_ignore = ui.checkbox(
                "ignore target", value=account["ignore_target_met"]
            )
            chk_image = ui.checkbox("image", value=False)

        # results
        result_container = ui.column().classes("mt-1")

        async def _run(
            _settings: Settings = settings,
            _container: ui.element = result_container,
        ) -> None:
            _container.clear()
            with _container:
                ui.spinner(size="sm")
            try:
                workflow = await run.io_bound(RunWorkflow, _settings)
                options = RunExecutionOptions(
                    skip_submit_wait=chk_skip.value or _settings.skip_wait,
                    force_submit=chk_force.value or _settings.force_submit,
                    ignore_target_met=chk_ignore.value or _settings.ignore_target_met,
                    track_image_path=(
                        _default_track_image_path() if chk_image.value else None
                    ),
                    disable_progress=True,
                )
                report = await run.io_bound(
                    workflow.run_full, progress=None, options=options
                )
                _render_result(_container, report.to_dict())
            except Exception as exc:  # noqa: BLE001
                _render_error(_container, str(exc))

        with ui.row().classes("gap-2 mt-2"):
            ui.button("Refresh", icon="refresh", on_click=_fetch_counts)
            ui.button(
                "Records",
                icon="history",
                on_click=lambda d=ui.dialog(): _open_records_dialog(settings, d),
            )  # type: ignore[arg-type]
            ui.button("Run", icon="play_arrow", color="emerald", on_click=_run)


# -- pages -------------------------------------------------------------


@ui.page("/")
def dashboard() -> None:
    accounts, settings_list, error = _load_accounts()

    with ui.column().classes("w-full max-w-3xl mx-auto gap-4"):
        ui.label("Dashboard").classes("text-2xl font-semibold")

        if error:
            with ui.card().classes("bg-red-50 border border-red-200"):
                ui.label(error).classes("text-sm text-red-700")

        if not accounts:
            ui.label(
                "No accounts configured. "
                "Add accounts in fake-track.toml or set environment variables."
            ).classes("text-sm text-slate-500 mt-4")

        for account, settings in zip(accounts, settings_list):
            _build_account_card(account, settings)


@ui.page("/config")
def config_page() -> None:
    config_text, error = _read_config_text()

    with ui.column().classes("w-full max-w-3xl mx-auto gap-4"):
        ui.label("Config").classes("text-2xl font-semibold")

        if error:
            with ui.card().classes("bg-red-50 border border-red-200"):
                ui.label(error).classes("text-sm text-red-700")
        elif config_text:
            ui.code(config_text, language="toml").classes("w-full max-h-[70vh]")
        else:
            ui.label("No config content available.").classes("text-sm text-slate-500")
