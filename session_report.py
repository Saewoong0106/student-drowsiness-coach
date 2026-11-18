"""Read/export local session statistics; never invoke an LLM."""

import argparse
from pathlib import Path
import sys
import sqlite3

from study_coach.storage import SessionStore

ROOT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description="공부 세션 기록 조회·JSON/CSV 내보내기")
    parser.add_argument("--db", type=Path, default=ROOT / "data/private/study.sqlite3")
    parser.add_argument("--session", help="생략하면 최근 세션")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--export", type=Path)
    args = parser.parse_args()
    if not args.db.is_file():
        print("아직 기록이 없습니다. 공부 세션을 먼저 실행하세요.")
        return 0
    store = None
    try:
        store = SessionStore(args.db)
        rows = store.list_sessions()
        if not rows:
            print("아직 기록이 없습니다.")
            return 0
        if args.list:
            for row in rows:
                print(f"{row['session_id']} | {row['source']} | {row['status']} | 공부 {row['study_seconds']:.1f}초 | 의심 {row['eye_closure_count']}회")
            return 0
        summary, events = store.read(args.session or rows[0]["session_id"])
        print(f"세션: {summary['session_id']} ({summary['source']}, {summary['status']})")
        print(f"시작: {summary['started_at']}")
        print(f"보정 {summary['setup_seconds']:.1f}초 / 공부 {summary['study_seconds']:.1f}초 / 휴식 {summary['break_seconds']:.1f}초")
        print(f"관측 {summary['observable_seconds']:.1f}초 / 미측정 {summary['unobservable_seconds']:.1f}초")
        print(f"졸음 의심 {summary['eye_closure_count']}회 / 누적 {summary['eye_closure_seconds']:.1f}초")
        print(f"고개 숙임 {summary.get('head_down_count', 0)}회 / 누적 {summary.get('head_down_seconds', 0.0):.1f}초")
        ratio = summary['eye_closure_ratio']
        print("관측 시간 대비 눈 감김: " + ("측정 불가" if ratio is None else f"{ratio * 100:.1f}%"))
        if summary['status'] == 'running':
            print("진행 중 또는 비정상 종료 전 저장된 임시 기록입니다. 확정 결과로 해석하지 마세요.")
        for event in events:
            print(f"  이벤트({event.get('type', 'eye_closure_suspected')}): 시작 {event['condition_started_s']:.2f} / 확정 {event['confirmed_s']:.2f} / 종료 {event['ended_s']} / 중단 {event['interrupted_reason']}")
        if args.export:
            for path in store.export(summary['session_id'], args.export):
                print(f"내보내기: {path}")
        print("눈 감김 기록이며 실제 집중도·수면 진단·LLM 코칭 결과가 아닙니다.")
    except (KeyError, OSError, sqlite3.Error) as exc:
        print(f"기록 조회 실패: {exc}", file=sys.stderr)
        return 1
    finally:
        if store is not None:
            store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
