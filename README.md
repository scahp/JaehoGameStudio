# JaehoGameStudio

AI plugins for game-engine development with Claude Code, Codex and OpenClaw.

개인 게임 엔진 개발을 위한 **Claude Code / Codex / OpenClaw 에이전트 플러그인**입니다. 일곱 역할의 지침과 프로젝트별 체크포인트를 공유합니다. 엔진 실행 파일에 링크하는 라이브러리는 아니며, 엔진 저장소의 개발 도구로 사용합니다.

| 에이전트 | 책임 |
| --- | --- |
| jgs-director | 목표, 작업 배정, 모듈 계약, 통합 검증 |
| jgs-graphics | 렌더러, GPU 자원, 셰이더, 조명, 그래픽스 성능 |
| jgs-editor | 에디터, 뷰포트, Inspector, 자산 UI, Undo/Redo |
| jgs-core | 플랫폼, 메모리, 파일, 스레드, 직렬화, 자산 로딩 |
| jgs-network | 프로토콜, 복제, 동기화, 예측, 서버 권위 |
| jgs-devrel | 외부 엔진 비교, 근거 조사, 도입·라이선스 검토 |
| jgs-particles | 이미터, 효과 데이터, CPU/GPU 시뮬레이션 |

## 서브모듈 하나로 Claude Code와 Codex 사용하기

jEngine에는 `tools/JaehoGameStudio` 서브모듈과 양쪽 도구의 프로젝트용 역할·스킬이
함께 들어 있습니다. 서브모듈을 포함해 받은 뒤 새 대화를 열면 사용할 수 있습니다.

```sh
git submodule update --init -- tools/JaehoGameStudio
```

다른 엔진에 처음 연결하거나 동기화할 때는 엔진 루트에서 실행합니다.
Python **3.11 이상**이 필요하며 추가 Python 패키지는 없습니다.
Windows에서는 설치된 버전에 맞춰 `python3` 대신 `py -3.11` 또는 `python`을 사용합니다.

```sh
git submodule add https://github.com/scahp/JaehoGameStudio.git tools/JaehoGameStudio
python3 tools/JaehoGameStudio/scripts/sync-studio.py --project .
python3 tools/JaehoGameStudio/scripts/init-project.py .
```

첫 Python 명령은 공통 원본과 아래 프로젝트 폴더를 동기화합니다.
기존 사용자 설정·다른 에이전트·프로젝트 체크포인트는 보존합니다.
동일한 이름의 기존 파일이 다르면 충돌을 보고하고 쓰지 않습니다.
두 번째 명령은 없는 체크포인트 파일만 생성하는 선택 사항입니다.

```text
engine/
├── tools/JaehoGameStudio/       # 하나의 Git 서브모듈
│   ├── plugin.json             # Codex/OpenClaw 패키지
│   ├── .claude-plugin/         # Claude 플러그인·마켓플레이스
│   ├── team.json + roles/      # 공유 역할 정의
│   ├── agents/jgs-*.md         # Claude native 에이전트
│   ├── .codex/agents/jgs-*.toml # Codex native 에이전트
│   ├── skills/                 # 공통 스킬과 부속 파일
│   ├── .jgs-mirror.json         # 원본의 동기화 기준 해시
│   └── scripts/sync-studio.py
├── .claude/agents/             # Claude 프로젝트 에이전트
├── .claude/skills/             # Claude 프로젝트 스킬
├── .codex/agents/              # Codex 프로젝트 에이전트
├── .agents/skills/             # Codex 프로젝트 스킬
├── .jgs-mirror.json             # 상대 패키지 경로와 동기화 해시
└── .jaeho-studio/              # 엔진별 실제 작업 기록
```

일곱 개발 역할과 유지보수용 `jgs-sync` 스킬이 제공됩니다.
Codex에서는 `$jgs-director`, Claude 프로젝트에서는 `/jgs-director`로 스킬을 사용할 수 있습니다.
실제 병렬 위임은 “jgs-director가 코어·그래픽스·에디터 에이전트에게 나누어 맡겨줘”처럼 요청합니다.
역할에 모델·추론 강도·도구·권한을 고정하지 않으므로 기존 호스트 설정을 따릅니다.
새 Codex 에이전트를 추가하면 새 대화에서 확인하세요.

## 양방향 미러링

**Claude 쪽에서 수정하거나 만들면 Codex 쪽에도 반영하고, 반대 방향도 동일합니다.**
역할·스킬 및 프로젝트 지침에 이 절차를 넣었으며 `jgs-sync`가 상세 사용법을 제공합니다.
엔진 루트에서 수정 작업을 마치기 전에 실행합니다.

```sh
python3 tools/JaehoGameStudio/scripts/sync-studio.py --project .
python3 tools/JaehoGameStudio/scripts/sync-studio.py --project . --check
```

- 에이전트: Claude Markdown ↔ 공통 `roles/`·`team.json` ↔ Codex TOML. 새 역할은 `jgs-` 접두사를 사용합니다.
- 스킬: `.claude/skills/` ↔ 공통 `skills/` ↔ `.agents/skills/`. `scripts/`, `references/`, `assets/`와 실행 비트도 포함합니다.
- 기준 해시와 비교해 한쪽 수정을 반영하며, 서로 다른 동시 수정은 **전체 쓰기 전에 충돌**로 보고합니다. 파일 시간으로 승자를 정하지 않습니다.
- `--check`는 파일을 변경하지 않고 동기화 누락을 검사합니다. 사라진 사본은 복구하며 삭제·이름 변경을 자동 전파하지 않습니다.
- Windows에서도 심볼릭 링크나 관리자 권한 없이 사용할 수 있는 실제 파일 구조입니다. `.gitattributes`에는 공유 파일 경로의 LF 규칙만 병합해 체크아웃 후에도 해시가 일치하게 유지합니다.

