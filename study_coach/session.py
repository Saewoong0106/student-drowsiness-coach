"""Deterministic timing: callers supply seconds, never frame counts."""

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from math import isfinite
from statistics import median
from uuid import uuid4

from .contracts import (
    CoachingResult,
    DEFAULT_CONFIG_VERSION,
    Event,
    FrameMeasurement,
    SCHEMA_VERSION,
    SessionSummary,
    TimeWindow,
    merged_interval_seconds,
)


def _is_finite_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(value)


@dataclass(frozen=True)
class Settings:
    calibration_seconds: float = 10.0
    calibration_samples: int = 30
    calibration_coverage: float = 0.8
    minimum_baseline: float = 0.12
    close_ratio: float = 0.65
    open_ratio: float = 0.75
    closure_seconds: float = 2.0
    recovery_seconds: float = 1.0
    maximum_gap: float = 0.5
    head_down_pitch_deg: float = 20.0
    head_up_pitch_deg: float = 15.0
    head_down_seconds: float = 3.0
    head_recovery_seconds: float = 1.0
    maximum_abs_pitch_deg: float = 90.0
    window_seconds: float = 2400.0
    version: str = DEFAULT_CONFIG_VERSION

    def __post_init__(self):
        if not (0 < self.close_ratio < self.open_ratio <= 1):
            raise ValueError("Invalid eye thresholds")
        if not (0 < self.head_up_pitch_deg < self.head_down_pitch_deg <= self.maximum_abs_pitch_deg):
            raise ValueError("Invalid head pitch thresholds")
        if any(not _is_finite_number(x) or x <= 0 for x in (
            self.calibration_seconds,
            self.closure_seconds,
            self.recovery_seconds,
            self.maximum_gap,
            self.minimum_baseline,
            self.head_down_seconds,
            self.head_recovery_seconds,
            self.maximum_abs_pitch_deg,
            self.window_seconds,
        )) or self.calibration_samples < 2 or not (0 < self.calibration_coverage <= 1):
            raise ValueError("Invalid timing or calibration settings")


class EyeDetector:
    def __init__(self, settings, session_id):
        self.settings = settings
        self.session_id = session_id
        self.events = []
        self.active = None
        self.candidate = None
        self.recovery = None
        self.last_valid = None
        self.last_time = None
        self.state = "unobservable"

    def interrupt(self, reason):
        if self.active:
            self.active.ended_s = max(self.active.condition_started_s, self.last_valid)
            self.active.interrupted_reason = reason
        self.active = self.candidate = self.recovery = None
        self.last_valid = None
        self.last_time = None
        self.state = "unobservable"

    def update(self, t, ear, baseline):
        if not _is_finite_number(t) or t < 0 or (self.last_time is not None and t < self.last_time):
            raise ValueError("Timestamps must not decrease")
        t = float(t)
        if self.last_time is not None and t - self.last_time > self.settings.maximum_gap + 1e-9:
            self.interrupt("input_gap")
        self.last_time = t
        if ear is None or not _is_finite_number(ear) or not (0 <= float(ear) <= 0.8):
            self.interrupt("unobservable")
            self.last_time = t
            return False
        ear = float(ear)
        self.last_valid = t
        low = baseline * self.settings.close_ratio
        high = baseline * self.settings.open_ratio
        if self.active:
            if ear >= high:
                if self.recovery is None:
                    self.recovery = t
                if t - self.recovery + 1e-9 >= self.settings.recovery_seconds:
                    self.active.ended_s = self.recovery
                    self.active = self.candidate = self.recovery = None
                    self.state = "normal"
                else:
                    self.state = "recovering"
            else:
                self.recovery = None
                self.state = "eyes_closed"
            return False
        if ear < low:
            if self.candidate is None:
                self.candidate = t
            self.state = "closing"
            if t - self.candidate + 1e-9 >= self.settings.closure_seconds:
                self.active = Event(
                    str(uuid4()),
                    self.session_id,
                    "eye_closure_suspected",
                    self.candidate,
                    t,
                    config_version=self.settings.version,
                )
                self.events.append(self.active)
                self.state = "eyes_closed"
                return True
        else:
            self.candidate = None
            self.state = "normal"
        return False


