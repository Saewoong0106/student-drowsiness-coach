"""Pixel-correct eye aspect ratio and head pitch / occlusion measurement (#4, #22)."""

from dataclasses import dataclass
from math import atan2, degrees, dist, isfinite

from .contracts import FrameMeasurement

LEFT_EYE = (362, 385, 387, 263, 373, 380)
RIGHT_EYE = (33, 160, 158, 133, 153, 144)

FOREHEAD_TOP = 10
NOSE_TIP = 1
CHIN_BOTTOM = 152
RIGHT_CHEEK = 234
LEFT_CHEEK = 454
POSE_LANDMARKS = (
    FOREHEAD_TOP,
    NOSE_TIP,
    CHIN_BOTTOM,
    RIGHT_CHEEK,
    LEFT_CHEEK,
)
NEUTRAL_NOSE_RATIO = 0.53


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


@dataclass(frozen=True)
class HeadPoseMeasurement:
    """Head pitch and validity measurement from facial landmarks (#22)."""

    pitch_delta_deg: float | None
    pose_valid: bool
    invalid_reason: str | None = None
    raw_pitch_deg: float | None = None
    yaw_asymmetry: float | None = None

    @property
    def reason(self) -> str | None:
        return self.invalid_reason


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


def measure_head_pose(
    landmarks,
    width: int,
    height: int,
    *,
    baseline_pitch_deg: float | None = None,
    max_yaw_asymmetry: float = 0.34,
    minimum_face_height: float = 24.0,
    minimum_face_width: float = 20.0,
) -> HeadPoseMeasurement:
    """Calculate downward-positive head pitch and reliability from facial landmarks (#22)."""
    if not landmarks:
        return HeadPoseMeasurement(None, False, "face_missing")
    if len(landmarks) <= max(POSE_LANDMARKS):
        return HeadPoseMeasurement(None, False, "incomplete_landmarks")

    px = {}
    for index in POSE_LANDMARKS:
        p = landmarks[index]
        z_raw = getattr(p, "z", 0.0)
        z_val = 0.0 if z_raw is None else z_raw
        if not (
            isfinite(p.x)
            and isfinite(p.y)
            and isfinite(z_val)
            and 0.0 <= p.x <= 1.0
            and 0.0 <= p.y <= 1.0
        ):
            return HeadPoseMeasurement(None, False, "pose_outside_frame")
        px[index] = (p.x * width, p.y * height, float(z_val) * width)

    forehead = px[FOREHEAD_TOP]
    nose = px[NOSE_TIP]
    chin = px[CHIN_BOTTOM]
    right_cheek = px[RIGHT_CHEEK]
    left_cheek = px[LEFT_CHEEK]

    face_height = chin[1] - forehead[1]
    face_width = left_cheek[0] - right_cheek[0]

    if (
        face_height < minimum_face_height
        or face_width < minimum_face_width
        or not (forehead[1] < nose[1] < chin[1])
    ):
        return HeadPoseMeasurement(None, False, "pose_geometry_unreliable")

    nose_ratio = (nose[1] - forehead[1]) / face_height
    aspect = face_height / face_width
    roll_deg = degrees(atan2(left_cheek[1] - right_cheek[1], face_width))
    if not (0.20 <= nose_ratio <= 0.85 and 0.40 <= aspect <= 2.60) or abs(roll_deg) > 55.0:
        return HeadPoseMeasurement(None, False, "pose_geometry_unreliable")

    face_center_x = 0.5 * (right_cheek[0] + left_cheek[0])
    yaw_asymmetry = abs(nose[0] - face_center_x) / face_width
    right_half = nose[0] - right_cheek[0]
    left_half = left_cheek[0] - nose[0]
    if right_half <= 0.0 or left_half <= 0.0 or yaw_asymmetry > max_yaw_asymmetry:
        return HeadPoseMeasurement(
            None,
            False,
            "excessive_yaw",
            yaw_asymmetry=yaw_asymmetry,
        )

    pitch_2d_deg = (nose_ratio - NEUTRAL_NOSE_RATIO) * 150.0
    has_z = max(abs(forehead[2]), abs(nose[2]), abs(chin[2])) > 1e-4
    if has_z:
        pitch_3d_deg = degrees(atan2(chin[2] - forehead[2], face_height))
        raw_pitch_deg = 0.65 * pitch_3d_deg + 0.35 * pitch_2d_deg
    else:
        raw_pitch_deg = pitch_2d_deg

    base = (
        float(baseline_pitch_deg)
        if baseline_pitch_deg is not None and isfinite(baseline_pitch_deg)
        else 0.0
    )
    pitch_delta_deg = raw_pitch_deg - base
    if not isfinite(pitch_delta_deg) or abs(pitch_delta_deg) > 90.0:
        return HeadPoseMeasurement(
            None,
            False,
            "pose_out_of_range",
            raw_pitch_deg=raw_pitch_deg,
            yaw_asymmetry=yaw_asymmetry,
        )

    return HeadPoseMeasurement(
        pitch_delta_deg=pitch_delta_deg,
        pose_valid=True,
        invalid_reason=None,
        raw_pitch_deg=raw_pitch_deg,
        yaw_asymmetry=yaw_asymmetry,
    )


DEEP_HEAD_DOWN_OCCLUSION_DEG = 35.0


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
    """Build a FrameMeasurement from landmarks and head-pose measurement (T04 / #22)."""
    eye = measure_eyes(landmarks, width, height)
    active_measurer = (
        pose_measurer
        if pose_measurer is not None
        else (measure_head_pose if pitch_delta_deg is None and pose_valid is None else None)
    )
    pose_reason = None
    if active_measurer is not None and pitch_delta_deg is None:
        pose_output = active_measurer(landmarks, width, height)
        if isinstance(pose_output, dict):
            pitch_delta_deg = pose_output.get("pitch_delta_deg")
            if pose_valid is None:
                pose_valid = pose_output.get("pose_valid")
            pose_reason = pose_output.get("invalid_reason")
        elif isinstance(pose_output, tuple):
            if len(pose_output) >= 1:
                pitch_delta_deg = pose_output[0]
            if len(pose_output) >= 2 and pose_valid is None:
                pose_valid = pose_output[1]
            if len(pose_output) >= 3:
                pose_reason = pose_output[2]
        else:
            pitch_delta_deg = getattr(pose_output, "pitch_delta_deg", pose_output)
            if pose_valid is None and hasattr(pose_output, "pose_valid"):
                pose_valid = pose_output.pose_valid
            if hasattr(pose_output, "invalid_reason"):
                pose_reason = pose_output.invalid_reason
    if (
        eye.average is not None
        and pose_valid is not False
        and pitch_delta_deg is not None
        and isfinite(pitch_delta_deg)
        and float(pitch_delta_deg) >= DEEP_HEAD_DOWN_OCCLUSION_DEG
    ):
        eye = EyeMeasurement(None, None, "head_down_eye_occluded")
    if invalid_reason is None:
        invalid_reason = eye.reason if eye.reason is not None else pose_reason
    return eye.to_frame_measurement(
        session_id=session_id,
        timestamp_s=timestamp_s,
        pitch_delta_deg=pitch_delta_deg,
        pose_valid=pose_valid,
        invalid_reason=invalid_reason,
    )
