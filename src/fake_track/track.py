import itertools
import math
import random
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from heapq import heappop, heappush
from pathlib import Path

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
    road_routing_used: bool
    start_time: str
    end_time: str


@dataclass(slots=True)
class _RoadGraph:
    nodes: dict[int, tuple[float, float]]
    adjacency: dict[int, list[tuple[int, float]]]


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


def _resample_polyline(
    points: list[tuple[float, float]],
    step_m: float,
) -> list[tuple[float, float]]:
    if len(points) < 2:
        return points

    step_km = max(step_m, 0.5) / 1000.0
    sampled: list[tuple[float, float]] = [points[0]]
    dist_since_last = 0.0
    seg_start = points[0]

    for seg_end in points[1:]:
        local_start = seg_start
        local_end = seg_end
        local_len = haversine_km(
            local_start[0], local_start[1], local_end[0], local_end[1]
        )
        if local_len <= 1e-9:
            seg_start = seg_end
            continue

        while dist_since_last + local_len >= step_km:
            need_km = step_km - dist_since_last
            ratio = need_km / local_len
            new_point = (
                local_start[0] + (local_end[0] - local_start[0]) * ratio,
                local_start[1] + (local_end[1] - local_start[1]) * ratio,
            )
            sampled.append(new_point)
            local_start = new_point
            local_len = haversine_km(
                local_start[0], local_start[1], local_end[0], local_end[1]
            )
            dist_since_last = 0.0
            if local_len <= 1e-9:
                break

        dist_since_last += local_len
        seg_start = seg_end

    if sampled[-1] != points[-1]:
        sampled.append(points[-1])
    return sampled


def _turn_angle_deg(
    p1: tuple[float, float],
    p2: tuple[float, float],
    p3: tuple[float, float],
) -> float:
    v1 = (p2[0] - p1[0], p2[1] - p1[1])
    v2 = (p3[0] - p2[0], p3[1] - p2[1])
    n1 = math.hypot(v1[0], v1[1])
    n2 = math.hypot(v2[0], v2[1])
    if n1 <= 1e-12 or n2 <= 1e-12:
        return 0.0
    dot = v1[0] * v2[0] + v1[1] * v2[1]
    cos_v = max(-1.0, min(1.0, dot / (n1 * n2)))
    return math.degrees(math.acos(cos_v))


@lru_cache(maxsize=4)
def _load_road_graph_cached(
    abs_path: str,
    mtime_ns: int,
) -> _RoadGraph | None:
    del mtime_ns  # only used to bust cache when file changes

    try:
        root = ET.parse(abs_path).getroot()
    except ET.ParseError:
        return None

    nodes: dict[int, tuple[float, float]] = {}
    for node in root.findall("node"):
        node_id = node.get("id")
        lat = node.get("lat")
        lon = node.get("lon")
        if node_id is None or lat is None or lon is None:
            continue
        try:
            nodes[int(node_id)] = (float(lat), float(lon))
        except ValueError:
            continue

    adjacency: dict[int, list[tuple[int, float]]] = {}
    for way in root.findall("way"):
        has_highway = False
        for tag in way.findall("tag"):
            if tag.get("k") == "highway":
                has_highway = True
                break
        if not has_highway:
            continue

        refs: list[int] = []
        for nd in way.findall("nd"):
            ref = nd.get("ref")
            if ref is None:
                continue
            try:
                ref_id = int(ref)
            except ValueError:
                continue
            if ref_id in nodes:
                refs.append(ref_id)

        if len(refs) < 2:
            continue

        for node_a, node_b in zip(refs, refs[1:]):
            coord_a = nodes[node_a]
            coord_b = nodes[node_b]
            dist_km = haversine_km(coord_a[0], coord_a[1], coord_b[0], coord_b[1])
            if dist_km <= 0.0:
                continue
            adjacency.setdefault(node_a, []).append((node_b, dist_km))
            adjacency.setdefault(node_b, []).append((node_a, dist_km))

    if not adjacency:
        return None
    return _RoadGraph(nodes=nodes, adjacency=adjacency)


def _load_road_graph(map_path: str) -> _RoadGraph | None:
    path = Path(map_path)
    if not path.exists() or not path.is_file():
        return None
    try:
        mtime_ns = path.stat().st_mtime_ns
    except OSError:
        return None
    return _load_road_graph_cached(str(path.resolve()), mtime_ns)


