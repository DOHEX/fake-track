"""Web-layer helpers (non-component utilities)."""

from typing import Any

from fake_track.core.config import ConfigError, Settings
from fake_track.core.utils import mask_phone


def account_label(settings: Settings, index: int) -> str:
    name = settings.account_name or f"account-{index}"
    return f"{name} ({mask_phone(settings.phone)})"


def load_accounts() -> tuple[list[dict[str, Any]], list[Settings], str | None]:
    try:
        settings_list = Settings.load_all()
    except ConfigError as exc:
        return [], [], str(exc)
    accounts = [
        {
            "index": i,
            "label": account_label(s, i),
            "name": s.account_name or f"account-{i}",
            "skip_wait": s.skip_wait,
            "force_submit": s.force_submit,
            "ignore_target_met": s.ignore_target_met,
        }
        for i, s in enumerate(settings_list, start=1)
    ]
    return accounts, settings_list, None


OPTION_BADGES: list[tuple[str, str]] = [
    ("skip_wait", "emerald"),
    ("force_submit", "amber"),
    ("ignore_target_met", "sky"),
]
