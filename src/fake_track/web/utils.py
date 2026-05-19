"""Web-layer helpers (non-component utilities)."""

from dataclasses import dataclass

from fake_track.core.config import ConfigError, Settings
from fake_track.core.config_service import list_accounts
from fake_track.core.utils import mask_phone


def account_label(settings: Settings, index: int) -> str:
    name = settings.account_name or f"account-{index}"
    return f"{name} ({mask_phone(settings.phone)})"


@dataclass(slots=True)
class AccountCard:
    index: int
    label: str
    name: str
    phone: str
    password: str
    skip_wait: bool
    force_submit: bool
    ignore_target_met: bool
    auto_run_enabled: bool = False
    morning_time: str = "06:30"
    normal_time: str = "12:00"
    auto_run_jitter_minutes: int = 5
    start_lat: float | None = None
    start_lng: float | None = None
    target_distance_km: float | None = None
    target_pace_min_per_km: float | None = None


def load_accounts() -> tuple[list[AccountCard], list[Settings], str | None]:
    try:
        settings_list = Settings.load_all()
    except ConfigError as exc:
        return [], [], str(exc)
    raw_accounts = list_accounts()
    accounts: list[AccountCard] = []
    for i, s in enumerate(settings_list, start=1):
        raw = raw_accounts[i - 1] if i - 1 < len(raw_accounts) else None
        if raw is not None:
            accounts.append(
                AccountCard(
                    index=i,
                    label=account_label(s, i),
                    name=s.account_name or f"account-{i}",
                    phone=raw.phone,
                    password=raw.password,
                    skip_wait=s.skip_wait,
                    force_submit=s.force_submit,
                    ignore_target_met=s.ignore_target_met,
                    auto_run_enabled=raw.auto_run_enabled,
                    morning_time=raw.morning_time,
                    normal_time=raw.normal_time,
                    auto_run_jitter_minutes=raw.auto_run_jitter_minutes,
                    start_lat=raw.start_lat,
                    start_lng=raw.start_lng,
                    target_distance_km=raw.target_distance_km,
                    target_pace_min_per_km=raw.target_pace_min_per_km,
                )
            )
        else:
            accounts.append(
                AccountCard(
                    index=i,
                    label=account_label(s, i),
                    name=s.account_name or f"account-{i}",
                    phone=s.phone,
                    password=s.password,
                    skip_wait=s.skip_wait,
                    force_submit=s.force_submit,
                    ignore_target_met=s.ignore_target_met,
                )
            )
    return accounts, settings_list, None


OPTION_BADGES: list[tuple[str, str]] = [
    ("skip_wait", "emerald"),
    ("force_submit", "amber"),
    ("ignore_target_met", "sky"),
]