def _nearest_graph_node(
    graph: _RoadGraph,
    point: tuple[float, float],
) -> tuple[int, float] | None:
    nearest_id: int | None = None
    nearest_dist = float("inf")
    for node_id, (lat, lng) in graph.nodes.items():
        dist = haversine_km(point[0], point[1], lat, lng)
        if dist < nearest_dist:
            nearest_dist = dist
            nearest_id = node_id
    if nearest_id is None:
        return None
    return nearest_id, nearest_dist


def _shortest_path_nodes(
    graph: _RoadGraph,
    start_id: int,
    end_id: int,
) -> list[int] | None:
    if start_id == end_id:
        return [start_id]

    dist: dict[int, float] = {start_id: 0.0}
    prev: dict[int, int] = {}
    heap: list[tuple[float, int]] = [(0.0, start_id)]

    while heap:
        current_dist, current_id = heappop(heap)
        if current_dist > dist.get(current_id, float("inf")):
            continue
        if current_id == end_id:
            break

        for next_id, edge_km in graph.adjacency.get(current_id, []):
            candidate = current_dist + edge_km
            if candidate < dist.get(next_id, float("inf")):
                dist[next_id] = candidate
                prev[next_id] = current_id
                heappush(heap, (candidate, next_id))

    if end_id not in dist:
        return None

    path = [end_id]
    cursor = end_id
    while cursor != start_id:
        cursor = prev[cursor]
        path.append(cursor)
    path.reverse()
    return path


def _move_towards(
    source: tuple[float, float],
    target: tuple[float, float],
    distance_km: float,
) -> tuple[float, float]:
    if distance_km <= 0:
        return source
    total = haversine_km(source[0], source[1], target[0], target[1])
    if total <= 1e-9:
        return source
    ratio = min(1.0, distance_km / total)
    return (
        source[0] + (target[0] - source[0]) * ratio,
        source[1] + (target[1] - source[1]) * ratio,
    )


