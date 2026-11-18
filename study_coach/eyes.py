"""Pixel-correct eye aspect ratio. Geometry checks are not an occlusion model."""

from dataclasses import dataclass
from math import dist, isfinite

from .contracts import FrameMeasurement

LEFT_EYE = (362, 385, 387, 263, 373, 380)
RIGHT_EYE = (33, 160, 158, 133, 153, 144)


@dataclass(frozen=True)
class EyeMeasurement:
    left: float | None
    right: float | None
    reason: str | None = None

    @property
    def average(self):
        if self.left is None or self.right is None:
            return None
        return (self.left + self.right) / 2

    def to_frame_measurement(
        self,
        session_id: str,
        timestamp_s: float,
        *,
        pitch_delta_deg: float | None = None,
        pose_valid: bool | None = None,
        invalid_reason: str | None = None,
    ) -> FrameMeasurement:
        return FrameMeasurement.from_measurements(
            session_id=session_id,
            timestamp_s=timestamp_s,
            eye=self,
            pitch_delta_deg=pitch_delta_deg,
            pose_valid=pose_valid,
            invalid_reason=invalid_reason,
        )


def eye_ratio(points, minimum_width=12.0):
    if len(points) != 6 or not all(isfinite(x) and isfinite(y) for x, y in points):
        return None
    width = dist(points[0], points[3])
    if width < minimum_width:
        return None
    ratio = (dist(points[1], points[5]) + dist(points[2], points[4])) / (2 * width)
    return ratio if 0 <= ratio <= 0.8 else None


def measure_eyes(landmarks, width, height):
    if not landmarks:
        return EyeMeasurement(None, None, "face_missing")
    values = []
    for indices in (LEFT_EYE, RIGHT_EYE):
        if len(landmarks) <= max(indices):
            return EyeMeasurement(None, None, "incomplete_landmarks")
        points = []
        for index in indices:
            p = landmarks[index]
            if not (isfinite(p.x) and isfinite(p.y) and 0 <= p.x <= 1 and 0 <= p.y <= 1):
                return EyeMeasurement(None, None, "eye_outside_frame")
            points.append((p.x * width, p.y * height))
        values.append(eye_ratio(points))
    if None in values:
        return EyeMeasurement(None, None, "eye_geometry_unreliable")
    return EyeMeasurement(*values)


def measure_frame(
    session_id: str,
    timestamp_s: float,
    landmarks,
    width: int,
    height: int,
    *,
    pitch_delta_deg: float | None = None,
    pose_valid: bool | None = None,
    invalid_reason: str | None = None,
    pose_measurer=None,
) -> FrameMeasurement:
    """Build a FrameMeasurement from landmarks and an optional head-pose input/callback (T04)."""
    eye = measure_eyes(landmarks, width, height)
    if pose_measurer is not None and pitch_delta_deg is None:
        pose_output = pose_measurer(landmarks, width, height)
        if isinstance(pose_output, dict):
            pitch_delta_deg = pose_output.get("pitch_delta_deg")
            if pose_valid is None:
                pose_valid = pose_output.get("pose_valid")
            if invalid_reason is None:
                invalid_reason = pose_output.get("invalid_reason")
        elif isinstance(pose_output, tuple):
            if len(pose_output) >= 1:
                pitch_delta_deg = pose_output[0]
            if len(pose_output) >= 2 and pose_valid is None:
                pose_valid = pose_output[1]
            if len(pose_output) >= 3 and invalid_reason is None:
                invalid_reason = pose_output[2]
        else:
            pitch_delta_deg = getattr(pose_output, "pitch_delta_deg", pose_output)
            if pose_valid is None and hasattr(pose_output, "pose_valid"):
                pose_valid = pose_output.pose_valid
            if invalid_reason is None and hasattr(pose_output, "invalid_reason"):
                invalid_reason = pose_output.invalid_reason
    return eye.to_frame_measurement(
        session_id=session_id,
        timestamp_s=timestamp_s,
        pitch_delta_deg=pitch_delta_deg,
        pose_valid=pose_valid,
        invalid_reason=invalid_reason,
    )
