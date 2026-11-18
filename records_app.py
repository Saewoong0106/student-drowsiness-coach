"""Launch the local Python record/coaching screen; no camera or web server."""

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description="공부 기록·코칭 결과 화면")
    parser.add_argument("--db", type=Path, default=ROOT / "data/private/study.sqlite3")
    parser.add_argument("--adapter", type=Path, default=ROOT / "models/kogpt2-coaching-lora")
    args = parser.parse_args()
    try:
        import tkinter as tk
    except ImportError as exc:
        print(f"Tk 지원 Python을 설치하세요: {exc}", file=sys.stderr)
        return 1
    try:
        from study_coach.records_view import RecordsApp
        root = tk.Tk()
        RecordsApp(root, args.db, args.adapter)
        root.mainloop()
    except (ImportError, RuntimeError, tk.TclError) as exc:
        print(f"기록 화면 실행 실패: {exc}\nTk 지원 Python을 설치하고 다시 실행하세요.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
