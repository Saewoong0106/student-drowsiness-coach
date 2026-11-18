"""Exercise the real study process, database, and report without a camera."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import cv2
import numpy as np

from study_coach.storage import SessionStore

ROOT = Path(__file__).resolve().parents[1]


class StudyCliTests(unittest.TestCase):
    def invoke(self, script, *args):
        return subprocess.run(
            [sys.executable, str(ROOT / script), *map(str, args)],
            cwd=ROOT, capture_output=True, text=True, timeout=30,
        )

    def test_video_eof_persists_no_face_session_and_report_exports(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            video, db = root / "blank.avi", root / "session.sqlite3"
            writer = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*"MJPG"), 10, (320, 240))
            self.assertTrue(writer.isOpened())
            try:
                for _ in range(12):
                    writer.write(np.zeros((240, 320, 3), dtype=np.uint8))
            finally:
                writer.release()
            result = self.invoke("study_session.py", "--video", video, "--headless", "--db", db)
            self.assertEqual(result.returncode, 0, result.stderr)
            summary = json.loads(result.stdout.splitlines()[-1])
            self.assertEqual(summary["status"], "completed")
            self.assertEqual(summary["eye_closure_count"], 0)
            self.assertIsNone(summary["baseline"])
            self.assertIsNone(summary["eye_closure_ratio"])
            self.assertEqual(summary["study_seconds"], 0)
            store = SessionStore(db)
            try:
                stored, events = store.read(summary["session_id"])
                self.assertEqual(stored, summary)
                self.assertEqual(events, [])
            finally:
                store.close()
            report = self.invoke("session_report.py", "--db", db, "--export", root / "export")
            self.assertEqual(report.returncode, 0, report.stderr)
            self.assertIn("측정 불가", report.stdout)
            self.assertTrue((root / "export" / f"{summary['session_id']}.json").is_file())

    def test_missing_video_does_not_create_session_database(self):
        with tempfile.TemporaryDirectory() as folder:
            db = Path(folder) / "session.sqlite3"
            result = self.invoke("study_session.py", "--video", Path(folder) / "missing.mp4", "--headless", "--db", db)
            self.assertEqual(result.returncode, 1)
            self.assertIn("입력 영상 파일이 없습니다", result.stderr)
            self.assertFalse(db.exists())

    def test_headless_camera_requires_frame_limit(self):
        result = self.invoke("study_session.py", "--headless")
        self.assertEqual(result.returncode, 2)
        self.assertIn("--max-frames", result.stderr)

    def test_invalid_database_reports_actionable_failure(self):
        with tempfile.TemporaryDirectory() as folder:
            db = Path(folder) / "invalid.sqlite3"
            db.write_text("not a database")
            result = self.invoke("session_report.py", "--db", db)
            self.assertEqual(result.returncode, 1)
            self.assertIn("기록 조회 실패", result.stderr)


if __name__ == "__main__":
    unittest.main()
