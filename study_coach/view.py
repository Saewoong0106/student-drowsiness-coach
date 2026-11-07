"""Korean preview panel below the camera, without covering the eyes."""

from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from .eyes import LEFT_EYE, RIGHT_EYE


def duration(seconds):
    seconds = max(0, int(seconds))
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


class SessionView:
    def __init__(self):
        font = Path("/System/Library/Fonts/AppleSDGothicNeo.ttc")
        self.korean = font.is_file()
        self.large = ImageFont.truetype(str(font), 27) if self.korean else ImageFont.load_default(size=24)
        self.small = ImageFont.truetype(str(font), 19) if self.korean else ImageFont.load_default(size=17)

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
        label = {
            "calibrating": "보정 중 · 눈을 뜨고 정면을 봐 주세요",
            "paused": "휴식 중 · Space를 누르면 공부 재개",
            "normal": "정상 관측",
            "closing": "눈 감김 확인 중",
            "eyes_closed": "졸음 의심 · 눈 감김 2초 이상",
            "recovering": "눈 뜸 회복 확인 중",
            "unobservable": "측정 불가 · 얼굴과 눈을 확인해 주세요",
        }[state] if self.korean else state.replace("_", " ").title()
        color = (255, 120, 105) if state == "eyes_closed" else (130, 225, 170)
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
        info = f"졸음 의심 {snapshot['eye_closure_count']}회 · 눈 감김 {snapshot['eye_closure_seconds']:.1f}초 · 소리 {'끔' if alarm.muted else '켬'}"
        if not self.korean:
            info = f"Events {snapshot['eye_closure_count']} | Closed {snapshot['eye_closure_seconds']:.1f}s | Sound {'off' if alarm.muted else 'on'}"
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
