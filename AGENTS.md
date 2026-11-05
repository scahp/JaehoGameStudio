# JaehoGameStudio maintenance

This repository packages shared roles and skills for Codex, Claude Code and
OpenClaw. Engine code and project checkpoints belong in the consuming project.

When creating or changing an agent or skill on either platform, mirror it to the
other platform before finishing the task. Use Python 3.11 or later:

```sh
python3 scripts/sync-studio.py
python3 scripts/sync-studio.py --check
```

For an installed engine checkout, add `--project /absolute/path/to/engine` to
both commands. The project's `.jgs-mirror.json` records the relative package
path. Run the script from that source package; installed plugin caches are not
editable sources.

The shared role sources are `team.json` and `roles/`; native agents are
`agents/jgs-*.md` for Claude and `.codex/agents/jgs-*.toml` for Codex. Shared
skills, including their scripts, references and assets, are in `skills/`.
Developer personas are agents only. Do not create a same-named role skill;
skills describe reusable procedures such as `jgs-sync`.
New `jgs-*` agents and new skill directories are discovered in either native
project tree. Keep component names and file names consistent.

Sync detects edits using the last synchronized hashes. Resolve conflicting
edits explicitly, then rerun sync; never choose a winner from modification
times. Missing copies are restored. Deleting or renaming a component requires
an explicit coordinated retirement, rather than propagation of a missing file.
Published duplicate role skills are retired by `retired-skills.json`. Sync removes
only their recorded unchanged files, stops on local edits, and prevents old
project copies from recreating them.

Keep role model, reasoning, tool and permission overrides unset so each host
uses its existing settings. Preserve unrelated agents, settings and project
records. Run the focused Python tests when changing the synchronization tools.
