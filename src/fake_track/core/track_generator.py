import itertools
import math
import random
import time

from .geo import add_meter_jitter, haversine_km, polyline_length_km
from .models import (
    GeneratedTrack,
    PassPoint,
    TrackPoint,
    TrackRequest,
    format_timestamp_ms,
)
from .road_graph import _build_road_route_coords, _load_road_graph, _RoadGraph
from .track_filter import _measure_confirmed_distance, _turn_angle_deg


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

    if lat_delta < 1e-7 or lng_delta < 1e-7:
        return _linear_segment(start, end, step_m)

    bend = (start[0], end[1]) if rnd.random() < 0.5 else (end[0], start[1])
    bend = add_meter_jitter(
        bend[0],
        bend[1],
        rnd.uniform(-4.0, 4.0),
        rnd.uniform(-4.0, 4.0),
    )

    first = _linear_segment(start, bend, step_m)
    second = _linear_segment(bend, end, step_m)
    return first + second


def _order_must_pass_nodes(
    start: tuple[float, float],
    must_pass_points: list[PassPoint],
) -> list[tuple[float, float]]:
    nodes = [(p.lat, p.lng) for p in must_pass_points]
    if not nodes:
        return [start, start]

    if len(nodes) > 7:
        remaining = nodes[:]
        ordered: list[tuple[float, float]] = []
        cursor = start
        while remaining:
            next_idx = min(
                range(len(remaining)),
                key=lambda idx: haversine_km(
                    cursor[0], cursor[1], remaining[idx][0], remaining[idx][1]
                ),
            )
            cursor = remaining.pop(next_idx)
            ordered.append(cursor)

        improved = True
        while improved:
            improved = False
            path = [start, *ordered]
            for left in range(1, len(path) - 2):
                for right in range(left + 1, len(path) - 1):
                    old_len = haversine_km(
                        path[left - 1][0],
                        path[left - 1][1],
                        path[left][0],
                        path[left][1],
                    ) + haversine_km(
                        path[right][0],
                        path[right][1],
                        path[right + 1][0],
                        path[right + 1][1],
                    )
                    new_len = haversine_km(
                        path[left - 1][0],
                        path[left - 1][1],
                        path[right][0],
                        path[right][1],
                    ) + haversine_km(
                        path[left][0],
                        path[left][1],
                        path[right + 1][0],
                        path[right + 1][1],
                    )
                    if new_len + 1e-9 < old_len:
                        ordered[left - 1 : right] = reversed(ordered[left - 1 : right])
                        improved = True
                        break
                if improved:
                    break

        return [start, *ordered]

    best_order: tuple[tuple[float, float], ...] | None = None
    best_len = float("inf")
    for order in itertools.permutations(nodes):
        path = [start, *order]
        length = polyline_length_km(path)
        if length < best_len:
            best_len = length
            best_order = order

    assert best_order is not None
    return [start, *best_order]


def _inflate_route_distance(
    route_nodes: list[tuple[float, float]],
    target_distance_km: float,
    rnd: random.Random,
) -> list[tuple[float, float]]:
    current = polyline_length_km(route_nodes)
    if current >= target_distance_km:
        return route_nodes

    extended = list(route_nodes)
    attempts = 0
    while current < target_distance_km and attempts < 80:
        attempts += 1
        segments: list[tuple[int, float]] = []
        for idx in range(1, len(extended)):
            start_pt = extended[idx - 1]
            end_pt = extended[idx]
            segment_km = haversine_km(start_pt[0], start_pt[1], end_pt[0], end_pt[1])
            if segment_km >= 0.03:
                segments.append((idx, segment_km))
        if not segments:
            break

        total_segment_km = sum(length for _idx, length in segments)
        pick = rnd.uniform(0.0, total_segment_km)
        insert_idx = segments[-1][0]
        for idx, length in segments:
            pick -= length
            if pick <= 0.0:
                insert_idx = idx
                break

        start_pt = extended[insert_idx - 1]
        end_pt = extended[insert_idx]
        ratio = rnd.uniform(0.34, 0.66)
        mid = (
            start_pt[0] + (end_pt[0] - start_pt[0]) * ratio,
            start_pt[1] + (end_pt[1] - start_pt[1]) * ratio,
        )

        lat_scale = 111_320.0
        lng_scale = 111_320.0 * max(math.cos(math.radians(mid[0])), 1e-6)
        north_m = (end_pt[0] - start_pt[0]) * lat_scale
        east_m = (end_pt[1] - start_pt[1]) * lng_scale
        seg_m = max(1.0, math.hypot(north_m, east_m))
        side = -1.0 if rnd.random() < 0.5 else 1.0
        extra_needed_m = max(0.0, (target_distance_km - current) * 1000.0)
        offset_m = min(140.0, max(25.0, extra_needed_m * rnd.uniform(0.28, 0.46)))
        detour = add_meter_jitter(
            mid[0],
            mid[1],
            side * (-east_m / seg_m) * offset_m,
            side * (north_m / seg_m) * offset_m,
        )
        extended.insert(insert_idx, detour)
        current = polyline_length_km(extended)

    return extended


