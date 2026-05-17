from dataclasses import dataclass
from pathlib import Path

import matplotlib
import osmnx as ox

matplotlib.use("Agg")
from matplotlib import pyplot as plt

from .models import TrackPoint


@dataclass(frozen=True, slots=True)
class EdgeStyle:
    color: str
    width: float


_EDGE_STYLE_BY_HIGHWAY: dict[str, EdgeStyle] = {
    "motorway": EdgeStyle("#1D3557", 1.6),
    "trunk": EdgeStyle("#1D3557", 1.6),
    "primary": EdgeStyle("#457B9D", 1.4),
    "secondary": EdgeStyle("#4D908E", 1.2),
    "tertiary": EdgeStyle("#43AA8B", 1.1),
    "residential": EdgeStyle("#577590", 1.0),
    "living_street": EdgeStyle("#90BE6D", 1.0),
    "service": EdgeStyle("#F8961E", 0.9),
    "footway": EdgeStyle("#F3722C", 0.8),
    "path": EdgeStyle("#F94144", 0.8),
    "cycleway": EdgeStyle("#277DA1", 0.9),
    "steps": EdgeStyle("#F9844A", 0.8),
}
_DEFAULT_EDGE_STYLE = EdgeStyle("#6C757D", 0.8)


def _normalize_highway_type(raw: object) -> str | None:
    if isinstance(raw, (list, tuple)):
        if not raw:
            return None
        raw = raw[0]
    if raw is None:
        return None
    return str(raw)


def _resolve_edge_style(highway_type: str | None) -> EdgeStyle:
    return _EDGE_STYLE_BY_HIGHWAY.get(highway_type or "", _DEFAULT_EDGE_STYLE)


def render_track_overlay_png(
    map_path: str,
    points: list[TrackPoint],
    output_path: str,
    must_pass_points: list[dict] | None = None,
) -> str:
    if len(points) < 2:
        raise ValueError("Not enough points to render track overlay")

    osm_file = Path(map_path)
    if not osm_file.exists() or not osm_file.is_file():
        raise FileNotFoundError(f"Map file not found: {map_path}")

    try:
        graph = ox.graph_from_xml(str(osm_file), simplify=False, retain_all=True)
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"No highway geometry found in map: {map_path}") from exc

    if graph.number_of_nodes() == 0 or graph.number_of_edges() == 0:
        raise ValueError(f"No highway geometry found in map: {map_path}")

    edge_colors: list[str] = []
    edge_widths: list[float] = []
    for _u, _v, _k, data in graph.edges(keys=True, data=True):
        style = _resolve_edge_style(_normalize_highway_type(data.get("highway")))
        edge_colors.append(style.color)
        edge_widths.append(style.width)

    fig, ax = ox.plot_graph(
        graph,
        node_size=0,
        edge_color=edge_colors,
        edge_linewidth=edge_widths,
        bgcolor="#f5f7fa",
        show=False,
        close=False,
    )

    track_lats = [point.latitude for point in points]
    track_lons = [point.longitude for point in points]
    ax.plot(track_lons, track_lats, color="#DE3036", linewidth=2.8, zorder=4)

    if must_pass_points:
        pass_lats: list[float] = []
        pass_lons: list[float] = []
        for item in must_pass_points:
            try:
                pass_lats.append(float(item["lat"]))
                pass_lons.append(float(item["lng"]))
            except KeyError, TypeError, ValueError:
                continue
        if pass_lats and pass_lons:
            ax.scatter(
                pass_lons,
                pass_lats,
                s=26,
                color="#FFC700",
                edgecolors="#A16207",
                linewidths=0.4,
                zorder=5,
            )

    ax.scatter(
        [points[0].longitude],
        [points[0].latitude],
        s=36,
        color="#16A34A",
        edgecolors="#FFFFFF",
        linewidths=0.6,
        zorder=6,
    )
    ax.scatter(
        [points[-1].longitude],
        [points[-1].latitude],
        s=36,
        color="#DC2626",
        edgecolors="#FFFFFF",
        linewidths=0.6,
        zorder=6,
    )

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(
        output,
        dpi=220,
        facecolor="#f5f7fa",
        bbox_inches="tight",
        pad_inches=0.02,
    )
    plt.close(fig)
    return str(output.resolve())
