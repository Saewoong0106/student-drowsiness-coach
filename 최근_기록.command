#!/bin/zsh
cd -- "$(dirname -- "$0")" || exit 1
if [[ ! -x .venv/bin/python ]]; then
  echo "프로젝트 Python 환경을 먼저 준비하세요."
  read "?Enter를 누르면 종료합니다."
  exit 1
fi
.venv/bin/python session_report.py
result=$?
read "?Enter를 누르면 종료합니다."
exit "$result"