플러그인 저장소만 수정한다면 `--project .`를 생략합니다.
스킬의 `SKILL.md`에는 `name`, `description`을 넣고 스킬 폴더 이름과 일치시킵니다.
관리되는 에이전트는 공통 `name`·`description`·본문만 사용합니다.
플랫폼 전용 모델·권한 필드는 다른 플랫폼으로 변환할 수 없으므로 오류를 보고하고 보존합니다.

이 동기화는 **작업을 수행하는 AI의 스킬 절차와 명시적 명령**으로 실행됩니다.
일반 텍스트 편집기의 저장을 감시하는 백그라운드 서비스는 설치하지 않습니다.
패키지 CI도 동기화 누락과 회귀 테스트를 macOS·Windows·Linux에서 검사합니다.

엔진은 서브모듈 버전을 커밋으로 고정합니다. 원격 `main` 변경은 자동 채택되지 않습니다.
새 버전을 가져온 뒤 동기화하고, 서브모듈 포인터·프로젝트 사본·`.jgs-mirror.json`을 함께 커밋합니다.
엔진 빌드에는 이 Python/스킬 패키지가 필요하지 않습니다.

## 플러그인으로 설치하기

위 프로젝트용 폴더를 사용하면 별도 플러그인 설치 없이 역할을 탐색할 수 있습니다.
여러 프로젝트에서 패키지 스킬을 쓰고 싶다면 기존 플러그인 설치 방식도 지원합니다.

### Codex

```sh
codex plugin marketplace add scahp/JaehoGameStudio --ref main
codex plugin add jaeho-game-studio@jaeho-studio
```

로컬 소스에서는 `codex plugin marketplace add .`를 사용합니다.
Codex 플러그인 설치와 native 에이전트 등록은 별개이므로, 엔진에는 위 동기화 명령으로
`.codex/agents/`를 등록합니다. 기존 Codex 전용 설치기 `scripts/install-codex-agents.py`도 유지합니다.

### Claude Code

```sh
claude plugin marketplace add scahp/JaehoGameStudio
claude plugin install jaeho-game-studio@jaeho-studio
```

이 패키지는 `.claude-plugin/plugin.json`, `agents/`, `skills/`를 제공합니다.
로컬에서 설치 없이 패키지를 확인할 때는 `claude --plugin-dir ./tools/JaehoGameStudio`를 사용합니다.
플러그인 스킬 이름에는 `/jaeho-game-studio:jgs-director`처럼 네임스페이스가 붙습니다.
설치 캐시는 편집 원본이 아닙니다. 변경은 서브모듈 원본 또는 위 프로젝트 사본에서 수행합니다.
플러그인 갱신 후 새 세션이나 해당 클라이언트의 다시 로드 절차를 사용하세요.

## 프로젝트 컨텍스트

```text
.jaeho-studio/
├── project.md
├── contracts.md
├── roles/<role>/checkpoint.md
├── tasks/
└── decisions/
```

각 역할은 자기 체크포인트와 필요한 계약을 읽고, 종료·인계 전에 변경·검증·미해결 항목을 기록합니다. 실제 세션 ID를 알고 있을 때만 `sessions.json`에 기록합니다. Claude Code·Codex·OpenClaw의 대화 이력은 별개이므로 이 파일들이 인계 지점입니다. 같은 역할의 동시 수정은 디렉터가 조율합니다.

프로젝트 기록·세션·자격증명·개인 설정을 이 플러그인 저장소에 넣지 않습니다. 프로젝트 언어, 그래픽스 API, 외부 엔진 경로는 소비하는 프로젝트의 지침에서 확인합니다.

## OpenClaw

Agent Plugins 번들을 지원하는 OpenClaw에서는 패키지를 링크해 스킬을 사용할 수 있습니다.

```sh
openclaw plugins install --link /absolute/path/to/JaehoGameStudio
```

호스트가 신뢰 확인을 요청하면 패키지 경로와 내용을 검토한 뒤 해당 호스트의 절차를 따릅니다. 독립 OpenClaw 에이전트와 역할별 작업 공간 등록은 별도 호스트 설정입니다. `team.json`은 역할 데이터이고 `openclaw-routing.json`은 라우팅 참고 자료이며 자동 적용되지 않습니다. 기존 등록과 허용 목록을 병합하고 사용 중인 호스트의 설정을 검증하세요.

## 유지보수와 검사

`sync-studio.py`로 원본과 native 역할·스킬을 함께 유지합니다. 충돌 보고가 나오면 해당 파일을 검토한 뒤 다시 동기화합니다. 실제 프로젝트의 체크포인트는 소비하는 엔진 저장소에 둡니다.

```sh
python3 scripts/sync-studio.py --check
python3 -m unittest discover -s tests -v
claude plugin validate .claude-plugin/plugin.json --strict
claude --plugin-dir . plugin details jaeho-game-studio
```

형식 근거: [OpenAI plugin packaging](https://developers.openai.com/plugins/build/plugins), [Codex custom agents](https://learn.chatgpt.com/docs/agent-configuration/subagents#custom-agents), [Claude agents](https://code.claude.com/docs/en/sub-agents), [Claude plugin layout](https://code.claude.com/docs/en/plugins-reference), [OpenClaw bundles](https://docs.openclaw.ai/plugins/bundles).

[MIT License](LICENSE)
