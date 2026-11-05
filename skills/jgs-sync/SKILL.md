---
name: jgs-sync
description: JaehoGameStudio의 Claude Code·Codex 에이전트와 스킬을 양방향 동기화한다. 어느 쪽에서든 역할·스킬을 추가하거나 수정할 때 사용한다.
---

# Claude Code와 Codex 미러링

프로젝트 `.agents/plugins/marketplace.json`에서 `jaeho-game-studio`의
`source.path` 상대 경로로 원본을 찾는다. 별도 루트 상태 파일은 만들지 않는다.
패키지 안에서 작업하면 `team.json`이 있는 루트가 원본이다.
설치 캐시와 사용자 전역 폴더는 편집 원본이 아니다.

역할은 패키지 `agents/jgs-*.md` ↔ `codex-agents/jgs-*.toml` ↔
`roles/`·`team.json` 사이에 반영한다. 새 역할 이름은 `jgs-`로 시작한다.
스킬은 양쪽이 사용하는 공통 `skills/<name>/`에서 수정하며 부속 파일도
그 안에 둔다. 개발자 역할을 같은 이름의 스킬로 다시 만들지 않는다.

Python 3.11 이상으로 실행한다. Windows에서는 `py -3.11` 등을 사용한다.

```sh
python3 <package>/scripts/sync-studio.py
python3 <package>/scripts/sync-studio.py --check
```

프로젝트 등록도 검사하려면 두 명령에 `--project <engine>`을 추가한다.
`.agents/plugins/marketplace.json`에 플러그인 경로를 등록하고
`.codex/config.toml`에는 에이전트의 상대 `config_file` 경로를 등록한다. 프로젝트에 정의를 복사하거나 심볼릭 링크·정션을 만들지 않는다.
구버전 복사본에 로컬 수정이
있으면 삭제 전에 패키지에 보존하고 충돌을 해결한다.

서로 다른 동시 수정은 덮어쓰지 않는다. 삭제·이름 변경은 별도로 검토한다.
모델·추론 강도·권한·도구는 역할 파일에 고정하지 않는다.
엔진의 체크포인트와 계약은 해당 프로젝트 `.jaeho-studio/`에 보존한다.

Codex 호스트 등록을 변경한 경우 새 세션에서 확인한다. Claude는
`claude --plugin-dir <package>`로 원본을 직접 읽으며 실행 중 수정은
`/reload-plugins`로 반영한다. 마켓플레이스로 설치한 클라이언트 캐시는
호스트가 관리하므로 소스 직접 로딩과 구분한다. 갱신 자동화는 만들지 않는다.
