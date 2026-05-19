from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


@dataclass(slots=True)
class TrackPoint:
    latitude: float
    longitude: float
    timestamp: int
    accuracy: int


@dataclass(slots=True, frozen=True)
class PassPoint:
    lat: float
    lng: float


@dataclass(slots=True)
class GeneratedTrack:
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
class TrackRequest:
    start: tuple[float, float]
    must_pass_points: list[PassPoint]
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


@dataclass(slots=True, frozen=True)
class RunSummary:
    run_type: str | None = None
    morning: int | None = None
    normal: int | None = None
    effective: int | None = None
    target_effective: int | None = None
    target_met: bool | None = None
    generated_distance_km: float | None = None
    generated_distance_raw_km: float | None = None
    generated_distance_confirmed_km: float | None = None
    generated_confirmed_point_count: int | None = None
    generated_duration_sec: int | None = None
    generated_pace_min_per_km: float | None = None
    generated_pass_point: int | None = None
    generated_point_count: int | None = None
    generated_distance_guard_min_km: float | None = None
    generated_distance_guard_attempts: int | None = None
    generated_quality_guard_attempts: int | None = None
    generated_road_routing_used: bool | None = None
    generated_track_image_enabled: bool | None = None
    generated_submit_wait_skipped: bool | None = None
    generated_force_submit: bool | None = None
    generated_ignore_target_met: bool | None = None
    generated_ignored_target_skip_reason: str | None = None
    generated_track_image: str | None = None
    skip_reason: str | None = None
    uploaded_batches: int | None = None
    uploaded_point_count: int | None = None
    target_duration_sec: int | None = None
    server_warning_detected: bool | None = None
    server_warning_treated_as_success: bool | None = None
    record_payload: dict | None = None
    pass_point_count: int | None = None


@dataclass(slots=True, frozen=True)
class ServerResponses:
    login: dict | None = None
    randrunInfo: dict | None = None
    createLine: dict | None = None
    checkRecord: dict | None = None
    updateRecordNew: dict | None = None
    recordInfo: dict | None = None
    GetPathPoints: dict | None = None
    run_counts: dict | None = None


def format_timestamp_ms(timestamp_ms: int) -> str:
    return datetime.fromtimestamp(timestamp_ms / 1000).strftime("%Y-%m-%d %H:%M:%S")
