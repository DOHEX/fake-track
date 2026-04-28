import json
import random
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from rich.progress import (
    BarColumn,
    Progress,
    TaskProgressColumn,
    TextColumn,
    TimeRemainingColumn,
)

from .client import ApiError, CampusRunClient
from .config import Settings
from .crypto import aes_encrypt, encryption_self_check
from .geo import gcj02_to_wgs84, haversine_km, wgs84_to_gcj02
from .models import (
    RunningData,
    RunType,
    TrackBuildResult,
    TrackFilterPolicy,
    TrackGenerationRequest,
    TrackPoint,
)
from .payloads import build_path_upload_payload, build_run_summary_payload
from .track import TrackGenerator
from .visualize import render_track_overlay_png

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


@dataclass(slots=True)
class RunExecutionOptions:
    skip_submit_wait: bool = False
    force_submit: bool = False
    ignore_target_met: bool = False
    track_image_path: str | None = None


@dataclass(slots=True, frozen=True)
class ServerRunLimits:
    required_distance_km: float
    min_pace_min_per_km: float
    max_pace_min_per_km: float


@dataclass(slots=True, frozen=True)
class RunTargetPlan:
    target_distance_km: float
    target_pace_min_per_km: float
    target_duration_sec: int
    distance_guard_min_km: float


@dataclass(slots=True, frozen=True)
class TrackGenerationContext:
    start: tuple[float, float]
    pass_points: list[dict[str, Any]]
    coordinate_bridge_applied: bool


