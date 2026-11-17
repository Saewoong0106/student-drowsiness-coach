# 웹 UI 연결 계약

UI 브랜치만으로 백엔드는 실행되지 않습니다. 아래는 현재 UI가 요구하는 HTTP 계약이며 기존 Python `SessionSummary`·`CoachingResult` 저장 계약과의 통합은 후속 작업입니다.

## 공통 규칙

- UI와 API는 같은 origin에서 제공합니다. 로그인은 HttpOnly 세션 쿠키로 유지하며 fetch는 `credentials: same-origin`을 사용합니다.
- POST 요청은 JSON 본문과 `Content-Type: application/json`, `X-Study-Request: 1` 헤더를 보냅니다. 서버에서 Origin·세션·기록 소유권을 검증해야 합니다.
- 성공과 실패 모두 JSON으로 응답합니다. 실패 형식은 `{ "error": "사용자에게 보여 줄 설명" }`입니다. 401은 로그인 만료, 403은 권한/프로필 미완료, 409는 세션 상태 충돌입니다.
- `started_at`·`ended_at`은 Unix 밀리초, `study_ms`·`break_ms`는 밀리초 누적 시간입니다. 이벤트의 `_s` 필드는 세션 시작 후 경과 초입니다.

## 인증·프로필

| 메서드·경로 | 요청 | 성공 응답 |
|---|---|---|
| GET `/api/config` | 없음 | `{googleReady, studyFocuses: [{id, label, description}]}` |
| GET `/api/me` | 없음 | `{user}`; 비로그인은 401 |
| POST `/api/username/check` | `{username}` | `{available, username}` |
| POST `/api/register` | `{username, name, email, phone, password}` | `{user}` 및 세션 쿠키 |
| POST `/api/login` | `{identifier, password}` | `{user}` 및 세션 쿠키 |
| POST `/api/profile` | `{username, phone, study_focus}` | `{user}` |
| POST `/api/logout` | `{}` | `{ok: true}` 및 쿠키 만료 |
| GET `/auth/google` | 브라우저 이동 | OAuth 설정 시 인증 화면으로 리다이렉트 |

`user`는 `id, name, username, phone, provider, study_focus, onboarding_complete`를 포함합니다. 미입력 프로필 값은 null일 수 있습니다. `googleReady=false`면 Google 로그인 링크를 비활성화합니다. 비밀번호 및 토큰을 응답에 포함하지 않습니다.

## 세션

| 메서드·경로 | 요청 | 성공 응답 |
|---|---|---|
| GET `/api/studies` | 없음 | `{studies: [...]}` 최신순, 현재 계정 기록만 |
| POST `/api/studies/start` | `{title}` | `{study}` |
| POST `/api/studies/:id/break` | `{}` | `{study}` |
| POST `/api/studies/:id/resume` | `{}` | `{study}` |
| POST `/api/studies/:id/finish` | `{}` | `{study}` |
| POST `/api/studies/:id/demo` | `{action: "generate"}` 또는 `{action: "fallback"}` | `{study}`; 가상 시연 기록에만 허용 |

세션 예시:

```json
{"id":"S-example","title":"전공 공부","started_at":1794877200000,"ended_at":null,"status":"studying","study_ms":12000,"break_ms":0}
```

`status`는 `studying`, `break`, `finished` 중 하나입니다. 진행 중인 세션은 계정당 하나이며 `ended_at=null`입니다. 누적 시간은 응답 시점의 서버 기준 값입니다. 카메라가 없어도 타이머는 진행됩니다. 현재 웹 시간에는 보정 시간이 포함되며 Python의 보정 제외 시간과 통합할 때 조정이 필요합니다.

감지와 연결된 기록에는 `detection_events` 배열을 추가합니다. 미연결이면 필드를 생략하고, 연결됐으나 확정 이벤트가 없으면 빈 배열을 보냅니다. 가상 기록만 `demo` 객체를 포함하며 실제 기록에 예시 코칭을 섞지 않습니다.

## 감지

POST `/api/studies/:id/detection`은 `{type: "measurement", eyes, matrix, width, height}`를 받습니다. `eyes`는 정규화된 `[x,y]` 좌표 12개 또는 빈 배열, `matrix`는 얼굴 변환 행렬 원소 16개 또는 빈 배열입니다. 영상 자체는 보내지 않습니다. 현재 공부 중인 세션에만 허용합니다.

응답은 `{detection: {phase, calibration_progress, eye_state, head_state, eye_count, head_count, triggered}}`입니다. `phase`는 `calibrating`, `calibration_retry`, `unobservable`, `ready`를 사용합니다. `triggered`는 이번 응답에서 새로 확정된 이벤트 배열이며 빈 배열이면 소리를 내지 않습니다.

POST `/api/studies/:id/detection/reset`은 `{}`를 받아 `{ok: true}`를 반환하고 재보정을 준비합니다. UI는 Worker를 다시 시작합니다.

감지 이벤트 예시:

```json
{"event_id":"E-example","session_id":"S-example","type":"eye_closure_suspected","condition_started_s":10,"confirmed_s":12,"ended_s":14}
```

실제 감지 기록의 이벤트는 현재 `eye_closure_suspected`와 `head_down`입니다. 진행 중 이벤트는 `ended_s=null`이며 재조회 때 같은 ID를 갱신합니다. 상태 표시·음소거·재보정은 브라우저별이며 다중 탭 동기화는 아직 보장하지 않습니다.

## 정적 자원

live UI는 서버 루트에 제공합니다. `/base.css`는 상위 `styles.css`, `/ui.css`, `/ui.js`, `/demo-ui.js`, `/detection.js`, `/face-worker.js`는 live 파일을 제공합니다. Worker가 사용하는 `/vision/vision_bundle.mjs`, `/vision/wasm/*`, `/vision/face_landmarker.task`는 별도 설치해야 합니다. localhost 또는 HTTPS와 Worker·WebAssembly를 허용하는 CSP가 필요합니다.
