"""Track domain facade.

This module exposes the public track-generation API while delegating
implementation to focused modules.
"""

from .models import (
    TrackBuildResult,
    TrackFilterPolicy,
    TrackGenerationRequest,
    TrackPoint,
)
from .payloads import build_path_upload_payload, build_run_summary_payload
from .track_generator import TrackGenerator

__all__ = [
    "TrackPoint",
    "TrackBuildResult",
    "TrackFilterPolicy",
    "TrackGenerationRequest",
    "TrackGenerator",
    "build_run_summary_payload",
    "build_path_upload_payload",
]
