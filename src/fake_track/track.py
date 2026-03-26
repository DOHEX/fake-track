import itertools
import random
import time
from dataclasses import dataclass
from datetime import datetime

from .geo import add_meter_jitter, haversine_km, polyline_length_km


@dataclass(slots=True)
class TrackPoint:
    latitude: float
    longitude: float
    timestamp: int
    accuracy: int


@dataclass(slots=True)
class TrackBuildResult:
    points: list[TrackPoint]
    distance_km: float
    duration_sec: int
    pace_min_per_km: float
    must_pass_count: int
    start_time: str
    end_time: str


def _linear_segment(
    start: tuple[float, float],
    end: tuple[float, float],
    step_m: float,
) -> list[tuple[float, float]]:
    segment_km = haversine_km(start[0], start[1], end[0], end[1])
    segment_m = segment_km * 1000
    if segment_m == 0:
        return [end]

    steps = max(1, int(segment_m / max(step_m, 0.5)))

    points: list[tuple[float, float]] = []
    for i in range(1, steps + 1):
        ratio = i / steps
        lat = start[0] + (end[0] - start[0]) * ratio
        lng = start[1] + (end[1] - start[1]) * ratio
        points.append((lat, lng))

    points[-1] = end
    return points


def _axis_aligned_segment(
    start: tuple[float, float],
    end: tuple[float, float],
    step_m: float,
    rnd: random.Random,
) -> list[tuple[float, float]]:
    lat_delta = abs(end[0] - start[0])
    lng_delta = abs(end[1] - start[1])

    # If movement is already mostly one-axis, keep it direct.
    if lat_delta < 1e-7 or lng_delta < 1e-7:
        return _linear_segment(start, end, step_m)

    if rnd.random() < 0.5:
        bend = (start[0], end[1])
    else:
        bend = (end[0], start[1])

    # Keep bend close to axis-aligned geometry with very small offset.
    bend = add_meter_jitter(
        bend[0],
        bend[1],
        rnd.uniform(-4.0, 4.0),
        rnd.uniform(-4.0, 4.0),
    )

    first = _linear_segment(start, bend, step_m)
    second = _linear_segment(bend, end, step_m)
    return first + second


def _ordered_route_nodes(
    start: tuple[float, float],
    must_pass_points: list[dict],
) -> list[tuple[float, float]]:
    nodes = [(float(item["lat"]), float(item["lng"])) for item in must_pass_points]
    if not nodes:
        return [start, start]

    # Find the shortest cycle start -> all points -> start to avoid excessive mileage.
    best_order: tuple[tuple[float, float], ...] | None = None
    best_len = float("inf")
    for order in itertools.permutations(nodes):
        cycle = [start, *order, start]
        length = polyline_length_km(cycle)
        if length < best_len:
            best_len = length
            best_order = order

    assert best_order is not None
    return [start, *best_order, start]


def _extend_route_slightly(
    route_nodes: list[tuple[float, float]],
    target_distance_km: float,
    rnd: random.Random,
) -> list[tuple[float, float]]:
    current = polyline_length_km(route_nodes)
    if current >= target_distance_km:
        return route_nodes

    extended = list(route_nodes)
    start = route_nodes[0]

    # Add tiny detours near start instead of another full pass-point loop.
    while current < target_distance_km:
        detour = add_meter_jitter(
            start[0],
            start[1],
            rnd.uniform(15.0, 35.0),
            rnd.uniform(15.0, 35.0),
        )
        extended.insert(-1, detour)
        current = polyline_length_km(extended)

    return extended


