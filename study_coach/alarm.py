"""A non-blocking one-shot sound for each newly confirmed event."""

from pathlib import Path
import subprocess
import sys


class Alarm:
    def __init__(self, muted=False):
        self.muted = muted
        self.process = None
        self.error = None

    def ring(self):
        self.check()
        if self.muted:
            return
        if self.process is not None and self.process.poll() is None:
            return
        sound = Path("/System/Library/Sounds/Glass.aiff")
        try:
            if sys.platform == "darwin" and sound.is_file():
                self.process = subprocess.Popen(
                    ["/usr/bin/afplay", "-v", "0.4", str(sound)],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
            else:
                self.error = "이 환경에서는 소리 재생을 지원하지 않습니다. 화면 알림을 확인하세요."
        except OSError as exc:
            self.error = str(exc)

    def check(self):
        if self.process is not None:
            result = self.process.poll()
            if result is not None:
                if result != 0:
                    self.error = "소리 재생에 실패했습니다. 출력 장치와 볼륨을 확인하세요."
                self.process = None

    def close(self):
        if self.process is not None:
            if self.process.poll() is None:
                self.process.terminate()
            try:
                self.process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=1)
            self.process = None
