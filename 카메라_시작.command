#!/bin/zsh
setopt pipefail
cd -- "$(dirname -- "$0")" || exit 1
if [[ ! -x .venv/bin/python ]]; then
  echo "프로젝트 Python 환경이 없습니다. docs/first-run.md를 확인하세요."
  read "?Enter를 누르면 종료합니다."
  exit 1
fi
mkdir -p .cache
.venv/bin/python -u camera_preview.py "$@" 2>&1 | tee .cache/preview-last-run.log
result=$?
if [[ $result -ne 0 ]]; then
  echo "위 오류를 확인하세요. 카메라 권한은 시스템 설정 > 개인정보 보호 및 보안 > 카메라에서 확인합니다."
fi
read "?Enter를 누르면 종료합니다."
exit "$result"
