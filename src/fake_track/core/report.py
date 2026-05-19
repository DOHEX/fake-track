import json
from dataclasses import dataclass, fields
from datetime import datetime
from pathlib import Path
from typing import Any

from .client import ApiResponse
from .models import RunCounts, RunSummary, RunType, ServerResponses

SUCCESS_RECORD_WARNINGS = frozenset({"恭喜您成绩达标"})


@dataclass(slots=True)
class RunReport:
    success: bool
    mode: str
    record_id: int | None
    summary: RunSummary
    server: ServerResponses
    warning: str | None

    def to_dict(self) -> dict[str, object]:
        return {
            "success": self.success,
            "mode": self.mode,
            "record_id": self.record_id,
            "summary": _dataclass_to_dict(self.summary),
            "server": _dataclass_to_dict(self.server),
            "warning": self.warning,
        }


@dataclass(slots=True)
class _RunResponses:
    login: ApiResponse
    rand_info: ApiResponse
    line: ApiResponse
    check: ApiResponse
    update: ApiResponse
    record: ApiResponse
    path_info: ApiResponse


def _dataclass_to_dict(obj: object) -> dict[str, object]:
    result: dict[str, object] = {}
    for f in fields(obj):  # type: ignore[arg-type]
        result[f.name] = getattr(obj, f.name)
    return result


def _make_skip_summary(
    run_type: RunType | None,
    run_counts: RunCounts | None,
    skip_reason: str,
) -> RunSummary:
    return RunSummary(
        run_type=run_type.value if run_type else None,
        morning=run_counts.morning if run_counts else None,
        normal=run_counts.normal if run_counts else None,
        effective=run_counts.effective if run_counts else None,
        target_effective=run_counts.target_effective if run_counts else None,
        skip_reason=skip_reason,
    )


def _extract_record_warning(record: ApiResponse) -> str | None:
    if isinstance(record.data, dict):
        warning = record.data.get("warning")
    else:
        warning = (
            f"recordInfo response data is not a dict: {type(record.data).__name__}"
        )
    return str(warning).strip() if warning else None


def _is_success_record_warning(warning: str | None) -> bool:
    return warning in SUCCESS_RECORD_WARNINGS


def build_full_report(
    record_id: int,
    run_type: RunType,
    run_counts: RunCounts,
    run_data: Any,  # GeneratedTrack
    final_plan: Any,  # TargetPlan
    run_options: Any,  # RunExecutionOptions
    target_skip_reason: str | None,
    summary_payload: dict[str, Any],
    uploaded_batches: int,
    upload_point_count: int,
    track_image: str | None,
    distance_guard_attempts: int,
    quality_guard_attempts: int,
    responses: _RunResponses,
) -> RunReport:
    warning_text = _extract_record_warning(responses.record)
    is_success_warning = _is_success_record_warning(warning_text)

    return RunReport(
        success=warning_text is None or is_success_warning,
        mode="full",
        record_id=record_id,
        summary=RunSummary(
            run_type=run_type.value,
            morning=run_counts.morning,
            normal=run_counts.normal,
            effective=run_counts.effective,
            target_effective=run_counts.target_effective,
            target_met=(
                run_counts.target_effective > 0
                and run_counts.effective >= run_counts.target_effective
            ),
            generated_distance_km=round(run_data.distance_km, 4),
            generated_distance_raw_km=round(run_data.raw_distance_km, 4),
            generated_distance_confirmed_km=round(run_data.confirmed_distance_km, 4),
            generated_confirmed_point_count=run_data.confirmed_point_count,
            generated_duration_sec=run_data.duration_sec,
            generated_pace_min_per_km=round(run_data.pace_min_per_km, 4),
            generated_pass_point=run_data.must_pass_count,
            generated_point_count=len(run_data.points),
            generated_distance_guard_min_km=round(final_plan.distance_guard_min_km, 4),
            generated_distance_guard_attempts=distance_guard_attempts,
            generated_quality_guard_attempts=quality_guard_attempts,
            generated_road_routing_used=run_data.road_routing_used,
            generated_track_image_enabled=(run_options.track_image_path is not None),
            generated_submit_wait_skipped=run_options.skip_submit_wait,
            generated_force_submit=run_options.force_submit,
            generated_ignore_target_met=run_options.ignore_target_met,
            generated_ignored_target_skip_reason=target_skip_reason,
            generated_track_image=track_image,
            uploaded_batches=uploaded_batches,
            uploaded_point_count=upload_point_count,
            target_duration_sec=final_plan.target_duration_sec,
            server_warning_detected=warning_text is not None,
            server_warning_treated_as_success=is_success_warning,
            record_payload=summary_payload,
        ),
        server=ServerResponses(
            login=responses.login.raw,
            randrunInfo=responses.rand_info.raw,
            createLine=responses.line.raw,
            checkRecord=responses.check.raw,
            updateRecordNew=responses.update.raw,
            recordInfo=responses.record.raw,
            GetPathPoints=responses.path_info.raw,
        ),
        warning=warning_text,
    )


def write_report_file(report: RunReport, report_path: str | None) -> None:
    if not report_path:
        return

    path = Path(report_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "report": report.to_dict(),
    }
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