def build_human_like_track(
    start: tuple[float, float],
    must_pass_points: list[dict],
    target_distance_km: float,
    target_pace_min_per_km: float,
    sample_interval_sec: int,
    must_pass_radius_km: float,
    jitter_m: float,
    timestamp_jitter_ms: int,
    accuracy_min: int,
    accuracy_max: int,
    seed: int | None = None,
) -> TrackBuildResult:
    rnd = random.Random(seed)

    route_nodes = _ordered_route_nodes(start, must_pass_points)
    route_nodes = _extend_route_slightly(route_nodes, target_distance_km, rnd)

    speed_m_per_s = 1000.0 / (target_pace_min_per_km * 60.0)
    step_m = speed_m_per_s * sample_interval_sec

    coords: list[tuple[float, float]] = [start]
    for i in range(1, len(route_nodes)):
        if rnd.random() < 0.15:
            coords.extend(
                _axis_aligned_segment(route_nodes[i - 1], route_nodes[i], step_m, rnd)
            )
        else:
            coords.extend(_linear_segment(route_nodes[i - 1], route_nodes[i], step_m))

    now_ms = int(time.time() * 1000)
    point_jitter_m = max(0.35, jitter_m * 0.35)
    points: list[TrackPoint] = []
    base_interval_ms = max(350, sample_interval_sec * 1000)
    ts_jitter = max(0, timestamp_jitter_ms)
    current_ts = now_ms
    for idx, (lat, lng) in enumerate(coords):
        if idx > 0:
            step_ms = base_interval_ms + rnd.randint(-ts_jitter, ts_jitter)
            current_ts += max(350, step_ms)
        north = rnd.uniform(-point_jitter_m, point_jitter_m)
        east = rnd.uniform(-point_jitter_m, point_jitter_m)
        jitter_lat, jitter_lng = add_meter_jitter(lat, lng, north, east)
        accuracy = rnd.randint(accuracy_min, accuracy_max)
        points.append(
            TrackPoint(
                latitude=jitter_lat,
                longitude=jitter_lng,
                timestamp=current_ts,
                accuracy=accuracy,
            )
        )

    if len(points) < 2:
        raise ValueError("Track generation failed: not enough points")

    distance_km = 0.0
    for i in range(1, len(points)):
        a = points[i - 1]
        b = points[i]
        distance_km += haversine_km(a.latitude, a.longitude, b.latitude, b.longitude)

    duration_sec = max(
        1, int(round((points[-1].timestamp - points[0].timestamp) / 1000))
    )
    pace = duration_sec / 60.0 / max(distance_km, 1e-6)

    pass_count = 0
    for p in must_pass_points:
        p_lat, p_lng = float(p["lat"]), float(p["lng"])
        hit = any(
            haversine_km(pt.latitude, pt.longitude, p_lat, p_lng) <= must_pass_radius_km
            for pt in points
        )
        if hit:
            pass_count += 1

    start_time = datetime.fromtimestamp(points[0].timestamp / 1000).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    end_time = datetime.fromtimestamp(points[-1].timestamp / 1000).strftime(
        "%Y-%m-%d %H:%M:%S"
    )

    return TrackBuildResult(
        points=points,
        distance_km=distance_km,
        duration_sec=duration_sec,
        pace_min_per_km=pace,
        must_pass_count=pass_count,
        start_time=start_time,
        end_time=end_time,
    )


def make_summary_payload(
    record_id: int, run: TrackBuildResult, compensation_factor: float
) -> dict:
    mileage_m = round(run.distance_km * compensation_factor * 1000)
    return {
        "record_id": record_id,
        "pace": run.pace_min_per_km or 1,
        "running_time": run.duration_sec,
        "mileage": mileage_m,
        "start_time": run.start_time,
        "end_time": run.end_time,
        "pass_point": run.must_pass_count,
        "step_count": 1,
    }


def make_path_batch_payload(record_id: int, points: list[TrackPoint]) -> dict:
    return {
        "record_id": record_id,
        "path_point": [
            {
                "name": "",
                "lat": p.latitude,
                "lng": p.longitude,
                "timestamp": p.timestamp,
                "accuracy": p.accuracy,
            }
            for p in points
        ],
        "path_image": "",
    }
