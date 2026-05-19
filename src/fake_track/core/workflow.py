import json
import random
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from itertools import batched
from typing import Any

from rich.progress import (
    BarColumn,
    Progress,
    TaskProgressColumn,
    TextColumn,
    TimeRemainingColumn,
)

from .client import ApiError, ApiResponse, CampusRunClient
from .config import Settings
from .crypto import aes_encrypt, encryption_self_check
from .geo import gcj02_to_wgs84, haversine_km, wgs84_to_gcj02
from .models import (
    GeneratedTrack,
    PassPoint,
    RunCounts,
    RunType,
    TrackFilterPolicy,
    TrackPoint,
    TrackRequest,
    classify_run_type,
)
from .payloads import (
    build_counts_payload,
    build_path_upload_payload,
    build_run_summary_payload,
    dicts_to_pass_points,
)
from .report import (
    RunReport,
    _make_skip_summary,
    _RunResponses,
    build_full_report,
    write_report_file,
)
from .track_generator import TrackGenerator
from .visualize import render_track_overlay_png

ProgressCallback = Callable[[str], None]
UPLOAD_BATCH_SIZE = 50


@dataclass(slots=True)
class RunExecutionOptions:
    skip_submit_wait: bool = False
    force_submit: bool = False
    ignore_target_met: bool = False
    track_image_path: str | None = None
    disable_progress: bool = False


@dataclass(slots=True, frozen=True)
class RunLimits:
    required_distance_km: float
    min_pace_min_per_km: float
    max_pace_min_per_km: float


@dataclass(slots=True, frozen=True)
class TargetPlan:
    target_distance_km: float
    target_pace_min_per_km: float
    target_duration_sec: int
    distance_guard_min_km: float


@dataclass(slots=True, frozen=True)
class TrackContext:
    start: tuple[float, float]
    pass_points: list[PassPoint]


@dataclass(slots=True)
class SubmissionTrack:
    run_data: GeneratedTrack
    upload_points: list[TrackPoint]
    final_plan: TargetPlan
    distance_guard_attempts: int
    quality_guard_attempts: int
    track_image: str | None


