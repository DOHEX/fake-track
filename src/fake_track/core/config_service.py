"""TOML config read/write service layer.

Uses tomlkit for comment-preserving round-trips.
Validates data through existing pydantic models before writing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import tomlkit
from pydantic import ValidationError
from tomlkit.items import AoT

from fake_track.core.config import (
    CONFIG_PATH,
    AccountConfig,
    GuardConfig,
    NetworkConfig,
    RouteConfig,
    RunConfig,
    Settings,
)
from fake_track.core.config import format_validation_error as _fmt_err


@dataclass(slots=True, frozen=True)
class AccountInfo:
    index: int
    name: str
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


_SECTION_MODELS: dict[str, type] = {
    "network": NetworkConfig,
    "run": RunConfig,
    "route": RouteConfig,
    "guard": GuardConfig,
}


def _native(value: Any) -> Any:
    """Convert tomlkit types to native Python for JSON serialization."""
    if value is None:
        return None
    if hasattr(value, "unwrap"):
        return value.unwrap()
    return value


def _ensure_aot_entry_separation(aot: AoT) -> None:
    """Ensure AoT entries are separated by a newline.

    tomlkit preserves EOF trivia; when the last key has no trailing newline,
    appending a new table can produce `...""[[section]]` on the same line.
    """
    if len(aot) == 0:
        return

    last = aot[-1]
    body = getattr(getattr(last, "value", None), "body", None)
    if not body:
        return

    last_item = body[-1][1]
    trivia = getattr(last_item, "trivia", None)
    if trivia is None:
        return

    trail = getattr(trivia, "trail", "") or ""
    if "\n" not in trail:
        trivia.trail = f"{trail}\n\n"


def _read_document() -> tuple[tomlkit.TOMLDocument | None, str | None]:
    """Read or create a tomlkit document from CONFIG_PATH."""
    if not CONFIG_PATH.exists():
        return tomlkit.document(), None
    try:
        return tomlkit.parse(CONFIG_PATH.read_text(encoding="utf-8")), None
    except Exception as exc:
        return None, f"Cannot parse {CONFIG_PATH}: {exc}"


def _write_document(doc: tomlkit.TOMLDocument) -> str | None:
    """Atomically write document to CONFIG_PATH. Returns None on success."""
    tmp = CONFIG_PATH.with_suffix(".toml.tmp")
    try:
        tmp.write_text(tomlkit.dumps(doc), encoding="utf-8")
        tmp.replace(CONFIG_PATH)
        return None
    except OSError as exc:
        return f"Cannot write {CONFIG_PATH}: {exc}"


def list_accounts() -> list[AccountInfo]:
    """Return a lightweight list of accounts with an index field."""
    doc, _err = _read_document()
    if doc is None:
        return []
    accounts_aot = doc.get("accounts")
    if not isinstance(accounts_aot, AoT):
        return []
    result: list[AccountInfo] = []
    for i, item in enumerate(accounts_aot):
        result.append(
            AccountInfo(
                index=i,
                name=str(item.get("name", "")),
                phone=str(item.get("phone", "")),
                password=str(item.get("password", "")),
                start_lat=_native(item.get("start_lat")),
                start_lng=_native(item.get("start_lng")),
                target_distance_km=_native(item.get("target_distance_km")),
                target_pace_min_per_km=_native(item.get("target_pace_min_per_km")),
                skip_wait=_native(item.get("skip_wait")),
                force_submit=_native(item.get("force_submit")),
                ignore_target_met=_native(item.get("ignore_target_met")),
                auto_run_enabled=bool(_native(item.get("auto_run_enabled", False))),
                morning_time=str(item.get("morning_time", "06:30")),
                normal_time=str(item.get("normal_time", "12:00")),
                auto_run_jitter_minutes=int(
                    _native(item.get("auto_run_jitter_minutes", 5)) or 5
                ),
            )
        )
    return result


def add_account(data: dict[str, Any]) -> str | None:
    """Validate data as AccountConfig and append to TOML. Returns error or None."""
    account = AccountConfig.model_validate(data)
    doc, err = _read_document()
    if doc is None:
        return err

    account_table = tomlkit.table()
    account_table["name"] = account.name or ""
    account_table["phone"] = account.phone
    account_table["password"] = account.password
    for key in (
        "start_lat",
        "start_lng",
        "target_distance_km",
        "target_pace_min_per_km",
        "skip_wait",
        "force_submit",
        "ignore_target_met",
        "auto_run_enabled",
        "morning_time",
        "normal_time",
        "auto_run_jitter_minutes",
    ):
        val = getattr(account, key)
        if val is not None:
            account_table[key] = val

    accounts = doc.setdefault("accounts", tomlkit.aot())
    if not isinstance(accounts, AoT):
        return "config: 'accounts' exists but is not an array of tables"
    _ensure_aot_entry_separation(accounts)
    accounts.append(account_table)
    return _write_document(doc)


def update_account(index: int, data: dict[str, Any]) -> str | None:
    """Validate and replace an existing account by index."""
    account = AccountConfig.model_validate(data)
    doc, err = _read_document()
    if doc is None:
        return err

    accounts = doc.get("accounts")
    if not isinstance(accounts, AoT):
        return "No accounts section found"
    if index < 0 or index >= len(accounts):
        return f"Account index {index} out of range (0..{len(accounts) - 1})"

    item = accounts[index]
    item["name"] = account.name or ""
    item["phone"] = account.phone
    item["password"] = account.password
    for key in (
        "start_lat",
        "start_lng",
        "target_distance_km",
        "target_pace_min_per_km",
        "skip_wait",
        "force_submit",
        "ignore_target_met",
        "auto_run_enabled",
        "morning_time",
        "normal_time",
        "auto_run_jitter_minutes",
    ):
        val = getattr(account, key)
        if val is not None:
            item[key] = val
        elif key in item:
            del item[key]

    return _write_document(doc)


def delete_account(index: int) -> str | None:
    """Remove an account by index."""
    doc, err = _read_document()
    if doc is None:
        return err

    accounts = doc.get("accounts")
    if not isinstance(accounts, AoT):
        return "No accounts section found"
    if index < 0 or index >= len(accounts):
        return f"Account index {index} out of range (0..{len(accounts) - 1})"
    accounts.pop(index)
    return _write_document(doc)


def get_section(section: str) -> dict[str, Any] | None:
    """Read a TOML section, returning defaults from the pydantic model if missing."""
    model_cls = _SECTION_MODELS.get(section)
    if model_cls is None:
        return None

    doc, _err = _read_document()
    table = doc.get(section) if doc is not None else None

    if table is not None and hasattr(table, "unwrap"):
        raw = table.unwrap()
        if isinstance(raw, dict):
            defaults = model_cls().model_dump()
            defaults.update({k: v for k, v in raw.items() if k in defaults})
            return defaults

    return model_cls().model_dump()


def update_section(section: str, data: dict[str, Any]) -> str | None:
    """Validate data against the corresponding pydantic model and write to TOML."""
    model_cls = _SECTION_MODELS.get(section)
    if model_cls is None:
        return f"Unknown section: {section}"

    try:
        model_instance = model_cls.model_validate(data)
    except ValidationError as exc:
        return f"Validation failed for [{section}]:\n- {_fmt_err(exc)}"

    doc, err = _read_document()
    if doc is None:
        return err

    table = doc.setdefault(section, tomlkit.table())
    new_values = model_instance.model_dump()
    for key, value in new_values.items():
        table[key] = value  # type: ignore[index]

    return _write_document(doc)


def reload_settings() -> list[Settings]:
    """Re-read TOML and return fresh Settings list."""
    return Settings.load_all()
