import math

from .geo import haversine_km
from .models import TrackFilterPolicy, TrackPoint


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


def _is_stationary_like(
    previous_confirmed: TrackPoint,
    candidate: TrackPoint,
    policy: TrackFilterPolicy,
) -> bool:
    dist_km = haversine_km(
        previous_confirmed.latitude,
        previous_confirmed.longitude,
        candidate.latitude,
        candidate.longitude,
    )
    dist_m = dist_km * 1000.0
    dt_sec = (candidate.timestamp - previous_confirmed.timestamp) / 1000.0
    if dt_sec <= 0:
        return dist_m < 1.0

    speed_m_s = dist_m / dt_sec
    if candidate.accuracy > 80:
        if dt_sec < 5:
            return False
        if dist_m < 5.0:
            return True
        if speed_m_s < 1.0:
            return True
        return speed_m_s < 2.0 and dist_m < 10.0

    return dist_m < policy.min_move_distance_m and speed_m_s < policy.min_move_speed_m_s


def _measure_confirmed_distance(
    points: list[TrackPoint],
    policy: TrackFilterPolicy,
) -> tuple[float, list[TrackPoint]]:
    if len(points) < 2:
        return 0.0, points[:]

    validation_buffer: list[TrackPoint] = []
    confirmed_path: list[TrackPoint] = []
    last_confirmed: TrackPoint | None = None
    confirmed_distance_km = 0.0

    for point in points:
        if point.accuracy > policy.gps_accuracy_threshold_m:
            continue

        if last_confirmed is not None and _is_stationary_like(
            previous_confirmed=last_confirmed,
            candidate=point,
            policy=policy,
        ):
            if validation_buffer:
                stationary_anchor = validation_buffer[-1]
                last_confirmed = stationary_anchor
                if not confirmed_path or confirmed_path[-1] != stationary_anchor:
                    confirmed_path.append(stationary_anchor)
            validation_buffer = [point]
            continue

        if not validation_buffer:
            validation_buffer.append(point)
            last_confirmed = point
            if not confirmed_path:
                confirmed_path.append(point)
            continue

        validation_buffer.append(point)
        if len(validation_buffer) > 3:
            validation_buffer.pop(0)
        if len(validation_buffer) < 3:
            continue

        p0, p1, p2 = validation_buffer
        d01_km = haversine_km(p0.latitude, p0.longitude, p1.latitude, p1.longitude)
        d12_km = haversine_km(p1.latitude, p1.longitude, p2.latitude, p2.longitude)
        t01_sec = (p1.timestamp - p0.timestamp) / 1000.0
        t12_sec = (p2.timestamp - p1.timestamp) / 1000.0
        if t01_sec <= 0 or t12_sec <= 0:
            validation_buffer = [p2]
            continue

        v01_m_s = d01_km * 1000.0 / t01_sec
        v12_m_s = d12_km * 1000.0 / t12_sec
        max_d01_km = min(
            policy.max_jump_distance_km,
            policy.max_speed_threshold_m_s * t01_sec / 1000.0,
        )
        max_d12_km = min(
            policy.max_jump_distance_km,
            policy.max_speed_threshold_m_s * t12_sec / 1000.0,
        )
        angle_deg = _turn_angle_deg(
            (p0.latitude, p0.longitude),
            (p1.latitude, p1.longitude),
            (p2.latitude, p2.longitude),
        )

        primary_ok = (
            v01_m_s <= policy.max_speed_threshold_m_s
            and v12_m_s <= policy.max_speed_threshold_m_s
            and d01_km <= max_d01_km
            and d12_km <= max_d12_km
            and angle_deg < policy.primary_angle_threshold_deg
        )
        if primary_ok:
            confirmed_distance_km += d01_km
            last_confirmed = p1
            if not confirmed_path or confirmed_path[-1] != p1:
                confirmed_path.append(p1)
            validation_buffer = [p1, p2]
            continue

        relaxed_jump_km = min(
            policy.max_jump_distance_km * 2.0,
            policy.max_speed_threshold_m_s * t12_sec / 1000.0,
        )
        secondary_ok = (
            v12_m_s <= policy.max_speed_threshold_m_s * 1.5
            and d12_km <= relaxed_jump_km
            and angle_deg < policy.secondary_angle_threshold_deg
        )
        if secondary_ok:
            d02_km = haversine_km(p0.latitude, p0.longitude, p2.latitude, p2.longitude)
            t02_sec = (p2.timestamp - p0.timestamp) / 1000.0
            if t02_sec > 0:
                v02_m_s = d02_km * 1000.0 / t02_sec
                if (
                    v02_m_s <= policy.max_speed_threshold_m_s
                    and d02_km <= policy.max_jump_distance_km * 2.0
                ):
                    confirmed_distance_km += d02_km
                    last_confirmed = p2
                    if not confirmed_path or confirmed_path[-1] != p2:
                        confirmed_path.append(p2)
                    validation_buffer = [p2]
                    continue

        validation_buffer = [p2]

    return confirmed_distance_km, confirmed_path