class RunWorkflow:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client = CampusRunClient(settings)
        self.track_generator = TrackGenerator(
            road_map_path=settings.route.road_map_path,
        )

    @staticmethod
    def _format_seconds(seconds: float) -> str:
        total = max(0, round(seconds))
        hours, rem = divmod(total, 3600)
        minutes, secs = divmod(rem, 60)
        if hours > 0:
            return f"{hours}:{minutes:02d}:{secs:02d}"
        return f"{minutes:02d}:{secs:02d}"

    @staticmethod
    def _classify_run_type() -> RunType | None:
        return classify_run_type(datetime.now())

    @staticmethod
    def _check_target_counts(
        run_counts: RunCounts,
        run_type: RunType,
    ) -> str | None:
        if run_counts.target_effective <= 0:
            return None

        if (
            run_counts.morning >= run_counts.target_effective
            and run_counts.normal >= run_counts.target_effective
        ):
            return (
                f"Both morning ({run_counts.morning}) and normal "
                f"({run_counts.normal}) targets already met "
                f"(target={run_counts.target_effective})"
            )

        if (
            run_type is RunType.MORNING
            and run_counts.morning >= run_counts.target_effective
        ):
            return (
                f"Morning run target already met: "
                f"{run_counts.morning}/{run_counts.target_effective}"
            )
        if (
            run_type is RunType.NORMAL
            and run_counts.normal >= run_counts.target_effective
        ):
            return (
                f"Normal run target already met: "
                f"{run_counts.normal}/{run_counts.target_effective}"
            )

        return None

    def _make_skip_report(
        self,
        skip_reason: str,
        run_counts: RunCounts | None = None,
        run_type: RunType | None = None,
        server_responses: dict[str, Any] | None = None,
    ) -> RunReport:
        from .models import ServerResponses

        report = RunReport(
            success=True,
            mode="skipped",
            record_id=None,
            summary=_make_skip_summary(run_type, run_counts, skip_reason),
            server=ServerResponses(**(server_responses or {})),
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
        pass_points: list[PassPoint],
        converter: Callable[[float, float], tuple[float, float]],
    ) -> list[PassPoint]:
        return [PassPoint(*converter(p.lat, p.lng)) for p in pass_points]

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
        must_pass_points: list[PassPoint],
        radius_km: float,
    ) -> int:
        hits = 0
        for pp in must_pass_points:
            if any(
                haversine_km(point.latitude, point.longitude, pp.lat, pp.lng)
                <= radius_km
                for point in points
            ):
                hits += 1
        return hits

    @staticmethod
    def _trailing_endpoint_cluster_ratio(points: list[TrackPoint]) -> float:
        if len(points) < 2:
            return 0.0

        endpoint = points[-1]
        clustered = 0
        for point in reversed(points):
            if (
                haversine_km(
                    point.latitude,
                    point.longitude,
                    endpoint.latitude,
                    endpoint.longitude,
                )
                > 0.08
            ):
                break
            clustered += 1
        return clustered / len(points)

    def _track_quality_issue(
        self,
        run_data: GeneratedTrack,
        expected_pass_count: int,
    ) -> str | None:
        if run_data.must_pass_count < expected_pass_count:
            return (
                "pass-point guard failed: "
                f"{run_data.must_pass_count}/{expected_pass_count} points hit"
            )

        raw_loss_km = run_data.raw_distance_km - run_data.confirmed_distance_km
        if (
            run_data.raw_distance_km > 0
            and raw_loss_km > 0.25
            and run_data.confirmed_distance_km / run_data.raw_distance_km < 0.72
        ):
            return (
                "filter guard failed: confirmed distance lost "
                f"{raw_loss_km:.2f}km from raw path"
            )

        endpoint_cluster_ratio = self._trailing_endpoint_cluster_ratio(run_data.points)
        if (
            endpoint_cluster_ratio > 0.16
            and len(run_data.points) * endpoint_cluster_ratio > 60
        ):
            return (
                "route-shape guard failed: "
                f"{endpoint_cluster_ratio:.0%} of points cluster near endpoint"
            )

        return None

    def _extract_server_limits(
        self, login_data: dict[str, Any]
    ) -> tuple[int, RunLimits]:
        student_id = int(login_data.get("id", 0))
        if not student_id:
            raise ApiError("Login response missing student id")

        shortest_distance_m = float(login_data.get("shortest_distance", 2000) or 2000)
        min_pace_sec = float(login_data.get("min_pace", 180) or 180)
        max_pace_sec = float(login_data.get("max_pace", 1000) or 1000)

        return student_id, RunLimits(
            required_distance_km=max(2.0, shortest_distance_m / 1000.0),
            min_pace_min_per_km=min_pace_sec / 60.0,
            max_pace_min_per_km=max_pace_sec / 60.0,
        )

    def _fetch_run_counts(
        self,
        student_id: int,
        progress: ProgressCallback | None,
    ) -> tuple[RunCounts, ApiResponse]:
        run_counts_resp = self.client.fetch_run_counts(student_id)
        run_counts = build_counts_payload(student_id, run_counts_resp.data)
        self._emit(
            progress,
            f"  Run counts: morning={run_counts.morning}, "
            f"normal={run_counts.normal}, "
            f"effective={run_counts.effective}, "
            f"target_effective={run_counts.target_effective}",
        )
        return run_counts, run_counts_resp

    def _plan_target(
        self,
        limits: RunLimits,
    ) -> TargetPlan:
        run_config = self.settings.run
        guard_config = self.settings.guard
        hard_distance_floor_km = max(2.0, limits.required_distance_km)
        distance_guard_min_km = max(
            hard_distance_floor_km,
            limits.required_distance_km
            * max(0.0, guard_config.distance_tolerance_ratio),
        )
        distance_guard_min_km += 0.015

        base_distance_km = max(
            run_config.target_distance_km, limits.required_distance_km
        )
        distance_ratio = random.uniform(1.0, 1.0 + run_config.distance_jitter_ratio)
        target_distance_km = max(
            distance_guard_min_km + 0.02,
            limits.required_distance_km,
            base_distance_km * distance_ratio,
        )

        base_pace = min(
            max(run_config.target_pace_min_per_km, limits.min_pace_min_per_km),
            limits.max_pace_min_per_km,
        )
        pace_ratio = random.uniform(
            max(0.0, 1.0 - run_config.pace_jitter_ratio),
            1.0 + run_config.pace_jitter_ratio,
        )
        target_pace_min_per_km = min(
            max(base_pace * pace_ratio, limits.min_pace_min_per_km),
            limits.max_pace_min_per_km,
        )

        expected_duration_sec = int(target_distance_km * target_pace_min_per_km * 60)
        delta_sec = max(30, int(expected_duration_sec * 0.15))
        target_duration_sec = random.randint(
            max(60, expected_duration_sec - delta_sec),
            max(60, expected_duration_sec + delta_sec),
        )

        return TargetPlan(
            target_distance_km=target_distance_km,
            target_pace_min_per_km=target_pace_min_per_km,
            target_duration_sec=target_duration_sec,
            distance_guard_min_km=distance_guard_min_km,
        )

    def _prepare_track_context(
        self,
        pass_points: list[dict[str, Any]],
        progress: ProgressCallback | None,
    ) -> TrackContext:
        run_config = self.settings.run
        start = gcj02_to_wgs84(run_config.start_lat, run_config.start_lng)
        gcj_pass_points = dicts_to_pass_points(pass_points)
        wgs_pass_points = self._convert_pass_points(gcj_pass_points, gcj02_to_wgs84)
        self._emit(
            progress,
            "Coordinate bridge: gcj02 -> wgs84 for internal processing",
        )

        return TrackContext(
            start=start,
            pass_points=wgs_pass_points,
        )

    def _build_generation_request(
        self,
        context: TrackContext,
        plan: TargetPlan,
    ) -> TrackRequest:
        run_config = self.settings.run
        route_config = self.settings.route
        points_config = self.settings.run
        guard_config = self.settings.guard
        return TrackRequest(
            start=context.start,
            must_pass_points=context.pass_points,
            target_distance_km=plan.target_distance_km,
            target_pace_min_per_km=plan.target_pace_min_per_km,
            sample_interval_sec=run_config.sample_interval_sec,
            must_pass_radius_km=run_config.must_pass_radius_km,
            jitter_m=points_config.point_jitter_m,
            timestamp_jitter_ms=points_config.timestamp_jitter_ms,
            accuracy_min=points_config.point_accuracy_min,
            accuracy_max=points_config.point_accuracy_max,
            road_routing_enabled=route_config.road_routing_enabled,
            road_map_path=route_config.road_map_path,
            road_snap_max_m=route_config.road_snap_max_m,
            filter_policy=TrackFilterPolicy(
                max_speed_threshold_m_s=guard_config.max_speed_threshold_m_s,
                max_jump_distance_km=guard_config.max_jump_distance_km,
                min_move_distance_m=guard_config.min_move_distance_m,
                min_move_speed_m_s=guard_config.min_move_speed_m_s,
                gps_accuracy_threshold_m=guard_config.gps_accuracy_threshold_m,
                primary_angle_threshold_deg=guard_config.primary_angle_threshold_deg,
                secondary_angle_threshold_deg=guard_config.secondary_angle_threshold_deg,
            ),
        )

    def _generate_with_guards(
        self,
        context: TrackContext,
        limits: RunLimits,
        plan: TargetPlan,
        progress: ProgressCallback | None,
    ) -> tuple[GeneratedTrack, int, int, TargetPlan]:
        current_plan = plan
        run_data = self.track_generator.generate(
            self._build_generation_request(context, current_plan)
        )

        for _ in range(3):
            actual_pace = run_data.duration_sec / 60.0 / max(run_data.distance_km, 1e-6)
            deviation = abs(actual_pace - current_plan.target_pace_min_per_km) / max(
                current_plan.target_pace_min_per_km, 1e-6
            )
            if deviation < 0.2:
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
                f"Adjust pace: "
                f"{current_plan.target_pace_min_per_km:.2f} -> {corrected_pace:.2f} min/km",
            )
            current_plan = TargetPlan(
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
            confirmed_ratio = run_data.confirmed_distance_km / max(
                run_data.raw_distance_km, 1e-6
            )
            correction_km = deficit_km / max(0.55, min(1.05, confirmed_ratio))
            next_target_distance = current_plan.target_distance_km + max(
                0.06,
                correction_km + 0.025,
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

            current_plan = TargetPlan(
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

        quality_guard_attempts = 0
        expected_pass_count = len(context.pass_points)
        quality_issue = self._track_quality_issue(run_data, expected_pass_count)
        while quality_issue is not None and quality_guard_attempts < 3:
            quality_guard_attempts += 1
            next_target_distance = current_plan.target_distance_km + 0.04
            self._emit(
                progress,
                (
                    f"Quality guard {quality_guard_attempts}/3: "
                    f"{quality_issue}; rebuild target {next_target_distance:.2f}km"
                ),
            )
            current_plan = TargetPlan(
                target_distance_km=next_target_distance,
                target_pace_min_per_km=current_plan.target_pace_min_per_km,
                target_duration_sec=current_plan.target_duration_sec,
                distance_guard_min_km=current_plan.distance_guard_min_km,
            )
            run_data = self.track_generator.generate(
                self._build_generation_request(context, current_plan)
            )
            if run_data.distance_km < distance_floor_km:
                quality_issue = (
                    "distance guard failed after quality rebuild: "
                    f"{run_data.distance_km:.2f}km < {distance_floor_km:.2f}km"
                )
            else:
                quality_issue = self._track_quality_issue(run_data, expected_pass_count)

        if quality_issue is not None:
            raise ApiError(
                "Track generation failed quality guard: "
                f"{quality_issue} after {quality_guard_attempts} rebuilds"
            )

        return run_data, distance_guard_attempts, quality_guard_attempts, current_plan

    def _finalize_for_submission(
        self,
        context: TrackContext,
        limits: RunLimits,
        plan: TargetPlan,
        track_image_path: str | None,
        progress: ProgressCallback | None,
    ) -> SubmissionTrack:
        run_data, distance_guard_attempts, quality_guard_attempts, final_plan = (
            self._generate_with_guards(
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

        upload_points = self._convert_track_points(run_data.points, wgs84_to_gcj02)
        self._emit(progress, "Coordinate bridge: wgs84 -> gcj02 for API upload")

        track_image = self._render_track_image(
            track_image_path=track_image_path,
            run_data=run_data,
            context=context,
            progress=progress,
        )

        return SubmissionTrack(
            run_data=run_data,
            upload_points=upload_points,
            final_plan=final_plan,
            distance_guard_attempts=distance_guard_attempts,
            quality_guard_attempts=quality_guard_attempts,
            track_image=track_image,
        )

    def _render_track_image(
        self,
        track_image_path: str | None,
        run_data: GeneratedTrack,
        context: TrackContext,
        progress: ProgressCallback | None,
    ) -> str | None:
        if not track_image_path:
            return None

        try:
            track_image = render_track_overlay_png(
                map_path=self.settings.route.road_map_path,
                points=run_data.points,
                output_path=track_image_path,
                must_pass_points=context.pass_points,
            )
            self._emit(progress, f"Track image saved: {track_image}")
            return track_image
        except Exception as exc:  # pylint: disable=broad-except
            self._emit(progress, f"Track image skipped: {exc}")
        return None

    def _create_run_record(
        self,
        student_id: int,
        pass_points: list[dict[str, Any]],
        progress: ProgressCallback | None,
    ) -> tuple[ApiResponse, int]:
        line = self.client.create_run_record(
            student_id=student_id,
            pass_points=pass_points,
        )
        record_id = int((line.data or {}).get("record_id", 0))
        if not record_id:
            raise ApiError("createLine missing record_id")

        self._emit(
            progress,
            "IMPORTANT: Please do NOT open the mini app while this run is in progress.",
        )
        return line, record_id

    def _build_summary_payload(
        self,
        record_id: int,
        track: SubmissionTrack,
        pass_points: list[PassPoint],
    ) -> dict[str, float | int | str]:
        summary_payload = build_run_summary_payload(
            record_id=record_id,
            run_result=track.run_data,
        )
        summary_payload["pass_point"] = self._count_pass_hits(
            points=track.upload_points,
            must_pass_points=pass_points,
            radius_km=self.settings.run.must_pass_radius_km,
        )
        return summary_payload

    def _build_full_report(
        self,
        record_id: int,
        run_type: RunType,
        run_counts: RunCounts,
        track: SubmissionTrack,
        run_options: RunExecutionOptions,
        target_skip_reason: str | None,
        summary_payload: dict[str, Any],
        uploaded_batches: int,
        responses: _RunResponses,
    ) -> RunReport:
        return build_full_report(
            record_id=record_id,
            run_type=run_type,
            run_counts=run_counts,
            run_data=track.run_data,
            final_plan=track.final_plan,
            run_options=run_options,
            target_skip_reason=target_skip_reason,
            summary_payload=summary_payload,
            uploaded_batches=uploaded_batches,
            upload_point_count=len(track.upload_points),
            track_image=track.track_image,
            distance_guard_attempts=track.distance_guard_attempts,
            quality_guard_attempts=track.quality_guard_attempts,
            responses=responses,
        )

    def _wait_before_submit(
        self,
        run_duration_sec: int,
        skip_wait: bool,
        disable_progress: bool,
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
        if progress is None or disable_progress:
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

        run_counts, run_counts_resp = self._fetch_run_counts(student_id, progress)

        target_skip_reason = self._check_target_counts(run_counts, run_type)
        if target_skip_reason is not None and not run_options.ignore_target_met:
            self._emit(progress, f"  Skipping: {target_skip_reason}")
            return self._make_skip_report(
                skip_reason=target_skip_reason,
                run_counts=run_counts,
                run_type=run_type,
                server_responses={"run_counts": run_counts_resp.raw},
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

        plan = self._plan_target(limits)
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
            self.settings.run.start_lat, self.settings.run.start_lng
        )
        pass_points = rand_info.data if isinstance(rand_info.data, list) else []
        if not pass_points:
            raise ApiError("randrunInfo returned empty pass points")

        context = self._prepare_track_context(pass_points, progress)

        self._emit(progress, "Step 5/9: generate track")
        track = self._finalize_for_submission(
            context=context,
            limits=limits,
            plan=plan,
            track_image_path=run_options.track_image_path,
            progress=progress,
        )

        self._emit(progress, "Step 6/9: create running record")
        line, record_id = self._create_run_record(
            student_id=student_id,
            pass_points=pass_points,
            progress=progress,
        )

        self._wait_before_submit(
            run_duration_sec=track.run_data.duration_sec,
            skip_wait=run_options.skip_submit_wait,
            disable_progress=run_options.disable_progress,
            progress=progress,
        )

        gcj_pass_points = dicts_to_pass_points(pass_points)
        summary_payload = self._build_summary_payload(
            record_id=record_id,
            track=track,
            pass_points=gcj_pass_points,
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
        uploaded_batches = self._upload_batches(
            record_id,
            track.upload_points,
            run_options.disable_progress,
            progress,
        )

        self._emit(progress, "Fetch result: recordInfo")
        record = self.client.fetch_record_info(record_id)
        self._emit(progress, "Fetch result: GetPathPoints")
        path_info = self.client.fetch_path_points(record_id)

        report = self._build_full_report(
            record_id=record_id,
            run_type=run_type,
            run_counts=run_counts,
            track=track,
            run_options=run_options,
            target_skip_reason=target_skip_reason,
            summary_payload=summary_payload,
            uploaded_batches=uploaded_batches,
            responses=_RunResponses(
                login=login,
                rand_info=rand_info,
                line=line,
                check=check,
                update=update,
                record=record,
                path_info=path_info,
            ),
        )

        self._write_report(report)
        self._emit(progress, "Run completed")
        return report

    def _upload_batches(
        self,
        record_id: int,
        points: list[TrackPoint],
        disable_progress: bool,
        progress: ProgressCallback | None = None,
    ) -> int:
        if not points:
            return 0

        total_batches = (len(points) + UPLOAD_BATCH_SIZE - 1) // UPLOAD_BATCH_SIZE
        uploaded = 0

        if progress is None or disable_progress:
            for segment in batched(points, UPLOAD_BATCH_SIZE, strict=False):
                self._upload_batch(record_id, segment)
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
            for segment in batched(points, UPLOAD_BATCH_SIZE, strict=False):
                self._upload_batch(record_id, segment)
                uploaded += 1
                progress_bar.update(task_id, advance=1)

        return uploaded

    def _upload_batch(
        self,
        record_id: int,
        points: Sequence[TrackPoint],
    ) -> None:
        payload = build_path_upload_payload(record_id, points)
        encrypted = aes_encrypt(
            json.dumps(payload, ensure_ascii=False),
            self.settings.run_key,
        )
        self.client.upload_path_batch(encrypted)

    def _write_report(self, report: RunReport) -> None:
        write_report_file(report, self.settings.output.report_path)
