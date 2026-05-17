from collections.abc import Sequence

from .models import TrackBuildResult, TrackPoint


def build_run_summary_payload(
    record_id: int,
    run_result: TrackBuildResult,
    compensation_factor: float,
) -> dict[str, float | int | str]:
    mileage_m = round(run_result.distance_km * compensation_factor * 1000)
    return {
        "record_id": record_id,
        "pace": run_result.pace_min_per_km or 1,
        "running_time": run_result.duration_sec,
        "mileage": mileage_m,
        "start_time": run_result.start_time,
        "end_time": run_result.end_time,
        "pass_point": run_result.must_pass_count,
        "step_count": 1,
    }


def build_path_upload_payload(
    record_id: int,
    points: Sequence[TrackPoint],
) -> dict[str, int | str | list[dict[str, int | float | str]]]:
    return {
        "record_id": record_id,
        "path_point": [
            {
                "name": "",
                "lat": point.latitude,
                "lng": point.longitude,
                "timestamp": point.timestamp,
                "accuracy": point.accuracy,
            }
            for point in points
        ],
        "path_image": "",
    }
