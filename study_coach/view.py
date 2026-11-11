"""Korean preview panel below the camera, without covering the eyes."""

import os
from pathlib import Path
import sys

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from .eyes import LEFT_EYE, RIGHT_EYE


def duration(seconds):
    seconds = max(0, int(seconds))
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def load_fonts():
    """Use a system Korean font, falling back to English if it cannot load."""
    candidates = []
    if sys.platform == "darwin":
        candidates.append(Path("/System/Library/Fonts/AppleSDGothicNeo.ttc"))
    elif sys.platform == "win32":
        windows = Path(os.environ.get("WINDIR", r"C:\Windows"))
        candidates.append(windows / "Fonts" / "malgun.ttf")
    for font in candidates:
        if font.is_file():
            try:
                return True, ImageFont.truetype(str(font), 27), ImageFont.truetype(str(font), 19)
            except OSError:
                continue
    return False, ImageFont.load_default(size=24), ImageFont.load_default(size=17)


class SessionView:
    def __init__(self):
        self.korean, self.large, self.small = load_fonts()

    def draw(self, frame, landmarks, measurement, session, alarm, fps, mirror=True):
        frame = frame.copy()
        h, w = frame.shape[:2]
        eyes = LEFT_EYE + RIGHT_EYE
        for index in eyes:
            if index < len(landmarks):
                p = landmarks[index]
                cv2.circle(frame, (round(p.x * w), round(p.y * h)), 3, (0, 225, 255), -1)
        if mirror:
            frame = cv2.flip(frame, 1)
        width = 800
        frame = cv2.resize(frame, (width, round(h * width / w)))
        panel = np.full((250, width, 3), (28, 24, 21), dtype=np.uint8)
        canvas = np.vstack((frame, panel))
        image = Image.fromarray(cv2.cvtColor(canvas, cv2.COLOR_BGR2RGB))
        draw = ImageDraw.Draw(image)
        y = frame.shape[0] + 12
        state = session.state
        labels = {
            "calibrating": "보정 중 · 눈을 뜨고 정면을 봐 주세요",
            "paused": "휴식 중 · Space를 누르면 공부 재개",
            "normal": "정상 관측",
            "closing": "눈 감김 확인 중",
            "eyes_closed": "졸음 의심 · 눈 감김 2초 이상",
            "bending": "고개 숙임 확인 중",
            "head_down": "고개 숙임 · 3초 이상 지속",
            "recovering": "눈·자세 회복 확인 중",
            "unobservable": "측정 불가 · 얼굴과 눈을 확인해 주세요",
        }
        label = labels.get(state, state.replace("_", " ").title()) if self.korean else state.replace("_", " ").title()
        color = (255, 120, 105) if state in ("eyes_closed", "head_down") else (130, 225, 170)
        draw.text((18, y), label, font=self.large, fill=color)
        snapshot = session.snapshot()
        t = session.last_time or 0
        if session.baseline is None:
            elapsed = 0 if session.calibration_started is None else t - session.calibration_started
            remaining = max(0, session.settings.calibration_seconds - elapsed)
            hint = f"보정 남은 시간 {remaining:.1f}초 · 얼굴이 잘 보이는 상태를 유지하세요"
            if session.calibration_message.startswith("retry"):
                hint = "다시 보정 중 · 눈을 충분히 뜨고 카메라를 정면으로 보세요"
            if not self.korean:
                hint = f"Calibration: {remaining:.1f}s remaining. Keep eyes open."
        else:
            hint = f"공부 {duration(snapshot['study_seconds'])}   휴식 {duration(snapshot['break_seconds'])}   미측정 {duration(snapshot['unobservable_seconds'])}"
            if not self.korean:
                hint = f"Study {duration(snapshot['study_seconds'])} | Break {duration(snapshot['break_seconds'])} | Unknown {duration(snapshot['unobservable_seconds'])}"
        draw.text((18, y + 43), hint, font=self.small, fill=(235, 235, 235))
        value = "--" if measurement.average is None else f"{measurement.average:.3f}"
        baseline = "--" if session.baseline is None else f"{session.baseline:.3f}"
        ratio = "--" if session.baseline is None or measurement.average is None else f"{100 * measurement.average / session.baseline:.0f}%"
        details = f"EAR {value}   기준 {baseline}   평소 대비 {ratio}   {fps:.1f} FPS"
        if not self.korean:
            details = f"EAR {value} | Baseline {baseline} | Relative {ratio} | {fps:.1f} FPS"
        draw.text((18, y + 76), details, font=self.small, fill=(235, 235, 235))
        head_count = snapshot.get("head_down_count", 0)
        head_seconds = snapshot.get("head_down_seconds", 0.0)
        info = (
            f"졸음 의심 {snapshot['eye_closure_count']}회({snapshot['eye_closure_seconds']:.1f}초) · "
            f"고개 숙임 {head_count}회({head_seconds:.1f}초) · 소리 {'끔' if alarm.muted else '켬'}"
        )
        if not self.korean:
            info = (
                f"Eyes {snapshot['eye_closure_count']} ({snapshot['eye_closure_seconds']:.1f}s) | "
                f"Head {head_count} ({head_seconds:.1f}s) | Sound {'off' if alarm.muted else 'on'}"
            )
        draw.text((18, y + 109), info, font=self.small, fill=(235, 235, 235))
        controls = "Space 휴식/재개   C 재보정   M 소리   Q/Esc 종료·저장" if self.korean else "Space: pause | C: recalibrate | M: mute | Q/Esc: save & quit"
        draw.text((18, y + 148), controls, font=self.small, fill=(180, 200, 225))
        warning = "필기·고개 숙임·가림 구분은 미완성입니다. 영상은 저장하지 않습니다."
        if not self.korean:
            warning = "Head-down / occlusion not validated. No video recording."
        draw.text((18, y + 185), warning, font=self.small, fill=(180, 180, 180))
        if alarm.error:
            draw.text((18, y + 214), "소리 재생 오류 · 화면 알림을 확인하세요" if self.korean else "Sound error: use visual alert", font=self.small, fill=(255, 160, 110))
        return cv2.cvtColor(np.asarray(image), cv2.COLOR_RGB2BGR)
