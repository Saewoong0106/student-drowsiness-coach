# 모듈을 연결하는 데이터 규칙

초기 제안 v0.1입니다. 실제 구현 전 담당자들이 확인하고 변경 사항은 PR로 함께 수정합니다. 시간 단위는 초, 각도는 도, 비율은 0~1로 통일합니다. 화면에서만 분·퍼센트로 바꿉니다.

현재 `study_coach/contracts.py`와 `study_coach/session.py`에 `FrameMeasurement`, `Event`, `SessionSummary`(`TimeWindow`), `CoachingResult` 데이터 계약과 눈 감김(2초 진입·1초 회복)·고개 숙임(`pitch_delta_deg` 20도 이상 3초 진입·15도 미만 1초 회복) 판정 로직 및 고개 각도 입력 인터페이스(`measure_frame`, `StudySession.step_measurement`)가 구현되어 있습니다. 영상에서 실제 고개 각도를 추출하는 측정 모듈(#4)과 KoGPT2 코칭 생성기(#8)는 후속 단계에서 이 인터페이스에 연결합니다.

## 영상 측정 담당이 판정 담당에게 전달

`FrameMeasurement`의 필드:

| 필드 | 형식 | 의미 |
|---|---|---|
| session_id | 문자열 | 세션 식별자 |
| timestamp_s | 실수 | 세션 기준 단조 증가 시간 또는 영상 재생 시간 |
| eye_valid | 참/거짓 | 눈 열림 측정이 유효한지 |
| pose_valid | 참/거짓 | 고개 측정이 유효한지 |
| ear_left, ear_right | 실수 또는 null | 양쪽 눈 개방 비율 |
| pitch_delta_deg | 실수 또는 null | 보정 자세 대비 아래 방향 양수 |
| invalid_reason | 문자열 또는 null | 얼굴 없음·가림·입력 중단 등 |

측정 불가 값을 0으로 넣으면 ‘완전히 감긴 눈’과 혼동합니다. null과 유효 여부를 사용합니다. 눈·고개 측정의 유효성을 따로 보존하고 각 유형의 감지·관측률을 따로 계산할 수 있게 합니다. 대표 화면 상태는 측정 부족을 우선 알립니다.

## 판정 담당이 기록 담당에게 전달

`Event`의 필드: `event_id, session_id, type, condition_started_s, confirmed_s, ended_s, interrupted_reason, config_version`.

- type: `eye_closure_suspected`, `head_down`, `unobservable`, `break`.
- 눈 감김·고개 숙임은 독립 이벤트입니다. 이벤트 확정 후 시작 시각은 조건 시작 시각으로 소급합니다.
- 확정 전 짧게 끝난 후보는 졸음 횟수로 세지 않습니다.
- 이벤트 종료는 회복이 확인되었을 때 회복 조건이 시작된 시각으로 기록합니다.
- 미검출·휴식·세션 종료로 중단되면 마지막 유효 시각에 닫고 사유를 남깁니다.
- `event_id`를 고유 키로 사용해 중복 저장을 막습니다. 진행 중 이벤트는 종료 시각을 갱신합니다.
- 영상 시간과 실제 날짜·시각을 섞지 않습니다. UI 날짜에는 시간대를 보존합니다.

## 통계 담당이 코칭 담당에게 전달

`SessionSummary`의 필드: `session_id, goal, study_seconds, break_seconds, observable_seconds, unobservable_seconds, eye_closure_count, eye_closure_seconds, head_down_count, windows, schema_version`.

각 시간창에는 `start_s, end_s, observable_seconds, eye_closure_count`를 넣습니다. 전반·후반 비교는 관측량을 함께 봅니다.

```json
{
  "session_id": "synthetic-example-001",
  "goal": "자료구조 복습",
  "study_seconds": 3600,
  "break_seconds": 0,
  "observable_seconds": 3480,
  "unobservable_seconds": 120,
  "eye_closure_count": 3,
  "eye_closure_seconds": 18,
  "head_down_count": 4,
  "windows": [
    {"start_s": 0, "end_s": 2400, "observable_seconds": 2320, "eye_closure_count": 1},
    {"start_s": 2400, "end_s": 3600, "observable_seconds": 1160, "eye_closure_count": 2}
  ],
  "schema_version": "0.1"
}
```

이는 합성 예시입니다. 공부 시간은 세션 전체 시간에서 사용자 휴식과 보정 시간을 뺀 값이고 실제 집중 시간과 같지 않습니다. 미측정 시간은 공부 시간 안의 미관측 구간입니다. 초기 구현에서는 얼굴·눈 관측 가능 시간을 졸음 통계의 분모로 사용하고 자세 관측률은 별도로 기록할 예정입니다.

코칭 학습 레코드는 `id, scenario, group_id, source, reviewed, input, target`를 갖습니다. `source`는 팀 작성·생성 보조 등 실제 작성 경로를 기록합니다. 통계 간 모순, 음수, 관측 시간 초과, 중복 구간은 학습 전에 검증합니다.

## 코칭 담당이 화면에 전달

`CoachingResult`: `session_id, model_id, model_revision, dataset_version, status, raw_text, display_text, validation_errors, elapsed_seconds, is_fallback`.

status는 `pending`, `running`, `succeeded`, `failed` 중 하나입니다. 통계를 복사해 요청하고 완료 시 세션 ID를 대조합니다. 검증 실패 시 원출력을 보존하고 화면의 대체 안내에는 대체임을 표시합니다. 모델 원출력과 프로그램이 만든 사실 요약을 구분해 성능을 계산합니다.
