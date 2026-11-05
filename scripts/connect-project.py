#!/usr/bin/env python3
"""Register a submodule by relative paths, without copying definitions or creating links."""
import argparse
import json
from pathlib import Path
import runpy
import sys

sys.dont_write_bytecode = True
BEGIN = "# BEGIN JAEHOGAMESTUDIO REFERENCES"
END = "# END JAEHOGAMESTUDIO REFERENCES"


def without_block(data, begin, end):
    text = data.decode("utf-8")
    lines = text.splitlines(keepends=True)
    starts = [i for i, line in enumerate(lines) if line.strip() == begin]
    ends = [i for i, line in enumerate(lines) if line.strip() == end]
    if not starts and not ends:
        return data
    if len(starts) != 1 or len(ends) != 1 or starts[0] >= ends[0]:
        raise ValueError("invalid managed configuration block; existing file preserved")
    return "".join(lines[:starts[0]] + lines[ends[0] + 1:]).encode("utf-8")


def connect(source, project, mirror, helpers, check=False, quiet=False):
    Tree, digest = mirror["Tree"], mirror["digest"]
    package, host = Tree(source, helpers), Tree(project, helpers)
    if source == project or not source.is_relative_to(project):
        raise ValueError("the plugin must be a real directory inside the consuming project")
    relative = source.relative_to(project)
    host.directory(relative)  # Reject symlinks and Windows junctions, including ancestors.
    agents = mirror["read_agents"](package, Path("codex-agents"), False)
    if not agents:
        raise ValueError("package has no Codex agents")
    state_raw = host.read(mirror["STATE"])
    # Read former state only to migrate it away; native configuration already
    # records the package path and is the source of truth for new projects.
    state = json.loads(state_raw) if state_raw is not None else None
    if state_raw is not None and (not isinstance(state, dict) or state.get("schema") not in (1, 2)):
        raise ValueError("invalid former project registration; file preserved")
    if state and (project / state.get("package", "")).resolve() != source:
        raise ValueError("project registration points to a different package")
    plans = {}

    def plan(path, data):
        path = Path(path)
        old = host.read(path)
        if old != data:
            mode = host.modes[path] if old is not None else 0o644
            plans[(project, path)] = (host, old, data, mode, mode)

    # Retire only unchanged managed copies. Unrelated agents/skills stay in place.
    if state and state.get("schema") == 1:
        state = mirror["load_state"](host)
        for directory, claude in ((".claude/agents", True), (".codex/agents", False)):
            found = mirror["read_agents"](host, Path(directory), claude)
            for name, expected in state["agents"].items():
                if name not in found:
                    continue
                if digest(found[name]) != expected:
                    raise ValueError("local agent edits must be preserved in the package before migration: " + name)
                plan(Path(directory) / (name + (".md" if claude else ".toml")), None)
        for directory in (".claude/skills", ".agents/skills"):
            found = mirror["read_skills"](host, Path(directory))
            for name, expected in state["skills"].items():
                if name not in found:
                    continue
                if digest(found[name]) != expected:
                    raise ValueError("local skill edits must be preserved in the package before migration: " + name)
                plan(Path(directory) / name, None)
    elif state and (state.get("schema") != 2 or state.get("mode") != "references"):
        raise ValueError("unsupported project registration state")
    # A fresh project must not already contain competing JGS role definitions.
    if not state or state.get("schema") == 2:
        for directory, claude in ((".claude/agents", True), (".codex/agents", False)):
            overlap = set(mirror["read_agents"](host, Path(directory), claude)) & set(agents)
            if overlap:
                raise ValueError("competing project role definitions; preserve and remove them first: " + ", ".join(sorted(overlap)))

    codex_old = host.read(".codex/config.toml")
    if codex_old is not None:
        codex_base = without_block(codex_old, BEGIN, END)
        if codex_base != codex_old:
            # Remove our former registration, preserving unrelated host config.
            remaining = mirror["tomllib"].loads(codex_base.decode("utf-8"))
            plan(".codex/config.toml", codex_base if remaining else None)
    # Keep existing marketplace metadata and unrelated entries.
    marketplace_raw = host.read(".agents/plugins/marketplace.json")
    marketplace = json.loads(marketplace_raw) if marketplace_raw else {"name": "project-tools", "plugins": []}
    entries = marketplace.setdefault("plugins", [])
    target = {"name": "jaeho-game-studio", "source": {"source": "local", "path": "./" + relative.as_posix()},
              "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"}, "category": "Developer Tools"}
    matches = [entry for entry in entries if entry.get("name") == target["name"]]
    if len(matches) > 1 or (matches and matches[0].get("source") != target["source"]):
        raise ValueError("existing Codex plugin source conflicts; configuration preserved")
    if not matches:
        entries.append(target)
        plan(".agents/plugins/marketplace.json", mirror["encoded"](marketplace))
    settings_raw = host.read(".claude/settings.json")
    settings = json.loads(settings_raw) if settings_raw else {}
    source_entry = {"source": {"source": "directory", "path": "./" + relative.as_posix()}}
    known = settings.setdefault("extraKnownMarketplaces", {})
    if "jaeho-studio" in known and known["jaeho-studio"].get("source") != source_entry["source"]:
        raise ValueError("existing Claude marketplace source conflicts; configuration preserved")
    if "jaeho-studio" not in known:
        known["jaeho-studio"] = source_entry
    settings.setdefault("enabledPlugins", {}).setdefault("jaeho-game-studio@jaeho-studio", True)
    if not settings_raw or settings != json.loads(settings_raw):
        plan(".claude/settings.json", mirror["encoded"](settings))
    if state_raw is not None:
        plan(mirror["STATE"], None)
    attributes = host.read(".gitattributes")
    if attributes is not None:
        cleaned = without_block(attributes, "# BEGIN JAEHOGAMESTUDIO MIRRORS", "# END JAEHOGAMESTUDIO MIRRORS")
        if cleaned != attributes:
            plan(".gitattributes", cleaned.rstrip(b"\r\n") + b"\n")
    package.ensure_unchanged()
    host.ensure_unchanged()
    if check:
        if not quiet:
            for _, path in plans:
                print("out-of-date project/" + path.as_posix())
        return int(bool(plans))
    mirror["publish"](plans, helpers)
    # Empty former containers are harmless; remove them only when empty.
    for directory in (".claude/skills/jgs-sync", ".agents/skills/jgs-sync", ".claude/skills", ".agents/skills", ".claude/agents", ".codex/agents", ".codex"):
        try:
            host.directory(directory)
            (project / directory).rmdir()
        except (OSError, ValueError):
            pass
    print("registered plugin paths; package contains " + str(len(agents)) + " agent definitions; no project .codex or definition copies created")
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project", type=Path)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    mirror = runpy.run_path(str(Path(__file__).with_name("sync-studio.py")))
    helpers = runpy.run_path(str(Path(__file__).with_name("init-project.py")))
    try:
        if mirror["tomllib"] is None:
            raise ValueError("Python 3.11+ is required")
        project = helpers["resolve_project"](args.project)
        return connect(Path(__file__).resolve().parents[1], project, mirror, helpers, args.check)
    except (OSError, ValueError, KeyError, TypeError) as error:
        print("error: " + str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
