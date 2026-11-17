# 학습 기록·코칭 웹 UI (#23 후속)

이 브랜치는 김수현 담당 웹 UI의 검토용 소스만 포함합니다. 기존 Python 감지기·저장 API·모델·서버 구현을 새 기여로 포함하지 않습니다.

## 화면 확인

- `index.html`: 더블 클릭으로 확인하는 가상 기록·코칭 시안입니다. 세션 선택·검색·상태 필터·가상 JSON 내보내기가 동작합니다. 로그인 계정이나 실제 DB가 필요하지 않습니다.
- `live/index.html`: 서버 연결을 전제로 한 로그인·회원가입·공부 분야 선택·웹캠·타이머·기록 UI입니다. HTML만 열거나 정적 서버만 실행하면 전체 기능이 동작하지 않습니다.

## UI 업로드 범위

HTML/CSS/브라우저 JavaScript와 이 안내만 포함합니다. `server.mjs`, Python 연결 코드, DB·로그인 정보·개인 세션, `.env`, OAuth 비밀키, 가상환경, `node_modules`, 얼굴 모델, 로컬 관리·시드 도구는 포함하지 않습니다. 로컬 시연 계정의 식별 정보와 비밀번호도 포함하지 않습니다.

## live 화면이 요구하는 연결

- 인증·프로필: `/api/config`, `/api/me`, `/api/register`, `/api/login`, `/api/logout`, `/api/username/check`, `/api/profile`, `/auth/google`
- 기록: `/api/studies`, `/api/studies/start`, `/api/studies/:id/break|resume|finish`
- 시연 결과: `/api/studies/:id/demo`
- 실제 감지: `/api/studies/:id/detection`, `/api/studies/:id/detection/reset`
- 브라우저 측정 자원: `/face-worker.js`, `/vision/vision_bundle.mjs`, `/vision/wasm/*`, `/vision/face_landmarker.task`
- 서버는 `/base.css`에 상위 `styles.css`를 제공하고 `/detection.js`, `/demo-ui.js`, `/ui.js`, `/ui.css`를 제공해야 합니다.
- Web Worker와 WebAssembly를 허용하는 CSP, localhost 또는 HTTPS, 사용자 카메라·오디오 허용이 필요합니다. MediaPipe 의존성과 모델은 이 업로드에 포함하지 않습니다.

기존 프로젝트의 `SessionSummary`, `CoachingResult`, `save_coaching/read_coaching`과 실제 서버 계약을 맞추는 후속 통합이 필요합니다. 현재 로컬 웹 서버는 기존 Python 저장 DB와 분리된 구현이며 그대로 통합 완료로 간주하지 않습니다.

## 구현·확인 범위와 남은 점

세션 탐색·가상 코칭 상태, 로그인/가입 입력·중복 확인 요청, 공부 분야 선택, 카메라·타이머 조작, 작은 감지 주의문·비프음·재보정, 실제/가상 기록 구분 UI를 구현했습니다. 눈 감김 2초, 고개 숙임 20도 이상 3초 기준은 기존 문서를 따릅니다.

로컬 전체 작업본에서 Node 자동 검사 10개, Python 합성 판정 검사 4개와 Node→Python 프로세스 연결을 확인했습니다. 서버·Python·시험 코드는 이번 UI 전용 업로드에서 제외하므로 이 브랜치만으로 전체 검사를 재현할 수 없습니다. 실제 웹캠 감지 정확도·고개 각도 부호·비프음 청취·브라우저 화면 검수는 완료되지 않았습니다.

실제 LLM 코칭, 관측 가능 시간·미측정 비율, 기존 저장 API 연결, 10분 시간 오차, 카메라 재연결·다중 탭, Google OAuth 실제 설정, 회원 관리, 서버 실행 경로 오류·Ctrl+C 종료 처리는 후속 이슈에서 추적합니다. 웹 타이머는 현재 보정 10초도 공부 시간에 포함하므로 기존 Python 시간 정의와 맞춰야 합니다.

담당·기여 기준은 #13, 주요 후속 범위는 #23입니다. 고개 측정 어댑터는 #22 담당자와 조율하며 기존 이슈를 자동 완료 처리하지 않습니다.
