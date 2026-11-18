"""Local eye measurement, timed events, module contracts, and study records."""

from .contracts import (
    CoachingResult,
    DEFAULT_CONFIG_VERSION,
    Event,
    FrameMeasurement,
    SCHEMA_VERSION,
    SessionSummary,
    TimeWindow,
    VALID_COACHING_STATUSES,
    VALID_EVENT_TYPES,
    merge_intervals,
    merged_interval_seconds,
)
from .eyes import EyeMeasurement, LEFT_EYE, RIGHT_EYE, eye_ratio, measure_eyes, measure_frame
from .session import EyeDetector, HeadPoseDetector, Settings, StudySession

__all__ = [
    "CoachingResult",
    "DEFAULT_CONFIG_VERSION",
    "EyeDetector",
    "EyeMeasurement",
    "Event",
    "FrameMeasurement",
    "HeadPoseDetector",
    "LEFT_EYE",
    "RIGHT_EYE",
    "SCHEMA_VERSION",
    "SessionSummary",
    "Settings",
    "StudySession",
    "TimeWindow",
    "VALID_COACHING_STATUSES",
    "VALID_EVENT_TYPES",
    "eye_ratio",
    "measure_eyes",
    "measure_frame",
    "merge_intervals",
    "merged_interval_seconds",
]
