"""Shared module contracts defined in docs/interfaces.md (schema v0.1)."""

from dataclasses import asdict, dataclass, field
from math import isfinite

SCHEMA_VERSION = "0.1"
DEFAULT_CONFIG_VERSION = "eyes-v0.1"
VALID_EVENT_TYPES = ("eye_closure_suspected", "head_down", "unobservable", "break")
VALID_COACHING_STATUSES = ("pending", "running", "succeeded", "failed")


def _is_valid_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(value)


def _is_valid_ear(value):
    return _is_valid_number(value) and 0.0 <= float(value) <= 0.8


def _is_valid_pitch(value, max_abs_deg=90.0):
    return _is_valid_number(value) and abs(float(value)) <= max_abs_deg


def merge_intervals(intervals):
    """Merge overlapping or adjacent [start_s, end_s] intervals."""
    cleaned = []
    for start_s, end_s in intervals:
        if not (_is_valid_number(start_s) and _is_valid_number(end_s)):
            continue
        start_val = float(start_s)
        end_val = float(end_s)
        if end_val <= start_val:
            continue
        cleaned.append((start_val, end_val))
    if not cleaned:
        return []
    cleaned.sort(key=lambda item: (item[0], item[1]))
    merged = [cleaned[0]]
    for current_start, current_end in cleaned[1:]:
        prev_start, prev_end = merged[-1]
        if current_start <= prev_end + 1e-9:
            merged[-1] = (prev_start, max(prev_end, current_end))
        else:
            merged.append((current_start, current_end))
    return merged


def merged_interval_seconds(intervals):
    """Compute the union duration in seconds without double-counting overlaps."""
    return sum(end_s - start_s for start_s, end_s in merge_intervals(intervals))


@dataclass(frozen=True)
class FrameMeasurement:
    """Per-frame measurement contract passed from vision (T04) to detection (T05)."""

    session_id: str
    timestamp_s: float
    eye_valid: bool = False
    pose_valid: bool = False
    ear_left: float | None = None
    ear_right: float | None = None
    pitch_delta_deg: float | None = None
    invalid_reason: str | None = None

    def __post_init__(self):
        if not isinstance(self.session_id, str) or not self.session_id.strip():
            raise ValueError("session_id must be a non-empty string")
        if not _is_valid_number(self.timestamp_s) or float(self.timestamp_s) < 0:
            raise ValueError("timestamp_s must be a finite non-negative number")
        object.__setattr__(self, "timestamp_s", float(self.timestamp_s))

        left_ok = _is_valid_ear(self.ear_left)
        right_ok = _is_valid_ear(self.ear_right)
        object.__setattr__(self, "ear_left", float(self.ear_left) if left_ok else None)
        object.__setattr__(self, "ear_right", float(self.ear_right) if right_ok else None)

        effective_eye_valid = bool(self.eye_valid) and left_ok and right_ok
        object.__setattr__(self, "eye_valid", effective_eye_valid)
        if not effective_eye_valid:
            object.__setattr__(self, "ear_left", None)
            object.__setattr__(self, "ear_right", None)

        pitch_ok = _is_valid_pitch(self.pitch_delta_deg)
        effective_pose_valid = bool(self.pose_valid) and pitch_ok
        object.__setattr__(self, "pose_valid", effective_pose_valid)
        object.__setattr__(
            self,
            "pitch_delta_deg",
            float(self.pitch_delta_deg) if effective_pose_valid else None,
        )

        if not effective_eye_valid and not effective_pose_valid and self.invalid_reason is None:
            object.__setattr__(self, "invalid_reason", "unobservable")

    @property
    def ear_average(self) -> float | None:
        if not self.eye_valid or self.ear_left is None or self.ear_right is None:
            return None
        return (self.ear_left + self.ear_right) / 2.0

    @property
    def average(self) -> float | None:
        return self.ear_average

    @property
    def effective_pitch_delta_deg(self) -> float | None:
        if not self.pose_valid or self.pitch_delta_deg is None:
            return None
        return self.pitch_delta_deg

    @classmethod
    def from_measurements(
        cls,
        session_id: str,
        timestamp_s: float,
        eye=None,
        *,
        ear_left: float | None = None,
        ear_right: float | None = None,
        eye_valid: bool | None = None,
        pitch_delta_deg: float | None = None,
        pose_valid: bool | None = None,
        invalid_reason: str | None = None,
    ) -> "FrameMeasurement":
        """Construct a FrameMeasurement from an EyeMeasurement and optional head pose value."""
        if eye is not None:
            ear_left = getattr(eye, "left", ear_left)
            ear_right = getattr(eye, "right", ear_right)
            if invalid_reason is None:
                invalid_reason = getattr(eye, "reason", None)
        inferred_eye_valid = (
            (_is_valid_ear(ear_left) and _is_valid_ear(ear_right))
            if eye_valid is None
            else bool(eye_valid)
        )
        inferred_pose_valid = (
            _is_valid_pitch(pitch_delta_deg)
            if pose_valid is None
            else bool(pose_valid)
        )
        return cls(
            session_id=session_id,
            timestamp_s=timestamp_s,
            eye_valid=inferred_eye_valid,
            pose_valid=inferred_pose_valid,
            ear_left=ear_left,
            ear_right=ear_right,
            pitch_delta_deg=pitch_delta_deg,
            invalid_reason=invalid_reason,
        )

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "FrameMeasurement":
        return cls(
            session_id=data["session_id"],
            timestamp_s=data["timestamp_s"],
            eye_valid=data.get("eye_valid", False),
            pose_valid=data.get("pose_valid", False),
            ear_left=data.get("ear_left"),
            ear_right=data.get("ear_right"),
            pitch_delta_deg=data.get("pitch_delta_deg"),
            invalid_reason=data.get("invalid_reason"),
        )


