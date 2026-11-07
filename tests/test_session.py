import csv
import json
import math
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from study_coach.eyes import LEFT_EYE, RIGHT_EYE, eye_ratio, measure_eyes
from study_coach.session import EyeDetector, Settings, StudySession
from study_coach.storage import SessionStore


def calibrated():
    session = StudySession(Settings(calibration_seconds=1, calibration_samples=5), source="synthetic")
    for i in range(11):
        session.step(i / 10, 0.3)
    assert session.baseline == 0.3
    return session


class MeasurementTests(unittest.TestCase):
    def test_known_eye_ratio(self):
        points = [(0, 0), (10, -3), (20, -3), (30, 0), (20, 3), (10, 3)]
        self.assertAlmostEqual(eye_ratio(points), 0.2)

    def test_aspect_ratio_uses_pixel_dimensions(self):
        landmarks = [SimpleNamespace(x=0.5, y=0.5) for _ in range(478)]
        for indices, origin in [(LEFT_EYE, 100), (RIGHT_EYE, 200)]:
            points = [(0, 0), (10, -3), (20, -3), (30, 0), (20, 3), (10, 3)]
            for index, (x, y) in zip(indices, points):
                landmarks[index] = SimpleNamespace(x=(x + origin) / 640, y=(y + 100) / 480)
        self.assertAlmostEqual(measure_eyes(landmarks, 640, 480).average, 0.2)

    def test_missing_or_invalid_geometry_is_not_zero(self):
        self.assertIsNone(measure_eyes([], 640, 480).average)
        self.assertIsNone(eye_ratio([(0, 0)] * 6))
        self.assertIsNone(eye_ratio([(math.nan, 0)] * 6))
        landmarks = [SimpleNamespace(x=2, y=0.5) for _ in range(478)]
        self.assertIsNone(measure_eyes(landmarks, 640, 480).average)


class DetectorTests(unittest.TestCase):
    def test_short_blink_does_not_alarm(self):
        detector = EyeDetector(Settings(), "test")
        for i in range(20):
            self.assertFalse(detector.update(i / 10, 0.1, 0.3))
        detector.update(2, 0.3, 0.3)
        self.assertEqual(detector.events, [])

    def test_exact_threshold_and_one_alarm_per_event(self):
        detector = EyeDetector(Settings(), "test")
        alarms = [detector.update(i / 10, 0.1, 0.3) for i in range(41)]
        self.assertEqual(sum(alarms), 1)
        self.assertTrue(alarms[20])
        self.assertEqual(detector.events[0].confirmed_s, 2)
        self.assertEqual(detector.events[0].condition_started_s, 0)

    def test_recovery_is_backdated_and_hysteresis_does_not_flap(self):
        detector = EyeDetector(Settings(), "test")
        for i in range(21):
            detector.update(i / 10, 0.1, 0.3)
        for i in range(21, 31):
            detector.update(i / 10, 0.21, 0.3)
        self.assertIsNotNone(detector.active)
        for i in range(31, 42):
            detector.update(i / 10, 0.3, 0.3)
        self.assertIsNone(detector.active)
        self.assertAlmostEqual(detector.events[0].ended_s, 3.1)

    def test_missing_face_closes_at_last_valid_time(self):
        detector = EyeDetector(Settings(), "test")
        for i in range(26):
            detector.update(i / 10, 0.1, 0.3)
        detector.update(2.6, None, 0.3)
        self.assertEqual(detector.events[0].ended_s, 2.5)
        self.assertEqual(detector.events[0].interrupted_reason, "unobservable")
        self.assertFalse(detector.update(2.7, 0.1, 0.3))

    def test_gap_cannot_bridge_two_short_closures(self):
        detector = EyeDetector(Settings(), "test")
        for i in range(20):
            detector.update(i / 10, 0.1, 0.3)
        self.assertFalse(detector.update(2.5, 0.1, 0.3))
        self.assertEqual(detector.candidate, 2.5)
        self.assertEqual(len(detector.events), 0)

    def test_gap_closes_confirmed_event_and_starts_new_candidate(self):
        detector = EyeDetector(Settings(), "test")
        for i in range(21):
            detector.update(i / 10, 0.1, 0.3)
        self.assertFalse(detector.update(3, 0.1, 0.3))
        self.assertEqual(detector.events[0].ended_s, 2)
        self.assertEqual(detector.events[0].interrupted_reason, "input_gap")
        self.assertEqual(detector.candidate, 3)

    def test_same_event_timing_at_different_frame_rates(self):
        for fps in (5, 10, 30):
            with self.subTest(fps=fps):
                detector = EyeDetector(Settings(), "test")
                for i in range(4 * fps + 1):
                    t = i / fps
                    detector.update(t, 0.1 if t < 3 else 0.3, 0.3)
                self.assertEqual(len(detector.events), 1)
                self.assertAlmostEqual(detector.events[0].confirmed_s, 2)
                self.assertAlmostEqual(detector.events[0].ended_s, 3)