class HeadPoseDetector:
    """Time-based head-down detector (T05): >= 20 deg for 3s enters, < 15 deg for 1s recovers."""

    def __init__(self, settings, session_id):
        self.settings = settings
        self.session_id = session_id
        self.events = []
        self.active = None
        self.candidate = None
        self.recovery = None
        self.last_valid = None
        self.last_time = None
        self.state = "unobservable"

    def interrupt(self, reason):
        if self.active:
            self.active.ended_s = max(self.active.condition_started_s, self.last_valid)
            self.active.interrupted_reason = reason
        self.active = self.candidate = self.recovery = None
        self.last_valid = None
        self.last_time = None
        self.state = "unobservable"

    def update(self, t, pitch_delta_deg, *, pose_valid=True):
        if not _is_finite_number(t) or t < 0 or (self.last_time is not None and t < self.last_time):
            raise ValueError("Timestamps must not decrease")
        t = float(t)
        if self.last_time is not None and t - self.last_time > self.settings.maximum_gap + 1e-9:
            self.interrupt("input_gap")
        self.last_time = t
        valid = (
            bool(pose_valid)
            and pitch_delta_deg is not None
            and _is_finite_number(pitch_delta_deg)
            and abs(float(pitch_delta_deg)) <= self.settings.maximum_abs_pitch_deg
        )
        if not valid:
            self.interrupt("unobservable")
            self.last_time = t
            return False
        pitch = float(pitch_delta_deg)
        self.last_valid = t
        down_threshold = self.settings.head_down_pitch_deg
        up_threshold = self.settings.head_up_pitch_deg
        if self.active:
            if pitch < up_threshold:
                if self.recovery is None:
                    self.recovery = t
                if t - self.recovery + 1e-9 >= self.settings.head_recovery_seconds:
                    self.active.ended_s = self.recovery
                    self.active = self.candidate = self.recovery = None
                    self.state = "normal"
                else:
                    self.state = "recovering"
            else:
                self.recovery = None
                self.state = "head_down"
            return False
        if pitch >= down_threshold:
            if self.candidate is None:
                self.candidate = t
            self.state = "bending"
            if t - self.candidate + 1e-9 >= self.settings.head_down_seconds:
                self.active = Event(
                    str(uuid4()),
                    self.session_id,
                    "head_down",
                    self.candidate,
                    t,
                    config_version=self.settings.version,
                )
                self.events.append(self.active)
                self.state = "head_down"
                return True
        else:
            self.candidate = None
            self.state = "normal"
        return False


