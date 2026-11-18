# 첫 카메라 실행

Mac과 Windows의 새 설치는 [공통 실행 안내](cross-platform.md)를 따릅니다. Windows에서 얼굴·눈 위치만 확인할 때는 설치 후 `start_camera.bat`를 실행합니다. 아래 `.command`와 `.venv/bin/python` 명령은 Mac용입니다.

이번 단계는 얼굴과 눈 위치를 표시하는 시제품입니다. 아직 눈 감김 지속시간, 졸음 알람, 고개 각도, 공부 기록, LLM은 구현하지 않았습니다. 얼굴 점이 나오는 것만으로 가림이나 졸음을 정확히 판단할 수 있는 것은 아닙니다.

## 준비된 맥북에서 시작

1. 프로젝트 폴더의 `카메라_시작.command`를 더블클릭합니다.
2. macOS가 카메라 접근을 물으면 실행 앱의 접근을 허용합니다.
3. 카메라를 정면으로 봅니다. 얼굴 점은 초록색, 눈 주변 주요 점은 노란색입니다.
4. `Face found`는 얼굴 검출, `No face detected`는 얼굴 미검출입니다.
5. 화면을 선택한 상태에서 Q 또는 Esc로 종료합니다.

권한을 처음 허용했는데 창이 나오지 않으면 명령 파일을 한 번 더 실행합니다. OpenCV가 권한 승인 전에 입력 열기에 실패했을 수 있습니다. 재실행도 실패하면 `.cache/preview-last-run.log`의 오류 문구를 확인합니다. 이 파일에는 실행 메시지·요약 숫자만 남고 영상은 저장되지 않습니다.

실행 중 영상은 저장하거나 외부 서버로 보내지 않습니다. 최초 라이브러리·모델 설치에만 인터넷이 필요합니다. 종료하면 프레임 수·얼굴이 검출된 프레임 수 등 요약 숫자만 터미널에 표시됩니다.

## 오늘 직접 확인할 세 가지

- [ ] 카메라 영상이 화면에 나온다.
- [ ] 정면에서 얼굴·눈 위치의 점이 표시된다.
- [ ] 화면 밖으로 나가면 점이 사라지고, 돌아오면 다시 표시된다.

이어 Q로 창을 닫고 다시 실행해 카메라가 정상적으로 열리는지 확인합니다. 위 결과를 이슈 #1에 남기되 얼굴 사진을 첨부할 필요는 없습니다.

## 터미널 실행

프로젝트 폴더에서 다음 명령을 실행합니다.

```bash
.venv/bin/python camera_preview.py
```

외장 카메라 등 다른 장치는 `--camera 1`처럼 번호를 바꿔 확인합니다. 스마트폰 연동 카메라를 포함한 장치 순서는 환경마다 다릅니다.

```bash
.venv/bin/python camera_preview.py --camera 1
.venv/bin/python camera_preview.py --video /절대경로/샘플.mp4
.venv/bin/python camera_preview.py --video /절대경로/샘플.mp4 --headless --max-frames 100
.venv/bin/python camera_preview.py --image /절대경로/샘플.jpg --headless
```

영상 파일은 처리 가능한 속도로 실행합니다. 표시 FPS는 처리 속도이며 원본 재생 FPS와 다릅니다. 추론에는 파일의 미디어 시간을 사용합니다. 단일 이미지의 화면 확인에서는 키를 누르면 종료됩니다.

## 다른 컴퓨터에서 설치

Python 3.11 가상환경을 권장합니다. 시스템 Python 환경은 변경하지 않습니다. 테스트한 정확한 설치 버전은 `requirements-macos.txt`를 참고합니다. 다른 OS에서는 지원되는 패키지 버전을 별도로 검증해야 합니다.

```bash
python3.11 -m venv .venv
.venv/bin/python -m pip install -r requirements-macos.txt
.venv/bin/python scripts/download_model.py
.venv/bin/python camera_preview.py
```

모델은 Google 공식 배포의 버전 1을 사용하며 `models/`는 Git에서 제외합니다. [Face Landmarker 공식 안내](https://developers.google.com/edge/mediapipe/solutions/vision/face_landmarker?tab=Python), [Python 환경 안내](https://developers.google.com/edge/mediapipe/solutions/setup_python)

## 자주 막히는 곳

| 증상 | 확인 방법 |
|---|---|
| 카메라를 열지 못함 | 시스템 설정의 카메라 권한, 다른 화상회의 앱 사용 여부, 카메라 번호 확인 |
| 명령 파일 더블클릭이 막힘 | 터미널 실행 명령 사용. 실행 권한이 없으면 `chmod +x 카메라_시작.command` |
| 얼굴 모델 없음 | 모델 다운로드 명령 실행 |
| 화면은 나오지만 얼굴 없음 | 조명·정면 위치·카메라 거리 조정. 안경 반사와 가림도 확인 |
| 카메라 입력 중단 | 연결·권한 확인 후 다시 실행. 이 단계에는 자동 재연결이 없음 |
| 점이 있어도 눈이 가려짐 | 현재는 위치 추정 시제품. 점 존재를 눈 관측 신뢰도로 해석하지 않음 |

첫 화면 확인 후 다음 작업은 눈 열림 비율 측정과 개인별 보정입니다.

검증 범위와 남은 항목은 [첫 실행 확인 기록](first-run-validation.md)을 참고하세요.
