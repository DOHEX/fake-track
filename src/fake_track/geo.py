from math import atan2, cos, radians, sin, sqrt

EARTH_RADIUS_KM = 6371.0


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
