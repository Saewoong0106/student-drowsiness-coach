import csv
import json
import math
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from study_coach.contracts import (
    DEFAULT_CONFIG_VERSION,
    SCHEMA_VERSION,
    CoachingResult,
    Event,
    FrameMeasurement,
    SessionSummary,
    TimeWindow,
    merged_interval_seconds,
)
from study_coach.eyes import LEFT_EYE, RIGHT_EYE, eye_ratio, measure_eyes, measure_frame
from study_coach.session import EyeDetector, HeadPoseDetector, Settings, StudySession
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


class ContractTests(unittest.TestCase):
    def test_frame_measurement_null_safety_and_round_trip(self):
        missing = FrameMeasurement(session_id="s-1", timestamp_s=1.25)
        self.assertFalse(missing.eye_valid)
        self.assertFalse(missing.pose_valid)
        self.assertIsNone(missing.ear_left)
        self.assertIsNone(missing.ear_right)
        self.assertIsNone(missing.ear_average)
        self.assertIsNone(missing.pitch_delta_deg)
        self.assertEqual(missing.invalid_reason, "unobservable")

        nan_frame = FrameMeasurement(
            session_id="s-1",
            timestamp_s=2.0,
            eye_valid=True,
            pose_valid=True,
            ear_left=float("nan"),
            ear_right=float("nan"),
            pitch_delta_deg=float("nan"),
        )
        self.assertFalse(nan_frame.eye_valid)
        self.assertFalse(nan_frame.pose_valid)
        self.assertIsNone(nan_frame.ear_average)
        self.assertIsNone(nan_frame.pitch_delta_deg)

        out_of_range = FrameMeasurement(
            session_id="s-1",
            timestamp_s=2.5,
            eye_valid=True,
            pose_valid=True,
            ear_left=0.28,
            ear_right=0.28,
            pitch_delta_deg=120.0,
        )
        self.assertTrue(out_of_range.eye_valid)
        self.assertFalse(out_of_range.pose_valid)
        self.assertIsNone(out_of_range.pitch_delta_deg)

        valid = FrameMeasurement(
            session_id="s-1",
            timestamp_s=3.0,
            eye_valid=True,
            pose_valid=True,
            ear_left=0.26,
            ear_right=0.28,
            pitch_delta_deg=22.5,
        )
        restored = FrameMeasurement.from_dict(valid.to_dict())
        self.assertEqual(restored, valid)
        self.assertAlmostEqual(restored.ear_average, 0.27)
        self.assertAlmostEqual(restored.effective_pitch_delta_deg, 22.5)

        with self.assertRaises(ValueError):
            FrameMeasurement(session_id="s-1", timestamp_s=-0.1)
        with self.assertRaises(ValueError):
            FrameMeasurement(session_id="s-1", timestamp_s=float("nan"))

    def test_measure_frame_adapter_supports_pose_callback_and_missing_landmarks(self):
        empty = measure_frame("s-1", 1.0, [], 640, 480)
        self.assertFalse(empty.eye_valid)
        self.assertFalse(empty.pose_valid)
        self.assertIsNone(empty.ear_average)
        self.assertIsNone(empty.pitch_delta_deg)
        self.assertEqual(empty.invalid_reason, "face_missing")

        landmarks = [SimpleNamespace(x=0.5, y=0.5) for _ in range(478)]
        for indices, origin in [(LEFT_EYE, 100), (RIGHT_EYE, 200)]:
            points = [(0, 0), (10, -3), (20, -3), (30, 0), (20, 3), (10, 3)]
            for index, (x, y) in zip(indices, points):
                landmarks[index] = SimpleNamespace(x=(x + origin) / 640, y=(y + 100) / 480)

        measured = measure_frame(
            "s-1",
            4.5,
            landmarks,
            640,
            480,
            pose_measurer=lambda pts, w, h: (24.0, True),
        )
        self.assertTrue(measured.eye_valid)
        self.assertTrue(measured.pose_valid)
        self.assertAlmostEqual(measured.ear_average, 0.2)
        self.assertAlmostEqual(measured.pitch_delta_deg, 24.0)

    def test_event_contract_validation(self):
        event = Event(
            event_id="evt-1",
            session_id="2026-09-10T19:00:00",
            type="head_down",
            condition_started_s=10.0,
            confirmed_s=13.0,
            ended_s=18.5,
            interrupted_reason=None,
        )
        self.assertEqual(event.config_version, DEFAULT_CONFIG_VERSION)
        self.assertAlmostEqual(event.duration_s(), 8.5)
        self.assertEqual(Event.from_dict(event.to_dict()), event)

        with self.assertRaises(ValueError):
            Event("evt-2", "s", "unknown_type", 0.0, 2.0, 3.0)
        with self.assertRaises(ValueError):
            Event("evt-3", "s", "eye_closure_suspected", 5.0, 4.0, 6.0)
        with self.assertRaises(ValueError):
            Event("evt-4", "s", "eye_closure_suspected", 5.0, 7.0, 4.0)

    def test_session_summary_matches_interfaces_doc_and_rejects_contradictions(self):
        doc_payload = {
            "session_id": "2026-09-14T21:00:00",
            "goal": "embedded systems review",
            "study_seconds": 3600.0,
            "break_seconds": 600.0,
            "observable_seconds": 3420.0,
            "unobservable_seconds": 180.0,
            "eye_closure_count": 3,
            "eye_closure_seconds": 9.4,
            "head_down_count": 1,
            "windows": [
                {
                    "start_s": 0.0,
                    "end_s": 2400.0,
                    "observable_seconds": 2310.0,
                    "eye_closure_count": 0,
                },
                {
                    "start_s": 2400.0,
                    "end_s": 3600.0,
                    "observable_seconds": 1110.0,
                    "eye_closure_count": 3,
                },
            ],
            "schema_version": "0.1",
        }
        summary = SessionSummary.from_dict(doc_payload)
        self.assertEqual(summary.to_dict(), doc_payload)

        with self.assertRaises(ValueError):
            bad = dict(doc_payload, eye_closure_seconds=-1.0)
            SessionSummary.from_dict(bad)
        with self.assertRaises(ValueError):
            bad = dict(doc_payload, eye_closure_count=1, eye_closure_seconds=0.0)
            SessionSummary.from_dict(bad)
        with self.assertRaises(ValueError):
            bad = dict(doc_payload, eye_closure_seconds=4000.0)
            SessionSummary.from_dict(bad)
        with self.assertRaises(ValueError):
            bad = dict(
                doc_payload,
                windows=[
                    TimeWindow(0.0, 2400.0, 2000.0, 1),
                    TimeWindow(1800.0, 3600.0, 1420.0, 2),
                ],
            )
            SessionSummary.from_dict(bad)

    def test_coaching_result_validation_and_sqlite_persistence(self):
        session = calibrated()
        session.step(1.5, 0.3, 0.0, pose_valid=True)
        session.finish(2.0)
        summary = session.summary()

        coaching = CoachingResult(
            session_id=session.session_id,
            model_id="gemini-2.5-flash",
            model_revision="v1",
            dataset_version="drowsiness-coach-v0.1",
            status="succeeded",
            raw_text="40분 이후 졸음 이벤트가 집중되었습니다.",
            display_text="40분마다 5분 휴식과 노트북 거치대 높이 조정을 권장합니다.",
            validation_errors=[],
            elapsed_seconds=1.25,
            is_fallback=False,
        )
        self.assertTrue(coaching.matches_session(summary.session_id))
        with self.assertRaises(ValueError):
            CoachingResult(
                session_id=session.session_id,
                model_id="m",
                model_revision="v1",
                dataset_version="v1",
                status="invalid_status",
            )

        with tempfile.TemporaryDirectory() as folder:
            store = SessionStore(Path(folder) / "session.sqlite3")
            try:
                store.save(session)
                store.save_coaching(coaching)
                loaded = store.read_coaching(session.session_id)
                self.assertEqual(loaded, coaching.to_dict())
                self.assertEqual(CoachingResult.from_dict(loaded), coaching)
            finally:
                store.close()


