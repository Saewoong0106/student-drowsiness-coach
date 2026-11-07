"""Pixel-correct eye aspect ratio. Geometry checks are not an occlusion model."""

from dataclasses import dataclass
from math import dist, isfinite

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
