import os
import re
import tomllib
from pathlib import Path
from typing import Any

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


def format_validation_error(exc: ValidationError) -> str:
    details: list[str] = []
    for item in exc.errors():
        location = ".".join(str(part) for part in item.get("loc", ()))
        message = item.get("msg", "invalid value")
        details.append(f"{location}: {message}" if location else str(message))
    return "\n- ".join(details) if details else str(exc)


def _env_bool(key: str, default: bool = False) -> bool:
    value = os.environ.get(key)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def _resolve_option(
    account_value: bool | None,
    env_key: str,
    global_default: bool,
) -> bool:
    if account_value is not None:
        return account_value
    env_value = os.environ.get(env_key)
    if env_value is not None:
        return env_value.strip().lower() in ("1", "true", "yes", "on")
    return global_default


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
    def from_env(cls) -> CryptoSettings:
        try:
            return cls()  # type: ignore[call-arg]
        except ValidationError as exc:
            raise ConfigError(
                "Invalid environment configuration:\n- " + format_validation_error(exc)
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


class _ForbidExtraModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AccountConfig(_ForbidExtraModel):
    name: str | None = None
    phone: str
    password: str
    start_lat: float | None = None
    start_lng: float | None = None
    target_distance_km: float | None = None
    target_pace_min_per_km: float | None = None
    skip_wait: bool | None = None
    force_submit: bool | None = None
    ignore_target_met: bool | None = None
    auto_run_enabled: bool = False
    morning_time: str = "06:30"
    normal_time: str = "12:00"
    auto_run_jitter_minutes: int = 5

    @field_validator("morning_time")
    @classmethod
    def _validate_morning_time(cls, value: str) -> str:
        if not re.match(r"^06:[0-5]\d$", value):
            raise ValueError(f"Invalid morning_time: {value!r} (expected 06:00-06:59)")
        return value

    @field_validator("normal_time")
    @classmethod
    def _validate_normal_time(cls, value: str) -> str:
        if not re.match(r"^(?:0[7-9]|1\d|2[0-1]):[0-5]\d$", value):
            raise ValueError(f"Invalid normal_time: {value!r} (expected 07:00-21:59)")
        return value

    @field_validator("auto_run_jitter_minutes")
    @classmethod
    def _validate_jitter(cls, value: int) -> int:
        if value < 0 or value > 60:
            raise ValueError(f"auto_run_jitter_minutes must be 0-60 (got {value})")
        return value

    @field_validator("name", mode="before")
    @classmethod
    def _normalize_name(cls, value: object) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None


class NetworkConfig(_ForbidExtraModel):
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


class RunConfig(_ForbidExtraModel):
    start_lat: float = 30.83378
    start_lng: float = 121.504532
    target_distance_km: float = 2.03
    target_pace_min_per_km: float = 6.0
    sample_interval_sec: int = 1
    must_pass_radius_km: float = 0.05
    distance_jitter_ratio: float = 0.03
    pace_jitter_ratio: float = 0.08
    point_accuracy_min: int = 8
    point_accuracy_max: int = 25
    point_jitter_m: float = 2.5
    timestamp_jitter_ms: int = 220


class RouteConfig(_ForbidExtraModel):
    road_routing_enabled: bool = True
    road_map_path: str = "map.osm"
    road_snap_max_m: float = 120.0


class GuardConfig(_ForbidExtraModel):
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


class OutputConfig(_ForbidExtraModel):
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


class OptionsConfig(_ForbidExtraModel):
    skip_wait: bool = False
    force_submit: bool = False
    ignore_target_met: bool = False


class FileSettings(BaseSettings):
    model_config = SettingsConfigDict(
        toml_file=CONFIG_PATH,
        extra="forbid",
    )

    accounts: list[AccountConfig] = Field(default_factory=list)

    network: NetworkConfig = Field(default_factory=NetworkConfig)
    run: RunConfig = Field(default_factory=RunConfig)
    route: RouteConfig = Field(default_factory=RouteConfig)
    guard: GuardConfig = Field(default_factory=GuardConfig)
    output: OutputConfig = Field(default_factory=OutputConfig)
    options: OptionsConfig = Field(default_factory=OptionsConfig)

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


class Settings(_ForbidExtraModel):
    account_name: str | None = None
    phone: str
    password: str
    run_key: str
    skip_wait: bool = False
    force_submit: bool = False
    ignore_target_met: bool = False
    network: NetworkConfig = Field(default_factory=NetworkConfig)
    run: RunConfig = Field(default_factory=RunConfig)
    route: RouteConfig = Field(default_factory=RouteConfig)
    guard: GuardConfig = Field(default_factory=GuardConfig)
    output: OutputConfig = Field(default_factory=OutputConfig)

    @field_validator("run_key")
    @classmethod
    def _validate_run_key(cls, value: str) -> str:
        return _validate_aes_key(value)

    @classmethod
    def load(cls) -> Settings:
        try:
            credentials = CredentialSettings()  # type: ignore[call-arg]
            file_settings = FileSettings()
            options = file_settings.options
            return cls(
                account_name=None,
                phone=credentials.phone,
                password=credentials.password,
                run_key=credentials.run_key,
                skip_wait=_env_bool("FAKE_TRACK_SKIP_WAIT", options.skip_wait),
                force_submit=_env_bool("FAKE_TRACK_FORCE_SUBMIT", options.force_submit),
                ignore_target_met=_env_bool(
                    "FAKE_TRACK_IGNORE_TARGET_MET", options.ignore_target_met
                ),
                **file_settings.model_dump(exclude={"accounts", "options"}),
            )
        except ValidationError as exc:
            raise ConfigError(
                "Invalid configuration:\n- " + format_validation_error(exc)
            ) from exc
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(f"Invalid TOML config {CONFIG_PATH}: {exc}") from exc
        except OSError as exc:
            raise ConfigError(f"Cannot read config {CONFIG_PATH}: {exc}") from exc

    @classmethod
    def _apply_account_overrides(
        cls, base_payload: dict[str, Any], account: AccountConfig
    ) -> dict[str, Any]:
        """Override global run fields with account-level values if set."""
        payload = base_payload.copy()
        run_data = payload["run"].copy() if isinstance(payload["run"], dict) else {}
        overrides: dict[str, float] = {}
        if account.start_lat is not None:
            overrides["start_lat"] = account.start_lat
        if account.start_lng is not None:
            overrides["start_lng"] = account.start_lng
        if account.target_distance_km is not None:
            overrides["target_distance_km"] = account.target_distance_km
        if account.target_pace_min_per_km is not None:
            overrides["target_pace_min_per_km"] = account.target_pace_min_per_km
        if overrides:
            run_data.update(overrides)
            payload["run"] = RunConfig.model_validate(run_data)
        return payload

    @classmethod
    def load_all(cls) -> list[Settings]:
        try:
            file_settings = FileSettings()
            base_payload = file_settings.model_dump(exclude={"accounts", "options"})
            global_opts = file_settings.options
            accounts = list(file_settings.accounts)
            if accounts:
                crypto = CryptoSettings.from_env()
                return [
                    cls(
                        account_name=account.name,
                        phone=account.phone,
                        password=account.password,
                        run_key=crypto.run_key,
                        skip_wait=_resolve_option(
                            account.skip_wait,
                            "FAKE_TRACK_SKIP_WAIT",
                            global_opts.skip_wait,
                        ),
                        force_submit=_resolve_option(
                            account.force_submit,
                            "FAKE_TRACK_FORCE_SUBMIT",
                            global_opts.force_submit,
                        ),
                        ignore_target_met=_resolve_option(
                            account.ignore_target_met,
                            "FAKE_TRACK_IGNORE_TARGET_MET",
                            global_opts.ignore_target_met,
                        ),
                        **cls._apply_account_overrides(base_payload, account),
                    )
                    for account in accounts
                ]

            credentials = CredentialSettings()  # type: ignore[call-arg]
            return [
                cls(
                    account_name=None,
                    phone=credentials.phone,
                    password=credentials.password,
                    run_key=credentials.run_key,
                    skip_wait=_env_bool("FAKE_TRACK_SKIP_WAIT", global_opts.skip_wait),
                    force_submit=_env_bool(
                        "FAKE_TRACK_FORCE_SUBMIT", global_opts.force_submit
                    ),
                    ignore_target_met=_env_bool(
                        "FAKE_TRACK_IGNORE_TARGET_MET", global_opts.ignore_target_met
                    ),
                    **base_payload,
                )
            ]
        except ValidationError as exc:
            raise ConfigError(
                "Invalid configuration:\n- " + format_validation_error(exc)
            ) from exc
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(f"Invalid TOML config {CONFIG_PATH}: {exc}") from exc
        except OSError as exc:
            raise ConfigError(f"Cannot read config {CONFIG_PATH}: {exc}") from exc