class SessionTests(unittest.TestCase):
    def test_calibration_median_and_excluded_setup_time(self):
        session = StudySession(Settings(calibration_seconds=1, calibration_samples=5))
        for i in range(11):
            session.step(i / 10, 0.05 if i == 3 else 0.3)
        self.assertEqual(session.baseline, 0.3)
        self.assertEqual(session.study_seconds, 0)
        self.assertAlmostEqual(session.setup_seconds, 1)

    def test_closed_eye_calibration_is_retried(self):
        session = StudySession(Settings(calibration_seconds=1, calibration_samples=5))
        for i in range(11):
            session.step(i / 10, 0.05)
        self.assertIsNone(session.baseline)
        self.assertEqual(session.calibration_message, "retry_eyes_open")

    def test_missing_calibration_and_zero_denominator(self):
        session = StudySession()
        for i in range(101):
            session.step(i / 10, None)
        self.assertIsNone(session.baseline)
        self.assertIsNone(session.snapshot()["eye_closure_ratio"])
        self.assertEqual(session.study_seconds, 0)

    def test_sparse_calibration_is_retried(self):
        session = StudySession(Settings(calibration_seconds=1, calibration_samples=5))
        for i in range(11):
            session.step(i / 10, 0.3 if i % 2 == 0 else None)
        self.assertIsNone(session.baseline)
        self.assertEqual(session.calibration_message, "retry_face_visible")

    def test_pause_excludes_study_and_clears_pending_closure(self):
        session = calibrated()
        for i in range(11, 26):
            session.step(i / 10, 0.1)
        session.toggle_pause(2.5)
        for i in range(26, 56):
            session.step(i / 10, 0.1)
        self.assertAlmostEqual(session.study_seconds, 1.5)
        self.assertAlmostEqual(session.break_seconds, 3)
        session.toggle_pause(5.5)
        session.step(5.6, 0.1)
        self.assertEqual(len(session.detector.events), 0)

    def test_pause_closes_active_event(self):
        session = calibrated()
        for i in range(11, 36):
            session.step(i / 10, 0.1)
        session.toggle_pause(3.5)
        self.assertEqual(session.detector.events[0].ended_s, 3.5)
        self.assertEqual(session.detector.events[0].interrupted_reason, "pause")

    def test_recalibration_preserves_prior_counts(self):
        session = calibrated()
        for i in range(11, 36):
            session.step(i / 10, 0.1)
        session.recalibrate(3.5)
        before = session.study_seconds
        for i in range(36, 47):
            session.step(i / 10, 0.32)
        self.assertAlmostEqual(session.baseline, 0.32)
        self.assertEqual(session.study_seconds, before)
        self.assertEqual(len(session.detector.events), 1)
        self.assertEqual(len(session.calibration_history), 2)

    def test_observation_partition_and_unknown_gap(self):
        session = calibrated()
        session.step(1.1, 0.3)
        session.step(2.1, 0.3)
        session.step(2.2, None)
        session.step(2.3, 0.3)
        session.step(2.4, 0.3)
        session.finish(2.4)
        self.assertAlmostEqual(session.study_seconds, session.observable_seconds + session.unobservable_seconds)
        self.assertAlmostEqual(session.observable_seconds, 0.2)
        self.assertAlmostEqual(session.unobservable_seconds, 1.2)

    def test_finish_idempotent_and_active_event_closed(self):
        session = calibrated()
        for i in range(11, 36):
            session.step(i / 10, 0.1)
        session.finish(3.6)
        snapshot = session.snapshot()
        session.finish(10)
        self.assertEqual(session.snapshot(), snapshot)
        self.assertEqual(session.detector.events[0].ended_s, 3.5)
        self.assertLessEqual(snapshot["eye_closure_ratio"], 1)

    def test_non_monotonic_timestamp_rejected(self):
        session = calibrated()
        with self.assertRaises(ValueError):
            session.step(0, 0.3)
        with self.assertRaises(ValueError):
            session.step(float("nan"), 0.3)

    def test_nan_eye_value_is_unobservable(self):
        session = calibrated()
        self.assertFalse(session.step(1.1, float("nan")))
        self.assertEqual(session.state, "unobservable")


class StorageTests(unittest.TestCase):
    def test_active_then_completed_event_is_updated_without_duplication(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "session.sqlite3"
            session = calibrated()
            for i in range(11, 36):
                session.step(i / 10, 0.1)
            store = SessionStore(path)
            store.save(session)
            store.save(session)
            _, pending = store.read(session.session_id)
            self.assertEqual(len(pending), 1)
            self.assertIsNone(pending[0]["ended_s"])
            session.finish(3.5)
            store.save(session)
            store.close()
            store = SessionStore(path)
            try:
                summary, events = store.read(session.session_id)
                self.assertEqual(summary["status"], "completed")
                self.assertEqual(len(events), 1)
                self.assertEqual(events[0]["ended_s"], 3.5)
                summary_path, csv_path = store.export(session.session_id, Path(folder) / "export")
                self.assertEqual(json.loads(summary_path.read_text())["summary"]["eye_closure_count"], 1)
                with csv_path.open(encoding="utf-8-sig") as source:
                    self.assertEqual(len(list(csv.DictReader(source))), 1)
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
