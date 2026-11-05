#!/usr/bin/env python3
"""Create project-local Codex role profiles; preserve existing custom profiles."""
import argparse
import json
from pathlib import Path
import runpy
import sys


def toml_string(value):
    # JSON's quoted-string escapes form a TOML basic-string subset. TOML also
    # forbids a literal DEL, which JSON otherwise permits. UTF-8 stays readable.
    return json.dumps(value, ensure_ascii=False).replace("\x7f", "\\u007f")


def agent_files(source_root, helpers):
    team_path = Path("team.json")
    if helpers["checked_destination"](source_root, team_path) is None:
        raise ValueError("plugin team.json is missing")
    team = json.loads((source_root / team_path).read_text(encoding="utf-8"))
    roles = helpers["ROLES"]
    if not isinstance(team, dict) or set(team) != set(roles):
        raise ValueError("team.json must define exactly the seven JaehoGameStudio roles")
    files = {}
    for role in roles:
        entry = team[role]
        if not isinstance(entry, dict) or entry.get("agent") != "jgs-" + role:
            raise ValueError("team.json has an invalid agent ID for " + role)
        name, description = entry["agent"], entry.get("purpose")
        if not isinstance(description, str) or not description.strip():
            raise ValueError("team.json has no description for " + role)
        relative = Path("roles") / (role + ".md")
        if helpers["checked_destination"](source_root, relative) is None:
            raise ValueError("plugin role source is missing: " + relative.as_posix())
        instructions = (source_root / relative).read_text(encoding="utf-8")
        if not instructions.strip():
            raise ValueError("plugin role source is empty: " + relative.as_posix())
        text = "\n".join((
            "name = " + toml_string(name),
            "description = " + toml_string(description),
            "developer_instructions = " + toml_string(instructions),
            "",
        ))
        files[Path(".codex") / "agents" / (name + ".toml")] = text.encode("utf-8")
    return files


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project", type=Path, help="existing engine project directory")
    args = parser.parse_args(argv)
    try:
        # Load the sibling helper without importing/caching a module or running
        # its CLI. Installing agents never initializes .jaeho-studio context.
        helpers = runpy.run_path(str(Path(__file__).with_name("init-project.py")))
        project = helpers["resolve_project"](args.project)
        files = agent_files(Path(__file__).resolve().parents[1], helpers)
        for status, name in helpers["publish_files"](project, files, require_identical=True):
            print(status + " " + name.as_posix())
    except (OSError, ValueError) as error:
        print("error: " + str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
