"""Auto-run scheduler using APScheduler for per-account timed execution."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from apscheduler import AsyncScheduler, ConflictPolicy
from apscheduler.triggers.cron import CronTrigger

from fake_track.core.config import Settings
from fake_track.core.config_service import AccountInfo, list_accounts

_logger = logging.getLogger(__name__)

_JOB_ID_PREFIX = "ft_auto"

# Module-level registry so APScheduler can reference _dispatch_job by module path.
_registry: dict[str, Callable[[], Awaitable[None]]] = {}


async def _dispatch_job(schedule_id: str) -> None:
    """Module-level entry point called by APScheduler when a schedule fires."""
    job_func = _registry.get(schedule_id)
    if job_func is None:
        _logger.warning("No job registered for schedule %s", schedule_id)
        return
    try:
        await job_func()
    except Exception:
        _logger.exception("Auto-run job failed: %s", schedule_id)


def _schedule_id(account_index: int, run_type: str) -> str:
    return f"{_JOB_ID_PREFIX}_{account_index}_{run_type}"


RunCallback = Callable[[Settings, str, AccountInfo], Awaitable[None]]


class AutoRunScheduler:
    """Manages per-account auto-run schedules via APScheduler."""

    def __init__(self) -> None:
        self._scheduler: AsyncScheduler | None = None
        self._enabled: bool = False
        self._on_run: RunCallback | None = None

    @property
    def enabled(self) -> bool:
        return self._enabled

    def set_on_run(self, callback: RunCallback) -> None:
        """Set the async callback invoked when a scheduled run should execute."""
        self._on_run = callback

    async def start(self) -> None:
        """Initialize and start the scheduler. Call once on the event loop."""
        self._scheduler = AsyncScheduler()
        await self._scheduler.__aenter__()

    async def shutdown(self) -> None:
        """Clean up the scheduler."""
        if self._scheduler is not None:
            _registry.clear()
            await self._scheduler.__aexit__(None, None, None)
            self._scheduler = None

    async def set_enabled(self, value: bool) -> None:
        """Enable or disable all auto-run schedules."""
        if value == self._enabled or self._scheduler is None:
            return
        self._enabled = value
        if value:
            await self._sync_jobs()
            for sched in await self._scheduler.get_schedules():
                sched_id = getattr(sched, "id", "")
                if isinstance(sched_id, str) and sched_id.startswith(_JOB_ID_PREFIX):
                    await self._scheduler.unpause_schedule(sched_id)
            _logger.info("Auto-run scheduler enabled")
        else:
            for sched in await self._scheduler.get_schedules():
                sched_id = getattr(sched, "id", "")
                if isinstance(sched_id, str) and sched_id.startswith(_JOB_ID_PREFIX):
                    await self._scheduler.pause_schedule(sched_id)
            _logger.info("Auto-run scheduler disabled")

    async def resync(self) -> None:
        """Reconcile schedules with current config. Call after account changes."""
        if not self._enabled or self._scheduler is None:
            return
        await self._sync_jobs()

    async def _sync_jobs(self) -> None:
        """Add schedules for enabled accounts; remove stale ones."""
        assert self._scheduler is not None

        try:
            raw_accounts = list_accounts()
            settings_list = Settings.load_all()
        except Exception:
            _logger.exception("Failed to load config during sync")
            return

        desired_ids: set[str] = set()

        for raw in raw_accounts:
            idx = raw.index
            if not raw.auto_run_enabled:
                continue
            try:
                settings = settings_list[idx]
            except IndexError:
                continue

            for run_type in ("morning", "normal"):
                sid = _schedule_id(idx, run_type)
                desired_ids.add(sid)

                time_str = getattr(raw, f"{run_type}_time", "")
                if not time_str:
                    continue
                try:
                    hour, minute = map(int, time_str.split(":"))
                except ValueError:
                    _logger.warning(
                        "Invalid %s_time for account %d: %r", run_type, idx, time_str
                    )
                    continue

                jitter_seconds = raw.auto_run_jitter_minutes * 60

                if self._on_run is None:
                    return

                on_run = self._on_run

                async def _create_job(
                    _settings: Settings = settings,
                    _run_type: str = run_type,
                    _raw: AccountInfo = raw,
                ) -> None:
                    await on_run(_settings, _run_type, _raw)  # noqa: B023

                _registry[sid] = _create_job

                await self._scheduler.add_schedule(
                    _dispatch_job,
                    CronTrigger(hour=hour, minute=minute),  # type: ignore[call-arg]
                    args=(sid,),
                    id=sid,
                    max_jitter=jitter_seconds,
                    misfire_grace_time=60,
                    conflict_policy=ConflictPolicy.replace,
                )

        # Remove schedules no longer in config
        for sched in await self._scheduler.get_schedules():
            sched_id = getattr(sched, "id", "")
            if isinstance(sched_id, str) and sched_id.startswith(_JOB_ID_PREFIX):
                if sched_id not in desired_ids:
                    _registry.pop(sched_id, None)
                    await self._scheduler.remove_schedule(sched_id)


scheduler = AutoRunScheduler()
