"""Deterministic timing: callers supply seconds, never frame counts."""

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from math import isfinite
from statistics import median
from uuid import uuid4


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
    version: str = "eyes-v0.1"

    def __post_init__(self):
        if not (0 < self.close_ratio < self.open_ratio <= 1):
            raise ValueError("Invalid eye thresholds")
        if any(not isfinite(x) or x <= 0 for x in (
            self.calibration_seconds, self.closure_seconds, self.recovery_seconds,
            self.maximum_gap, self.minimum_baseline,
        )) or self.calibration_samples < 2 or not (0 < self.calibration_coverage <= 1):
            raise ValueError("Invalid timing or calibration settings")


@dataclass
class Event:
    event_id: str
    session_id: str
    type: str
    condition_started_s: float
    confirmed_s: float
    ended_s: float | None = None
    interrupted_reason: str | None = None


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
        if self.last_time is not None and t < self.last_time:
            raise ValueError("Timestamps must not decrease")
        if self.last_time is not None and t - self.last_time > self.settings.maximum_gap + 1e-9:
            self.interrupt("input_gap")
        self.last_time = t
        if ear is None:
            self.interrupt("unobservable")
            self.last_time = t
            return False
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
                self.active = Event(str(uuid4()), self.session_id, "eye_closure_suspected", self.candidate, t)
                self.events.append(self.active)
                self.state = "eyes_closed"
                return True
        else:
            self.candidate = None
            self.state = "normal"
        return False


class StudySession:
    def __init__(self, settings=None, source="camera", goal=""):
        self.settings = settings or Settings()
        self.session_id = str(uuid4())
        self.started_at = datetime.now(timezone.utc).isoformat()
        self.source = source
        self.goal = goal
        self.status = "running"
        self.ended_at = None
        self.detector = EyeDetector(self.settings, self.session_id)
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
        self.setup_seconds = self.study_seconds = self.break_seconds = 0.0
        self.observable_seconds = self.unobservable_seconds = 0.0
        self.ear = None

    def _advance(self, t, valid):
        if not isfinite(t) or t < 0 or (self.last_time is not None and t < self.last_time):
            raise ValueError("Timestamps must be finite, non-negative, and monotonic")
        dt = 0 if self.last_time is None else t - self.last_time
        observed = valid and self.last_valid and dt <= self.settings.maximum_gap + 1e-9
        if self.phase == "paused":
            self.break_seconds += dt
        elif self.phase == "studying":
            self.study_seconds += dt
            if observed:
                self.observable_seconds += dt
            else:
                self.unobservable_seconds += dt
        else:
            self.setup_seconds += dt
            if self.calibration_started is not None and observed:
                self.calibration_observed += dt
        self.last_time, self.last_valid = t, valid

    def _clear_calibration(self):
        self.calibration_started = None
        self.calibration_values = []
        self.calibration_observed = 0.0

    def step(self, t, ear):
        if self.status != "running":
            raise RuntimeError("Session already ended")
        valid = ear is not None and isfinite(ear) and 0 <= ear <= 0.8
        ear = ear if valid else None
        self._advance(t, valid)
        self.ear = ear
        if self.paused:
            return False
        if self.baseline is None:
            if valid:
                if self.calibration_started is None:
                    self.calibration_started = t
                self.calibration_values.append(ear)
            if self.calibration_started is not None and t - self.calibration_started + 1e-9 >= self.settings.calibration_seconds:
                elapsed = t - self.calibration_started
                enough = (len(self.calibration_values) >= self.settings.calibration_samples
                          and self.calibration_observed >= elapsed * self.settings.calibration_coverage)
                baseline = median(self.calibration_values) if self.calibration_values else 0
                if enough and baseline >= self.settings.minimum_baseline:
                    self.baseline = baseline
                    self.phase = "studying"
                    self.calibration_message = "complete"
                    self.calibration_history.append({"time_s": t, "baseline": baseline, "samples": len(self.calibration_values)})
                    self.detector.update(t, ear, baseline)
                else:
                    self._clear_calibration()
                    self.calibration_message = "retry_eyes_open" if enough else "retry_face_visible"
            return False
        return self.detector.update(t, ear, self.baseline)

    def toggle_pause(self, t):
        self._advance(t, self.last_valid)
        self.detector.interrupt("pause")
        self.paused = not self.paused
        self.phase = "paused" if self.paused else ("studying" if self.baseline else "calibrating")
        if self.baseline is None:
            self._clear_calibration()
        self.last_valid = False

    def recalibrate(self, t):
        self._advance(t, self.last_valid)
        self.detector.interrupt("recalibration")
        self.baseline = None
        self.paused = False
        self.phase = "calibrating"
        self.last_valid = False
        self._clear_calibration()
        self.calibration_message = "look_forward"

    def finish(self, t, reason="user_exit"):
        if self.status != "running":
            return
        self._advance(t, self.last_valid)
        self.detector.interrupt(reason)
        self.status = "completed" if reason in ("user_exit", "video_end", "frame_limit") else "interrupted"
        self.ended_at = datetime.now(timezone.utc).isoformat()

    @property
    def state(self):
        return self.phase if self.phase != "studying" else self.detector.state

    def snapshot(self):
        t = self.last_time or 0.0
        closure = sum(max(0, (event.ended_s if event.ended_s is not None else t) - event.condition_started_s)
                      for event in self.detector.events)
        return {
            "session_id": self.session_id, "started_at": self.started_at, "ended_at": self.ended_at,
            "source": self.source, "goal": self.goal, "status": self.status, "state": self.state,
            "study_seconds": self.study_seconds, "break_seconds": self.break_seconds,
            "setup_seconds": self.setup_seconds, "observable_seconds": self.observable_seconds,
            "unobservable_seconds": self.unobservable_seconds,
            "eye_closure_count": len(self.detector.events), "eye_closure_seconds": closure,
            "eye_closure_ratio": closure / self.observable_seconds if self.observable_seconds > 0 else None,
            "baseline": self.baseline, "calibration_history": self.calibration_history,
            "settings": asdict(self.settings), "schema_version": "0.1",
        }

    def event_rows(self):
        return [asdict(event) for event in self.detector.events]
