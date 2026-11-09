"""Run the real preview process on small generated files; never open the camera."""

import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]


class PreviewTests(unittest.TestCase):
    def invoke(self, *args):
        return subprocess.run(
            [sys.executable, str(ROOT / "camera_preview.py"), *map(str, args)],
            cwd=ROOT, capture_output=True, text=True, timeout=30,
        )

    def summary(self, completed):
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return json.loads(completed.stdout.splitlines()[-1])

    def test_blank_image_is_unobserved_and_not_a_fake_face(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "blank.png"
            self.assertTrue(cv2.imwrite(str(path), np.zeros((480, 640, 3), dtype=np.uint8)))
            result = self.summary(self.invoke("--image", path, "--headless"))
            self.assertEqual(result["frames"], 1)
            self.assertEqual(result["frames_with_face"], 0)
            self.assertFalse(result["recording"])

    def test_video_limit_and_end_of_file(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "blank.avi"
            writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 10, (320, 240))
            self.assertTrue(writer.isOpened())
            try:
                for _ in range(12):
                    writer.write(np.zeros((240, 320, 3), dtype=np.uint8))
            finally:
                writer.release()
            limited = self.summary(self.invoke("--video", path, "--headless", "--max-frames", 5))
            self.assertEqual(limited["frames"], 5)
            complete = self.summary(self.invoke("--video", path, "--headless"))
            self.assertEqual(complete["frames"], 12)
            self.assertEqual(complete["frames_with_face"], 0)

    def test_missing_input_exits_with_actionable_error(self):
        result = self.invoke("--image", ROOT / "does-not-exist.png", "--headless")
        self.assertEqual(result.returncode, 1)
        self.assertIn("입력 파일을 찾을 수 없습니다", result.stderr)

    def test_headless_camera_must_have_a_stop_condition(self):
        result = self.invoke("--headless")
        self.assertEqual(result.returncode, 2)
        self.assertIn("--max-frames", result.stderr)

    def test_model_loads_from_path_with_korean_and_spaces(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            model = root / "한글 model" / "face.task"
            model.parent.mkdir()
            shutil.copyfile(ROOT / "models" / "face_landmarker.task", model)
            image = root / "blank.png"
            self.assertTrue(cv2.imwrite(str(image), np.zeros((480, 640, 3), dtype=np.uint8)))
            result = self.summary(self.invoke("--image", image, "--model", model, "--headless"))
            self.assertEqual(result["frames_with_face"], 0)


if __name__ == "__main__":
    unittest.main()
