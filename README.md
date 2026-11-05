# JaehoGameStudio

Claude Code / Codex / OpenClaw용 게임 엔진 개발 플러그인입니다.
일곱 역할과 공통 스킬을 하나의 Git 서브모듈로 제공합니다.

| 에이전트 | 책임 |
| --- | --- |
| jgs-director | 목표, 작업 배정, 모듈 계약, 통합 |
| jgs-graphics | 렌더러, GPU, 셰이더, 조명 |
| jgs-editor | 에디터, 뷰포트, Inspector, 자산 UI |
| jgs-core | 플랫폼, 메모리, 파일, 스레드, 자산 로딩 |
| jgs-network | 프로토콜, 복제, 예측, 서버 권위 |
| jgs-devrel | 외부 엔진 비교, 도입·라이선스 검토 |
| jgs-particles | 이미터, 효과 데이터, 파티클 시뮬레이션 |

## 디렉터리 구성

프로젝트에는 **경로 등록만**, 모든 에이전트·스킬·도구는 **서브모듈 안에** 둡니다.
심볼릭 링크·정션과 프로젝트 정의 복사본을 사용하지 않습니다. 플러그인 경로는
마켓플레이스 설정에 이미 있으므로 엔진 루트에 별도 상태 파일을 만들지 않습니다.

```text
jEngine/
├── .agents/plugins/marketplace.json  # Codex 플러그인 경로
├── .claude/settings.json             # Claude 마켓플레이스 경로
├── tools/JaehoGameStudio/            # Git 서브모듈
│   ├── plugin.json                   # Codex/OpenClaw 플러그인
│   ├── .claude-plugin/
│   │   ├── plugin.json               # Claude 플러그인
│   │   └── marketplace.json
│   ├── agents/jgs-*.md               # Claude 에이전트
│   ├── codex-agents/jgs-*.toml        # Codex 역할 구성
│   ├── skills/jgs-sync/SKILL.md       # 공통 미러링 스킬
│   ├── roles/ + team.json            # 공통 역할 데이터
│   ├── scripts/                      # 명시적으로 실행하는 도구
│   └── .jgs-mirror.json              # 패키지 내부 변환 기준
└── .jaeho-studio/                    # 프로젝트별 기록, Git 제외
```

`.codex/agents/`, `.claude/agents/`, `.agents/skills/`, `.claude/skills/`에
JaehoGameStudio 정의를 배포하지 않습니다. 다른 사용자의 로컬 정의는 보존합니다.
역할의 모델·추론 강도·권한·도구는 기존 호스트 설정을 따릅니다.

## 프로젝트에 연결

처음 연결할 때 프로젝트 루트에서 실행합니다. Python 3.11 이상이며 추가 패키지는 없습니다.
Windows에서는 설치된 `python` 또는 `py -3.11`을 사용합니다.

```sh
git submodule add https://github.com/scahp/JaehoGameStudio.git tools/JaehoGameStudio
python3 tools/JaehoGameStudio/scripts/sync-studio.py --project .
```

기존 jEngine을 받으면 등록 파일도 함께 받으므로 서브모듈만 초기화합니다.

```sh
git submodule update --init -- tools/JaehoGameStudio
```

연결 도구는 경로 설정을 병합합니다. 이전에 배포된 수정 없는 사본만 정리하며,
로컬 편집·충돌이 있으면 보존하고 중단합니다. 체크포인트 초기화는 선택 사항입니다.

```sh
python3 tools/JaehoGameStudio/scripts/init-project.py .
```

## Codex에서 사용

프로젝트에는 `.agents/plugins/marketplace.json`으로 플러그인 경로만 등록합니다.
프로젝트 `.codex` 폴더는 만들지 않습니다. 설치 시 활성화는 Codex 사용자 설정에 저장합니다.

```sh
codex plugin marketplace add .
codex plugin add jaeho-game-studio@jengine-tools
```

위 이름은 jEngine의 마켓플레이스 이름입니다. 다른 프로젝트의 기본 이름은
`project-tools`이며 해당 마켓플레이스의 `name`을 사용합니다.

**플러그인 등록과 커스텀 에이전트 등록은 별개입니다.** 이 패키지의 스킬은
플러그인으로 로드하지만 `.agents`만으로 `codex-agents/`의 일곱 역할을 자동 등록하지는
않습니다. Codex 커스텀 에이전트가 필요하면 호스트의 기존 등록을 사용하거나 사용자
설정에 별도로 등록합니다. 프로젝트 설정 도구가 전역 설정을 자동 변경하지 않습니다.

사용자 설정에서 원본을 직접 참조하는 예시는 다음과 같습니다.

