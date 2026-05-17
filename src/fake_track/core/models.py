from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


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
    raw_distance_km: float
    confirmed_distance_km: float
    confirmed_point_count: int
    duration_sec: int
    pace_min_per_km: float
    must_pass_count: int
    road_routing_used: bool
    start_time: str
    end_time: str


class RunType(Enum):
    MORNING = "morning"
    NORMAL = "normal"


def classify_run_type(dt: datetime) -> RunType | None:
    hour = dt.hour + dt.minute / 60.0
    if 6.0 <= hour < 7.0:
        return RunType.MORNING
    if 7.0 <= hour < 22.0:
        return RunType.NORMAL
    return None


def semester_for(dt: datetime) -> str:
    year = dt.year
    month = dt.month
    if month >= 9:
        return f"{year}-{year + 1} 第一学期"
    if month <= 7:
        return f"{year - 1}-{year} 第二学期"
    return f"{year}-{year + 1} 暑假"


@dataclass(slots=True, frozen=True)
class TrackFilterPolicy:
    max_speed_threshold_m_s: float = 10.0
    max_jump_distance_km: float = 0.1
    min_move_distance_m: float = 5.0
    min_move_speed_m_s: float = 0.8
    gps_accuracy_threshold_m: float = 100.0
    primary_angle_threshold_deg: float = 120.0
    secondary_angle_threshold_deg: float = 150.0


@dataclass(slots=True, frozen=True)
class TrackGenerationRequest:
    start: tuple[float, float]
    must_pass_points: list[dict[str, Any]]
    target_distance_km: float
    target_pace_min_per_km: float
    sample_interval_sec: int
    must_pass_radius_km: float
    jitter_m: float
    timestamp_jitter_ms: int
    accuracy_min: int
    accuracy_max: int
    road_routing_enabled: bool = True
    road_map_path: str = "map.osm"
    road_snap_max_m: float = 120.0
    random_seed: int | None = None
    filter_policy: TrackFilterPolicy = field(default_factory=TrackFilterPolicy)


@dataclass(slots=True, frozen=True)
class RunCounts:
    morning: int
    normal: int
    effective: int
    target_effective: int


@dataclass(slots=True)
class RecordItem:
    id: int
    running_time: int
    status: int
    mileage: float
    speed: str
    kcal: str
    step_frequency: int
    motto: str
    path_image: str
    start_time: str
    end_time: str
    pub_time: str


def format_timestamp_ms(timestamp_ms: int) -> str:
    return datetime.fromtimestamp(timestamp_ms / 1000).strftime("%Y-%m-%d %H:%M:%S")
