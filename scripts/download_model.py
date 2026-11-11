"""Download the official MediaPipe Face Landmarker model (no user data)."""

import hashlib
from pathlib import Path
import sys
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
URL = "https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task"
DESTINATION = ROOT / "models" / "face_landmarker.task"
EXPECTED_SHA256 = "64184e229b263107bc2b804c6625db1341ff2bb731874b0bcc2fe6544e0bc9ff"


def main():
    DESTINATION.parent.mkdir(exist_ok=True)
    partial = DESTINATION.with_suffix(".download")
    try:
        print("Google 공식 얼굴 모델을 다운로드합니다.")
        with urllib.request.urlopen(URL, timeout=60) as response, partial.open("wb") as output:
            while chunk := response.read(1024 * 1024):
                output.write(chunk)
        if partial.stat().st_size < 1_000_000:
            raise ValueError("다운로드 파일 크기가 예상보다 작습니다.")
        digest = hashlib.sha256(partial.read_bytes()).hexdigest()
        if digest != EXPECTED_SHA256:
            raise ValueError("공식 버전 1의 확인된 SHA256과 다릅니다. 기존 모델은 변경하지 않습니다.")
        partial.replace(DESTINATION)
        print(f"저장 완료: {DESTINATION}\nSHA256: {digest}")
    except Exception as exc:
        partial.unlink(missing_ok=True)
        print(f"모델 다운로드 실패: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
