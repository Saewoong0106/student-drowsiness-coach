# Mac과 Windows에서 실행하기

같은 Python 감지·시간 계산·SQLite 코드를 공유합니다. 실행 파일, 시스템 소리, 한글 글꼴은 운영체제에 맞게 선택합니다. 현재 안내 대상은 **Apple Silicon Mac과 Windows 10/11 x86-64, Python 3.11 64비트**입니다. Windows ARM 및 Intel Mac은 별도 확인 대상입니다.

## 현재 확인 범위

2026-11-09에 Windows용 실행 파일, 비동기 시스템 알림음, 맑은 고딕 선택, 공통 설치 목록과 두 운영체제용 자동 검사 설정을 추가했습니다. Windows 소리·글꼴 분기는 모의 환경으로 검사하며, 실제 Windows 실행 결과와 구분합니다. GitHub Actions는 변경이 업로드된 뒤 실행됩니다. 카메라와 소리 청취는 자동 검사로 확인할 수 없습니다.

Windows x86-64·Python 3.11 대상 패키지의 의존성 해결과 배포 파일 다운로드를 설치 없는 시험으로 확인했습니다. 이는 Windows에서 패키지 가져오기·실제 추론에 성공했다는 뜻은 아닙니다.

| 확인 항목 | 2026-11-09 결과 |
|---|---|
| Mac 자동 검사 | 기존 30개와 추가 9개, 총 39개 통과 |
| Mac 의존성 검사 | `pip check` 통과 |
| Windows 소리·글꼴 분기 | 모의 환경에서 비동기 호출·오류·글꼴 대체 처리 확인 |
| 한글·공백이 있는 모델 경로 | Mac에서 실제 모델 로드·추론 성공 |
| Windows 패키지 | x86-64·Python 3.11 배포 파일·의존성 해결 확인 |
| Windows 실제 카메라·소리·글꼴·실행 파일 | 미검증 |
| GitHub Actions의 Mac·Windows 검사 | [Actions](https://github.com/Saewoong0106/student-drowsiness-coach/actions)에서 해당 커밋의 실행 결과 확인. 실제 카메라·소리 청취는 별도 시험 |

## 처음 한 번 설치

저장소의 코드·문서·실행 파일을 내려받고 프로젝트 폴더에서 진행합니다. 다른 컴퓨터의 `.venv`를 복사하지 말고 각자 새로 만듭니다. 가상환경 활성화 명령 없이 아래 Python 경로를 직접 사용하므로 PowerShell 실행 정책을 바꿀 필요가 없습니다.

### Windows — PowerShell

Python 3.11 64비트와 Python Launcher(`py`)를 설치한 상태에서 실행합니다. `py`가 없다면 설치된 Python 3.11의 `python`으로 첫 명령을 실행하세요.

```powershell
py -3.11 -m venv .venv
$env:PYTHONUTF8 = "1"
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe scripts/download_model.py
.\.venv\Scripts\python.exe study_session.py
```

이후에는 `start_study.bat`를 더블클릭합니다. 모델은 설치 때 한 번 다운로드하며 이후 로컬 파일을 사용합니다.

### Mac — 터미널

```sh
python3.11 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pip check
.venv/bin/python scripts/download_model.py
.venv/bin/python study_session.py
```

이미 준비된 맥북은 기존 `.venv`와 `.command`를 계속 사용합니다. 기존 Mac 전체 설치 환경을 재현하려면 `requirements-macos.txt`를 사용합니다. 공통 `requirements.txt`는 직접 사용하는 패키지 버전을 고정하며, 간접 의존성 전체를 고정한 파일은 아닙니다.

## 설치 이후 실행 파일

| 용도 | Mac | Windows |
|---|---|---|
| 공부·눈 감김 알림·기록 | `공부_시작.command` | `start_study.bat` |
| 얼굴·눈 위치만 확인 | `카메라_시작.command` | `start_camera.bat` |
| 최근 기록 조회 | `최근_기록.command` | `recent_sessions.bat` |

Windows 실행 파일은 자신의 폴더로 이동하고 프로젝트 전용 Python을 사용합니다. 오류 확인을 위해 실행 후 창이 대기합니다. Mac 실행 파일은 `.cache/`에 실행 로그를 남기며, Windows 실행 파일에서는 터미널의 오류 메시지를 확인합니다. 어느 실행 파일도 얼굴 영상을 기록하지 않습니다.

Windows에서 옵션을 주려면 PowerShell에서 다음처럼 실행합니다.

```powershell
.\start_study.bat --camera 1
.\recent_sessions.bat --list
.\recent_sessions.bat --export data/private/export
```

Space 휴식·재개, C 재보정, M 음소거, Q/Esc 종료 키는 두 운영체제에서 같습니다. 키는 카메라 창을 선택한 상태에서 누릅니다.

## 운영체제별 차이

| 항목 | Mac | Windows |
|---|---|---|
| 카메라 연결 | AVFoundation | OpenCV의 기본 카메라 선택 |
| 알림 소리 | `afplay`로 시스템 소리 재생 | Python 기본 `winsound`로 시스템 알림음 비동기 재생 |
| 한국어 화면 | Apple SD Gothic Neo | Windows 폴더의 `Fonts/malgun.ttf`(맑은 고딕) |
| 글꼴이 없거나 로드 실패 | 영어 화면으로 전환 | 영어 화면으로 전환 |
| 기록 위치 | 프로젝트 안 `data/private/study.sqlite3` | 프로젝트 안 `data/private/study.sqlite3` |

소리 처리가 감지 루프를 기다리게 하지 않습니다. Windows 시스템 소리 구성에 따라 알림음이 무음일 수 있으므로 볼륨·출력 장치·시스템 소리 설정을 직접 확인합니다. 글꼴 유무와 관계없이 기록에는 한국어가 보존됩니다.

Windows에서 카메라를 열지 못하면 설정의 카메라 접근 및 데스크톱 앱 접근 허용, 다른 회의 앱의 카메라 점유를 확인합니다. 외장 웹캠은 `--camera 1`처럼 번호를 바꿔 봅니다. Mac은 실행 앱의 카메라 권한을 확인하고 최초 권한 승인 후 재실행합니다.

OpenCV 패키지는 `opencv-contrib-python` 하나만 설치합니다. `opencv-python`이나 화면 없는 `headless` 패키지를 같은 환경에 추가하지 않습니다. 설치가 꼬이면 다른 프로젝트의 환경을 변경하지 말고 이 프로젝트의 가상환경을 새로 준비합니다.

## 두 운영체제의 완료 기준

각 운영체제 담당자가 아래 실제 결과를 이슈 #1과 관련 PR에 남깁니다. 공통 화면·저장을 맡은 김수현이 Windows 시험 결과 취합을 맡고, 실제 시험은 Windows PC를 사용하는 팀원이 진행하는 것을 제안합니다. 강세웅은 Mac 결과와 통합을 담당합니다.

- [ ] OS·CPU·Python·설치 패키지 버전과 사용 카메라 기록
- [ ] 카메라 열기, 10초 보정, 한국어 표시 확인
- [ ] 짧은 깜빡임은 무경보, 약 3초 눈 감김은 1회 알림 확인
- [ ] 소리 청취, M 음소거, Space 휴식·재개 확인
- [ ] Q 종료 후 최근 기록 조회·CSV 내보내기 확인
- [ ] 얼굴 미검출·카메라 번호 오류·재실행 확인

카메라 없는 자동 검사는 두 운영체제에서 아래처럼 실행합니다. Windows에서는 위 설치 절차처럼 먼저 `PYTHONUTF8=1`을 설정합니다.

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

```sh
.venv/bin/python -m unittest discover -s tests -v
```

자동 검사 통과는 실제 카메라·알림 소리 성공이나 감지 정확도를 뜻하지 않습니다. 이후 KoGPT2를 붙일 때도 학습 환경과 앱 실행 환경을 구분하고 Mac의 MPS, Windows의 사용 가능한 CPU/GPU를 별도로 점검합니다. 현재 변경에는 LLM 학습을 포함하지 않습니다.

공식 자료: [MediaPipe Python 지원 환경](https://developers.google.com/edge/mediapipe/solutions/setup_python), [MediaPipe 1.2.2 배포 파일](https://pypi.org/project/mediapipe/1.2.2/#files), [Windows winsound](https://docs.python.org/3.11/library/winsound.html).