class StudySession:
    def __init__(self, settings=None, source="camera", goal="", pose_measurer=None):
        self.settings = settings or Settings()
        self.session_id = str(uuid4())
        self.started_at = datetime.now(timezone.utc).isoformat()
        self.source = source
        self.goal = goal
        self.pose_measurer = pose_measurer
        self.status = "running"
        self.ended_at = None
        self.detector = EyeDetector(self.settings, self.session_id)
        self.head_detector = HeadPoseDetector(self.settings, self.session_id)
        self.baseline = None
        self.calibration_history = []
        self.calibration_started = None
        self.calibration_values = []
        self.calibration_observed = 0.0
        self.calibration_message = "look_forward"
        self.paused = False
        self.phase = "calibrating"
        self.last_time = None
        self.last_valid = False
        self.last_pose_valid = False
        self.setup_seconds = self.study_seconds = self.break_seconds = 0.0
        self.observable_seconds = self.unobservable_seconds = 0.0
        self.pose_observable_seconds = self.pose_unobservable_seconds = 0.0
        self._observed_intervals = []
        self.ear = None
        self.pitch_delta_deg = None
        self.last_measurement = None
        self.last_triggered = {"eye_closure_suspected": False, "head_down": False}

    def _advance(self, t, valid, pose_valid=False):
        if not _is_finite_number(t) or t < 0 or (self.last_time is not None and t < self.last_time):
            raise ValueError("Timestamps must be finite, non-negative, and monotonic")
        t = float(t)
        prev_time = self.last_time
        dt = 0.0 if prev_time is None else t - prev_time
        within_gap = dt <= self.settings.maximum_gap + 1e-9
        observed = valid and self.last_valid and within_gap
        pose_observed = pose_valid and self.last_pose_valid and within_gap
        if self.phase == "paused":
            self.break_seconds += dt
        elif self.phase == "studying":
            self.study_seconds += dt
            if observed:
                self.observable_seconds += dt
                if dt > 0 and prev_time is not None:
                    if self._observed_intervals and abs(self._observed_intervals[-1][1] - prev_time) <= 1e-9:
                        self._observed_intervals[-1] = (self._observed_intervals[-1][0], t)
                    else:
                        self._observed_intervals.append((prev_time, t))
            else:
                self.unobservable_seconds += dt
            if pose_observed:
                self.pose_observable_seconds += dt
            else:
                self.pose_unobservable_seconds += dt
        else:
            self.setup_seconds += dt
            if self.calibration_started is not None and observed:
                self.calibration_observed += dt
        self.last_time, self.last_valid = t, valid
        self.last_pose_valid = pose_valid

    def _clear_calibration(self):
        self.calibration_started = None
        self.calibration_values = []
        self.calibration_observed = 0.0

    def step_measurement(self, measurement: FrameMeasurement) -> bool:
        """Process a canonical FrameMeasurement contract object."""
        if not isinstance(measurement, FrameMeasurement):
            raise TypeError("Expected a FrameMeasurement instance")
        if measurement.session_id != self.session_id:
            raise ValueError("FrameMeasurement session_id does not match StudySession")
        self.last_measurement = measurement
        return self.step(
            measurement.timestamp_s,
            measurement.ear_average,
            measurement.effective_pitch_delta_deg,
            eye_valid=measurement.eye_valid,
            pose_valid=measurement.pose_valid,
            invalid_reason=measurement.invalid_reason,
        )

    def step_pose(self, t: float, pitch_delta_deg: float | None, *, pose_valid: bool = True) -> bool:
        """Convenience interface to supply head pitch delta alongside the latest or missing EAR."""
        return self.step(t, self.ear, pitch_delta_deg, pose_valid=pose_valid)

    def step(
        self,
        t,
        ear=None,
        pitch_delta_deg=None,
        *,
        eye_valid=None,
        pose_valid=None,
        invalid_reason=None,
    ):
        if isinstance(t, FrameMeasurement):
            return self.step_measurement(t)
        if self.status != "running":
            raise RuntimeError("Session already ended")
        valid_eye = (
            (True if eye_valid is None else bool(eye_valid))
            and ear is not None
            and _is_finite_number(ear)
            and 0 <= float(ear) <= 0.8
        )
        ear = float(ear) if valid_eye else None
        valid_pose = (
            (True if pose_valid is None else bool(pose_valid))
            and pitch_delta_deg is not None
            and _is_finite_number(pitch_delta_deg)
            and abs(float(pitch_delta_deg)) <= self.settings.maximum_abs_pitch_deg
        )
        pitch = float(pitch_delta_deg) if valid_pose else None

        self._advance(t, valid_eye, valid_pose)
        t = float(t)
        self.ear = ear
        self.pitch_delta_deg = pitch
        self.last_measurement = FrameMeasurement(
            session_id=self.session_id,
            timestamp_s=t,
            eye_valid=valid_eye,
            pose_valid=valid_pose,
            ear_left=ear,
            ear_right=ear,
            pitch_delta_deg=pitch,
            invalid_reason=invalid_reason,
        )
        self.last_triggered = {"eye_closure_suspected": False, "head_down": False}

        if self.paused:
            return False
        if self.baseline is None:
            if valid_eye:
                if self.calibration_started is None:
                    self.calibration_started = t
                self.calibration_values.append(ear)
            if self.calibration_started is not None and t - self.calibration_started + 1e-9 >= self.settings.calibration_seconds:
                elapsed = t - self.calibration_started
                enough = (
                    len(self.calibration_values) >= self.settings.calibration_samples
                    and self.calibration_observed >= elapsed * self.settings.calibration_coverage
                )
                baseline = median(self.calibration_values) if self.calibration_values else 0
                if enough and baseline >= self.settings.minimum_baseline:
                    self.baseline = baseline
                    self.phase = "studying"
                    self.calibration_message = "complete"
                    self.calibration_history.append({
                        "time_s": t,
                        "baseline": baseline,
                        "samples": len(self.calibration_values),
                    })
                    self.detector.update(t, ear, baseline)
                    self.head_detector.update(t, pitch, pose_valid=valid_pose)
                else:
                    self._clear_calibration()
                    self.calibration_message = "retry_eyes_open" if enough else "retry_face_visible"
            return False
        eye_alarm = self.detector.update(t, ear, self.baseline)
        head_alarm = self.head_detector.update(t, pitch, pose_valid=valid_pose)
        self.last_triggered = {"eye_closure_suspected": eye_alarm, "head_down": head_alarm}
        return bool(eye_alarm or head_alarm)

    def toggle_pause(self, t):
        self._advance(t, self.last_valid, self.last_pose_valid)
        self.detector.interrupt("pause")
        self.head_detector.interrupt("pause")
        self.paused = not self.paused
        self.phase = "paused" if self.paused else ("studying" if self.baseline else "calibrating")
        if self.baseline is None:
            self._clear_calibration()
        self.last_valid = False
        self.last_pose_valid = False
        self.last_triggered = {"eye_closure_suspected": False, "head_down": False}

    def recalibrate(self, t):
        self._advance(t, self.last_valid, self.last_pose_valid)
        self.detector.interrupt("recalibration")
        self.head_detector.interrupt("recalibration")
        self.baseline = None
        self.paused = False
        self.phase = "calibrating"
        self.last_valid = False
        self.last_pose_valid = False
        self._clear_calibration()
        self.calibration_message = "look_forward"
        self.last_triggered = {"eye_closure_suspected": False, "head_down": False}

    def finish(self, t, reason="user_exit"):
        if self.status != "running":
            return
        self._advance(t, self.last_valid, self.last_pose_valid)
        self.detector.interrupt(reason)
        self.head_detector.interrupt(reason)
        self.status = "completed" if reason in ("user_exit", "video_end", "frame_limit") else "interrupted"
        self.ended_at = datetime.now(timezone.utc).isoformat()
        self.last_triggered = {"eye_closure_suspected": False, "head_down": False}

    @property
    def state(self):
        if self.phase != "studying":
            return self.phase
        if self.detector.state == "unobservable":
            return "unobservable"
        if self.detector.state != "normal":
            return self.detector.state
        if self.head_detector.state in ("head_down", "bending", "recovering"):
            return self.head_detector.state
        return "normal"

    @property
    def pose_state(self):
        return self.phase if self.phase != "studying" else self.head_detector.state

    @property
    def events(self):
        combined = list(self.detector.events) + list(self.head_detector.events)
        combined.sort(key=lambda event: (event.condition_started_s, event.confirmed_s, event.type))
        return combined

    def windows(self):
        t = float(self.last_time or 0.0)
        if t <= 0 or (self.study_seconds <= 0 and not self.detector.events):
            return []
        window_size = self.settings.window_seconds
        result = []
        start_s = 0.0
        while start_s < t - 1e-9:
            end_s = min(t, start_s + window_size)
            is_last = end_s >= t - 1e-9
            obs = 0.0
            for span_start, span_end in self._observed_intervals:
                overlap_start = max(start_s, span_start)
                overlap_end = min(end_s, span_end)
                if overlap_end > overlap_start:
                    obs += overlap_end - overlap_start
            obs = min(end_s - start_s, obs)
            count = 0
            for event in self.detector.events:
                if is_last:
                    if start_s - 1e-9 <= event.confirmed_s <= end_s + 1e-9:
                        count += 1
                else:
                    if start_s - 1e-9 <= event.confirmed_s < end_s - 1e-9:
                        count += 1
            result.append(
                TimeWindow(
                    start_s=start_s,
                    end_s=end_s,
                    observable_seconds=obs,
                    eye_closure_count=count,
                ).to_dict()
            )
            start_s = end_s
        return result

    def summary(self) -> SessionSummary:
        snap = self.snapshot()
        return SessionSummary.from_dict(snap)

    def snapshot(self):
        t = float(self.last_time or 0.0)
        eye_intervals = [
            (event.condition_started_s, event.ended_s if event.ended_s is not None else t)
            for event in self.detector.events
        ]
        head_intervals = [
            (event.condition_started_s, event.ended_s if event.ended_s is not None else t)
            for event in self.head_detector.events
        ]
        closure = merged_interval_seconds(eye_intervals)
        head_down = merged_interval_seconds(head_intervals)
        combined = merged_interval_seconds(eye_intervals + head_intervals)
        return {
            "session_id": self.session_id,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "source": self.source,
            "goal": self.goal,
            "status": self.status,
            "state": self.state,
            "pose_state": self.pose_state,
            "study_seconds": self.study_seconds,
            "break_seconds": self.break_seconds,
            "setup_seconds": self.setup_seconds,
            "observable_seconds": self.observable_seconds,
            "unobservable_seconds": self.unobservable_seconds,
            "pose_observable_seconds": self.pose_observable_seconds,
            "pose_unobservable_seconds": self.pose_unobservable_seconds,
            "eye_closure_count": len(self.detector.events),
            "eye_closure_seconds": closure,
            "eye_closure_ratio": closure / self.observable_seconds if self.observable_seconds > 0 else None,
            "head_down_count": len(self.head_detector.events),
            "head_down_seconds": head_down,
            "combined_event_seconds": combined,
            "windows": self.windows(),
            "baseline": self.baseline,
            "calibration_history": self.calibration_history,
            "settings": asdict(self.settings),
            "schema_version": SCHEMA_VERSION,
        }

    def event_rows(self):
        return [asdict(event) for event in self.events]
