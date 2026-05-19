import random
from dataclasses import dataclass
from functools import lru_cache
from heapq import heappop, heappush
from pathlib import Path

from .geo import haversine_km, polyline_length_km
from .osm import load_osm_highway_data


@dataclass(slots=True)
class _RoadGraph:
    nodes: dict[int, tuple[float, float]]
    adjacency: dict[int, list[tuple[int, float]]]


@dataclass(slots=True, frozen=True)
class _RoadAnchor:
    coord_index: int
    node_id: int
    progress_ratio: float


@dataclass(slots=True, frozen=True)
class _RoadDetour:
    anchor: _RoadAnchor
    nodes: tuple[int, ...]
    distance_km: float


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


def _node_path_distance_km(
    graph: _RoadGraph,
    node_ids: list[int] | tuple[int, ...],
) -> float:
    return polyline_length_km([graph.nodes[node_id] for node_id in node_ids])


def _node_path_coords(
    graph: _RoadGraph,
    node_ids: list[int] | tuple[int, ...],
) -> list[tuple[float, float]]:
    return [graph.nodes[node_id] for node_id in node_ids]


def _dedupe_coords(
    points: list[tuple[float, float]],
    min_gap_km: float = 1e-6,
) -> list[tuple[float, float]]:
    deduped: list[tuple[float, float]] = []
    for point in points:
        if not deduped:
            deduped.append(point)
            continue
        if (
            haversine_km(deduped[-1][0], deduped[-1][1], point[0], point[1])
            > min_gap_km
        ):
            deduped.append(point)
    return deduped


@lru_cache(maxsize=4)
def _load_road_graph_cached(
    abs_path: str,
    mtime_ns: int,
) -> _RoadGraph | None:
    del mtime_ns

    osm_data = load_osm_highway_data(abs_path)
    if osm_data is None:
        return None

    edge_lengths: dict[int, dict[int, float]] = {}

    def add_edge(node_a: int, node_b: int, distance_km: float) -> None:
        neighbors = edge_lengths.setdefault(node_a, {})
        previous = neighbors.get(node_b)
        if previous is None or distance_km < previous:
            neighbors[node_b] = distance_km

    for way in osm_data.ways:
        for node_a, node_b in zip(way.refs, way.refs[1:], strict=False):
            coord_a = osm_data.nodes[node_a]
            coord_b = osm_data.nodes[node_b]
            dist_km = haversine_km(coord_a[0], coord_a[1], coord_b[0], coord_b[1])
            if dist_km <= 0.0:
                continue
            add_edge(node_a, node_b, dist_km)
            add_edge(node_b, node_a, dist_km)

    if not edge_lengths:
        return None
    adjacency = {
        node_id: sorted(neighbors.items())
        for node_id, neighbors in edge_lengths.items()
        if neighbors
    }
    nodes = {node_id: osm_data.nodes[node_id] for node_id in adjacency}
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
    blocked_ids: set[int] | None = None,
    max_distance_km: float | None = None,
) -> list[int] | None:
    blocked = blocked_ids or set()
    if start_id in blocked or end_id in blocked:
        return None

    if start_id == end_id:
        return [start_id]

    dist: dict[int, float] = {start_id: 0.0}
    prev: dict[int, int] = {}
    heap: list[tuple[float, int]] = [(0.0, start_id)]

    while heap:
        current_dist, current_id = heappop(heap)
        if current_dist > dist.get(current_id, float("inf")):
            continue
        if max_distance_km is not None and current_dist > max_distance_km:
            continue
        if current_id == end_id:
            break

        for next_id, edge_km in graph.adjacency.get(current_id, []):
            if next_id in blocked:
                continue
            candidate = current_dist + edge_km
            if max_distance_km is not None and candidate > max_distance_km:
                continue
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


def _find_route_anchors(
    graph: _RoadGraph,
    coords: list[tuple[float, float]],
    road_snap_max_m: float,
) -> list[_RoadAnchor]:
    if len(coords) < 4:
        return []

    total_km = max(polyline_length_km(coords), 1e-9)
    snap_limit_km = min(0.012, max(0.003, road_snap_max_m / 1000.0 * 0.12))
    end_point = coords[-1]
    anchors: list[_RoadAnchor] = []
    seen: set[int] = set()
    traveled_km = 0.0

    for idx in range(1, len(coords) - 1):
        previous = coords[idx - 1]
        current = coords[idx]
        traveled_km += haversine_km(previous[0], previous[1], current[0], current[1])
        progress_ratio = traveled_km / total_km
        if progress_ratio < 0.12 or progress_ratio > 0.78:
            continue
        if haversine_km(current[0], current[1], end_point[0], end_point[1]) < 0.18:
            continue

        nearest = _nearest_graph_node(graph, current)
        if nearest is None:
            continue
        node_id, snap_km = nearest
        if snap_km > snap_limit_km or node_id in seen:
            continue
        neighbor_count = len(
            {neighbor_id for neighbor_id, _edge_km in graph.adjacency.get(node_id, [])}
        )
        if neighbor_count < 2:
            continue

        seen.add(node_id)
        anchors.append(
            _RoadAnchor(
                coord_index=idx,
                node_id=node_id,
                progress_ratio=progress_ratio,
            )
        )

    return anchors


