from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import osmnx as ox


@dataclass(slots=True)
class HighwayWay:
    refs: list[int]
    highway_type: str | None


@dataclass(slots=True)
class OSMHighwayData:
    nodes: dict[int, tuple[float, float]]
    ways: list[HighwayWay]


def _normalize_highway_type(raw: object) -> str | None:
    if isinstance(raw, (list, tuple)):
        if not raw:
            return None
        raw = raw[0]
    if raw is None:
        return None
    return str(raw)


def _load_osm_highway_data_via_osmnx(abs_path: str) -> OSMHighwayData | None:
    try:
        graph = ox.graph_from_xml(abs_path, simplify=False, retain_all=True)
    except Exception:
        return None

    nodes: dict[int, tuple[float, float]] = {}
    for node_id, data in graph.nodes(data=True):
        lat = data.get("y")
        lon = data.get("x")
        if lat is None or lon is None:
            continue
        try:
            nodes[int(node_id)] = (float(lat), float(lon))
        except TypeError, ValueError:
            continue

    if not nodes:
        return None

    ways: list[HighwayWay] = []
    for node_u, node_v, _key, data in graph.edges(keys=True, data=True):
        try:
            ref_u = int(node_u)
            ref_v = int(node_v)
        except TypeError, ValueError:
            continue
        if ref_u not in nodes or ref_v not in nodes or ref_u == ref_v:
            continue

        ways.append(
            HighwayWay(
                refs=[ref_u, ref_v],
                highway_type=_normalize_highway_type(data.get("highway")),
            )
        )

    if not ways:
        return None

    return OSMHighwayData(nodes=nodes, ways=ways)


@lru_cache(maxsize=4)
def _load_osm_highway_data_cached(
    abs_path: str,
    mtime_ns: int,
) -> OSMHighwayData | None:
    del mtime_ns  # cache key only
    return _load_osm_highway_data_via_osmnx(abs_path)


def load_osm_highway_data(map_path: str) -> OSMHighwayData | None:
    path = Path(map_path)
    if not path.exists() or not path.is_file():
        return None

    try:
        mtime_ns = path.stat().st_mtime_ns
    except OSError:
        return None

    return _load_osm_highway_data_cached(str(path.resolve()), mtime_ns)
