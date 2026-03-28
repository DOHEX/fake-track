import os
from dataclasses import dataclass
from pathlib import Path


class ConfigError(ValueError):
    pass


def _load_env_file_if_present(path: Path) -> None:
    if not path.exists() or not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _get_first_env(names: tuple[str, ...]) -> str:
    for name in names:
        value = os.getenv(name)
        if value is not None and value.strip() != "":
            return value.strip()
    return ""


def _get_required_env(names: tuple[str, ...]) -> str:
    value = _get_first_env(names)
    if not value:
        joined = ", ".join(names)
        raise ConfigError(f"Missing required environment variable (one of): {joined}")
    return value


def _get_float_env_any(names: tuple[str, ...], default: float) -> float:
    for name in names:
        raw = os.getenv(name)
        if raw is None or raw.strip() == "":
            continue
        try:
            return float(raw)
        except ValueError as exc:
            raise ConfigError(f"Invalid float for {name}: {raw}") from exc
    return default


def _get_int_env_any(names: tuple[str, ...], default: int) -> int:
    for name in names:
        raw = os.getenv(name)
        if raw is None or raw.strip() == "":
            continue
        try:
            return int(raw)
        except ValueError as exc:
            raise ConfigError(f"Invalid integer for {name}: {raw}") from exc
    return default


def _get_bool_env_any(names: tuple[str, ...], default: bool) -> bool:
    for name in names:
        raw = os.getenv(name)
        if raw is None or raw.strip() == "":
            continue
        value = raw.strip().lower()
        if value in {"1", "true", "yes", "on"}:
            return True
        if value in {"0", "false", "no", "off"}:
            return False
        raise ConfigError(f"Invalid boolean for {name}: {raw}")
    return default


@dataclass(slots=True)
class Settings:
    phone: str
    password: str
    run_key: str

    base_url_xcxapi: str = "https://run.ecust.edu.cn/xcxapi"
    base_url_root: str = "https://run.ecust.edu.cn"
    referer: str = "https://servicewechat.com/wxfa4e6078551d719e/49/page-frame.html"
    user_agent: str = (
        "Mozilla/5.0 (Linux; Android 16) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Mobile Safari/537.36 MicroMessenger"
    )

    start_lat: float = 30.83378
    start_lng: float = 121.504532
    target_distance_km: float = 2.03
    target_pace_min_per_km: float = 6.0
    target_duration_min_sec: int = 460
    target_duration_max_sec: int = 490
    sample_interval_sec: int = 1
    must_pass_radius_km: float = 0.05
    compensation_factor: float = 1.0
    point_accuracy_min: int = 8
    point_accuracy_max: int = 25
    point_jitter_m: float = 2.5
    timestamp_jitter_ms: int = 220

    road_routing_enabled: bool = True
    road_map_path: str = "map.osm"
    road_snap_max_m: float = 120.0
    road_coordinate_bridge_enabled: bool = True

    distance_jitter_ratio: float = 0.03
    pace_jitter_ratio: float = 0.08

    timeout_sec: int = 20
    retry_count: int = 3

    report_path: str | None = None

    @classmethod
    def from_env(cls) -> "Settings":
        _load_env_file_if_present(Path(".env"))
        _load_env_file_if_present(Path(".env.local"))

        run_key = _get_required_env(("FAKE_TRACK_KEY", "FAKE_TRACK_SECRET"))
        if len(run_key.encode("utf-8")) not in {16, 24, 32}:
            raise ConfigError("FAKE_TRACK_KEY must be 16/24/32 bytes for AES")

        return cls(
            phone=_get_required_env(("FAKE_TRACK_PHONE",)),
            password=_get_required_env(("FAKE_TRACK_PASSWORD",)),
            run_key=run_key,
            base_url_xcxapi=(
                _get_first_env(("FAKE_TRACK_BASE_URL_XCXAPI",))
                or "https://run.ecust.edu.cn/xcxapi"
            ).rstrip("/"),
            base_url_root=(
                _get_first_env(("FAKE_TRACK_BASE_URL_ROOT",))
                or "https://run.ecust.edu.cn"
            ).rstrip("/"),
            referer=(
                _get_first_env(("FAKE_TRACK_REFERER",))
                or "https://servicewechat.com/wxfa4e6078551d719e/49/page-frame.html"
            ),
            user_agent=(
                _get_first_env(("FAKE_TRACK_USER_AGENT",))
                or "Mozilla/5.0 (Linux; Android 16) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Mobile Safari/537.36 MicroMessenger"
            ),
            start_lat=_get_float_env_any(("FAKE_TRACK_START_LAT",), 30.83378),
            start_lng=_get_float_env_any(("FAKE_TRACK_START_LNG",), 121.504532),
            target_distance_km=_get_float_env_any(
                ("FAKE_TRACK_TARGET_DISTANCE_KM",),
                2.03,
            ),
            target_pace_min_per_km=_get_float_env_any(
                ("FAKE_TRACK_TARGET_PACE_MIN_PER_KM",),
                6.0,
            ),
            target_duration_min_sec=_get_int_env_any(
                ("FAKE_TRACK_TARGET_DURATION_MIN_SEC",),
                460,
            ),
            target_duration_max_sec=_get_int_env_any(
                ("FAKE_TRACK_TARGET_DURATION_MAX_SEC",),
                490,
            ),
            sample_interval_sec=_get_int_env_any(
                ("FAKE_TRACK_SAMPLE_INTERVAL_SEC",), 1
            ),
            must_pass_radius_km=_get_float_env_any(
                ("FAKE_TRACK_MUST_PASS_RADIUS_KM",),
                0.05,
            ),
            compensation_factor=_get_float_env_any(
                ("FAKE_TRACK_COMPENSATION_FACTOR",),
                1.0,
            ),
            point_accuracy_min=_get_int_env_any(
                ("FAKE_TRACK_POINT_ACCURACY_MIN",),
                8,
            ),
            point_accuracy_max=_get_int_env_any(
                ("FAKE_TRACK_POINT_ACCURACY_MAX",),
                25,
            ),
            point_jitter_m=_get_float_env_any(("FAKE_TRACK_POINT_JITTER_M",), 2.5),
            timestamp_jitter_ms=_get_int_env_any(
                ("FAKE_TRACK_TIMESTAMP_JITTER_MS",),
                220,
            ),
            road_routing_enabled=_get_bool_env_any(
                ("FAKE_TRACK_ROAD_ROUTING_ENABLED",),
                True,
            ),
            road_map_path=(_get_first_env(("FAKE_TRACK_ROAD_MAP_PATH",)) or "map.osm"),
            road_snap_max_m=_get_float_env_any(
                ("FAKE_TRACK_ROAD_SNAP_MAX_M",),
                120.0,
            ),
            road_coordinate_bridge_enabled=_get_bool_env_any(
                ("FAKE_TRACK_ROAD_COORDINATE_BRIDGE_ENABLED",),
                True,
            ),
            distance_jitter_ratio=_get_float_env_any(
                ("FAKE_TRACK_DISTANCE_JITTER_RATIO",),
                0.03,
            ),
            pace_jitter_ratio=_get_float_env_any(
                ("FAKE_TRACK_PACE_JITTER_RATIO",), 0.08
            ),
            timeout_sec=_get_int_env_any(("FAKE_TRACK_TIMEOUT_SEC",), 20),
            retry_count=_get_int_env_any(("FAKE_TRACK_RETRY_COUNT",), 3),
            report_path=_get_first_env(("FAKE_TRACK_REPORT_PATH",)) or None,
        )