def _build_fallback_route_coords(
    route_nodes: list[tuple[float, float]],
    target_distance_km: float,
    step_m: float,
    rnd: random.Random,
) -> list[tuple[float, float]]:
    inflated_nodes = _inflate_route_distance(
        route_nodes,
        target_distance_km,
        rnd,
    )
    coords = [inflated_nodes[0]]
    for index in range(1, len(inflated_nodes)):
        if rnd.random() < 0.15:
            coords.extend(
                _axis_aligned_segment(
                    inflated_nodes[index - 1],
                    inflated_nodes[index],
                    step_m,
                    rnd,
                )
            )
        else:
            coords.extend(
                _linear_segment(
                    inflated_nodes[index - 1],
                    inflated_nodes[index],
                    step_m,
                )
            )
    return coords


class TrackGenerator:
    def __init__(self, road_map_path: str | None = None) -> None:
        self._road_map_path = road_map_path
        self._cached_graph: _RoadGraph | None = None

    @property
    def road_graph(self) -> _RoadGraph | None:
        if self._cached_graph is None and self._road_map_path:
            self._cached_graph = _load_road_graph(self._road_map_path)
        return self._cached_graph

    def generate(self, request: TrackRequest) -> GeneratedTrack:
        rnd = random.Random(request.random_seed)

        route_nodes = _order_must_pass_nodes(request.start, request.must_pass_points)

        speed_m_per_s = 1000.0 / (request.target_pace_min_per_km * 60.0)
        step_m = speed_m_per_s * request.sample_interval_sec

        cached_graph = self.road_graph

        used_road_network = request.road_routing_enabled
        if request.road_routing_enabled:
            coords = _build_road_route_coords(
                route_nodes=route_nodes,
                target_distance_km=request.target_distance_km,
                step_m=step_m,
                rnd=rnd,
                road_map_path=request.road_map_path,
                road_snap_max_m=request.road_snap_max_m,
                must_pass_radius_km=request.must_pass_radius_km,
                graph=cached_graph,
            )
            if coords is None:
                used_road_network = False
                coords = _build_fallback_route_coords(
                    route_nodes=route_nodes,
                    target_distance_km=request.target_distance_km,
                    step_m=step_m,
                    rnd=rnd,
                )
        else:
            coords = _build_fallback_route_coords(
                route_nodes=route_nodes,
                target_distance_km=request.target_distance_km,
                step_m=step_m,
                rnd=rnd,
            )

        now_ms = int(time.time() * 1000)
        point_jitter_m = (
            max(0.2, request.jitter_m * 0.15)
            if used_road_network
            else max(0.35, request.jitter_m * 0.35)
        )

        points: list[TrackPoint] = []
        base_interval_ms = max(350, request.sample_interval_sec * 1000)
        ts_jitter = max(0, request.timestamp_jitter_ms)
        current_ts = now_ms

        for idx, (lat, lng) in enumerate(coords):
            if idx > 0:
                step_ms = base_interval_ms + rnd.randint(-ts_jitter, ts_jitter)
                if used_road_network and idx >= 2:
                    turn_angle = _turn_angle_deg(
                        coords[idx - 2],
                        coords[idx - 1],
                        coords[idx],
                    )
                    if turn_angle > 35.0:
                        step_ms = int(
                            step_ms * (1.0 + min(0.8, (turn_angle - 35.0) / 90.0))
                        )
                step_ms = int(step_ms * rnd.uniform(0.92, 1.12))
                current_ts += max(350, step_ms)

            north = rnd.uniform(-point_jitter_m, point_jitter_m)
            east = rnd.uniform(-point_jitter_m, point_jitter_m)
            jitter_lat, jitter_lng = add_meter_jitter(lat, lng, north, east)
            points.append(
                TrackPoint(
                    latitude=jitter_lat,
                    longitude=jitter_lng,
                    timestamp=current_ts,
                    accuracy=rnd.randint(request.accuracy_min, request.accuracy_max),
                )
            )

        if len(points) < 2:
            raise ValueError("Track generation failed: not enough points")

        raw_distance_km = 0.0
        for idx in range(1, len(points)):
            previous = points[idx - 1]
            current = points[idx]
            raw_distance_km += haversine_km(
                previous.latitude,
                previous.longitude,
                current.latitude,
                current.longitude,
            )

        confirmed_distance_km, confirmed_path = _measure_confirmed_distance(
            points,
            request.filter_policy,
        )
        if confirmed_distance_km <= 1e-6:
            confirmed_distance_km = raw_distance_km
            confirmed_path = points

        duration_sec = max(
            1, round((points[-1].timestamp - points[0].timestamp) / 1000)
        )
        pace = duration_sec / 60.0 / max(confirmed_distance_km, 1e-6)

        pass_count = 0
        for pp in request.must_pass_points:
            hit = any(
                haversine_km(point.latitude, point.longitude, pp.lat, pp.lng)
                <= request.must_pass_radius_km
                for point in points
            )
            if hit:
                pass_count += 1

        return GeneratedTrack(
            points=points,
            distance_km=confirmed_distance_km,
            raw_distance_km=raw_distance_km,
            confirmed_distance_km=confirmed_distance_km,
            confirmed_point_count=len(confirmed_path),
            duration_sec=duration_sec,
            pace_min_per_km=pace,
            must_pass_count=pass_count,
            road_routing_used=used_road_network,
            start_time=format_timestamp_ms(points[0].timestamp),
            end_time=format_timestamp_ms(points[-1].timestamp),
        )
