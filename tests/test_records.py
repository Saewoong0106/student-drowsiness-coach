"""Record/coaching integration with temporary SQLite; no real camera/model/data."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from study_coach.coaching import RuleBasedCoach
from study_coach.records import RecordService, coaching_lock, summary_text
from study_coach.session import StudySession
from study_coach.storage import SessionStore


class RecordFixture:
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / "study.sqlite3"
        self.service = RecordService(self.db)
        self.a = StudySession(goal="수학", source="synthetic")
        self.a.finish(0)
        self.b = StudySession(goal="영어", source="synthetic")
        self.b.finish(0)
        store = SessionStore(self.db)
        store.save(self.a)
        store.save(self.b)
        store.close()


class RecordsTests(RecordFixture, unittest.TestCase):
    def test_missing_db_does_not_create_file(self):
        path = Path(self.temp.name) / "absent" / "study.sqlite3"
        service = RecordService(path)
        self.assertEqual(service.list_sessions(), [])
        with self.assertRaises(FileNotFoundError):
            service.generate("missing")
        self.assertFalse(path.parent.exists())

    def test_generation_survives_reopen_and_exports_existing_contract(self):
        result = self.service.generate(self.a.session_id)
        reopened = RecordService(self.db)
        self.assertEqual(reopened.read(self.a.session_id)[2], result.to_dict())
        self.assertIsNone(reopened.read(self.b.session_id)[2])
        paths = reopened.export(self.a.session_id, Path(self.temp.name) / "export")
        payload = json.loads(paths[0].read_text(encoding="utf-8"))
        self.assertEqual(payload["coaching"], result.to_dict())
        self.assertEqual(payload["summary"]["session_id"], self.a.session_id)
        self.assertIn("event_id", paths[1].read_text(encoding="utf-8-sig"))

    def test_running_and_interrupted_sessions_cannot_generate(self):
        for state in ("running", "interrupted"):
            self.a.status = state
            store = SessionStore(self.db)
            store.save(self.a)
            store.close()
            with self.assertRaises(ValueError):
                self.service.generate(self.a.session_id)
            self.assertIsNone(self.service.read(self.a.session_id)[2])

    def test_model_failure_saves_explicit_fallback(self):
        class BrokenCoach:
            def generate(self, summary):
                raise RuntimeError("model unavailable")
        result = self.service.generate(self.a.session_id, coach=BrokenCoach())
        self.assertEqual(result.status, "failed")
        self.assertTrue(result.is_fallback)
        self.assertIn("model unavailable", result.validation_errors)
        self.assertIn("규칙 기반 대체 안내", result.display_text)
        self.assertEqual(self.service.read(self.a.session_id)[2], result.to_dict())

    def test_wrong_session_result_is_replaced_with_safe_fallback(self):
        class WrongCoach:
            def generate(self, summary):
                return replace(RuleBasedCoach().generate(summary), session_id="wrong")
        result = self.service.generate(self.a.session_id, coach=WrongCoach())
        self.assertEqual(result.session_id, self.a.session_id)
        self.assertIn("session_id_mismatch", result.validation_errors)
        self.assertIsNone(self.service.read(self.b.session_id)[2])

    def test_duplicate_generation_is_blocked_and_running_status_is_visible(self):
        started, release = threading.Event(), threading.Event()
        class SlowCoach:
            def generate(self, summary):
                started.set()
                if not release.wait(5):
                    raise RuntimeError("test timeout")
                return RuleBasedCoach().generate(summary)
        with ThreadPoolExecutor() as pool:
            future = pool.submit(self.service.generate, self.a.session_id, coach=SlowCoach())
            try:
                self.assertTrue(started.wait(5))
                self.assertEqual(self.service.read(self.a.session_id)[2]["status"], "running")
                with self.assertRaises(RuntimeError):
                    RecordService(self.db).generate(self.a.session_id)
                self.assertIsNone(self.service.read(self.b.session_id)[2])
            finally:
                release.set()
            self.assertEqual(future.result(timeout=5).status, "succeeded")

    def test_changed_statistics_reject_late_result(self):
        session, db = self.a, self.db
        class EditingCoach:
            def generate(self, summary):
                session.goal = "변경된 목표"
                store = SessionStore(db)
                store.save(session)
                store.close()
                return RuleBasedCoach().generate(summary)
        with self.assertRaisesRegex(ValueError, "변경"):
            self.service.generate(self.a.session_id, coach=EditingCoach())
        saved = self.service.read(self.a.session_id)[2]
        self.assertEqual(saved["status"], "failed")
        self.assertEqual(saved["validation_errors"], ["session_changed"])

    def test_lock_is_released_after_exception(self):
        with self.assertRaises(ValueError):
            with coaching_lock(self.db):
                raise ValueError("test")
        self.assertEqual(self.service.generate(self.a.session_id).status, "succeeded")

    def test_failed_save_is_reported_and_does_not_claim_success(self):
        original = SessionStore.save_coaching
        def fail_final(store, result):
            if result.status == "succeeded":
                raise OSError("disk full")
            original(store, result)
        with patch.object(SessionStore, "save_coaching", fail_final):
            with self.assertRaisesRegex(OSError, "disk full"):
                self.service.generate(self.a.session_id)
        # A previous interrupted job can be explicitly regenerated.
        self.assertEqual(self.service.generate(self.a.session_id).status, "succeeded")

    def test_zero_observation_and_existing_union_are_displayed(self):
        summary = self.a.snapshot()
        summary["combined_event_seconds"] = 5
        text = summary_text(summary)
        self.assertIn("측정 불가", text)
        self.assertIn("한 번만 센 시간: 5.0초", text)

    def test_export_rejects_path_ids(self):
        with self.assertRaises(ValueError):
            self.service.export("../outside", self.temp.name)


class RecordsViewTests(RecordFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        try:
            import tkinter as tk
        except ImportError as exc:
            self.skipTest(f"Tk unavailable: {exc}")
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(f"Tk display unavailable: {exc}")
        self.root.withdraw()
        from study_coach.records_view import RecordsApp
        self.app = RecordsApp(self.root, self.db, Path(self.temp.name) / "adapter")
        self.addCleanup(self.app.close)

    def test_view_filter_and_empty_selection_clear_detail(self):
        self.app.search.set("수학")
        self.assertEqual(self.app.sessions.get_children(), (self.a.session_id,))
        self.app.search.set("없는 목표")
        self.assertIsNone(self.app.selected_id)
        self.assertIn("표시할 기록", self.app.summary.get("1.0", "end"))
        self.assertIn("disabled", self.app.generate_button.state())

    def test_view_late_completion_keeps_current_session(self):
        from concurrent.futures import Future
        result = self.service.generate(self.a.session_id)
        future = Future()
        future.set_result(result)
        self.app.future = future
        self.app.job_session = self.a.session_id
        self.app.sessions.selection_set(self.b.session_id)
        self.app.select_session()
        self.app.poll()
        self.assertEqual(self.app.selected_id, self.b.session_id)
        self.assertIn("아직 코칭을 생성하지", self.app.coaching.get("1.0", "end"))
