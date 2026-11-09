"""First local face-landmark preview. No recording and no drowsiness judgment."""

import argparse
import json
import math
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parent
MODEL_PATH = ROOT / "models" / "face_landmarker.task"
EYE_POINTS = (33, 160, 158, 133, 153, 144, 362, 385, 387, 263, 373, 380)


def arguments():
    parser = argparse.ArgumentParser(description="로컬 카메라·영상의 얼굴과 눈 위치 확인")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--camera", type=int, default=0, help="카메라 번호 (기본: 0)")
    source.add_argument("--video", type=Path, help="기존 영상 파일")
    source.add_argument("--image", type=Path, help="기존 이미지 파일")
    parser.add_argument("--headless", action="store_true", help="화면 없이 읽기·추론 확인")
    parser.add_argument("--max-frames", type=int, help="지정 프레임 수 후 자동 종료")
    parser.add_argument("--model", type=Path, default=MODEL_PATH)
    args = parser.parse_args()
    if args.max_frames is not None and args.max_frames < 1:
        parser.error("--max-frames는 1 이상이어야 합니다.")
    if args.headless and not (args.video or args.image or args.max_frames):
        parser.error("화면 없는 카메라 시험에는 --max-frames가 필요합니다.")
    return args


def annotate(cv2, frame, landmarks, fps, mirror):
    """Render only the current result, so missing faces never retain old markers."""
    height, width = frame.shape[:2]
    for i, point in enumerate(landmarks):
        x, y = round(point.x * width), round(point.y * height)
        if 0 <= x < width and 0 <= y < height:
            eye = i in EYE_POINTS
            cv2.circle(frame, (x, y), 3 if eye else 1,
                       (0, 220, 255) if eye else (100, 210, 100), -1)
    if mirror:
        frame = cv2.flip(frame, 1)
    cv2.rectangle(frame, (0, 0), (width, 90), (25, 25, 25), -1)
    status = "Face found - eye points in yellow" if landmarks else "No face detected - look at the camera"
    cv2.putText(frame, status, (12, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.56,
                (120, 230, 120) if landmarks else (80, 180, 255), 1, cv2.LINE_AA)
    cv2.putText(frame, f"{fps:.1f} FPS | Q / Esc: quit | No recording", (12, 56),
                cv2.FONT_HERSHEY_SIMPLEX, 0.52, (235, 235, 235), 1, cv2.LINE_AA)
    cv2.putText(frame, "Landmarks only - not a drowsiness detector", (12, 78),
                cv2.FONT_HERSHEY_SIMPLEX, 0.45, (190, 190, 190), 1, cv2.LINE_AA)
    return frame


def run(args):
    cache_dir = ROOT / ".cache" / "matplotlib"
    cache_dir.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("MPLCONFIGDIR", str(cache_dir))
    try:
        import cv2
        import mediapipe as mp
    except ImportError as exc:
        raise RuntimeError("필요한 라이브러리가 없습니다. docs/first-run.md의 설치 절차를 확인하세요.") from exc
    if not args.model.is_file():
        raise RuntimeError("얼굴 모델이 없습니다. 가상환경의 Python으로 scripts/download_model.py를 실행하세요. docs/cross-platform.md를 확인하세요.")
    source_path = args.image or args.video
    if source_path and not source_path.is_file():
        raise RuntimeError(f"입력 파일을 찾을 수 없습니다: {source_path}")

    vision = mp.tasks.vision
    options = vision.FaceLandmarkerOptions(
        base_options=mp.tasks.BaseOptions(model_asset_buffer=args.model.read_bytes()),
        running_mode=vision.RunningMode.IMAGE if args.image else vision.RunningMode.VIDEO,
        num_faces=1,
        min_face_detection_confidence=0.5,
        min_face_presence_confidence=0.5,
        min_tracking_confidence=0.5,
    )
    capture = None
    count = detected = 0
    started = time.monotonic()
    last_timestamp = -1
    source_fps = None
    window = "Study Coach - Face Preview"
    try:
        with vision.FaceLandmarker.create_from_options(options) as landmarker:
            print("얼굴 모델 준비 완료.", flush=True)
            if not args.image:
                if args.video:
                    capture = cv2.VideoCapture(str(args.video))
                    source_fps = capture.get(cv2.CAP_PROP_FPS)
                    if not math.isfinite(source_fps) or source_fps <= 0:
                        raise RuntimeError("영상 FPS를 읽지 못했습니다. 시간 정보가 있는 다른 영상을 사용하세요.")
                else:
                    backend = cv2.CAP_AVFOUNDATION if sys.platform == "darwin" else cv2.CAP_ANY
                    print(f"카메라 {args.camera} 연결 중. 최초 권한 허용 후에는 다시 실행해야 할 수 있습니다.", flush=True)
                    capture = cv2.VideoCapture(args.camera, backend)
                    capture.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
                    capture.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
                if not capture.isOpened():
                    raise RuntimeError("입력을 열지 못했습니다. 카메라 권한·다른 앱의 카메라 사용·카메라 번호를 확인하세요.")
            if not args.headless:
                print("미리보기 창을 엽니다.", flush=True)
                cv2.namedWindow(window, cv2.WINDOW_NORMAL)
                cv2.resizeWindow(window, 800, 600)
            started = time.monotonic()
            failed_since = None
            while True:
                if args.image:
                    frame = cv2.imread(str(args.image))
                    ok = frame is not None
                else:
                    ok, frame = capture.read()
                if not ok:
                    if args.video and count:
                        print("영상 입력 종료. 파일 손상이 있으면 재생 시간과 처리 프레임 수도 확인하세요.")
                        break
                    if args.image or args.video:
                        raise RuntimeError("입력 파일에서 이미지를 읽지 못했습니다.")
                    failed_since = failed_since or time.monotonic()
                    if time.monotonic() - failed_since > 2:
                        raise RuntimeError("카메라 프레임이 2초 동안 들어오지 않았습니다. 연결과 권한을 확인한 뒤 다시 실행하세요.")
                    if not args.headless and cv2.waitKey(30) & 0xFF in (27, ord("q")):
                        break
                    time.sleep(0.02)
                    continue
                failed_since = None
                if frame.shape[1] > 960:
                    ratio = 960 / frame.shape[1]
                    frame = cv2.resize(frame, (960, round(frame.shape[0] * ratio)))
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
                if args.image:
                    result = landmarker.detect(mp_image)
                else:
                    # File timestamps use media time, not the speed of this computer.
                    if args.video:
                        media_ms = capture.get(cv2.CAP_PROP_POS_MSEC)
                        expected_ms = count * 1000 / source_fps
                        timestamp = round(media_ms) if math.isfinite(media_ms) and media_ms > last_timestamp else round(expected_ms)
                    else:
                        timestamp = int((time.monotonic() - started) * 1000)
                    timestamp = max(last_timestamp + 1, timestamp)
                    last_timestamp = timestamp
                    result = landmarker.detect_for_video(mp_image, timestamp)
                landmarks = result.face_landmarks[0] if result.face_landmarks else []
                count += 1
                detected += bool(landmarks)
                elapsed = max(time.monotonic() - started, 0.001)
                if not args.headless:
                    display = annotate(cv2, frame, landmarks, count / elapsed, mirror=not bool(source_path))
                    cv2.imshow(window, display)
                    key = cv2.waitKey(0 if args.image else 1) & 0xFF
                    if key in (27, ord("q"), ord("Q")):
                        break
                    if cv2.getWindowProperty(window, cv2.WND_PROP_VISIBLE) < 1:
                        break
                if args.image or (args.max_frames and count >= args.max_frames):
                    break
    finally:
        if capture is not None:
            capture.release()
        if not args.headless:
            cv2.destroyAllWindows()
    elapsed = max(time.monotonic() - started, 0.001)
    print(json.dumps({"frames": count, "frames_with_face": detected,
                      "elapsed_seconds": round(elapsed, 3),
                      "processing_fps": round(count / elapsed, 2),
                      "recording": False}, ensure_ascii=False))


def main():
    args = arguments()
    print("얼굴·눈 위치 확인을 시작합니다. 영상 저장·외부 전송은 하지 않습니다.", flush=True)
    print("화면에서 Q 또는 Esc를 누르면 종료됩니다. 졸음 판정은 다음 단계입니다.", flush=True)
    try:
        run(args)
    except KeyboardInterrupt:
        print("사용자가 종료했습니다.")
    except Exception as exc:
        print(f"실행 실패: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