@dataclass
class Event:
    """Timed event contract passed from detection (T05) to storage (T06)."""

    event_id: str
    session_id: str
    type: str
    condition_started_s: float
    confirmed_s: float
    ended_s: float | None = None
    interrupted_reason: str | None = None
    config_version: str = DEFAULT_CONFIG_VERSION

    def __post_init__(self):
        if not isinstance(self.event_id, str) or not self.event_id.strip():
            raise ValueError("event_id must be a non-empty string")
        if not isinstance(self.session_id, str) or not self.session_id.strip():
            raise ValueError("session_id must be a non-empty string")
        if self.type not in VALID_EVENT_TYPES:
            raise ValueError(f"Invalid event type: {self.type}")
        if not (_is_valid_number(self.condition_started_s) and _is_valid_number(self.confirmed_s)):
            raise ValueError("Event timestamps must be finite numbers")
        if self.condition_started_s < 0 or self.confirmed_s + 1e-9 < self.condition_started_s:
            raise ValueError("Event confirmed_s must be non-negative and >= condition_started_s")
        self.condition_started_s = float(self.condition_started_s)
        self.confirmed_s = float(self.confirmed_s)
        if self.ended_s is not None:
            if not _is_valid_number(self.ended_s) or float(self.ended_s) + 1e-9 < self.condition_started_s:
                raise ValueError("Event ended_s must be finite and >= condition_started_s")
            self.ended_s = float(self.ended_s)
        if not isinstance(self.config_version, str) or not self.config_version.strip():
            raise ValueError("config_version must be a non-empty string")

    def duration_s(self, current_s: float | None = None) -> float:
        end = self.ended_s if self.ended_s is not None else current_s
        if end is None or not _is_valid_number(end):
            return 0.0
        return max(0.0, float(end) - self.condition_started_s)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Event":
        return cls(
            event_id=data["event_id"],
            session_id=data["session_id"],
            type=data["type"],
            condition_started_s=data["condition_started_s"],
            confirmed_s=data["confirmed_s"],
            ended_s=data.get("ended_s"),
            interrupted_reason=data.get("interrupted_reason"),
            config_version=data.get("config_version") or DEFAULT_CONFIG_VERSION,
        )


@dataclass(frozen=True)
class TimeWindow:
    """Time-window slice within SessionSummary."""

    start_s: float
    end_s: float
    observable_seconds: float
    eye_closure_count: int

    def __post_init__(self):
        if not (_is_valid_number(self.start_s) and _is_valid_number(self.end_s)):
            raise ValueError("TimeWindow boundaries must be finite numbers")
        if float(self.start_s) < 0 or float(self.end_s) < float(self.start_s):
            raise ValueError("TimeWindow must satisfy 0 <= start_s <= end_s")
        span = float(self.end_s) - float(self.start_s)
        if not _is_valid_number(self.observable_seconds) or float(self.observable_seconds) < 0:
            raise ValueError("observable_seconds must be a finite non-negative number")
        if float(self.observable_seconds) > span + 1e-6:
            raise ValueError("observable_seconds cannot exceed window duration")
        if isinstance(self.eye_closure_count, bool) or not isinstance(self.eye_closure_count, int) or self.eye_closure_count < 0:
            raise ValueError("eye_closure_count must be a non-negative integer")
        object.__setattr__(self, "start_s", float(self.start_s))
        object.__setattr__(self, "end_s", float(self.end_s))
        object.__setattr__(self, "observable_seconds", float(self.observable_seconds))

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "TimeWindow":
        return cls(
            start_s=data["start_s"],
            end_s=data["end_s"],
            observable_seconds=data["observable_seconds"],
            eye_closure_count=int(data["eye_closure_count"]),
        )


