# 팀 협업 방법

이슈는 할 일 카드, 브랜치는 개인 작업 공간, PR은 변경 검토 요청, main은 팀이 함께 쓰는 확정본입니다.

## 작업 절차

1. 이슈에 목적, 담당자, 목표일, 선행 작업, 완료 기준을 적습니다.
2. 작업 시작 댓글을 남기고 main의 최신 상태에서 작업 브랜치를 만듭니다.
3. 한 이슈의 목적에 맞게 코드·문서를 수정하고 검증합니다.
4. PR에 변경 이유, 관련 이슈, 확인 결과와 남은 문제를 적습니다.
5. 세웅이 검토하고 병합합니다. 세웅이 작성한 PR은 다른 팀원에게 검토받고 병합합니다.
6. 각자 main을 최신 상태로 내려받습니다. GitHub 병합만으로 로컬 파일이 자동 갱신되지는 않습니다.

브랜치 이름 예: `feature/12-eye-landmarks`, `fix/23-camera-gap`, `docs/5-datasets`.

```bash
# 커밋하지 않은 변경이 없는 상태에서 실행
git switch main
git pull --ff-only origin main
git switch -c feature/12-eye-landmarks
# 작업 후
git add <확인한-파일>
git commit -m "feat: 눈 열림 측정 추가"
git push -u origin feature/12-eye-landmarks
```

`git add` 전에 변경 파일을 확인합니다. 토큰·영상·가중치·실제 개인정보는 커밋하지 않습니다. 충돌이 나면 다른 사람의 변경을 지우지 말고 해당 담당자와 해결합니다.

## 이슈와 PR 연결

이슈의 모든 완료 조건을 PR이 충족하면 PR 본문에 `Closes #12`처럼 적습니다. 기본 브랜치에 병합되면 연결된 이슈가 닫힙니다. 성능 시험이 남아 있으면 `Related to #12`로 연결해 열어 둡니다. [GitHub 공식 설명](https://docs.github.com/en/issues/tracking-your-work-with-issues/using-issues/linking-a-pull-request-to-an-issue)

매 이슈는 다음 정보를 유지합니다.

- 담당자 1명과 협업자
- 해야 할 것과 하지 않을 것
- 목표일과 먼저 완료해야 하는 이슈
- 확인 가능한 완료 조건
- 결과 PR·문서·실험 로그 링크
- 막힌 점과 다음 행동

작업 현황은 이슈를 기준으로 관리합니다. README에 동일 상태를 계속 복제하지 않습니다. 전체 진행 이슈에서 개별 이슈 링크를 모아 봅니다. 팀원 초대 및 GitHub 계정별 담당 지정은 저장소 소유자가 진행합니다.

## 병합 규칙의 의미

main 직접 푸시 금지와 세웅 최종 병합은 우선 **팀 운영 규칙**입니다. 템플릿만 추가했다고 GitHub가 강제하는 것은 아닙니다. 필요하면 소유자가 저장소 설정에서 PR·리뷰 요구사항을 설정하고 실제 적용 여부를 확인합니다. 비공개 저장소의 보호 기능은 계정 요금제에 따라 다릅니다. [보호된 브랜치 공식 안내](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches)

## 자동화 범위

기본 GitHub 협업만으로 팀원이 이슈를 읽고 담당 작업을 진행할 수 있습니다. 이슈 생성이 AI 실행이나 자동 코딩을 시작하지는 않습니다. 현재 저장소에는 자동 댓글·자동 배정·자동 병합 기능을 넣지 않습니다.

이후 자동 요약이 필요하면 이슈 생성·수정 이벤트를 받는 GitHub Actions 또는 별도 서비스에서 요약 초안을 만들도록 확장할 수 있습니다. API 비용·비밀키 관리·중복 실행 방지·실패 표시가 필요하며, 이슈 본문은 외부 입력으로 취급합니다. AI의 제안만으로 PR을 자동 병합하지 않습니다. 기존 GitHub 트리아지 프로젝트는 참고 자료이며 이 저장소에서 자동 실행되는 것으로 간주하지 않습니다.
