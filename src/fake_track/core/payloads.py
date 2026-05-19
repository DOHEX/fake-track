from collections.abc import Sequence

from .models import GeneratedTrack, PassPoint, RunCounts, TrackPoint


def build_run_summary_payload(
    record_id: int,
    run_result: GeneratedTrack,
) -> dict[str, float | int | str]:
    mileage_m = round(run_result.distance_km * 1000)
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


def build_counts_payload(student_id: int, counts_data: object) -> RunCounts:
    from .client import ApiError

    if not isinstance(counts_data, dict):
        raise ApiError(
            f"Run counts response data is not a dict: {type(counts_data).__name__}"
        )
    return RunCounts(
        morning=int(counts_data.get("morning", 0)),
        normal=int(counts_data.get("universal", 0)),
        effective=int(counts_data.get("effective", 0)),
        target_effective=int(counts_data.get("target_effective", 0)),
    )


def pass_points_to_dicts(points: Sequence[PassPoint]) -> list[dict[str, float]]:
    return [{"lat": p.lat, "lng": p.lng} for p in points]


def dicts_to_pass_points(items: list[dict]) -> list[PassPoint]:
    result: list[PassPoint] = []
    for item in items:
        try:
            result.append(PassPoint(lat=float(item["lat"]), lng=float(item["lng"])))
        except KeyError, TypeError, ValueError:
            continue
    return result
