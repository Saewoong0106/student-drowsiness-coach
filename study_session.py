"""Run an eye-closure study session and save only numerical local records."""

import argparse
import json
import math
import os
from pathlib import Path
import sys
import time

from study_coach.alarm import Alarm
from study_coach.eyes import EyeMeasurement, measure_eyes, measure_frame
from study_coach.session import Settings, StudySession
from study_coach.storage import SessionStore

ROOT = Path(__file__).resolve().parent
DEFAULT_DB = ROOT / "data" / "private" / "study.sqlite3"


def arguments():
    parser = argparse.ArgumentParser(description="눈 감김·알람·공부 기록 시제품")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--camera", type=int, default=0)
    source.add_argument("--video", type=Path)
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--max-frames", type=int)
    parser.add_argument("--no-sound", action="store_true")
    parser.add_argument("--goal", default="")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--model", type=Path, default=ROOT / "models" / "face_landmarker.task")
    args = parser.parse_args()
    if args.max_frames is not None and args.max_frames < 1:
        parser.error("--max-frames는 1 이상이어야 합니다.")
    if args.headless and not (args.video or args.max_frames):
        parser.error("카메라의 화면 없는 시험에는 --max-frames가 필요합니다.")
    return args


def run(args, pose_measurer=None):
    cache = ROOT / ".cache" / "matplotlib"
    cache.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("MPLCONFIGDIR", str(cache))
    import cv2
    import mediapipe as mp
    from study_coach.view import SessionView

    if not args.model.is_file():
        raise RuntimeError("모델이 없습니다. scripts/download_model.py를 실행하세요.")
    if args.video and not args.video.is_file():
        raise RuntimeError("입력 영상 파일이 없습니다.")
    capture = None
    store = None
    session = None
    alarm = Alarm(muted=args.no_sound or args.headless or bool(args.video))
    frame_count = 0
    start = None
    current_t = 0.0
    exit_reason = "user_exit"
    window = "Study Coach - Study Session"
    failure = None
    vision = mp.tasks.vision
    try:
        options = vision.FaceLandmarkerOptions(
            base_options=mp.tasks.BaseOptions(model_asset_buffer=args.model.read_bytes()),
            running_mode=vision.RunningMode.VIDEO, num_faces=1,
            min_face_detection_confidence=0.5, min_face_presence_confidence=0.5,
            min_tracking_confidence=0.5,
        )
        with vision.FaceLandmarker.create_from_options(options) as model:
            if args.video:
                capture = cv2.VideoCapture(str(args.video))
                source_fps = capture.get(cv2.CAP_PROP_FPS)
                if not math.isfinite(source_fps) or source_fps <= 0:
                    raise RuntimeError("영상의 시간 정보를 읽지 못했습니다.")
            else:
                backend = cv2.CAP_AVFOUNDATION if sys.platform == "darwin" else cv2.CAP_ANY
                capture = cv2.VideoCapture(args.camera, backend)
                capture.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
                capture.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
            if not capture.isOpened():
                raise RuntimeError("카메라·영상을 열지 못했습니다. 최초 카메라 권한 허용 후 다시 실행하세요.")
            session = StudySession(source="video" if args.video else "camera", goal=args.goal)
            store = SessionStore(args.db)
            store.save(session)
            view = SessionView()
            if not args.headless:
                cv2.namedWindow(window, cv2.WINDOW_NORMAL)
                cv2.resizeWindow(window, 900, 900)
            print("처음 10초 동안 눈을 뜨고 정면을 보세요. 보정 완료 후 공부 기록을 시작합니다.", flush=True)
            print("Space 휴식/재개 · C 재보정 · M 소리 · Q/Esc 저장·종료", flush=True)
            start = time.monotonic()
            last_mp_timestamp = -1
            last_save = start
            failed_since = None
            while True:
                ok, frame = capture.read()
                now = time.monotonic()
                if not ok:
                    if args.video:
                        exit_reason = "video_end"
                        if not frame_count:
                            raise RuntimeError("영상에서 첫 프레임을 읽지 못했습니다.")
                        break
                    current_t = now - start
                    session.step(current_t, None)
                    failed_since = failed_since or now
                    store.save(session)
                    if now - failed_since > 2:
                        raise RuntimeError("카메라 입력이 2초 동안 끊겼습니다. 기록은 저장하고 종료합니다.")
                    if not args.headless:
                        import numpy as np
                        blank = np.zeros((480, 640, 3), dtype=np.uint8)
                        cv2.imshow(window, view.draw(blank, [], EyeMeasurement(None, None), session, alarm, 0))
                        if cv2.waitKey(30) & 0xFF in (27, ord("q"), ord("Q")):
                            break
                    time.sleep(0.02)
                    continue
                failed_since = None
                if frame.shape[1] > 960:
                    frame = cv2.resize(frame, (960, round(frame.shape[0] * 960 / frame.shape[1])))
                if args.video:
                    media_ms = capture.get(cv2.CAP_PROP_POS_MSEC)
                    fallback_ms = frame_count * 1000 / source_fps
                    timestamp_ms = round(media_ms) if math.isfinite(media_ms) and media_ms > last_mp_timestamp else round(fallback_ms)
                    timestamp_ms = max(last_mp_timestamp + 1, timestamp_ms)
                    current_t = timestamp_ms / 1000
                else:
                    current_t = now - start
                    timestamp_ms = max(last_mp_timestamp + 1, int(current_t * 1000))
                last_mp_timestamp = timestamp_ms
                image = mp.Image(image_format=mp.ImageFormat.SRGB, data=cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
                result = model.detect_for_video(image, timestamp_ms)
                landmarks = result.face_landmarks[0] if result.face_landmarks else []
                measurement = measure_frame(
                    session.session_id,
                    current_t,
                    landmarks,
                    frame.shape[1],
                    frame.shape[0],
                    pose_measurer=pose_measurer or session.pose_measurer,
                )
                new_event = session.step_measurement(measurement)
                if new_event:
                    alarm.ring()
                    if session.last_triggered.get("eye_closure_suspected"):
                        print(f"졸음 의심 이벤트 {len(session.detector.events)}회 · {current_t:.2f}초", flush=True)
                    if session.last_triggered.get("head_down"):
                        print(f"고개 숙임 이벤트 {len(session.head_detector.events)}회 · {current_t:.2f}초", flush=True)
                alarm.check()
                frame_count += 1
                if new_event or now - last_save >= 1:
                    store.save(session)
                    last_save = now
                if not args.headless:
                    fps = frame_count / max(time.monotonic() - start, 0.001)
                    cv2.imshow(window, view.draw(frame, landmarks, measurement, session, alarm, fps, mirror=not bool(args.video)))
                    key = cv2.waitKey(1) & 0xFF
                    if key in (27, ord("q"), ord("Q")) or cv2.getWindowProperty(window, cv2.WND_PROP_VISIBLE) < 1:
                        break
                    if key == ord(" "):
                        session.toggle_pause(current_t)
                        store.save(session)
                    elif key in (ord("c"), ord("C")):
                        session.recalibrate(current_t)
                        store.save(session)
                    elif key in (ord("m"), ord("M")):
                        alarm.muted = not alarm.muted
                        if alarm.muted:
                            alarm.close()
                if args.max_frames and frame_count >= args.max_frames:
                    exit_reason = "frame_limit"
                    break
    except KeyboardInterrupt:
        exit_reason = "user_exit"
    except Exception as exc:
        exit_reason = "error"
        failure = exc
    finally:
        try:
            if session is not None and store is not None:
                if start is not None and not args.video:
                    current_t = max(current_t, time.monotonic() - start)
                session.finish(current_t, exit_reason)
                try:
                    store.save(session)
                    print("세션 기록 저장 완료: " + session.session_id, flush=True)
                    print(json.dumps(session.snapshot(), ensure_ascii=False), flush=True)
                finally:
                    store.close()
        except Exception as exc:
            print(f"마지막 기록 저장 실패: {exc}", file=sys.stderr)
            failure = failure or exc
        finally:
            if capture is not None:
                capture.release()
            alarm.close()
            if not args.headless:
                cv2.destroyAllWindows()
    if failure is not None:
        raise failure


def main():
    args = arguments()
    print("눈 감김·공부 기록을 시작합니다. 영상 대신 횟수와 시간만 로컬에 저장합니다.", flush=True)
    try:
        run(args)
    except Exception as exc:
        print(f"실행 실패: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