class HeadPoseDetectorTests(unittest.TestCase):
    def test_short_head_bend_under_3_seconds_does_not_trigger(self):
        detector = HeadPoseDetector(Settings(), "test")
        for i in range(30):
            self.assertFalse(detector.update(i / 10, 22.0, pose_valid=True))
        self.assertFalse(detector.update(3.0, 5.0, pose_valid=True))
        self.assertEqual(detector.events, [])
        self.assertEqual(detector.state, "normal")

    def test_pitch_20_degrees_for_3_seconds_triggers_once_without_duplicate_events(self):
        detector = HeadPoseDetector(Settings(), "test")
        alarms = [detector.update(i / 10, 20.0, pose_valid=True) for i in range(51)]
        self.assertEqual(sum(alarms), 1)
        self.assertTrue(alarms[30])
        self.assertEqual(len(detector.events), 1)
        self.assertEqual(detector.events[0].type, "head_down")
        self.assertEqual(detector.events[0].condition_started_s, 0.0)
        self.assertEqual(detector.events[0].confirmed_s, 3.0)
        self.assertEqual(detector.state, "head_down")

    def test_recovery_under_15_degrees_for_1_second_closes_and_backdates_event(self):
        detector = HeadPoseDetector(Settings(), "test")
        for i in range(31):
            detector.update(i / 10, 24.0, pose_valid=True)
        # Hysteresis band (15.0 <= pitch < 20.0) keeps head_down active
        for i in range(31, 41):
            detector.update(i / 10, 17.5, pose_valid=True)
        self.assertIsNotNone(detector.active)
        self.assertEqual(detector.state, "head_down")

        # Recovery starts at 4.1s (< 15.0 deg) and completes at 5.1s
        for i in range(41, 52):
            detector.update(i / 10, 10.0, pose_valid=True)
        self.assertIsNone(detector.active)
        self.assertEqual(detector.state, "normal")
        self.assertAlmostEqual(detector.events[0].ended_s, 4.1)
        self.assertAlmostEqual(detector.events[0].duration_s(), 4.1)
        self.assertIsNone(detector.events[0].interrupted_reason)

    def test_brief_recovery_cancelled_by_hysteresis_or_rebend(self):
        detector = HeadPoseDetector(Settings(), "test")
        for i in range(31):
            detector.update(i / 10, 25.0, pose_valid=True)
        # 0.5s recovery (< 1.0s) followed by 16.0 deg (hysteresis band) must not close event
        for i in range(31, 36):
            detector.update(i / 10, 12.0, pose_valid=True)
        self.assertEqual(detector.state, "recovering")
        detector.update(3.6, 16.0, pose_valid=True)
        self.assertEqual(detector.state, "head_down")
        self.assertIsNotNone(detector.active)
        self.assertEqual(len(detector.events), 1)

    def test_missing_or_invalid_pose_closes_active_event_at_last_valid_time(self):
        detector = HeadPoseDetector(Settings(), "test")
        for i in range(36):
            detector.update(i / 10, 23.0, pose_valid=True)
        self.assertFalse(detector.update(3.6, None, pose_valid=False))
        self.assertEqual(detector.state, "unobservable")
        self.assertIsNone(detector.active)
        self.assertAlmostEqual(detector.events[0].ended_s, 3.5)
        self.assertEqual(detector.events[0].interrupted_reason, "unobservable")
        # Out-of-range pitch (> 90 deg) is also treated as unobservable
        self.assertFalse(detector.update(3.7, 135.0, pose_valid=True))
        self.assertEqual(detector.state, "unobservable")

    def test_frame_gap_cannot_bridge_short_head_bends_and_closes_active_event(self):
        detector = HeadPoseDetector(Settings(), "test")
        for i in range(25):
            detector.update(i / 10, 22.0, pose_valid=True)
        # 0.6s gap (> 0.5s maximum_gap) resets candidate instead of confirming at 3.0s
        self.assertFalse(detector.update(3.0, 22.0, pose_valid=True))
        self.assertEqual(len(detector.events), 0)
        self.assertEqual(detector.candidate, 3.0)

        # Confirm event at 6.0s, then inject a 1.0s gap at 7.0s
        for i in range(31, 61):
            detector.update(i / 10, 22.0, pose_valid=True)
        self.assertEqual(len(detector.events), 1)
        self.assertIsNotNone(detector.active)
        self.assertFalse(detector.update(7.0, 22.0, pose_valid=True))
        self.assertEqual(detector.events[0].ended_s, 6.0)
        self.assertEqual(detector.events[0].interrupted_reason, "input_gap")
        self.assertEqual(detector.candidate, 7.0)

    def test_same_head_down_timing_across_frame_rates(self):
        for fps in (5, 10, 30):
            with self.subTest(fps=fps):
                detector = HeadPoseDetector(Settings(), "test")
                for i in range(6 * fps + 1):
                    t = i / fps
                    pitch = 24.0 if t < 4.0 else 8.0
                    detector.update(t, pitch, pose_valid=True)
                self.assertEqual(len(detector.events), 1)
                self.assertAlmostEqual(detector.events[0].confirmed_s, 3.0)
                self.assertAlmostEqual(detector.events[0].ended_s, 4.0)