def _cycle_detours_for_anchor(
    graph: _RoadGraph,
    anchor: _RoadAnchor,
    max_cycle_km: float,
) -> list[_RoadDetour]:
    neighbors = list(dict(graph.adjacency.get(anchor.node_id, [])).items())
    if len(neighbors) < 2:
        return []

    detours: list[_RoadDetour] = []
    signatures: set[tuple[int, ...]] = set()
    for left_idx, (left_id, left_km) in enumerate(neighbors):
        for right_id, right_km in neighbors[left_idx + 1 :]:
            budget_km = max_cycle_km - left_km - right_km
            if budget_km <= 0.02:
                continue

            middle = _shortest_path_nodes(
                graph,
                left_id,
                right_id,
                blocked_ids={anchor.node_id},
                max_distance_km=budget_km,
            )
            if middle is None or len(middle) < 2:
                continue

            nodes = (anchor.node_id, *middle, anchor.node_id)
            distance_km = _node_path_distance_km(graph, nodes)
            if distance_km < 0.12 or distance_km > max_cycle_km:
                continue

            forward = nodes[1:-1]
            reverse = tuple(reversed(forward))
            signature = min(forward, reverse)
            if signature in signatures:
                continue
            signatures.add(signature)
            detours.append(
                _RoadDetour(
                    anchor=anchor,
                    nodes=nodes,
                    distance_km=distance_km,
                )
            )

    detours.sort(key=lambda item: item.distance_km)
    return detours


def _spur_detour_for_anchor(
    graph: _RoadGraph,
    anchor: _RoadAnchor,
    rnd: random.Random,
) -> _RoadDetour | None:
    neighbors = [
        (neighbor_id, edge_km)
        for neighbor_id, edge_km in graph.adjacency.get(anchor.node_id, [])
        if 0.02 <= edge_km <= 0.14
    ]
    if not neighbors:
        return None
    neighbor_id, edge_km = rnd.choice(neighbors)
    nodes = (anchor.node_id, neighbor_id, anchor.node_id)
    return _RoadDetour(
        anchor=anchor,
        nodes=nodes,
        distance_km=edge_km * 2.0,
    )


def _insert_road_detours(
    graph: _RoadGraph,
    coords: list[tuple[float, float]],
    target_distance_km: float,
    rnd: random.Random,
    road_snap_max_m: float,
) -> list[tuple[float, float]]:
    current_km = polyline_length_km(coords)
    if current_km >= target_distance_km:
        return coords

    anchors = _find_route_anchors(graph, coords, road_snap_max_m)
    if not anchors:
        return coords

    insertions: dict[int, list[list[tuple[float, float]]]] = {}
    used_anchors: set[int] = set()
    used_signatures: set[tuple[int, ...]] = set()

    for _attempt in range(min(10, max(2, len(anchors)))):
        remaining_km = target_distance_km - current_km
        if remaining_km <= 0.02:
            break

        max_cycle_km = min(0.85, max(0.22, remaining_km + 0.16))
        candidates: list[tuple[float, _RoadDetour]] = []
        for anchor in anchors:
            if anchor.node_id in used_anchors:
                continue
            for detour in _cycle_detours_for_anchor(graph, anchor, max_cycle_km):
                signature = detour.nodes[1:-1]
                if signature in used_signatures:
                    continue
                overshoot_km = max(0.0, detour.distance_km - remaining_km)
                if overshoot_km > 0.18:
                    continue
                midpoint_bias = abs(anchor.progress_ratio - 0.48) * 0.12
                score = (
                    abs(remaining_km - detour.distance_km)
                    + overshoot_km * 1.8
                    + midpoint_bias
                    + rnd.random() * 0.035
                )
                candidates.append((score, detour))

        if not candidates:
            spur_anchors = [
                anchor for anchor in anchors if anchor.node_id not in used_anchors
            ]
            rnd.shuffle(spur_anchors)
            for anchor in spur_anchors:
                detour = _spur_detour_for_anchor(graph, anchor, rnd)
                if detour is None:
                    continue
                if detour.distance_km - remaining_km > 0.12:
                    continue
                candidates.append(
                    (abs(remaining_km - detour.distance_km) + 0.4, detour)
                )
                break

        if not candidates:
            break

        candidates.sort(key=lambda item: item[0])
        chosen = rnd.choice(candidates[: min(3, len(candidates))])[1]
        used_anchors.add(chosen.anchor.node_id)
        used_signatures.add(chosen.nodes[1:-1])
        detour_coords = _node_path_coords(graph, chosen.nodes)
        insertions.setdefault(chosen.anchor.coord_index, []).append(detour_coords)
        current_km += chosen.distance_km

    if not insertions:
        return coords

    inflated: list[tuple[float, float]] = []
    for idx, coord in enumerate(coords):
        inflated.append(coord)
        for detour_coords in insertions.get(idx, []):
            for detour_coord in detour_coords:
                if (
                    haversine_km(
                        inflated[-1][0],
                        inflated[-1][1],
                        detour_coord[0],
                        detour_coord[1],
                    )
                    > 1e-6
                ):
                    inflated.append(detour_coord)

    return _dedupe_coords(inflated)


def _build_road_route_coords(
    route_nodes: list[tuple[float, float]],
    target_distance_km: float,
    step_m: float,
    rnd: random.Random,
    road_map_path: str,
    road_snap_max_m: float,
    must_pass_radius_km: float,
    graph: _RoadGraph | None = None,
) -> list[tuple[float, float]] | None:
    if graph is None:
        graph = _load_road_graph(road_map_path)
    if graph is None or len(route_nodes) < 2:
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

    coords = _insert_road_detours(
        graph=graph,
        coords=coords,
        target_distance_km=target_distance_km,
        rnd=rnd,
        road_snap_max_m=road_snap_max_m,
    )

    resampled = _resample_polyline(coords, step_m)
    if len(resampled) < 2:
        return None

    resampled[0] = route_nodes[0]
    resampled[-1] = hit_targets[-1]

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