class RunWorkflow:
    _DEVICE_COMPENSATION: dict[str, float] = {
        "oppo": 1.08,
        "realme": 1.08,
        "oneplus": 1.08,
        "vivo": 1.07,
        "iqoo": 1.07,
        "xiaomi": 1.06,
        "redmi": 1.06,
    }

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client = CampusRunClient(settings)
        self.track_generator = TrackGenerator()

    @staticmethod
    def _format_seconds(seconds: float) -> str:
        total = max(0, int(round(seconds)))
        hours, rem = divmod(total, 3600)
        minutes, secs = divmod(rem, 60)
        if hours > 0:
            return f"{hours}:{minutes:02d}:{secs:02d}"
        return f"{minutes:02d}:{secs:02d}"

    @staticmethod
    def _classify_run_type() -> RunType | None:
        now = datetime.now()
        time_decimal = now.hour + now.minute / 60.0 + now.second / 3600.0
        if 6.0 <= time_decimal < 7.0:
            return RunType.MORNING
        if 7.0 <= time_decimal < 22.0:
            return RunType.NORMAL
        return None

    @staticmethod
    def _check_target_counts(
        running_data: RunningData,
        run_type: RunType,
    ) -> str | None:
        if running_data.target_effective <= 0:
            return None

        if (
            running_data.morning >= running_data.target_effective
            and running_data.universal >= running_data.target_effective
        ):
            return (
                f"Both morning ({running_data.morning}) and normal "
                f"({running_data.universal}) targets already met "
                f"(target={running_data.target_effective})"
            )

        if (
            run_type is RunType.MORNING
            and running_data.morning >= running_data.target_effective
        ):
            return (
                f"Morning run target already met: "
                f"{running_data.morning}/{running_data.target_effective}"
            )
        if (
            run_type is RunType.NORMAL
            and running_data.universal >= running_data.target_effective
        ):
            return (
                f"Normal run target already met: "
                f"{running_data.universal}/{running_data.target_effective}"
            )

        return None

    def _make_skip_report(
        self,
        skip_reason: str,
        running_data: RunningData | None = None,
        run_type: RunType | None = None,
        server_responses: dict[str, Any] | None = None,
    ) -> RunReport:
        report = RunReport(
            success=True,
            mode="skipped",
            record_id=None,
            summary={
                "run_type": run_type.value if run_type else None,
                "morning": running_data.morning if running_data else None,
                "universal": running_data.universal if running_data else None,
                "effective": running_data.effective if running_data else None,
                "target_effective": (
                    running_data.target_effective if running_data else None
                ),
                "skip_reason": skip_reason,
            },
            server={
                **(server_responses or {}),
            },
            warning=skip_reason,
        )
        self._write_report(report)
        return report

    @staticmethod
    def _emit(progress: ProgressCallback | None, message: str) -> None:
        if progress is not None:
            progress(message)

    @staticmethod
    def _convert_pass_points(
        pass_points: list[dict[str, Any]],
        converter: Callable[[float, float], tuple[float, float]],
    ) -> list[dict[str, Any]]:
        converted: list[dict[str, Any]] = []
        for item in pass_points:
            cloned = dict(item)
            try:
                lat = float(item["lat"])
                lng = float(item["lng"])
            except KeyError, TypeError, ValueError:
                converted.append(cloned)
                continue
            out_lat, out_lng = converter(lat, lng)
            cloned["lat"] = out_lat
            cloned["lng"] = out_lng
            converted.append(cloned)
        return converted

    @staticmethod
    def _convert_track_points(
        points: list[TrackPoint],
        converter: Callable[[float, float], tuple[float, float]],
    ) -> list[TrackPoint]:
        converted: list[TrackPoint] = []
        for point in points:
            lat, lng = converter(point.latitude, point.longitude)
            converted.append(
                TrackPoint(
                    latitude=lat,
                    longitude=lng,
                    timestamp=point.timestamp,
                    accuracy=point.accuracy,
                )
            )
        return converted

    @staticmethod
    def _count_pass_hits(
        points: list[TrackPoint],
        must_pass_points: list[dict[str, Any]],
        radius_km: float,
    ) -> int:
        hits = 0
        for item in must_pass_points:
            try:
                p_lat = float(item["lat"])
                p_lng = float(item["lng"])
            except KeyError, TypeError, ValueError:
                continue
            if any(
                haversine_km(point.latitude, point.longitude, p_lat, p_lng) <= radius_km
                for point in points
            ):
                hits += 1
        return hits

    def _resolve_compensation_factor(self) -> float:
        brand = (self.settings.device_brand or "").strip().lower()
        if not brand:
            return float(self.settings.compensation_factor)

        if "honor" in brand:
            return 1.06

        for key, factor in self._DEVICE_COMPENSATION.items():
            if key in brand:
                return factor

        return float(self.settings.compensation_factor)

    def _extract_server_limits(
        self, login_data: dict[str, Any]
    ) -> tuple[int, ServerRunLimits]:
        student_id = int(login_data.get("id", 0))
        if not student_id:
            raise ApiError("Login response missing student id")

        shortest_distance_m = float(login_data.get("shortest_distance", 2000) or 2000)
        min_pace_sec = float(login_data.get("min_pace", 180) or 180)
        max_pace_sec = float(login_data.get("max_pace", 1000) or 1000)

        return student_id, ServerRunLimits(
            required_distance_km=max(2.0, shortest_distance_m / 1000.0),
            min_pace_min_per_km=min_pace_sec / 60.0,
            max_pace_min_per_km=max_pace_sec / 60.0,
        )

    def _build_target_plan(
        self,
        limits: ServerRunLimits,
        compensation_factor: float,
    ) -> RunTargetPlan:
        hard_distance_floor_km = max(2.0, limits.required_distance_km)
        compensated_required_km = limits.required_distance_km / max(
            0.01, compensation_factor
        )
        distance_guard_min_km = max(
            hard_distance_floor_km,
            compensated_required_km,
            limits.required_distance_km
            * max(0.0, self.settings.distance_tolerance_ratio),
        )
        distance_guard_min_km += 0.015

        base_distance_km = max(
            self.settings.target_distance_km, limits.required_distance_km
        )
        distance_ratio = random.uniform(
            max(0.0, 1.0 - self.settings.distance_jitter_ratio),
            1.0 + self.settings.distance_jitter_ratio,
        )
        target_distance_km = max(
            distance_guard_min_km + 0.02,
            limits.required_distance_km,
            base_distance_km * distance_ratio,
        )

        base_pace = min(
            max(self.settings.target_pace_min_per_km, limits.min_pace_min_per_km),
            limits.max_pace_min_per_km,
        )
        pace_ratio = random.uniform(
            max(0.0, 1.0 - self.settings.pace_jitter_ratio),
            1.0 + self.settings.pace_jitter_ratio,
        )
        target_pace_min_per_km = min(
            max(base_pace * pace_ratio, limits.min_pace_min_per_km),
            limits.max_pace_min_per_km,
        )

        duration_min_sec = min(
            self.settings.target_duration_min_sec,
            self.settings.target_duration_max_sec,
        )
        duration_max_sec = max(
            self.settings.target_duration_min_sec,
            self.settings.target_duration_max_sec,
        )
        target_duration_sec = random.randint(
            max(60, duration_min_sec),
            max(60, duration_max_sec),
        )

        return RunTargetPlan(
            target_distance_km=target_distance_km,
            target_pace_min_per_km=target_pace_min_per_km,
            target_duration_sec=target_duration_sec,
            distance_guard_min_km=distance_guard_min_km,
        )

    def _prepare_track_context(
        self,
        pass_points: list[dict[str, Any]],
        progress: ProgressCallback | None,
    ) -> TrackGenerationContext:
        start = (self.settings.start_lat, self.settings.start_lng)
        converted_pass_points = pass_points
        bridge_applied = False

        if (
            self.settings.road_routing_enabled
            and self.settings.road_coordinate_bridge_enabled
        ):
            start = gcj02_to_wgs84(start[0], start[1])
            converted_pass_points = self._convert_pass_points(
                pass_points, gcj02_to_wgs84
            )
            bridge_applied = True
            self._emit(progress, "Coordinate bridge: gcj02 -> wgs84 for road routing")

        return TrackGenerationContext(
            start=start,
            pass_points=converted_pass_points,
            coordinate_bridge_applied=bridge_applied,
        )

    def _build_generation_request(
        self,
        context: TrackGenerationContext,
        plan: RunTargetPlan,
    ) -> TrackGenerationRequest:
        return TrackGenerationRequest(
            start=context.start,
            must_pass_points=context.pass_points,
            target_distance_km=plan.target_distance_km,
            target_pace_min_per_km=plan.target_pace_min_per_km,
            sample_interval_sec=self.settings.sample_interval_sec,
            must_pass_radius_km=self.settings.must_pass_radius_km,
            jitter_m=self.settings.point_jitter_m,
            timestamp_jitter_ms=self.settings.timestamp_jitter_ms,
            accuracy_min=self.settings.point_accuracy_min,
            accuracy_max=self.settings.point_accuracy_max,
            road_routing_enabled=self.settings.road_routing_enabled,
            road_map_path=self.settings.road_map_path,
            road_snap_max_m=self.settings.road_snap_max_m,
            filter_policy=TrackFilterPolicy(
                max_speed_threshold_m_s=self.settings.max_speed_threshold_m_s,
                max_jump_distance_km=self.settings.max_jump_distance_km,
                min_move_distance_m=self.settings.min_move_distance_m,
                min_move_speed_m_s=self.settings.min_move_speed_m_s,
                gps_accuracy_threshold_m=self.settings.gps_accuracy_threshold_m,
                primary_angle_threshold_deg=self.settings.primary_angle_threshold_deg,
                secondary_angle_threshold_deg=self.settings.secondary_angle_threshold_deg,
            ),
        )

    def _generate_track_with_guards(
        self,
        context: TrackGenerationContext,
        limits: ServerRunLimits,
        plan: RunTargetPlan,
        progress: ProgressCallback | None,
    ) -> tuple[TrackBuildResult, int, RunTargetPlan]:
        current_plan = plan
        run_data = self.track_generator.generate(
            self._build_generation_request(context, current_plan)
        )

        for _ in range(3):
            if (
                self.settings.target_duration_min_sec
                <= run_data.duration_sec
                <= self.settings.target_duration_max_sec
            ):
                break

            corrected_pace = (
                current_plan.target_pace_min_per_km
                * current_plan.target_duration_sec
                / max(1, run_data.duration_sec)
            )
            corrected_pace = min(
                max(corrected_pace, limits.min_pace_min_per_km),
                limits.max_pace_min_per_km,
            )
            if abs(corrected_pace - current_plan.target_pace_min_per_km) < 0.01:
                break

            self._emit(
                progress,
                (
                    "Adjust pace for duration window: "
                    f"{current_plan.target_pace_min_per_km:.2f} -> {corrected_pace:.2f} min/km"
                ),
            )
            current_plan = RunTargetPlan(
                target_distance_km=current_plan.target_distance_km,
                target_pace_min_per_km=corrected_pace,
                target_duration_sec=current_plan.target_duration_sec,
                distance_guard_min_km=current_plan.distance_guard_min_km,
            )
            run_data = self.track_generator.generate(
                self._build_generation_request(context, current_plan)
            )

        distance_floor_km = max(
            2.0,
            limits.required_distance_km,
            current_plan.distance_guard_min_km,
        )

        distance_guard_attempts = 0
        while run_data.distance_km < distance_floor_km and distance_guard_attempts < 7:
            deficit_km = max(0.0, distance_floor_km - run_data.distance_km)
            next_target_distance = current_plan.target_distance_km + max(
                0.10, deficit_km * 1.4
            )
            distance_guard_attempts += 1

            self._emit(
                progress,
                (
                    f"Distance guard {distance_guard_attempts}/7: "
                    f"floor {distance_floor_km:.2f}km, "
                    f"rebuild target {next_target_distance:.2f}km"
                ),
            )

            current_plan = RunTargetPlan(
                target_distance_km=next_target_distance,
                target_pace_min_per_km=current_plan.target_pace_min_per_km,
                target_duration_sec=current_plan.target_duration_sec,
                distance_guard_min_km=current_plan.distance_guard_min_km,
            )
            run_data = self.track_generator.generate(
                self._build_generation_request(context, current_plan)
            )

        if run_data.distance_km < distance_floor_km:
            raise ApiError(
                "Track generation failed distance guard: "
                f"need >= {distance_floor_km:.2f}km, got {run_data.distance_km:.2f}km "
                f"after {distance_guard_attempts} rebuilds"
            )

        return run_data, distance_guard_attempts, current_plan

    def _wait_before_submit(
        self,
        run_duration_sec: int,
        skip_wait: bool,
        progress: ProgressCallback | None,
    ) -> None:
        if skip_wait:
            return

        base_wait = max(0.0, float(run_duration_sec))
        jitter = random.uniform(-6.0, 6.0)
        wait_sec = max(0.0, base_wait + jitter)
        if wait_sec < 0.5:
            return

        self._emit(
            progress,
            f"Simulate running before submit: {self._format_seconds(wait_sec)}",
        )
        remaining = wait_sec
        if progress is None:
            while remaining > 0:
                sleep_sec = min(15.0, remaining)
                time.sleep(sleep_sec)
                remaining -= sleep_sec
            return

        with Progress(
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            TimeRemainingColumn(),
            transient=True,
        ) as progress_bar:
            task_id = progress_bar.add_task("Running", total=wait_sec)
            while remaining > 0:
                sleep_sec = min(1.0, remaining)
                time.sleep(sleep_sec)
                remaining -= sleep_sec
                progress_bar.update(task_id, advance=sleep_sec)

    def run_connectivity(self, progress: ProgressCallback | None = None) -> RunReport:
        self._emit(progress, "Step 1/3: login")
        login = self.client.authenticate_user()
        login_data = login.data if isinstance(login.data, dict) else {}
        student_id, _ = self._extract_server_limits(login_data)

        self._emit(progress, "Step 2/3: fetch route points")
        route = self.client.fetch_route_points(
            self.settings.start_lat, self.settings.start_lng
        )
        points = route.data if isinstance(route.data, list) else []

        self._emit(progress, "Step 3/3: create record")
        line = self.client.create_run_record(student_id=student_id, pass_points=points)
        record_id = int((line.data or {}).get("record_id", 0))

        report = RunReport(
            success=record_id > 0,
            mode="connectivity",
            record_id=record_id,
            summary={
                "student_id": student_id,
                "pass_point_count": len(points),
                "record_id": record_id,
            },
            server={
                "login": login.raw,
                "randrunInfo": route.raw,
                "createLine": line.raw,
            },
            warning=None,
        )
        self._write_report(report)
        self._emit(progress, f"Connectivity done, record_id={record_id}")
        return report

    def run_full(
        self,
        progress: ProgressCallback | None = None,
        options: RunExecutionOptions | None = None,
    ) -> RunReport:
        run_options = options or RunExecutionOptions()
        force_submit = run_options.force_submit

        self._emit(progress, "Step 1/9: crypto self-check")
        encryption_self_check(self.settings.run_key)

        self._emit(progress, "Step 2/9: login")
        login = self.client.authenticate_user()
        login_data = login.data if isinstance(login.data, dict) else {}
        student_id, limits = self._extract_server_limits(login_data)

        self._emit(progress, "Step 3/9: check run status")
        run_type = self._classify_run_type()

        if run_type is None:
            msg = (
                "Current time is outside valid run windows "
                "(morning: 06:00-07:00, normal: 07:00-22:00)"
            )
            self._emit(progress, f"  {msg}")
            return self._make_skip_report(msg)

        run_type_label = "morning run" if run_type is RunType.MORNING else "normal run"
        self._emit(progress, f"  Run type: {run_type_label} ({run_type.value})")

        running_data_resp = self.client.fetch_running_data(student_id)
        rd = running_data_resp.data
        if not isinstance(rd, dict):
            raise ApiError(
                f"RunningData response data is not a dict: {type(rd).__name__}"
            )
        running_data = RunningData(
            morning=int(rd.get("morning", 0)),
            universal=int(rd.get("universal", 0)),
            effective=int(rd.get("effective", 0)),
            target_effective=int(rd.get("target_effective", 0)),
        )
        self._emit(
            progress,
            f"  Running data: morning={running_data.morning}, "
            f"universal={running_data.universal}, "
            f"effective={running_data.effective}, "
            f"target_effective={running_data.target_effective}",
        )

        target_skip_reason = self._check_target_counts(running_data, run_type)
        if target_skip_reason is not None and not run_options.ignore_target_met:
            self._emit(progress, f"  Skipping: {target_skip_reason}")
            return self._make_skip_report(
                skip_reason=target_skip_reason,
                running_data=running_data,
                run_type=run_type,
                server_responses={"running_data": running_data_resp.raw},
            )
        if target_skip_reason is not None:
            self._emit(
                progress,
                f"  Target check ignored: {target_skip_reason}",
            )
            self._emit(
                progress,
                "  Target already met, but --ignore-target-met is enabled; continue.",
            )

        compensation_factor = self._resolve_compensation_factor()
        plan = self._build_target_plan(limits, compensation_factor)
        self._emit(
            progress,
            (
                f"Target: distance {plan.target_distance_km:.2f}km, "
                f"pace {plan.target_pace_min_per_km:.2f}min/km, "
                f"duration {self._format_seconds(plan.target_duration_sec)}"
            ),
        )

        self._emit(progress, "Step 4/9: fetch route points")
        rand_info = self.client.fetch_route_points(
            self.settings.start_lat, self.settings.start_lng
        )
        pass_points = rand_info.data if isinstance(rand_info.data, list) else []
        if not pass_points:
            raise ApiError("randrunInfo returned empty pass points")

        context = self._prepare_track_context(pass_points, progress)

        self._emit(progress, "Step 5/9: create running record")
        line = self.client.create_run_record(
            student_id=student_id, pass_points=pass_points
        )
        record_id = int((line.data or {}).get("record_id", 0))
        if not record_id:
            raise ApiError("createLine missing record_id")
        self._emit(
            progress,
            "IMPORTANT: Please do NOT open the mini app while this run is in progress.",
        )

        self._emit(progress, "Step 6/9: generate track")
        run_data, distance_guard_attempts, final_plan = (
            self._generate_track_with_guards(
                context=context,
                limits=limits,
                plan=plan,
                progress=progress,
            )
        )

        self._emit(
            progress,
            (
                f"Track ready: {len(run_data.points)} points, "
                f"duration {self._format_seconds(run_data.duration_sec)}, "
                f"distance {run_data.distance_km:.2f}km "
                f"(raw {run_data.raw_distance_km:.2f}km)"
            ),
        )
        self._emit(
            progress,
            (
                "Route mode: road-network"
                if run_data.road_routing_used
                else "Route mode: fallback (non-road)"
            ),
        )

        upload_points = run_data.points
        if context.coordinate_bridge_applied and run_data.road_routing_used:
            upload_points = self._convert_track_points(run_data.points, wgs84_to_gcj02)
            self._emit(progress, "Coordinate bridge: wgs84 -> gcj02 for upload")

        track_image = None
        if run_options.track_image_path:
            try:
                track_image = render_track_overlay_png(
                    map_path=self.settings.road_map_path,
                    points=run_data.points,
                    output_path=run_options.track_image_path,
                    must_pass_points=context.pass_points,
                )
                self._emit(progress, f"Track image saved: {track_image}")
            except Exception as exc:  # noqa: BLE001
                self._emit(progress, f"Track image skipped: {exc}")

        self._wait_before_submit(
            run_duration_sec=run_data.duration_sec,
            skip_wait=run_options.skip_submit_wait,
            progress=progress,
        )

        summary_payload = build_run_summary_payload(
            record_id=record_id,
            run_result=run_data,
            compensation_factor=compensation_factor,
        )
        summary_payload["pass_point"] = self._count_pass_hits(
            points=upload_points,
            must_pass_points=pass_points,
            radius_km=self.settings.must_pass_radius_km,
        )
        encrypted_summary = aes_encrypt(
            json.dumps(summary_payload, ensure_ascii=False),
            self.settings.run_key,
        )

        time.sleep(random.uniform(0.5, 1.3))

        self._emit(progress, "Step 7/9: checkRecord")
        check = self.client.validate_run_payload(encrypted_summary)
        check_status = None
        if isinstance(check.data, dict):
            check_status = int(check.data.get("status", 1))
        if not force_submit and check_status == 0:
            raise ApiError(f"checkRecord rejected data: {check.message}")
        if force_submit and check_status == 0:
            self._emit(
                progress,
                "checkRecord rejected, but force submit is enabled; continue.",
            )

        self._emit(progress, "Step 8/9: updateRecordNew")
        update = self.client.submit_run_summary(encrypted_summary)

        self._emit(progress, "Step 9/9: upload path batches")
        uploaded_batches = self._upload_batches(record_id, upload_points, progress)

        self._emit(progress, "Fetch result: recordInfo")
        record = self.client.fetch_record_info(record_id)
        self._emit(progress, "Fetch result: GetPathPoints")
        path_info = self.client.fetch_path_points(record_id)

        warning = record.data.get("warning") if isinstance(record.data, dict) else None

        report = RunReport(
            success=True,
            mode="full",
            record_id=record_id,
            summary={
                "generated_distance_km": round(run_data.distance_km, 4),
                "generated_distance_raw_km": round(run_data.raw_distance_km, 4),
                "generated_distance_confirmed_km": round(
                    run_data.confirmed_distance_km, 4
                ),
                "generated_confirmed_point_count": run_data.confirmed_point_count,
                "generated_duration_sec": run_data.duration_sec,
                "generated_pace_min_per_km": round(run_data.pace_min_per_km, 4),
                "generated_pass_point": run_data.must_pass_count,
                "generated_point_count": len(run_data.points),
                "generated_compensation_factor": compensation_factor,
                "generated_distance_guard_min_km": round(
                    final_plan.distance_guard_min_km, 4
                ),
                "generated_distance_guard_attempts": distance_guard_attempts,
                "generated_road_routing_used": run_data.road_routing_used,
                "generated_coordinate_bridge_applied": context.coordinate_bridge_applied,
                "generated_track_image_enabled": (
                    run_options.track_image_path is not None
                ),
                "generated_submit_wait_skipped": run_options.skip_submit_wait,
                "generated_force_submit": force_submit,
                "generated_ignore_target_met": run_options.ignore_target_met,
                "generated_ignored_target_skip_reason": target_skip_reason,
                "generated_track_image": track_image,
                "uploaded_batches": uploaded_batches,
                "uploaded_point_count": len(upload_points),
                "target_duration_sec": final_plan.target_duration_sec,
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

        batch_size = 50
        total_batches = (len(points) + batch_size - 1) // batch_size
        uploaded = 0

        offsets = range(0, len(points), batch_size)

        if progress is None:
            for offset in offsets:
                segment = points[offset : offset + batch_size]
                payload = build_path_upload_payload(record_id, segment)
                encrypted = aes_encrypt(
                    json.dumps(payload, ensure_ascii=False),
                    self.settings.run_key,
                )
                self.client.upload_path_batch(encrypted)
                uploaded += 1
            return uploaded

        self._emit(progress, f"Upload started: {total_batches} batches")
        with Progress(
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            TextColumn("{task.completed:.0f}/{task.total:.0f} batches"),
            transient=True,
        ) as progress_bar:
            task_id = progress_bar.add_task("Uploading", total=total_batches)
            for offset in offsets:
                segment = points[offset : offset + batch_size]
                payload = build_path_upload_payload(record_id, segment)
                encrypted = aes_encrypt(
                    json.dumps(payload, ensure_ascii=False),
                    self.settings.run_key,
                )
                self.client.upload_path_batch(encrypted)
                uploaded += 1
                progress_bar.update(task_id, advance=1)

        return uploaded

    def _write_report(self, report: RunReport) -> None:
        if not self.settings.report_path:
            return

        path = Path(self.settings.report_path)
        payload = {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "report": report.to_dict(),
        }
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
