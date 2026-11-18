"""Record-screen operations using the existing storage and coaching contracts."""

from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime
import os
from pathlib import Path

from .coaching import (
    DEFAULT_BASE_MODEL_ID, DEFAULT_DATASET_VERSION, DEFAULT_MODEL_REVISION,
    DEFAULT_RULE_MODEL_ID, build_fallback_result, generate_coaching_for_session,
)
from .contracts import CoachingResult, SessionSummary
from .storage import SessionStore


@contextmanager
def coaching_lock(db_path):
    """Serialize dashboard generation across threads/processes; released on exit/crash."""
    lock_path = Path(str(Path(db_path).resolve()) + ".coaching.lock")
    with lock_path.open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise RuntimeError("다른 창에서 코칭을 생성 중입니다. 완료 후 다시 시도하세요.") from exc
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


class RecordService:
    def __init__(self, db_path):
        self.db_path = Path(db_path).resolve()

    @contextmanager
    def _store(self):
        # Browsing must not create an empty DB or hide a wrong path.
        if not self.db_path.is_file():
            raise FileNotFoundError("공부 기록 DB가 없습니다. 먼저 study_session.py로 공부를 기록하세요.")
        store = SessionStore(self.db_path)
        try:
            yield store
        finally:
            store.close()

    def list_sessions(self):
        if not self.db_path.is_file():
            return []
        with self._store() as store:
            return store.list_sessions(limit=-1)

    def read(self, session_id):
        with self._store() as store:
            summary, events = store.read(session_id)
            result = store.read_coaching(session_id)
        if result is not None and result["session_id"] != session_id:
            raise ValueError("저장된 코칭의 세션 ID가 기록과 다릅니다.")
        return summary, events, result

    def export(self, session_id, destination):
        # SessionStore uses the ID as a filename. Reject paths from imported DBs.
        if any(c in session_id for c in '/\\:*?"<>|') or session_id in (".", ".."):
            raise ValueError("내보낼 수 없는 세션 ID입니다.")
        with self._store() as store:
            return store.export(session_id, destination)

    def generate(self, session_id, *, mode="rule", adapter_path=None, coach=None):
        if mode not in ("rule", "kogpt2", "lora"):
            raise ValueError("지원하지 않는 코칭 방식입니다.")
        if not self.db_path.is_file():
            raise FileNotFoundError("공부 기록 DB가 없습니다.")
        with coaching_lock(self.db_path), self._store() as store:
            snapshot, _ = store.read(session_id)
            if snapshot.get("status") != "completed":
                raise ValueError("정상 종료된 세션에서만 코칭을 생성할 수 있습니다.")
            summary = SessionSummary.from_dict(snapshot)
            model_id = DEFAULT_RULE_MODEL_ID if mode == "rule" else DEFAULT_BASE_MODEL_ID
            pending = CoachingResult(
                session_id=session_id, model_id=model_id,
                model_revision=DEFAULT_MODEL_REVISION, dataset_version=DEFAULT_DATASET_VERSION,
            )
            store.save_coaching(pending)
            store.save_coaching(replace(pending, status="running"))
            try:
                result = generate_coaching_for_session(
                    summary, mode=mode, adapter_path=adapter_path, coach=coach,
                )
                if result is None or not result.matches_session(session_id):
                    raise ValueError("session_id_mismatch")
                if result.status not in ("succeeded", "failed"):
                    raise ValueError("generation_not_finished")
            except Exception as exc:
                result = build_fallback_result(
                    summary, model_id=model_id, validation_errors=[str(exc)],
                )
            # Do not attach a late result to statistics changed by another writer.
            current, _ = store.read(session_id)
            if current != snapshot:
                store.save_coaching(replace(
                    pending, status="failed", validation_errors=["session_changed"],
                    display_text="생성 중 세션 기록이 변경되어 결과를 저장하지 않았습니다.",
                ))
                raise ValueError("세션 기록이 변경되었습니다. 새로고침 후 다시 생성하세요.")
            store.save_coaching(result)
            return result


def display_time(value):
    """Show the PC's local time with its UTC offset, preserving malformed values."""
    if not value:
        return "—"
    try:
        return datetime.fromisoformat(value).astimezone().strftime("%Y-%m-%d %H:%M:%S %z")
    except (ValueError, TypeError):
        return str(value)


def summary_text(summary):
    study = summary["study_seconds"]
    observable = summary["observable_seconds"]
    missing = summary["unobservable_seconds"]
    missing_ratio = f"{missing / study * 100:.1f}%" if study > 0 else "측정 불가"
    closure_ratio = (
        f"{summary['eye_closure_seconds'] / observable * 100:.1f}%"
        if observable > 0 else "측정 불가"
    )
    return (
        f"학습 목표: {summary.get('goal') or '일반 학습'}\n"
        f"세션 ID: {summary['session_id']}\n"
        f"시작: {display_time(summary.get('started_at'))}\n\n"
        f"공부 {study:.1f}초  ·  휴식 {summary['break_seconds']:.1f}초  ·  "
        f"보정 {summary.get('setup_seconds', 0):.1f}초\n"
        f"관측 가능 {observable:.1f}초  ·  미측정 {missing:.1f}초 ({missing_ratio})\n"
        f"눈 감김 의심 {summary['eye_closure_count']}회 / {summary['eye_closure_seconds']:.1f}초 "
        f"(관측 대비 {closure_ratio})\n"
        f"고개 숙임 {summary.get('head_down_count', 0)}회 / "
        f"{summary.get('head_down_seconds', 0):.1f}초\n"
        f"눈·고개 겹침을 한 번만 센 시간: {summary.get('combined_event_seconds', 0):.1f}초\n\n"
        "공부 시간은 보정·휴식을 제외한 기존 Python 집계값입니다.\n"
        "눈 감김·고개 숙임 기록은 실제 수면이나 집중도 점수가 아닙니다."
    )
