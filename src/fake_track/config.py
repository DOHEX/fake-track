import tomllib
from pathlib import Path

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
)
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    TomlConfigSettingsSource,
)


class ConfigError(ValueError):
    pass


CONFIG_PATH = Path("fake-track.toml")


def _validate_aes_key(value: str) -> str:
    if len(value.encode("utf-8")) not in {16, 24, 32}:
        raise ValueError("FAKE_TRACK_KEY must be 16/24/32 bytes for AES")
    return value


def _format_validation_error(exc: ValidationError) -> str:
    details: list[str] = []
    for item in exc.errors():
        location = ".".join(str(part) for part in item.get("loc", ()))
        message = item.get("msg", "invalid value")
        details.append(f"{location}: {message}" if location else str(message))
    return "\n- ".join(details) if details else str(exc)


class CryptoSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", ".env.local"),
        env_file_encoding="utf-8",
        env_ignore_empty=True,
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
            raise ConfigError(
                "Invalid environment configuration:\n- " + _format_validation_error(exc)
            ) from exc


class CredentialSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", ".env.local"),
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        case_sensitive=False,
        extra="ignore",
    )

    phone: str = Field(validation_alias=AliasChoices("FAKE_TRACK_PHONE"))
    password: str = Field(validation_alias=AliasChoices("FAKE_TRACK_PASSWORD"))
    run_key: str = Field(
        validation_alias=AliasChoices("FAKE_TRACK_KEY", "FAKE_TRACK_SECRET")
    )

    @field_validator("run_key")
    @classmethod
    def _validate_run_key(cls, value: str) -> str:
        return _validate_aes_key(value)


class NetworkConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_url_xcxapi: str = "https://run.ecust.edu.cn/xcxapi"
    base_url_root: str = "https://run.ecust.edu.cn"
    referer: str = "https://servicewechat.com/wxfa4e6078551d719e/49/page-frame.html"
    user_agent: str = (
        "Mozilla/5.0 (Linux; Android 16) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Mobile Safari/537.36 MicroMessenger"
    )
    timeout_sec: int = 20
    retry_count: int = 3

    @field_validator("base_url_xcxapi", "base_url_root")
    @classmethod
    def _normalize_base_url(cls, value: str) -> str:
        return value.rstrip("/")


class RunConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start_lat: float = 30.83378
    start_lng: float = 121.504532
    target_distance_km: float = 2.03
    target_pace_min_per_km: float = 6.0
    target_duration_min_sec: int = 460
    target_duration_max_sec: int = 490
    sample_interval_sec: int = 1
    must_pass_radius_km: float = 0.05
    compensation_factor: float = 1.0
    device_brand: str = ""
    distance_jitter_ratio: float = 0.03
    pace_jitter_ratio: float = 0.08


class RouteConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    road_routing_enabled: bool = True
    road_map_path: str = "map.osm"
    road_snap_max_m: float = 120.0
    road_coordinate_bridge_enabled: bool = True


class PointsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    point_accuracy_min: int = 8
    point_accuracy_max: int = 25
    point_jitter_m: float = 2.5
    timestamp_jitter_ms: int = 220


class GuardConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_speed_threshold_m_s: float = 10.0
    max_jump_distance_km: float = 0.1
    min_move_distance_m: float = 5.0
    min_move_speed_m_s: float = 0.8
    gps_accuracy_threshold_m: float = 100.0
    primary_angle_threshold_deg: float = 120.0
    secondary_angle_threshold_deg: float = 150.0
    distance_tolerance_ratio: float = 0.95

    @field_validator("distance_tolerance_ratio")
    @classmethod
    def _normalize_distance_tolerance_ratio(cls, value: float) -> float:
        return min(1.0, max(0.5, float(value)))


class OutputConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    report_path: str | None = None

    @field_validator("report_path", mode="before")
    @classmethod
    def _normalize_report_path(cls, value: object) -> str | None:
        if value is None:
            return None
        if isinstance(value, str):
            text = value.strip()
            return text or None
        return str(value)


class FileSettings(BaseSettings):
    model_config = SettingsConfigDict(
        toml_file=CONFIG_PATH,
        extra="forbid",
    )

    network: NetworkConfig = Field(default_factory=NetworkConfig)
    run: RunConfig = Field(default_factory=RunConfig)
    route: RouteConfig = Field(default_factory=RouteConfig)
    points: PointsConfig = Field(default_factory=PointsConfig)
    guard: GuardConfig = Field(default_factory=GuardConfig)
    output: OutputConfig = Field(default_factory=OutputConfig)

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        del env_settings, dotenv_settings, file_secret_settings
        return (
            init_settings,
            TomlConfigSettingsSource(settings_cls),
        )


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    phone: str
    password: str
    run_key: str
    network: NetworkConfig = Field(default_factory=NetworkConfig)
    run: RunConfig = Field(default_factory=RunConfig)
    route: RouteConfig = Field(default_factory=RouteConfig)
    points: PointsConfig = Field(default_factory=PointsConfig)
    guard: GuardConfig = Field(default_factory=GuardConfig)
    output: OutputConfig = Field(default_factory=OutputConfig)

    @field_validator("run_key")
    @classmethod
    def _validate_run_key(cls, value: str) -> str:
        return _validate_aes_key(value)

    @classmethod
    def load(cls) -> "Settings":
        try:
            credentials = CredentialSettings()
            file_settings = FileSettings()
            return cls(
                phone=credentials.phone,
                password=credentials.password,
                run_key=credentials.run_key,
                **file_settings.model_dump(),
            )
        except ValidationError as exc:
            raise ConfigError(
                "Invalid configuration:\n- " + _format_validation_error(exc)
            ) from exc
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(f"Invalid TOML config {CONFIG_PATH}: {exc}") from exc
        except OSError as exc:
            raise ConfigError(f"Cannot read config {CONFIG_PATH}: {exc}") from exc
