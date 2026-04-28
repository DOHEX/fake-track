from pydantic import AliasChoices, Field, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ConfigError(ValueError):
    pass


def _validate_aes_key(value: str) -> str:
    if len(value.encode("utf-8")) not in {16, 24, 32}:
        raise ValueError("FAKE_TRACK_KEY must be 16/24/32 bytes for AES")
    return value


class CryptoSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", ".env.local"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    run_key: str = Field(
        validation_alias=AliasChoices("FAKE_TRACK_KEY", "FAKE_TRACK_SECRET")
    )

    @field_validator("run_key")
    @classmethod
    def _validate_run_key(cls, value: str) -> str:
        return _validate_aes_key(value)

    @classmethod
    def from_env(cls) -> "CryptoSettings":
        try:
            return cls()
        except ValidationError as exc:
            details: list[str] = []
            for item in exc.errors():
                location = ".".join(str(part) for part in item.get("loc", ()))
                message = item.get("msg", "invalid value")
                details.append(f"{location}: {message}" if location else str(message))

            if details:
                raise ConfigError(
                    "Invalid environment configuration:\n- " + "\n- ".join(details)
                ) from exc
            raise ConfigError(str(exc)) from exc


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", ".env.local"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    phone: str = Field(validation_alias=AliasChoices("FAKE_TRACK_PHONE"))
    password: str = Field(validation_alias=AliasChoices("FAKE_TRACK_PASSWORD"))
    run_key: str = Field(
        validation_alias=AliasChoices("FAKE_TRACK_KEY", "FAKE_TRACK_SECRET")
    )

    base_url_xcxapi: str = Field(
        default="https://run.ecust.edu.cn/xcxapi",
        validation_alias=AliasChoices("FAKE_TRACK_BASE_URL_XCXAPI"),
    )
    base_url_root: str = Field(
        default="https://run.ecust.edu.cn",
        validation_alias=AliasChoices("FAKE_TRACK_BASE_URL_ROOT"),
    )
    referer: str = Field(
        default="https://servicewechat.com/wxfa4e6078551d719e/49/page-frame.html",
        validation_alias=AliasChoices("FAKE_TRACK_REFERER"),
    )
    user_agent: str = Field(
        default=(
            "Mozilla/5.0 (Linux; Android 16) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Mobile Safari/537.36 MicroMessenger"
        ),
        validation_alias=AliasChoices("FAKE_TRACK_USER_AGENT"),
    )

    start_lat: float = Field(
        30.83378,
        validation_alias=AliasChoices("FAKE_TRACK_START_LAT"),
    )
    start_lng: float = Field(
        121.504532,
        validation_alias=AliasChoices("FAKE_TRACK_START_LNG"),
    )
    target_distance_km: float = Field(
        2.03,
        validation_alias=AliasChoices("FAKE_TRACK_TARGET_DISTANCE_KM"),
    )
    target_pace_min_per_km: float = Field(
        6.0,
        validation_alias=AliasChoices("FAKE_TRACK_TARGET_PACE_MIN_PER_KM"),
    )
    target_duration_min_sec: int = Field(
        460,
        validation_alias=AliasChoices("FAKE_TRACK_TARGET_DURATION_MIN_SEC"),
    )
    target_duration_max_sec: int = Field(
        490,
        validation_alias=AliasChoices("FAKE_TRACK_TARGET_DURATION_MAX_SEC"),
    )
    sample_interval_sec: int = Field(
        1,
        validation_alias=AliasChoices("FAKE_TRACK_SAMPLE_INTERVAL_SEC"),
    )
    must_pass_radius_km: float = Field(
        0.05,
        validation_alias=AliasChoices("FAKE_TRACK_MUST_PASS_RADIUS_KM"),
    )
    compensation_factor: float = Field(
        1.0,
        validation_alias=AliasChoices("FAKE_TRACK_COMPENSATION_FACTOR"),
    )
    device_brand: str = Field(
        "",
        validation_alias=AliasChoices("FAKE_TRACK_DEVICE_BRAND"),
    )
    max_speed_threshold_m_s: float = Field(
        10.0,
        validation_alias=AliasChoices("FAKE_TRACK_MAX_SPEED_THRESHOLD_M_S"),
    )
    max_jump_distance_km: float = Field(
        0.1,
        validation_alias=AliasChoices("FAKE_TRACK_MAX_JUMP_DISTANCE_KM"),
    )
    min_move_distance_m: float = Field(
        5.0,
        validation_alias=AliasChoices("FAKE_TRACK_MIN_MOVE_DISTANCE_M"),
    )
    min_move_speed_m_s: float = Field(
        0.8,
        validation_alias=AliasChoices("FAKE_TRACK_MIN_MOVE_SPEED_M_S"),
    )
    gps_accuracy_threshold_m: float = Field(
        100.0,
        validation_alias=AliasChoices("FAKE_TRACK_GPS_ACCURACY_THRESHOLD_M"),
    )
    primary_angle_threshold_deg: float = Field(
        120.0,
        validation_alias=AliasChoices("FAKE_TRACK_PRIMARY_ANGLE_THRESHOLD_DEG"),
    )
    secondary_angle_threshold_deg: float = Field(
        150.0,
        validation_alias=AliasChoices("FAKE_TRACK_SECONDARY_ANGLE_THRESHOLD_DEG"),
    )
    distance_tolerance_ratio: float = Field(
        0.95,
        validation_alias=AliasChoices("FAKE_TRACK_DISTANCE_TOLERANCE_RATIO"),
    )
    point_accuracy_min: int = Field(
        8,
        validation_alias=AliasChoices("FAKE_TRACK_POINT_ACCURACY_MIN"),
    )
    point_accuracy_max: int = Field(
        25,
        validation_alias=AliasChoices("FAKE_TRACK_POINT_ACCURACY_MAX"),
    )
    point_jitter_m: float = Field(
        2.5,
        validation_alias=AliasChoices("FAKE_TRACK_POINT_JITTER_M"),
    )
    timestamp_jitter_ms: int = Field(
        220,
        validation_alias=AliasChoices("FAKE_TRACK_TIMESTAMP_JITTER_MS"),
    )

    road_routing_enabled: bool = Field(
        True,
        validation_alias=AliasChoices("FAKE_TRACK_ROAD_ROUTING_ENABLED"),
    )
    road_map_path: str = Field(
        "map.osm",
        validation_alias=AliasChoices("FAKE_TRACK_ROAD_MAP_PATH"),
    )
    road_snap_max_m: float = Field(
        120.0,
        validation_alias=AliasChoices("FAKE_TRACK_ROAD_SNAP_MAX_M"),
    )
    road_coordinate_bridge_enabled: bool = Field(
        True,
        validation_alias=AliasChoices("FAKE_TRACK_ROAD_COORDINATE_BRIDGE_ENABLED"),
    )

    distance_jitter_ratio: float = Field(
        0.03,
        validation_alias=AliasChoices("FAKE_TRACK_DISTANCE_JITTER_RATIO"),
    )
    pace_jitter_ratio: float = Field(
        0.08,
        validation_alias=AliasChoices("FAKE_TRACK_PACE_JITTER_RATIO"),
    )

    timeout_sec: int = Field(
        20,
        validation_alias=AliasChoices("FAKE_TRACK_TIMEOUT_SEC"),
    )
    retry_count: int = Field(
        3,
        validation_alias=AliasChoices("FAKE_TRACK_RETRY_COUNT"),
    )

    report_path: str | None = Field(
        default=None,
        validation_alias=AliasChoices("FAKE_TRACK_REPORT_PATH"),
    )

    @field_validator("run_key")
    @classmethod
    def _validate_run_key(cls, value: str) -> str:
        return _validate_aes_key(value)

    @field_validator("base_url_xcxapi", "base_url_root")
    @classmethod
    def _normalize_base_url(cls, value: str) -> str:
        return value.rstrip("/")

    @field_validator("distance_tolerance_ratio")
    @classmethod
    def _normalize_distance_tolerance_ratio(cls, value: float) -> float:
        return min(1.0, max(0.5, float(value)))

    @field_validator("report_path", mode="before")
    @classmethod
    def _normalize_report_path(cls, value: object) -> str | None:
        if value is None:
            return None
        if isinstance(value, str):
            text = value.strip()
            return text or None
        return str(value)

    @classmethod
    def from_env(cls) -> "Settings":
        try:
            return cls()
        except ValidationError as exc:
            details: list[str] = []
            for item in exc.errors():
                location = ".".join(str(part) for part in item.get("loc", ()))
                message = item.get("msg", "invalid value")
                details.append(f"{location}: {message}" if location else str(message))

            if details:
                raise ConfigError(
                    "Invalid environment configuration:\n- " + "\n- ".join(details)
                ) from exc
            raise ConfigError(str(exc)) from exc
