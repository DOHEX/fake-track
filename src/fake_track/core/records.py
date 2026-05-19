from datetime import datetime

from .models import classify_run_type, semester_for


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
        r["_run_type"] = rt.value if rt else ""
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
            rt = r.get("_run_type", "")
            if rt != run_type:
                continue
        if status:
            sc = int(r.get("status", 0))  # type: ignore[arg-type]
            if status == "valid" and sc != 1:
                continue
            if status == "invalid" and sc != 2:
                continue
        if semester:
            if str(r.get("_semester", "")) != semester:
                continue
        out.append(r)
    return out