@dataclass(frozen=True)
class SessionSummary:
    """Session summary contract passed from storage/aggregation (T06) to coaching (T07/T08)."""

    session_id: str
    goal: str
    study_seconds: float
    break_seconds: float
    observable_seconds: float
    unobservable_seconds: float
    eye_closure_count: int
    eye_closure_seconds: float
    head_down_count: int = 0
    windows: list[dict] = field(default_factory=list)
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self):
        if not isinstance(self.session_id, str) or not self.session_id.strip():
            raise ValueError("session_id must be a non-empty string")
        if not isinstance(self.goal, str):
            raise ValueError("goal must be a string")
        for field_name in (
            "study_seconds",
            "break_seconds",
            "observable_seconds",
            "unobservable_seconds",
            "eye_closure_seconds",
        ):
            val = getattr(self, field_name)
            if not _is_valid_number(val) or float(val) < 0:
                raise ValueError(f"{field_name} must be a finite non-negative number")
            object.__setattr__(self, field_name, float(val))

        for count_name in ("eye_closure_count", "head_down_count"):
            val = getattr(self, count_name)
            if isinstance(val, bool) or not isinstance(val, int) or val < 0:
                raise ValueError(f"{count_name} must be a non-negative integer")

        if self.observable_seconds + self.unobservable_seconds > self.study_seconds + 1e-5:
            raise ValueError("observable_seconds + unobservable_seconds cannot exceed study_seconds")
        if self.eye_closure_seconds > self.observable_seconds + 1e-5:
            raise ValueError("eye_closure_seconds cannot exceed observable_seconds")
        if self.eye_closure_count == 0 and self.eye_closure_seconds > 1e-9:
            raise ValueError("eye_closure_seconds must be 0 when eye_closure_count is 0")
        if self.eye_closure_count > 0 and self.eye_closure_seconds <= 0:
            raise ValueError("eye_closure_seconds must be positive when eye_closure_count > 0")

        normalized_windows = []
        last_end = None
        for item in self.windows or []:
            window = item if isinstance(item, TimeWindow) else TimeWindow.from_dict(item)
            if last_end is not None and window.start_s < last_end - 1e-6:
                raise ValueError("Time windows must not overlap or decrease")
            last_end = window.end_s
            normalized_windows.append(window.to_dict())
        object.__setattr__(self, "windows", normalized_windows)

        if not isinstance(self.schema_version, str) or not self.schema_version.strip():
            raise ValueError("schema_version must be a non-empty string")

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "SessionSummary":
        return cls(
            session_id=data["session_id"],
            goal=data.get("goal", ""),
            study_seconds=data["study_seconds"],
            break_seconds=data["break_seconds"],
            observable_seconds=data["observable_seconds"],
            unobservable_seconds=data["unobservable_seconds"],
            eye_closure_count=int(data["eye_closure_count"]),
            eye_closure_seconds=data["eye_closure_seconds"],
            head_down_count=int(data.get("head_down_count", 0)),
            windows=data.get("windows", []),
            schema_version=data.get("schema_version", SCHEMA_VERSION),
        )


@dataclass(frozen=True)
class CoachingResult:
    """Coaching output contract passed from LLM module (T08) to UI/app (T09/T10)."""

    session_id: str
    model_id: str
    model_revision: str
    dataset_version: str
    status: str = "pending"
    raw_text: str = ""
    display_text: str = ""
    validation_errors: list[str] = field(default_factory=list)
    elapsed_seconds: float | None = None
    is_fallback: bool = False

    def __post_init__(self):
        if not isinstance(self.session_id, str) or not self.session_id.strip():
            raise ValueError("session_id must be a non-empty string")
        for field_name in ("model_id", "model_revision", "dataset_version"):
            val = getattr(self, field_name)
            if not isinstance(val, str) or not val.strip():
                raise ValueError(f"{field_name} must be a non-empty string")
        if self.status not in VALID_COACHING_STATUSES:
            raise ValueError(f"Invalid coaching status: {self.status}")
        if not isinstance(self.raw_text, str) or not isinstance(self.display_text, str):
            raise ValueError("raw_text and display_text must be strings")
        if self.elapsed_seconds is not None:
            if not _is_valid_number(self.elapsed_seconds) or float(self.elapsed_seconds) < 0:
                raise ValueError("elapsed_seconds must be a finite non-negative number or None")
            object.__setattr__(self, "elapsed_seconds", float(self.elapsed_seconds))
        if not isinstance(self.is_fallback, bool):
            raise ValueError("is_fallback must be a boolean")
        errors = [str(err) for err in (self.validation_errors or [])]
        object.__setattr__(self, "validation_errors", errors)

    def matches_session(self, expected_session_id: str) -> bool:
        return self.session_id == expected_session_id

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "CoachingResult":
        return cls(
            session_id=data["session_id"],
            model_id=data["model_id"],
            model_revision=data["model_revision"],
            dataset_version=data["dataset_version"],
            status=data.get("status", "pending"),
            raw_text=data.get("raw_text", ""),
            display_text=data.get("display_text", ""),
            validation_errors=list(data.get("validation_errors", [])),
            elapsed_seconds=data.get("elapsed_seconds"),
            is_fallback=bool(data.get("is_fallback", False)),
        )