def _build_road_aligned_coords(
    route_nodes: list[tuple[float, float]],
    target_distance_km: float,
    step_m: float,
    rnd: random.Random,
    road_map_path: str,
    road_snap_max_m: float,
    must_pass_radius_km: float,
) -> list[tuple[float, float]] | None:
    graph = _load_road_graph(road_map_path)
    if graph is None:
        return None

    if len(route_nodes) < 2:
        return None

    snap_limit_km = max(1.0, road_snap_max_m) / 1000.0
    snapped_ids: list[int] = []
    snapped_coords: list[tuple[float, float]] = []
    for point in route_nodes:
        nearest = _nearest_graph_node(graph, point)
        if nearest is None:
            return None
        snapped_ids.append(nearest[0])
        snapped_coords.append(graph.nodes[nearest[0]])

    coords: list[tuple[float, float]] = [route_nodes[0]]
    hit_targets: list[tuple[float, float]] = [route_nodes[0]]
    for idx in range(1, len(snapped_ids)):
        prev_point = route_nodes[idx - 1]
        next_point = route_nodes[idx]
        prev_snap = snapped_coords[idx - 1]
        next_snap = snapped_coords[idx]

        dist_snap_to_point = haversine_km(
            next_point[0], next_point[1], next_snap[0], next_snap[1]
        )
        exact_hit_limit_km = max(
            0.005,
            min(snap_limit_km, max(0.001, must_pass_radius_km * 1.2)),
        )
        if dist_snap_to_point > exact_hit_limit_km and must_pass_radius_km > 0:
            target_point = _move_towards(
                source=next_point,
                target=next_snap,
                distance_km=max(0.001, must_pass_radius_km * 0.8),
            )
        else:
            target_point = next_point
        hit_targets.append(target_point)

        segment: list[tuple[float, float]] = []
        if (
            haversine_km(prev_point[0], prev_point[1], prev_snap[0], prev_snap[1])
            > 1e-6
        ):
            segment.append(prev_snap)

        path_nodes = _shortest_path_nodes(graph, snapped_ids[idx - 1], snapped_ids[idx])
        if path_nodes is None:
            if not segment:
                segment.append(prev_snap)
            if (
                haversine_km(segment[-1][0], segment[-1][1], next_snap[0], next_snap[1])
                > 1e-6
            ):
                segment.append(next_snap)
        else:
            path_coords = [graph.nodes[node_id] for node_id in path_nodes]
            if segment and path_coords:
                if (
                    haversine_km(
                        segment[-1][0],
                        segment[-1][1],
                        path_coords[0][0],
                        path_coords[0][1],
                    )
                    <= 1e-6
                ):
                    segment.extend(path_coords[1:])
                else:
                    segment.extend(path_coords)
            else:
                segment.extend(path_coords)

        # Allow stepping off the nearest road to ensure must-pass points can be touched.
        if (
            not segment
            or haversine_km(
                segment[-1][0], segment[-1][1], target_point[0], target_point[1]
            )
            > 1e-6
        ):
            segment.append(target_point)

        for coord in segment:
            if haversine_km(coords[-1][0], coords[-1][1], coord[0], coord[1]) > 1e-6:
                coords.append(coord)

    if len(coords) < 2:
        return None

    distance_km = polyline_length_km(coords)

    if distance_km < target_distance_km:
        tail_id = snapped_ids[-1]
        neighbors = graph.adjacency.get(tail_id, [])
        if neighbors:
            preferred = [
                (neighbor_id, edge_km)
                for neighbor_id, edge_km in neighbors
                if 0.01 <= edge_km <= 0.08
            ]
            candidates = preferred or neighbors
            detour_id, detour_km = rnd.choice(candidates)
            detour_pair_km = max(1e-6, detour_km * 2.0)
            gap_km = max(0.0, target_distance_km - distance_km)
            floor_loops = max(1, int(gap_km / detour_pair_km))
            ceil_loops = min(500, floor_loops + 1)
            candidates_loops = {floor_loops, ceil_loops}
            loops = min(
                candidates_loops,
                key=lambda n: abs(
                    distance_km + detour_pair_km * n - target_distance_km
                ),
            )

            anchor = graph.nodes[tail_id]
            end_point = coords[-1]
            detour_coords = coords[:-1]
            if (
                haversine_km(
                    detour_coords[-1][0],
                    detour_coords[-1][1],
                    anchor[0],
                    anchor[1],
                )
                > 1e-6
            ):
                detour_coords.append(anchor)
            for _ in range(max(1, min(500, loops))):
                detour_coords.extend([graph.nodes[detour_id], anchor])
                distance_km += detour_km * 2.0
            if (
                haversine_km(
                    detour_coords[-1][0],
                    detour_coords[-1][1],
                    end_point[0],
                    end_point[1],
                )
                > 1e-6
            ):
                detour_coords.append(end_point)
            coords = detour_coords

    if len(coords) < 2:
        return None

    resampled = _resample_polyline(coords, step_m)
    if len(resampled) < 2:
        return None

    resampled[0] = route_nodes[0]
    resampled[-1] = hit_targets[-1]

    # Keep must-hit targets on the final polyline so pass-point hit checks remain stable.
    for control_point in hit_targets[1:-1]:
        nearest_idx = min(
            range(len(resampled)),
            key=lambda idx: haversine_km(
                resampled[idx][0],
                resampled[idx][1],
                control_point[0],
                control_point[1],
            ),
        )
        resampled[nearest_idx] = control_point

    return resampled


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

    # Find the shortest open path start -> all points (do not force return to start).
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
    road_routing_enabled: bool = True,
    road_map_path: str = "map.osm",
    road_snap_max_m: float = 120.0,
) -> TrackBuildResult:
    rnd = random.Random(seed)

    route_nodes = _ordered_route_nodes(start, must_pass_points)

    speed_m_per_s = 1000.0 / (target_pace_min_per_km * 60.0)
    step_m = speed_m_per_s * sample_interval_sec

    used_road_network = road_routing_enabled
    if road_routing_enabled:
        coords = _build_road_aligned_coords(
            route_nodes=route_nodes,
            target_distance_km=target_distance_km,
            step_m=step_m,
            rnd=rnd,
            road_map_path=road_map_path,
            road_snap_max_m=road_snap_max_m,
            must_pass_radius_km=must_pass_radius_km,
        )
        if coords is None:
            raise ValueError(
                "Road routing is enabled but failed to build a road-aligned track"
            )
    else:
        route_nodes = _extend_route_slightly(route_nodes, target_distance_km, rnd)
        coords = [start]
        for i in range(1, len(route_nodes)):
            if rnd.random() < 0.15:
                coords.extend(
                    _axis_aligned_segment(
                        route_nodes[i - 1], route_nodes[i], step_m, rnd
                    )
                )
            else:
                coords.extend(
                    _linear_segment(route_nodes[i - 1], route_nodes[i], step_m)
                )

    now_ms = int(time.time() * 1000)
    if used_road_network:
        point_jitter_m = max(0.2, jitter_m * 0.15)
    else:
        point_jitter_m = max(0.35, jitter_m * 0.35)

    points: list[TrackPoint] = []
    base_interval_ms = max(350, sample_interval_sec * 1000)
    ts_jitter = max(0, timestamp_jitter_ms)
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
        road_routing_used=used_road_network,
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
