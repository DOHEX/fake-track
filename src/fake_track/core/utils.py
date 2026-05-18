"""Shared utilities used by both CLI and web layers."""

from datetime import datetime
from pathlib import Path

from fake_track.core.client import ApiError
from fake_track.core.models import RunType, classify_run_type, semester_for


def mask_phone(phone: str) -> str:
    text = phone.strip()
    if len(text) <= 4:
        return text
    return f"****{text[-4:]}"


def default_track_image_path(prefix: str = "track-overlay") -> Path:
    stamp = datetime.now().strftime(f"{prefix}-%Y%m%d-%H%M%S.png")
    return Path(".local") / "debug-images" / stamp


def build_counts_payload(student_id: int, counts_data: object) -> dict[str, int | bool]:
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


def parse_record_dt(record: dict[str, object]) -> datetime | None:
    start = str(record.get("start_time", "") or "")
    try:
        return datetime.strptime(start[:19], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def annotate_records(records: list[dict[str, object]]) -> list[dict[str, object]]:
    for r in records:
        dt = parse_record_dt(r)
        rt = classify_run_type(dt) if dt else None
        r["_run_type"] = rt
        r["_semester"] = semester_for(dt) if dt else ""
    return records


def filter_records(
    records: list[dict[str, object]],
    run_type: str | None = None,
    status: str | None = None,
    semester: str | None = None,
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
