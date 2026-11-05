# JaehoGameStudio

AI plugins for game-engine development with Codex and OpenClaw.

개인 게임 엔진 개발을 위한 **Codex / OpenClaw 에이전트 플러그인**입니다. 일곱 역할의 지침과 프로젝트별 체크포인트를 공유합니다. 엔진 실행 파일에 링크하는 라이브러리는 아니며, 엔진 저장소의 개발 도구로 사용합니다.

| 에이전트 | 책임 |
| --- | --- |
| jgs-director | 목표, 작업 배정, 모듈 계약, 통합 검증 |
| jgs-graphics | 렌더러, GPU 자원, 셰이더, 조명, 그래픽스 성능 |
| jgs-editor | 에디터, 뷰포트, Inspector, 자산 UI, Undo/Redo |
| jgs-core | 플랫폼, 메모리, 파일, 스레드, 직렬화, 자산 로딩 |
| jgs-network | 프로토콜, 복제, 동기화, 예측, 서버 권위 |
| jgs-devrel | 외부 엔진 비교, 근거 조사, 도입·라이선스 검토 |
| jgs-particles | 이미터, 효과 데이터, CPU/GPU 시뮬레이션 |

## Codex에서 설치하기

```sh
git clone https://github.com/scahp/JaehoGameStudio.git
cd JaehoGameStudio
codex plugin marketplace add .
codex plugin add jaeho-game-studio@jaeho-studio
```

로컬 마켓플레이스는 `.agents/plugins/marketplace.json`, 플러그인 원본은 루트 `plugin.json`과 `skills/`입니다. 데스크톱 앱에서는 Plugins의 **JaehoGameStudio** 소스에서 설치할 수도 있습니다. 설치 후 새 대화에서 `$jgs-director` 또는 `$jgs-graphics`처럼 역할을 호출합니다. 데스크톱 앱이 새 로컬 소스를 표시하지 않으면 앱을 다시 시작합니다.

원격 마켓플레이스로 등록하려면 첫 명령 대신 다음을 사용합니다.

```sh
codex plugin marketplace add scahp/JaehoGameStudio --ref main
```

스킬 설치와 별도로, 실제 위임할 커스텀 에이전트 일곱 개를 **해당 엔진 프로젝트에** 등록할 수 있습니다. Python 3.9 이상이 필요합니다. Windows에서는 `python3` 대신 `py -3` 또는 `python`을 사용합니다.

```sh
python3 scripts/install-codex-agents.py /absolute/path/to/engine
python3 scripts/init-project.py /absolute/path/to/engine
```

첫 명령은 엔진의 `.codex/agents/jgs-*.toml`에 역할 정의를 생성합니다. 기존 내용과 다르면 보존하고 오류를 보고하므로 먼저 차이를 검토하세요. 두 번째 명령은 `.jaeho-studio/`에 없는 컨텍스트 파일만 만듭니다. 기존 작업 기록을 덮어쓰지 않습니다. 에이전트 정의를 추가한 뒤 새 Codex 대화에서 사용하세요.

역할 파일에 모델·추론 강도를 고정하지 않습니다. 호스트가 결정한 부모 설정을 사용하며, 이 도구는 사용자 전역 설정·권한·자동화를 변경하지 않습니다.

예시 요청:

> `$jgs-director` 프로젝트 `/absolute/path/to/engine`에서 에셋 로딩의 병목을 조사하고 담당자별 수정 범위와 검증 방법을 정해줘.

## jEngine의 서브모듈로 사용하기

jEngine에서 다음 경로를 사용합니다.

```text
tools/JaehoGameStudio/           # 공유 플러그인, 지정 커밋으로 고정
.agents/plugins/marketplace.json # jEngine 개발 도구 마켓플레이스
.jaeho-studio/                  # 이 엔진의 실제 작업 기록
```

이미 서브모듈이 등록된 jEngine 체크아웃에서는 루트에서 실행합니다.

```sh
git submodule update --init -- tools/JaehoGameStudio
python3 tools/JaehoGameStudio/scripts/install-codex-agents.py .
python3 tools/JaehoGameStudio/scripts/init-project.py .
```

jEngine의 Plugins 소스 **jEngine development tools**에서 설치할 수 있습니다. CLI에서는 jEngine 루트에서 먼저 마켓플레이스를 등록합니다.

```sh
codex plugin marketplace add .
codex plugin add jaeho-game-studio@jengine-tools
```

새 프로젝트에 서브모듈을 처음 추가할 때는 `git submodule add https://github.com/scahp/JaehoGameStudio.git tools/JaehoGameStudio`를 사용합니다.

엔진은 플러그인 버전을 Git 커밋으로 고정합니다. 플러그인 원격 `main`이 갱신되어도 엔진의 포인터는 자동 변경되지 않습니다. 업데이트할 때는 새 커밋을 검토하고 서브모듈 포인터를 함께 커밋하세요. 엔진 빌드에는 이 Python/스킬 패키지가 필요하지 않습니다.

## 프로젝트 컨텍스트

```text
.jaeho-studio/
├── project.md
├── contracts.md
├── roles/<role>/checkpoint.md
├── tasks/
└── decisions/
```

각 역할은 자기 체크포인트와 필요한 계약을 읽고, 종료·인계 전에 변경·검증·미해결 항목을 기록합니다. 실제 세션 ID를 알고 있을 때만 `sessions.json`에 기록합니다. Codex와 OpenClaw의 대화 이력은 별개이므로 이 파일들이 인계 지점입니다. 같은 역할의 동시 수정은 디렉터가 조율합니다.

프로젝트 기록·세션·자격증명·개인 설정을 이 플러그인 저장소에 넣지 않습니다. 프로젝트 언어, 그래픽스 API, 외부 엔진 경로는 소비하는 프로젝트의 지침에서 확인합니다.

## OpenClaw

Agent Plugins 번들을 지원하는 OpenClaw에서는 패키지를 링크해 스킬을 사용할 수 있습니다.

```sh
openclaw plugins install --link /absolute/path/to/JaehoGameStudio
```

호스트가 신뢰 확인을 요청하면 패키지 경로와 내용을 검토한 뒤 해당 호스트의 절차를 따릅니다. 독립 OpenClaw 에이전트와 역할별 작업 공간 등록은 별도 호스트 설정입니다. `team.json`은 역할 데이터이고 `openclaw-routing.json`은 라우팅 참고 자료이며 자동 적용되지 않습니다. 기존 등록과 허용 목록을 병합하고 사용 중인 호스트의 설정을 검증하세요.

## 유지보수와 검사

`team.json`·`roles/`·`skills/`의 내용을 함께 유지합니다. 새 역할 지침을 프로젝트 에이전트에 반영할 때는 설치기의 충돌 보고를 보고 해당 파일을 검토합니다. 실제 프로젝트의 체크포인트는 소비하는 엔진 저장소에 둡니다.

```sh
python3 -m unittest discover -s tests -v
```

형식 근거: [OpenAI plugin packaging](https://developers.openai.com/plugins/build/plugins), [Codex custom agents](https://learn.chatgpt.com/docs/agent-configuration/subagents#custom-agents), [OpenClaw bundles](https://docs.openclaw.ai/plugins/bundles).

[MIT License](LICENSE)
