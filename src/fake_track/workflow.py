import json
import random
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from .client import ApiError, CampusRunClient
from .config import Settings
from .crypto import aes_encrypt, encryption_self_check
from .track import (
    TrackPoint,
    build_human_like_track,
    make_path_batch_payload,
    make_summary_payload,
)

ProgressCallback = Callable[[str], None]


@dataclass(slots=True)
class RunReport:
    success: bool
    mode: str
    record_id: int | None
    summary: dict[str, Any]
    server: dict[str, Any]
    warning: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class RunWorkflow:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client = CampusRunClient(settings)

    @staticmethod
    def _format_seconds(seconds: float) -> str:
        total = max(0, int(round(seconds)))
        hours, rem = divmod(total, 3600)
        minutes, secs = divmod(rem, 60)
        if hours > 0:
            return f"{hours}:{minutes:02d}:{secs:02d}"
        return f"{minutes:02d}:{secs:02d}"

    @staticmethod
    def _emit(progress: ProgressCallback | None, message: str) -> None:
        if progress is not None:
            progress(message)

    def _wait_before_submit(
        self,
        run_duration_sec: int,
        progress: ProgressCallback | None = None,
    ) -> None:
        mode = (self.settings.submit_wait_mode or "duration").lower()
        if mode == "none":
            return

        scale = max(0.0, self.settings.submit_wait_scale)
        base_wait = max(0.0, run_duration_sec * scale)
        jitter = random.uniform(
            -max(0, self.settings.submit_wait_jitter_sec),
            max(0, self.settings.submit_wait_jitter_sec),
        )
        wait_sec = max(0.0, base_wait + jitter)
        if wait_sec < 0.5:
            return

        self._emit(
            progress,
            f"Simulate running before submit: {self._format_seconds(wait_sec)}",
        )
        remaining = wait_sec
        while remaining > 0:
            sleep_sec = min(15.0, remaining)
            time.sleep(sleep_sec)
            remaining -= sleep_sec
            if remaining > 0:
                self._emit(
                    progress,
                    f"Running... remaining {self._format_seconds(remaining)}",
                )

    def run_connectivity(self, progress: ProgressCallback | None = None) -> RunReport:
        self._emit(progress, "Step 1/3: login")
        login = self.client.login()
        data = login.data if isinstance(login.data, dict) else {}
        student_id = int(data.get("id", 0))
        if not student_id:
            raise ApiError("Login response missing student id")

        self._emit(progress, "Step 2/3: fetch route points")
        info = self.client.rand_run_info(
            self.settings.start_lat, self.settings.start_lng
        )
        points = info.data if isinstance(info.data, list) else []

        self._emit(progress, "Step 3/3: create record")
        line = self.client.create_line(student_id=student_id, pass_point=points)
        record_id = int((line.data or {}).get("record_id", 0))

        summary = {
            "student_id": student_id,
            "pass_point_count": len(points),
            "record_id": record_id,
        }

        report = RunReport(
            success=record_id > 0,
            mode="connectivity",
            record_id=record_id,
            summary=summary,
            server={
                "login": login.raw,
                "randrunInfo": info.raw,
                "createLine": line.raw,
            },
            warning=None,
        )
        self._write_report(report)
        self._emit(progress, f"Connectivity done, record_id={record_id}")
        return report

    def run_full(
        self, force: bool = False, progress: ProgressCallback | None = None
    ) -> RunReport:
        self._emit(progress, "Step 1/8: crypto self-check")
        encryption_self_check(self.settings.run_key)

        self._emit(progress, "Step 2/8: login")
        login = self.client.login()
        login_data = login.data if isinstance(login.data, dict) else {}
        student_id = int(login_data.get("id", 0))
        if not student_id:
            raise ApiError("Login response missing student id")

        shortest_distance_m = float(login_data.get("shortest_distance", 2000) or 2000)
        min_pace_sec = float(login_data.get("min_pace", 180) or 180)
        max_pace_sec = float(login_data.get("max_pace", 1000) or 1000)

        base_distance_km = max(
            self.settings.target_distance_km, shortest_distance_m / 1000
        )
        distance_ratio = random.uniform(
            max(0.0, 1.0 - self.settings.distance_jitter_ratio),
            1.0 + self.settings.distance_jitter_ratio,
        )
        target_distance_km = max(
            shortest_distance_m / 1000, base_distance_km * distance_ratio
        )

        min_pace_min = min_pace_sec / 60
        max_pace_min = max_pace_sec / 60
        base_pace_min = min(
            max(self.settings.target_pace_min_per_km, min_pace_min), max_pace_min
        )
        pace_ratio = random.uniform(
            max(0.0, 1.0 - self.settings.pace_jitter_ratio),
            1.0 + self.settings.pace_jitter_ratio,
        )
        target_pace_min = min(
            max(base_pace_min * pace_ratio, min_pace_min), max_pace_min
        )
        duration_min_sec = min(
            self.settings.target_duration_min_sec, self.settings.target_duration_max_sec
        )
        duration_max_sec = max(
            self.settings.target_duration_min_sec, self.settings.target_duration_max_sec
        )
        target_duration_sec = random.randint(
            max(60, duration_min_sec), max(60, duration_max_sec)
        )
        self._emit(
            progress,
            (
                f"Target: distance {target_distance_km:.2f}km, "
                f"pace {target_pace_min:.2f}min/km, "
                f"duration {self._format_seconds(target_duration_sec)}"
            ),
        )

        self._emit(progress, "Step 3/8: fetch route points")
        rand_info = self.client.rand_run_info(
            self.settings.start_lat, self.settings.start_lng
        )
        pass_points = rand_info.data if isinstance(rand_info.data, list) else []
        if not pass_points:
            raise ApiError("randrunInfo returned empty pass points")

        self._emit(progress, "Step 4/8: create running record")
        line = self.client.create_line(student_id=student_id, pass_point=pass_points)
        record_id = int((line.data or {}).get("record_id", 0))
        if not record_id:
            raise ApiError("createLine missing record_id")
        self._emit(
            progress,
            "IMPORTANT: Please do NOT open the mini app while this run is in progress.",
        )

        self._emit(progress, "Step 5/8: generate track")

        def _build_track_for_pace(pace_min_per_km: float):
            return build_human_like_track(
                start=(self.settings.start_lat, self.settings.start_lng),
                must_pass_points=pass_points,
                target_distance_km=target_distance_km,
                target_pace_min_per_km=pace_min_per_km,
                sample_interval_sec=self.settings.sample_interval_sec,
                must_pass_radius_km=self.settings.must_pass_radius_km,
                jitter_m=self.settings.point_jitter_m,
                timestamp_jitter_ms=self.settings.timestamp_jitter_ms,
                accuracy_min=self.settings.point_accuracy_min,
                accuracy_max=self.settings.point_accuracy_max,
            )

        run_data = _build_track_for_pace(target_pace_min)
        for _ in range(3):
            if duration_min_sec <= run_data.duration_sec <= duration_max_sec:
                break

            corrected_pace = (
                target_pace_min * target_duration_sec / max(1, run_data.duration_sec)
            )
            corrected_pace = min(max(corrected_pace, min_pace_min), max_pace_min)
            if abs(corrected_pace - target_pace_min) < 0.01:
                break

            self._emit(
                progress,
                (
                    f"Adjust pace for duration window: {target_pace_min:.2f} -> "
                    f"{corrected_pace:.2f} min/km"
                ),
            )
            target_pace_min = corrected_pace
            run_data = _build_track_for_pace(target_pace_min)
        self._emit(
            progress,
            (
                f"Track ready: {len(run_data.points)} points, "
                f"duration {self._format_seconds(run_data.duration_sec)}, "
                f"distance {run_data.distance_km:.2f}km"
            ),
        )

        self._wait_before_submit(run_data.duration_sec, progress)

        summary_payload = make_summary_payload(
            record_id=record_id,
            run=run_data,
            compensation_factor=self.settings.compensation_factor,
        )
        encrypted_a = aes_encrypt(
            json.dumps(summary_payload, ensure_ascii=False), self.settings.run_key
        )

        # HAR shows checkRecord starts shortly after the simulated running window ends.
        time.sleep(random.uniform(0.5, 1.3))

        self._emit(progress, "Step 6/8: checkRecord")
        check = self.client.check_record(encrypted_a)
        check_status = None
        if isinstance(check.data, dict):
            check_status = int(check.data.get("status", 1))
        if not force and check_status == 0:
            raise ApiError(f"checkRecord rejected data: {check.message}")

        self._emit(progress, "Step 7/8: updateRecordNew")
        update = self.client.update_record(encrypted_a)

        self._emit(progress, "Step 8/8: upload path batches")
        uploaded_batches = self._upload_batches(record_id, run_data.points, progress)

        self._emit(progress, "Fetch result: recordInfo")
        record = self.client.record_info(record_id)
        self._emit(progress, "Fetch result: GetPathPoints")
        path_info = self.client.get_path_points(record_id)

        warning = None
        if isinstance(record.data, dict):
            warning = record.data.get("warning")

        report = RunReport(
            success=True,
            mode="full",
            record_id=record_id,
            summary={
                "generated_distance_km": round(run_data.distance_km, 4),
                "generated_duration_sec": run_data.duration_sec,
                "generated_pace_min_per_km": round(run_data.pace_min_per_km, 4),
                "generated_pass_point": run_data.must_pass_count,
                "generated_point_count": len(run_data.points),
                "uploaded_batches": uploaded_batches,
                "target_duration_sec": target_duration_sec,
                "record_payload": summary_payload,
            },
            server={
                "login": login.raw,
                "randrunInfo": rand_info.raw,
                "createLine": line.raw,
                "checkRecord": check.raw,
                "updateRecordNew": update.raw,
                "recordInfo": record.raw,
                "GetPathPoints": path_info.raw,
            },
            warning=warning,
        )
        self._write_report(report)
        self._emit(progress, "Run completed")
        return report

    def _upload_batches(
        self,
        record_id: int,
        points: list[TrackPoint],
        progress: ProgressCallback | None = None,
    ) -> int:
        if not points:
            return 0
        batch_size = max(1, self.settings.batch_size)
        batches = 0
        mode = (self.settings.upload_interval_mode or "realtime").lower()
        prev_batch_end_ts = points[0].timestamp
        total_batches = (len(points) + batch_size - 1) // batch_size

        self._emit(
            progress,
            f"Upload started: {total_batches} batches, mode={mode}",
        )

        for index, offset in enumerate(range(0, len(points), batch_size), start=1):
            segment = points[offset : offset + batch_size]

            if mode == "realtime":
                # Pace upload by the synthetic point timestamps to avoid a 1-minute upload window.
                gap_sec = max(0.2, (segment[-1].timestamp - prev_batch_end_ts) / 1000)
                scale = max(0.05, self.settings.upload_realtime_scale)
                jitter_ratio = max(0.0, self.settings.upload_realtime_jitter_ratio)
                delay = (
                    gap_sec
                    * scale
                    * random.uniform(1.0 - jitter_ratio, 1.0 + jitter_ratio)
                )
                self._emit(
                    progress,
                    (
                        f"Wait {self._format_seconds(delay)} before batch {index}/{total_batches}"
                    ),
                )
                time.sleep(max(0.2, delay))
            else:
                # Fast mode follows mini-app behavior: sequential upload with no extra sleep.
                pass

            payload = make_path_batch_payload(record_id, segment)
            encrypted = aes_encrypt(
                json.dumps(payload, ensure_ascii=False), self.settings.run_key
            )
            self.client.upload_path_points(encrypted)
            batches += 1
            prev_batch_end_ts = segment[-1].timestamp

            percent = int(round((batches / max(1, total_batches)) * 100))
            if mode == "realtime":
                remain_track_sec = max(
                    0.0, (points[-1].timestamp - prev_batch_end_ts) / 1000
                )
                remain_eta_sec = remain_track_sec * max(
                    0.05, self.settings.upload_realtime_scale
                )
                self._emit(
                    progress,
                    (
                        f"Upload {batches}/{total_batches} ({percent}%), "
                        f"ETA {self._format_seconds(remain_eta_sec)}"
                    ),
                )
            else:
                self._emit(progress, f"Upload {batches}/{total_batches} ({percent}%)")
        return batches

    def _write_report(self, report: RunReport) -> None:
        output = self.settings.report_path
        if not output:
            return
        path = Path(output)
        payload = {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "report": report.to_dict(),
        }
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
