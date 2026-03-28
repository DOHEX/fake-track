from math import atan2, cos, pi, radians, sin, sqrt

EARTH_RADIUS_KM = 6371.0
_GCJ_A = 6378245.0
_GCJ_EE = 0.00669342162296594323


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    lat1_r, lng1_r = radians(lat1), radians(lng1)
    lat2_r, lng2_r = radians(lat2), radians(lng2)
    d_lat = lat2_r - lat1_r
    d_lng = lng2_r - lng1_r
    a = sin(d_lat / 2) ** 2 + cos(lat1_r) * cos(lat2_r) * sin(d_lng / 2) ** 2
    c = 2 * atan2(sqrt(a), sqrt(1 - a))
    return EARTH_RADIUS_KM * c


def add_meter_jitter(
    lat: float, lng: float, north_m: float, east_m: float
) -> tuple[float, float]:
    delta_lat = north_m / 111_320.0
    lat_factor = max(cos(radians(lat)), 1e-6)
    delta_lng = east_m / (111_320.0 * lat_factor)
    return lat + delta_lat, lng + delta_lng


def polyline_length_km(points: list[tuple[float, float]]) -> float:
    if len(points) < 2:
        return 0.0
    total = 0.0
    for idx in range(1, len(points)):
        p1 = points[idx - 1]
        p2 = points[idx]
        total += haversine_km(p1[0], p1[1], p2[0], p2[1])
    return total


def _out_of_china(lat: float, lng: float) -> bool:
    return not (72.004 <= lng <= 137.8347 and 0.8293 <= lat <= 55.8271)


def _transform_lat(x: float, y: float) -> float:
    ret = -100.0 + 2.0 * x + 3.0 * y + 0.2 * y * y + 0.1 * x * y + 0.2 * sqrt(abs(x))
    ret += (20.0 * sin(6.0 * x * pi) + 20.0 * sin(2.0 * x * pi)) * 2.0 / 3.0
    ret += (20.0 * sin(y * pi) + 40.0 * sin(y / 3.0 * pi)) * 2.0 / 3.0
    ret += (160.0 * sin(y / 12.0 * pi) + 320.0 * sin(y * pi / 30.0)) * 2.0 / 3.0
    return ret


def _transform_lng(x: float, y: float) -> float:
    ret = 300.0 + x + 2.0 * y + 0.1 * x * x + 0.1 * x * y + 0.1 * sqrt(abs(x))
    ret += (20.0 * sin(6.0 * x * pi) + 20.0 * sin(2.0 * x * pi)) * 2.0 / 3.0
    ret += (20.0 * sin(x * pi) + 40.0 * sin(x / 3.0 * pi)) * 2.0 / 3.0
    ret += (150.0 * sin(x / 12.0 * pi) + 300.0 * sin(x / 30.0 * pi)) * 2.0 / 3.0
    return ret


def _wgs84_delta(lat: float, lng: float) -> tuple[float, float]:
    d_lat = _transform_lat(lng - 105.0, lat - 35.0)
    d_lng = _transform_lng(lng - 105.0, lat - 35.0)
    rad_lat = lat / 180.0 * pi
    magic = sin(rad_lat)
    magic = 1 - _GCJ_EE * magic * magic
    sqrt_magic = sqrt(magic)
    d_lat = (d_lat * 180.0) / (((_GCJ_A * (1 - _GCJ_EE)) / (magic * sqrt_magic)) * pi)
    d_lng = (d_lng * 180.0) / ((_GCJ_A / sqrt_magic) * cos(rad_lat) * pi)
    return d_lat, d_lng


def wgs84_to_gcj02(lat: float, lng: float) -> tuple[float, float]:
    if _out_of_china(lat, lng):
        return lat, lng
    d_lat, d_lng = _wgs84_delta(lat, lng)
    return lat + d_lat, lng + d_lng


def gcj02_to_wgs84(lat: float, lng: float) -> tuple[float, float]:
    if _out_of_china(lat, lng):
        return lat, lng
    d_lat, d_lng = _wgs84_delta(lat, lng)
    return lat - d_lat, lng - d_lng
