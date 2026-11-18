"""Check local training device (MPS/CUDA/CPU) and verify 3-assignee contract handoffs (#21)."""

import argparse
import importlib.metadata
import json
from pathlib import Path
import platform
import sys
import tempfile
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from study_coach.coaching import generate_coaching_for_session
from study_coach.contracts import (
    CoachingResult,
    Event,
    FrameMeasurement,
    SCHEMA_VERSION,
    SessionSummary,
)
from study_coach.eyes import LEFT_EYE, RIGHT_EYE, measure_frame
from study_coach.session import Settings, StudySession
from study_coach.storage import SessionStore


def _pkg_version(dist_name: str) -> str:
    try:
        return importlib.metadata.version(dist_name)
    except importlib.metadata.PackageNotFoundError:
        return "not_installed"


def check_device_and_packages() -> dict:
    """Inspect OS, Python, runtime packages, and PyTorch MPS/CUDA/CPU availability."""
    packages = {
        "mediapipe": _pkg_version("mediapipe"),
        "numpy": _pkg_version("numpy"),
        "opencv-contrib-python": _pkg_version("opencv-contrib-python"),
        "Pillow": _pkg_version("Pillow"),
        "torch": _pkg_version("torch"),
        "transformers": _pkg_version("transformers"),
        "peft": _pkg_version("peft"),
    }

    torch_info = {
        "installed": False,
        "version": "not_installed",
        "mps_built": False,
        "mps_available": False,
        "cuda_available": False,
        "selected_training_device": "unverified_install_torch_first",
    }
    try:
        import torch

        mps_built = bool(hasattr(torch.backends, "mps") and torch.backends.mps.is_built())
        mps_avail = bool(hasattr(torch.backends, "mps") and torch.backends.mps.is_available())
        cuda_avail = bool(torch.cuda.is_available())
        if mps_avail:
            selected = "mps"
        elif cuda_avail:
            selected = "cuda"
        else:
            selected = "cpu"
        torch_info = {
            "installed": True,
            "version": getattr(torch, "__version__", "unknown"),
            "mps_built": mps_built,
            "mps_available": mps_avail,
            "cuda_available": cuda_avail,
            "selected_training_device": selected,
        }
    except ImportError:
        pass

    return {
        "platform": sys.platform,
        "os": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "python_version": platform.python_version(),
        "python_executable": sys.executable,
        "packages": packages,
        "pytorch_device": torch_info,
    }


def _synthetic_landmarks(open_eyes: bool = True):
    landmarks = [SimpleNamespace(x=0.5, y=0.5) for _ in range(478)]
    v_offset = 3.0 if open_eyes else 1.0
    for indices, origin_x in ((LEFT_EYE, 100.0), (RIGHT_EYE, 200.0)):
        pts = [
            (0.0, 0.0),
            (10.0, -v_offset),
            (20.0, -v_offset),
            (30.0, 0.0),
            (20.0, v_offset),
            (10.0, v_offset),
        ]
        for idx, (dx, dy) in zip(indices, pts):
            landmarks[idx] = SimpleNamespace(x=(origin_x + dx) / 640.0, y=(100.0 + dy) / 480.0)
    return landmarks


def verify_cross_module_contracts() -> dict:
    """Verify the exact synthetic handoff across #22 (vision) -> #5 (detection) -> #8 (coaching) -> #23 (app/storage)."""
    settings = Settings(calibration_seconds=1.0, calibration_samples=5)
    session = StudySession(settings=settings, source="contract_check", goal="데이터 계약 연동 확인")

    open_lm = _synthetic_landmarks(open_eyes=True)
    closed_lm = _synthetic_landmarks(open_eyes=False)

    # Simulate 0.0~1.0s calibration, 1.1~3.2s eye closure, 3.3~4.4s recovery, 4.5~7.6s head down (24 deg)
    for step_i in range(0, 77):
        t = round(step_i / 10.0, 2)
        is_closed = 1.1 <= t <= 3.2
        pitch_deg = 24.0 if 4.5 <= t <= 7.6 else 4.0
        lm = closed_lm if is_closed else open_lm
        frame = measure_frame(
            session.session_id,
            t,
            lm,
            640,
            480,
            pose_measurer=lambda _lm, _w, _h, p=pitch_deg: {
                "pitch_delta_deg": p,
                "pose_valid": True,
                "invalid_reason": None,
            },
        )
        assert isinstance(frame, FrameMeasurement)
        session.step_measurement(frame)

    session.finish(7.7, reason="user_exit")
    summary = session.summary()
    assert isinstance(summary, SessionSummary)

    events = [Event.from_dict(row) for row in session.event_rows()]
    coaching = generate_coaching_for_session(summary, mode="rule")
    assert isinstance(coaching, CoachingResult)
    assert coaching.matches_session(summary.session_id)

    with tempfile.TemporaryDirectory() as folder:
        db_path = Path(folder) / "contract_check.sqlite3"
        store = SessionStore(db_path)
        try:
            store.save(session)
            store.save_coaching(coaching)
            stored_summary_dict, stored_events = store.read(session.session_id)
            stored_coaching_dict = store.read_coaching(session.session_id)
            restored_summary = SessionSummary.from_dict(stored_summary_dict)
            restored_coaching = CoachingResult.from_dict(stored_coaching_dict)
        finally:
            store.close()

    return {
        "schema_version": SCHEMA_VERSION,
        "contracts_verified": True,
        "session_id": summary.session_id,
        "eye_closure_count": restored_summary.eye_closure_count,
        "head_down_count": restored_summary.head_down_count,
        "event_types": [e.type for e in events],
        "stored_event_count": len(stored_events),
        "coaching_status": restored_coaching.status,
        "coaching_matches_session": restored_coaching.matches_session(restored_summary.session_id),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="학습 장치 점검 및 3담당 모듈 데이터 계약 연동 확인 (#21)")
    parser.add_argument("--output", type=Path, help="점검 결과 JSON 저장 경로")
    args = parser.parse_args(argv)

    report = {
        "environment": check_device_and_packages(),
        "contract_handoff": verify_cross_module_contracts(),
    }
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
