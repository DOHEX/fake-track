import binascii
import struct
import xml.etree.ElementTree as ET
import zlib
from pathlib import Path

from .track import TrackPoint


def _png_chunk(tag: bytes, data: bytes) -> bytes:
    return (
        struct.pack("!I", len(data))
        + tag
        + data
        + struct.pack("!I", binascii.crc32(tag + data) & 0xFFFFFFFF)
    )


def _write_png_rgb(path: Path, width: int, height: int, pixels: bytearray) -> None:
    raw = bytearray()
    stride = width * 3
    for y in range(height):
        raw.append(0)
        row_start = y * stride
        raw.extend(pixels[row_start : row_start + stride])

    png = bytearray(b"\x89PNG\r\n\x1a\n")
    png.extend(
        _png_chunk(
            b"IHDR",
            struct.pack("!IIBBBBB", width, height, 8, 2, 0, 0, 0),
        )
    )
    png.extend(_png_chunk(b"IDAT", zlib.compress(bytes(raw), level=9)))
    png.extend(_png_chunk(b"IEND", b""))
    path.write_bytes(png)


def _set_pixel(
    pixels: bytearray,
    width: int,
    height: int,
    x: int,
    y: int,
    color: tuple[int, int, int],
) -> None:
    if 0 <= x < width and 0 <= y < height:
        idx = (y * width + x) * 3
        pixels[idx : idx + 3] = bytes(color)


def _draw_line(
    pixels: bytearray,
    width: int,
    height: int,
    x0: int,
    y0: int,
    x1: int,
    y1: int,
    color: tuple[int, int, int],
    thickness: int = 1,
) -> None:
    dx = abs(x1 - x0)
    sx = 1 if x0 < x1 else -1
    dy = -abs(y1 - y0)
    sy = 1 if y0 < y1 else -1
    err = dx + dy

    while True:
        for ox in range(-thickness + 1, thickness):
            for oy in range(-thickness + 1, thickness):
                _set_pixel(pixels, width, height, x0 + ox, y0 + oy, color)

        if x0 == x1 and y0 == y1:
            break

        e2 = 2 * err
        if e2 >= dy:
            err += dy
            x0 += sx
        if e2 <= dx:
            err += dx
            y0 += sy


def _draw_circle(
    pixels: bytearray,
    width: int,
    height: int,
    cx: int,
    cy: int,
    radius: int,
    color: tuple[int, int, int],
) -> None:
    r2 = radius * radius
    for dx in range(-radius, radius + 1):
        for dy in range(-radius, radius + 1):
            if dx * dx + dy * dy <= r2:
                _set_pixel(pixels, width, height, cx + dx, cy + dy, color)


def save_track_overlay_png(
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

    root = ET.parse(osm_file).getroot()

    nodes: dict[str, tuple[float, float]] = {}
    for node in root.findall("node"):
        node_id = node.get("id")
        lat = node.get("lat")
        lon = node.get("lon")
        if node_id is None or lat is None or lon is None:
            continue
        try:
            nodes[node_id] = (float(lat), float(lon))
        except ValueError:
            continue

    ways: list[tuple[list[tuple[float, float]], str | None]] = []
    geo_points: list[tuple[float, float]] = []

    for way in root.findall("way"):
        refs = [nd.get("ref") for nd in way.findall("nd") if nd.get("ref") in nodes]
        if len(refs) < 2:
            continue

        htype = None
        for tag in way.findall("tag"):
            if tag.get("k") == "highway":
                htype = tag.get("v")
                break

        coords = [nodes[ref] for ref in refs if ref is not None]
        ways.append((coords, htype))
        geo_points.extend(coords)

    track_geo = [(pt.latitude, pt.longitude) for pt in points]
    geo_points.extend(track_geo)

    must_pass_geo: list[tuple[float, float]] = []
    if must_pass_points:
        for item in must_pass_points:
            try:
                must_pass_geo.append((float(item["lat"]), float(item["lng"])))
            except KeyError, TypeError, ValueError:
                continue
    geo_points.extend(must_pass_geo)

    if not geo_points:
        raise ValueError("No geometry found to render")

    lats = [p[0] for p in geo_points]
    lons = [p[1] for p in geo_points]
    min_lat, max_lat = min(lats), max(lats)
    min_lon, max_lon = min(lons), max(lons)

    width = 1500
    height = 1500
    pad = 50

    bg_color = (245, 247, 250)
    road_colors = {
        "motorway": (29, 53, 87),
        "trunk": (29, 53, 87),
        "primary": (69, 123, 157),
        "secondary": (77, 144, 142),
        "tertiary": (67, 170, 139),
        "residential": (87, 117, 144),
        "living_street": (144, 190, 109),
        "service": (248, 150, 30),
        "footway": (243, 114, 44),
        "path": (249, 65, 68),
        "cycleway": (39, 125, 161),
        "steps": (249, 132, 74),
    }

    pixels = bytearray(bg_color * (width * height))

    def to_xy(lat: float, lon: float) -> tuple[int, int]:
        lon_span = max(1e-12, max_lon - min_lon)
        lat_span = max(1e-12, max_lat - min_lat)
        xr = (lon - min_lon) / lon_span
        yr = (lat - min_lat) / lat_span
        x = int(pad + xr * (width - 1 - 2 * pad))
        y = int((height - 1 - pad) - yr * (height - 1 - 2 * pad))
        return x, y

    for coords, htype in ways:
        color = road_colors.get(htype, (108, 117, 125))
        thickness = 1
        if htype in {
            "primary",
            "secondary",
            "tertiary",
            "residential",
            "living_street",
        }:
            thickness = 2
        for a, b in zip(coords, coords[1:]):
            x0, y0 = to_xy(a[0], a[1])
            x1, y1 = to_xy(b[0], b[1])
            _draw_line(pixels, width, height, x0, y0, x1, y1, color, thickness)

    for a, b in zip(track_geo, track_geo[1:]):
        x0, y0 = to_xy(a[0], a[1])
        x1, y1 = to_xy(b[0], b[1])
        _draw_line(pixels, width, height, x0, y0, x1, y1, (222, 48, 54), 3)

    if must_pass_geo:
        for lat, lon in must_pass_geo:
            x, y = to_xy(lat, lon)
            _draw_circle(pixels, width, height, x, y, 4, (255, 199, 0))

    sx, sy = to_xy(track_geo[0][0], track_geo[0][1])
    ex, ey = to_xy(track_geo[-1][0], track_geo[-1][1])
    _draw_circle(pixels, width, height, sx, sy, 5, (22, 163, 74))
    _draw_circle(pixels, width, height, ex, ey, 5, (220, 38, 38))

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    _write_png_rgb(out, width, height, pixels)
    return str(out.resolve())