```toml
# ~/.codex/config.toml (프로젝트 밖의 사용자 설정)
[agents.jgs-graphics]
description = "렌더러, GPU 자원과 그래픽스 파이프라인을 개발한다."
config_file = "/absolute/path/to/engine/tools/JaehoGameStudio/codex-agents/jgs-graphics.toml"
```

역할 TOML의 실행 설정은 `developer_instructions`뿐입니다.
이름·설명은 미러링용 주석이며 모델이나 권한을 덮어쓰지 않습니다.

## Claude Code에서 사용

서브모듈 원본을 직접 읽으려면 프로젝트 루트에서 실행합니다.

```sh
claude --plugin-dir ./tools/JaehoGameStudio
```

일곱 에이전트와 `/jaeho-game-studio:jgs-sync`를 플러그인에서 읽습니다.
실행 중 파일을 수정했다면 `/reload-plugins`를 사용합니다.
`.claude/settings.json`의 경로는 마켓플레이스 등록용입니다. 일반 `claude`에서
마켓플레이스로 설치한 플러그인은 호스트 캐시를 사용합니다.
**서브모듈 변경을 직접 쓰려면 위 `--plugin-dir` 실행 방식을 사용하세요.**

클라이언트가 관리하는 설치 캐시는 프로젝트 사본과 별개입니다. 캐시를 원본에
연결하거나 직접 수정하지 않습니다. 설치·로딩 검증과 실제 계정 로그인 성공도 구분합니다.

## 양방향 미러링

어느 플랫폼에서 역할을 수정하든 패키지의 다른 형식에도 반영합니다.
공통 스킬은 하나의 `skills/`를 사용하므로 프로젝트로 복사하지 않습니다.
미러링 스킬과 스크립트도 모두 이 서브모듈 안에 있습니다.

```sh
python3 tools/JaehoGameStudio/scripts/sync-studio.py --project .
python3 tools/JaehoGameStudio/scripts/sync-studio.py --project . --check
```

- Claude `agents/` ↔ Codex `codex-agents/` ↔ 공통 `roles/`·`team.json`을 비교합니다.
- 충돌은 쓰기 전에 보고하며 파일 시간으로 승자를 정하지 않습니다.
- `--check`는 읽기 전용입니다. 새 역할은 패키지 내부 형식에 반영하며 호스트 등록은 별도입니다.
- 기존 역할 본문·공통 스킬 업데이트는 서브모듈 안에서 끝납니다.
- 삭제·이름 변경은 별도 검토합니다. 개발 역할은 에이전트로만 제공합니다.

패키지만 수정할 때는 `--project .`를 생략합니다. 파일 감시기, Git hook,
예약 작업, 자동 업데이트 서비스는 설치하지 않습니다.
구버전 호환용 `--legacy-copies`와 `install-codex-agents.py`는 신규 구성에 사용하지 않습니다.

## Git 업데이트와 프로젝트 기록

서브모듈은 부모 저장소가 지정한 커밋을 사용합니다. 부모를 받은 뒤 해당 버전을
체크아웃하면 됩니다. 플러그인의 원격 main을 자동으로 채택하지 않습니다.

```sh
git pull
git submodule update --init -- tools/JaehoGameStudio
```

역할 본문과 공통 스킬은 새 세션에서 갱신된 원본을 읽습니다. 새 역할 이름·설명은 패키지에
커밋하며 호스트의 에이전트 등록은 별도로 관리합니다. 패키지 커밋을 먼저 푸시하고 부모의
서브모듈 포인터를 갱신합니다.

실제 결정, 계약, 역할별 체크포인트는 프로젝트 `.jaeho-studio/`에 둡니다.
비공개 대화 이력, 세션과 자격증명은 플러그인 저장소에 넣지 않습니다.

## OpenClaw와 검사

OpenClaw는 호스트의 경로 등록으로 이 패키지를 로드합니다. 독립 역할 에이전트와
워크스페이스는 호스트 설정입니다. `team.json`과 `openclaw-routing.json`이 호스트
설정을 자동으로 바꾸지는 않습니다.

```sh
python3 scripts/sync-studio.py --check
python3 -m unittest discover -s tests
claude plugin validate .claude-plugin/plugin.json --strict
claude --plugin-dir . plugin details jaeho-game-studio
```

형식 근거: [Codex config_file](https://learn.chatgpt.com/docs/config-file/config-reference),
[OpenAI plugin packaging](https://developers.openai.com/plugins/build/plugins),
[Claude direct plugin loading](https://code.claude.com/docs/en/plugins/create),
[Claude plugin layout](https://code.claude.com/docs/en/plugins-reference).

[MIT License](LICENSE)
