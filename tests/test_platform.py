"""Platform boundaries: sound failures, Korean fonts, and headless rendering."""

import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np

from study_coach.alarm import Alarm
from study_coach.eyes import EyeMeasurement
from study_coach.session import StudySession
from study_coach.view import SessionView, load_fonts


class AlarmTests(unittest.TestCase):
    def windows_sound(self):
        return SimpleNamespace(PlaySound=Mock(), SND_ALIAS=1, SND_ASYNC=2, SND_NOSTOP=4)

    def test_windows_sound_is_async_and_stopped_on_close(self):
        sound = self.windows_sound()
        with patch("study_coach.alarm.sys.platform", "win32"), patch.dict("sys.modules", winsound=sound), patch("study_coach.alarm.subprocess.Popen") as popen:
            alarm = Alarm()
            alarm.error = "previous failure"
            alarm.ring()
            sound.PlaySound.assert_called_once_with("SystemExclamation", 7)
            popen.assert_not_called()
            self.assertIsNone(alarm.error)
            alarm.close()
            sound.PlaySound.assert_called_with(None, 0)
            self.assertIsNone(alarm.windows_sound)

    def test_windows_sound_failure_does_not_stop_detection(self):
        sound = self.windows_sound()
        sound.PlaySound.side_effect = RuntimeError("no audio device")
        with patch("study_coach.alarm.sys.platform", "win32"), patch.dict("sys.modules", winsound=sound):
            alarm = Alarm()
            alarm.ring()
            self.assertIn("no audio device", alarm.error)
            alarm.close()

    def test_muted_windows_alarm_does_not_play(self):
        sound = self.windows_sound()
        with patch("study_coach.alarm.sys.platform", "win32"), patch.dict("sys.modules", winsound=sound):
            alarm = Alarm(muted=True)
            alarm.ring()
            alarm.close()
            sound.PlaySound.assert_not_called()

    def test_macos_does_not_overlap_sound_and_reports_process_failure(self):
        process = Mock()
        process.poll.return_value = None
        with patch("study_coach.alarm.sys.platform", "darwin"), patch("study_coach.alarm.Path.is_file", return_value=True), patch("study_coach.alarm.subprocess.Popen", return_value=process) as popen:
            alarm = Alarm()
            alarm.ring()
            alarm.ring()
            popen.assert_called_once()
            self.assertEqual(popen.call_args.args[0][0], "/usr/bin/afplay")
            process.poll.return_value = 1
            alarm.check()
            self.assertIsNotNone(alarm.error)
            self.assertIsNone(alarm.process)


class FontTests(unittest.TestCase):
    def test_windows_uses_malgun_from_actual_windows_directory(self):
        with tempfile.TemporaryDirectory() as folder:
            font = Path(folder) / "Fonts" / "malgun.ttf"
            font.parent.mkdir()
            font.touch()
            large, small = object(), object()
            with patch("study_coach.view.sys.platform", "win32"), patch.dict(os.environ, WINDIR=folder), patch("study_coach.view.ImageFont.truetype", side_effect=[large, small]) as loader:
                self.assertEqual(load_fonts(), (True, large, small))
                self.assertEqual(loader.call_args_list[0].args, (str(font), 27))
                self.assertEqual(loader.call_args_list[1].args, (str(font), 19))

    def test_missing_windows_font_falls_back_to_readable_english(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch("study_coach.view.sys.platform", "win32"), patch.dict(os.environ, WINDIR=folder):
                korean, large, small = load_fonts()
            self.assertFalse(korean)
            self.assertGreater(large.getlength("Study"), 0)
            self.assertGreater(small.getlength("Sound error"), 0)

    def test_broken_windows_font_also_falls_back(self):
        with tempfile.TemporaryDirectory() as folder:
            font = Path(folder) / "Fonts" / "malgun.ttf"
            font.parent.mkdir()
            font.write_bytes(b"not a font")
            with patch("study_coach.view.sys.platform", "win32"), patch.dict(os.environ, WINDIR=folder):
                korean, _, _ = load_fonts()
            self.assertFalse(korean)

    def test_panel_renders_without_opening_camera_or_window(self):
        view = SessionView()
        session = StudySession()
        alarm = Alarm(muted=True)
        alarm.error = "synthetic failure"
        image = view.draw(np.zeros((480, 640, 3), dtype=np.uint8), [], EyeMeasurement(None, None), session, alarm, 10)
        self.assertEqual(image.shape, (850, 800, 3))
        self.assertGreater(np.count_nonzero(image[600:]), 0)


if __name__ == "__main__":
    unittest.main()