class SessionHeadAndContractIntegrationTests(unittest.TestCase):
    def test_head_down_triggers_even_when_eyes_are_unobservable(self):
        session = calibrated()
        alarms = []
        for i in range(11, 45):
            frame = FrameMeasurement(
                session_id=session.session_id,
                timestamp_s=i / 10,
                eye_valid=False,
                pose_valid=True,
                ear_left=None,
                ear_right=None,
                pitch_delta_deg=25.0,
            )
            alarms.append(session.step_measurement(frame))
        self.assertEqual(sum(alarms), 1)
        self.assertEqual(len(session.head_detector.events), 1)
        self.assertEqual(len(session.detector.events), 0)
        self.assertEqual(session.pose_state, "head_down")
        self.assertGreater(session.pose_observable_seconds, 3.0)

    def test_pause_and_recalibration_interrupt_head_down_and_suppress_break_events(self):
        session = calibrated()
        for i in range(11, 45):
            session.step(i / 10, 0.3, 25.0, pose_valid=True)
        self.assertEqual(len(session.head_detector.events), 1)
        session.toggle_pause(4.5)
        self.assertAlmostEqual(session.head_detector.events[0].ended_s, 4.4)
        self.assertEqual(session.head_detector.events[0].interrupted_reason, "pause")

        # Head bend during break must not create a new event
        for i in range(46, 90):
            self.assertFalse(session.step(i / 10, 0.3, 30.0, pose_valid=True))
        self.assertEqual(len(session.head_detector.events), 1)

        # Resume and start another head_down event, then recalibrate
        session.toggle_pause(9.0)
        for i in range(91, 125):
            session.step(i / 10, 0.3, 25.0, pose_valid=True)
        self.assertEqual(len(session.head_detector.events), 2)
        session.recalibrate(12.5)
        self.assertAlmostEqual(session.head_detector.events[1].ended_s, 12.4)
        self.assertEqual(session.head_detector.events[1].interrupted_reason, "recalibration")

    def test_overlapping_eye_closure_and_head_down_use_union_seconds_and_export_cleanly(self):
        session = calibrated()
        # From 1.1s to 5.1s: both eyes closed (0.1) and head down (25 deg)
        for i in range(11, 52):
            session.step(i / 10, 0.1, 25.0, pose_valid=True)
        session.finish(5.1)

        snap = session.snapshot()
        summary = session.summary()
        self.assertEqual(summary.eye_closure_count, 1)
        self.assertEqual(summary.head_down_count, 1)
        self.assertAlmostEqual(summary.eye_closure_seconds, 4.0)
        self.assertAlmostEqual(snap["head_down_seconds"], 4.0)
        self.assertAlmostEqual(snap["combined_event_seconds"], 4.0)
        self.assertLessEqual(snap["combined_event_seconds"], summary.observable_seconds)
        self.assertEqual(len(summary.windows), 1)
        self.assertEqual(summary.windows[0]["eye_closure_count"], 1)

        # Disjoint + partial overlap helper check
        self.assertAlmostEqual(
            merged_interval_seconds([(0.0, 3.0), (2.0, 5.0), (6.0, 7.0)]),
            6.0,
        )

        with tempfile.TemporaryDirectory() as folder:
            store = SessionStore(Path(folder) / "session.sqlite3")
            try:
                store.save(session)
                summary_path, csv_path = store.export(session.session_id, Path(folder) / "export")
                exported_json = json.loads(summary_path.read_text(encoding="utf-8"))
                self.assertEqual(exported_json["summary"]["head_down_count"], 1)
                self.assertEqual(exported_json["summary"]["schema_version"], SCHEMA_VERSION)
                with csv_path.open(encoding="utf-8-sig") as source:
                    rows = list(csv.DictReader(source))
                self.assertEqual({row["type"] for row in rows}, {"eye_closure_suspected", "head_down"})
                self.assertTrue(all(row["config_version"] == DEFAULT_CONFIG_VERSION for row in rows))
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
