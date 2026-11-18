# Python 기록·코칭 화면 (#23 / #25)

PR #26 리뷰에 따라 로그인·회원가입·OAuth·브라우저 자체 감지와 가상 웹 UI를 제거하고,
최신 main의 Python 감지·저장·코칭 모듈을 사용하는 로컬 기록 화면으로 바꿨습니다.
학생 1명·PC 1대에서 사용합니다. Tkinter 데스크톱 창이며 Node 서버·HTTP API는 없습니다.

## 실행

Python 3.11의 Tk 지원 설치본이 필요합니다. 기록 화면과 규칙 안내에는 추가 pip 패키지가
필요하지 않습니다. 카메라 실행에는 기존 README의 requirements.txt·얼굴 모델 설치가 필요합니다.

Windows PowerShell, 저장소 루트:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe records_app.py
```

가상환경 준비 후 `start_records.bat`으로도 실행할 수 있습니다.
Mac에서는 `.venv/bin/python records_app.py`를 실행합니다.
`No module named tkinter` 오류라면 Tk 지원 Python 배포판을 설치해야 합니다.

1. 기존 `study_session.py` / `start_study.bat`에서 공부하고 Q 또는 Esc로 종료합니다.
2. 기록 화면을 열거나 **새로고침(F5)** 을 누릅니다.
3. 세션을 선택해 기록 요약·이벤트·저장된 코칭을 확인합니다. 목표·ID·시각 검색과 세션 상태 필터를 제공합니다.
4. 정상 종료 세션에서 **코칭 생성**을 누릅니다. 기본값은 명시적으로 표시되는 규칙 기반 안내입니다.
5. **JSON / CSV 내보내기**로 기존 저장 API의 요약·이벤트·코칭 JSON과 이벤트 CSV를 저장합니다.

기본 DB는 `data/private/study.sqlite3`입니다. 기존 DB는 복제하거나 새 스키마로 변환하지 않습니다.
다른 DB는 `records_app.py --db 경로`로 선택합니다. DB가 없으면 빈 화면과 안내를 표시하고 파일을 만들지 않습니다.
공부·휴식·보정·미측정 및 눈/고개 합집합 시간은 기존 Python 집계값을 그대로 표시합니다.
시각은 PC의 현지 시간과 UTC 오프셋으로 표시합니다. 관측 시간이 0이면 비율은 측정 불가입니다.

## 코드 구성과 기존 기능 재사용

```text
records_app.py               # 실행 인자와 Tk 창 시작
start_records.bat            # Windows 실행
study_coach/
  records_view.py            # 목록·검색·탭·상태·비동기 생성 UI (추가)
  records.py                 # 기존 저장/코칭 함수 연결 (추가)
  storage.py                 # 기존 SessionStore, 변경 없음
  coaching.py                # 기존 추론·규칙·대체 응답, 변경 없음
  contracts.py               # 기존 SessionSummary/CoachingResult, 변경 없음
  session.py                 # 기존 감지·시간 집계, 변경 없음
study_session.py             # 기존 카메라·감지 실행, 변경 없음
session_report.py            # 기존 CLI 조회·내보내기, 변경 없음
tests/test_records.py        # SQLite 통합·오류·화면 회귀 검사
docs/records-ui.md           # 이 문서
data/private/study.sqlite3   # 기존 개인 DB (Git 제외)
```

이전 `coaching-ui-prototype/` 13개 파일은 브랜치에서 제거합니다. 인증, 브라우저 MediaPipe Worker,
Node API 계약, 웹 UI용 테스트는 새 Python 구조에 포함하지 않습니다. 원본은 이전 커밋에 남습니다.
기존 Python 구현의 담당·기여는 변경하지 않습니다.

## 코칭 생성·실패·복구

- `SessionStore.list_sessions/read/read_coaching`으로 조회합니다.
- 정상 종료(`completed`) 세션만 `SessionSummary` 사본으로 변환해 `generate_coaching_for_session`에 전달합니다.
- `save_coaching`으로 대기 → 생성 중 → 완료/실패 상태를 저장합니다. 재실행해도 `read_coaching`으로 확인합니다.
- 생성은 별도 작업 스레드, SQLite 연결도 작업별로 분리합니다. 카메라 루프는 이 화면에서 실행하지 않습니다.
- 같은 DB의 이 기록 화면끼리는 OS 파일 잠금으로 코칭을 한 번에 하나만 생성합니다. 잠금 파일은 DB 옆에 남아도
  프로세스 종료 시 잠금이 해제됩니다. 외부 스크립트의 코칭 쓰기까지 강제하는 잠금은 아닙니다.
- 세션 ID가 다른 응답은 거부하고 실패·대체 안내로 저장합니다. 생성 도중 기록이 변경되면 이전 통계의 결과를 저장하지 않습니다.
- 생성 중 다른 세션으로 이동해도 현재 선택된 세션만 다시 그립니다. 중복 클릭을 막습니다.
- 모델/패키지/어댑터 실패 시 기존 규칙 기반 대체 함수를 사용하고 실패와 대체 여부를 함께 표시합니다.
- 저장 실패는 성공으로 표시하지 않습니다. 이전 실행이 중단돼 대기/생성 중으로 남으면 새로고침 후 명시적으로 다시 생성할 수 있습니다.
- 생성 중에는 창 닫기를 보류합니다. 추론 취소/타임아웃은 제공하지 않으며, 강제 종료 후에는 다시 생성해야 합니다.
- 기존 결과를 다시 생성하거나 내보내기 파일을 덮어쓸 때는 화면에서 확인합니다.

기본 KoGPT2 및 LoRA는 main의 학습 안내에 따른 별도 패키지·모델이 필요합니다.
LoRA 기본 경로는 `models/kogpt2-coaching-lora`이며 `--adapter 경로`로 바꿉니다.
규칙 안내 성공은 실제 LLM 추론이나 모델 품질 검증 성공을 뜻하지 않습니다.

## 검증 방법과 남은 범위

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_records.py -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

새 검사는 임시 DB로 재실행 후 조회·JSON/CSV·미종료 생성 차단·모델 실패·잘못된 세션 ID·중복 요청·
생성 중 통계 변경·저장 실패·관측 0·경로형 ID 거부·검색 결과 없음·늦은 완료 후 세션 전환을 확인합니다.
Tk 화면이 없는 환경에서는 화면 검사 2개가 skip됩니다. 전체 검사는 기존 카메라 관련 의존성도 필요합니다.

#23 전체 완료로 보고하지 않습니다. 실제 Windows 카메라·소리·10분 시간 오차·카메라 중단/재연결,
#22 실제 측정 연결, 실제 KoGPT2/LoRA 추론, Mac 화면·설치 재현과 팀원 리뷰는 별도 확인이 남습니다.
