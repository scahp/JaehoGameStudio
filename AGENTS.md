# JaehoGameStudio maintenance

This repository is the complete shared plugin. Consuming projects register its
relative path. Never copy definitions into project discovery folders or create
symlinks, junctions, hooks, watchers or update jobs.

Claude agents live in `agents/`, Codex role configurations in `codex-agents/`,
shared role data in `team.json` and `roles/`, and shared procedures in `skills/`.
Developer personas are agents only; do not duplicate them as skills. Keep model,
reasoning, tools and permissions unset so roles inherit the existing host.

After creating or editing an agent or skill on either platform, mirror the
package's native formats before finishing. Use Python 3.11 or later:

```sh
python3 scripts/sync-studio.py
python3 scripts/sync-studio.py --check
```

When working from an engine, find the source through `.jgs-mirror.json`'s
`package` path. Adding `--project <engine>` also checks or updates its path
registrations. Ordinary role-body and skill edits stay inside the submodule.
Never edit a client's installed plugin cache. The explicit `--legacy-copies`
option exists only for old clients and regression coverage, not new projects.

Resolve conflicting edits without overwriting either side. Preserve unrelated
settings, agents and project records. Actual decisions, contracts and role
checkpoints belong in the consuming project's `.jaeho-studio/`. Keep credentials
and private conversation history out of this public package.
