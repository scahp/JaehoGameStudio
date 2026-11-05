#!/usr/bin/env python3
"""Mirror JaehoGameStudio agents and skills between Claude Code and Codex.

Python 3.11+; standard library only. No global configuration, hooks or watchers.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import runpy
import stat
import sys
import tempfile

sys.dont_write_bytecode = True
try:
    import tomllib
except ImportError:
    tomllib = None

STATE = Path(".jgs-mirror.json")
NAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
IGNORED = {".git", "__pycache__", ".DS_Store"}
ATTRIBUTES = (
    "# BEGIN JAEHOGAMESTUDIO MIRRORS\n"
    "/.claude/agents/jgs-*.md text eol=lf\n"
    "/.codex/agents/jgs-*.toml text eol=lf\n"
    "/.claude/skills/** text=auto eol=lf\n"
    "/.agents/skills/** text=auto eol=lf\n"
    "/.jgs-mirror.json text eol=lf\n"
    "# END JAEHOGAMESTUDIO MIRRORS\n"
).encode("ascii")


class SkillFile:
    def __init__(self, data, executable=False):
        self.data = data
        self.executable = executable


def quoted(value):
    return json.dumps(value, ensure_ascii=False).replace("\x7f", "\\u007f")


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def digest(value):
    if isinstance(value, SkillFile):
        return hashlib.sha256(value.data + (b"\x00executable" if value.executable else b"\x00regular")).hexdigest()
    return hashlib.sha256(value if isinstance(value, bytes) else encoded(value)).hexdigest()


def valid_name(name, agent=False):
    if not isinstance(name, str) or not NAME.fullmatch(name) or len(name) > 64:
        raise ValueError("invalid component name: " + repr(name))
    if agent and not name.startswith("jgs-"):
        raise ValueError("managed agents must have a jgs- prefix: " + name)
    return name


class Tree:
    def __init__(self, root, helpers):
        self.root = root
        self.helpers = helpers
        self.reads = {}
        self.modes = {}
        self.retired_skills = set()

    def read(self, relative):
        relative = Path(relative)
        info = self.helpers["checked_destination"](self.root, relative)
        data = None if info is None else (self.root / relative).read_bytes()
        self.reads.setdefault(relative, data)
        self.modes.setdefault(relative, None if info is None else info.st_mode & 0o777)
        return data

    def directory(self, relative):
        return self.helpers["checked_destination"](self.root, Path(relative), directory=True)

    def children(self, relative):
        if self.directory(relative) is None:
            return []
        return sorted((self.root / relative).iterdir())

    def text(self, relative):
        data = self.read(relative)
        return None if data is None else data.decode("utf-8").replace("\r\n", "\n")

    def ensure_unchanged(self):
        for relative, expected in tuple(self.reads.items()):
            if self.read(relative) != expected:
                raise ValueError("file changed during sync; retry after finishing edits: " + str(relative))
            info = self.helpers["checked_destination"](self.root, relative)
            mode = None if info is None else info.st_mode & 0o777
            if mode != self.modes[relative]:
                raise ValueError("file mode changed during sync: " + str(relative))


def scalar(lines, index):
    """Read the name/description YAML string subset, including folded blocks.

    Other skill frontmatter is kept as bytes and is never rewritten or parsed.
    Managed agent frontmatter deliberately has no model or permission overrides.
    """
    value = lines[index].split(":", 1)[1].strip()
    if value in ("|", "|-", "|+", ">", ">-", ">+"):
        following = []
        for line in lines[index + 1:]:
            if line and not line[0].isspace():
                break
            following.append(line)
        indents = [len(line) - len(line.lstrip()) for line in following if line.strip()]
        indent = min(indents) if indents else 0
        pieces = [line[indent:] for line in following]
        return (" " if value.startswith(">") else "\n").join(pieces).strip()
    if value.startswith('"'):
        return json.loads(value)
    if value.startswith("'"):
        if not value.endswith("'"):
            raise ValueError("unterminated YAML string")
        return value[1:-1].replace("''", "'")
    if not value or value[0] in "[{:*&!" or " #" in value:
        raise ValueError("use a plain or JSON-quoted name/description string")
    return value


def frontmatter(data, label, agent=False):
    text = data.decode("utf-8").replace("\r\n", "\n")
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        raise ValueError("missing YAML frontmatter: " + label)
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if end is None:
        raise ValueError("unclosed YAML frontmatter: " + label)
    header, fields = [line.rstrip("\n") for line in lines[1:end]], {}
    for i, line in enumerate(header):
        match = re.match(r"^([A-Za-z][A-Za-z0-9_-]*):", line)
        if match:
            key = match.group(1)
            if key in fields:
                raise ValueError("duplicate frontmatter key in " + label + ": " + key)
            fields[key] = scalar(header, i) if key in ("name", "description") else None
    if agent and set(fields) != {"name", "description"}:
        raise ValueError("managed agent frontmatter supports name and description only; "
                         "keep host-specific settings outside mirrored profiles: " + label)
    if not all(isinstance(fields.get(key), str) and fields[key].strip()
               for key in ("name", "description")):
        raise ValueError("name and description must be nonempty strings: " + label)
    return fields, "".join(lines[end + 1:]).lstrip("\n")


def profile(name, description, instructions):
    valid_name(name, agent=True)
    if not all(isinstance(value, str) and value.strip() for value in (description, instructions)):
        raise ValueError("agent description/instructions must be nonempty: " + name)
    return {"name": name, "description": description, "instructions": instructions}


def native_profile(data, relative, claude):
    if claude:
        fields, body = frontmatter(data, str(relative), agent=True)
        result = profile(fields["name"], fields["description"], body)
    else:
        fields = tomllib.loads(data.decode("utf-8"))
        if set(fields) != {"name", "description", "developer_instructions"}:
            raise ValueError("managed Codex agents support name, description and developer_instructions only: "
                             + str(relative))
        result = profile(fields["name"], fields["description"], fields["developer_instructions"])
    if result["name"] != relative.stem:
        raise ValueError("agent filename and name differ: " + str(relative))
    return result


def render_profile(value, claude):
    if claude:
        text = ("---\nname: " + quoted(value["name"]) + "\ndescription: "
                + quoted(value["description"]) + "\n---\n\n" + value["instructions"])
    else:
        text = ("name = " + quoted(value["name"]) + "\ndescription = "
                + quoted(value["description"]) + "\ndeveloper_instructions = "
                + quoted(value["instructions"]) + "\n")
    return text.encode("utf-8")


def read_agents(tree, relative, claude):
    result = {}
    suffix = ".md" if claude else ".toml"
    for path in tree.children(relative):
        if path.name.startswith("jgs-") and path.suffix == suffix:
            name = path.relative_to(tree.root)
            data = tree.read(name)
            value = native_profile(data, name, claude)
            result[value["name"]] = value
    return result


def read_skills(tree, relative):
    result = {}
    for path in tree.children(relative):
        if path.name in IGNORED or path.name.startswith("."):
            continue
        info = path.lstat()
        if stat.S_ISREG(info.st_mode):
            continue
        # Reject aliases before traversal; the copied layout also works on Windows.
        tree.directory(path.relative_to(tree.root))
        name = valid_name(path.name)
        entry = path.relative_to(tree.root) / "SKILL.md"
        data = tree.read(entry)
        if data is None and name not in tree.retired_skills:
            raise ValueError("skill directory has no SKILL.md: " + str(entry))
        if name not in tree.retired_skills:
            fields, _ = frontmatter(data, str(entry))
            if fields["name"] != name:
                raise ValueError("skill directory and name differ: " + str(entry))

        def walk(directory):
            for child in tree.children(directory):
                if child.name in IGNORED or child.suffix in (".pyc", ".pyo"):
                    continue
                rel = child.relative_to(tree.root)
                info = child.lstat()
                if stat.S_ISDIR(info.st_mode):
                    tree.directory(rel)
                    walk(rel)
                else:
                    content = tree.read(rel)
                    result[child.relative_to(tree.root / relative).as_posix()] = SkillFile(
                        content, bool(info.st_mode & 0o111) if os.name != "nt" else False)
        walk(path.relative_to(tree.root))
    return result


def load_state(tree):
    raw = tree.read(STATE)
    if raw is None:
        return None
    value = json.loads(raw)
    if (not isinstance(value, dict) or value.get("schema") != 1
            or not isinstance(value.get("package"), str)
            or not isinstance(value.get("agents"), dict) or not isinstance(value.get("skills"), dict)):
        raise ValueError("invalid mirror state in " + str(tree.root / STATE))
    for category in ("agents", "skills"):
        if not all(isinstance(key, str) and isinstance(item, str)
                   and re.fullmatch(r"[0-9a-f]{64}", item)
                   for key, item in value[category].items()):
            raise ValueError("invalid mirror hashes in " + str(tree.root / STATE))
    return value


def load_retirements(tree):
    raw = tree.read("retired-skills.json")
    if raw is None:
        return {}
    policy = json.loads(raw)
    if not isinstance(policy, dict) or policy.get("schema") != 1 or not isinstance(policy.get("skills"), dict):
        raise ValueError("invalid retired-skills.json")
    for name, files in policy["skills"].items():
        valid_name(name)
        if not isinstance(files, dict) or not files:
            raise ValueError("retired skill must record its published files: " + name)
        for relative, expected in files.items():
            path = Path(relative)
            if path.is_absolute() or path.drive or not path.parts or ".." in path.parts:
                raise ValueError("invalid retired skill path: " + relative)
            if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
                raise ValueError("invalid retired skill hash: " + relative)
    return policy["skills"]


def choose(label, candidates, baseline):
    values = {digest(value): value for value in candidates if value is not None}
    if not values:
        raise ValueError("all copies are missing; restore or explicitly retire " + label)
    changed = {key: value for key, value in values.items() if key != baseline}
    if len(changed) > 1:
        raise ValueError("mirror conflict: " + label + "; different edits found, all files preserved")
    return next(iter((changed or values).values()))


def choose_mirror(label, source_values, host_values, source_hash, host_hash):
    # Resolve uncommitted edits against each tree's own baseline first. A host
    # can still be at A after the package was published at B; an intentional
    # package edit back to A must not be mistaken for an unchanged host copy.
    source_values = [value for value in source_values if value is not None]
    host_values = [value for value in host_values if value is not None]
    source = choose(label, source_values, source_hash) if source_values else None
    host = choose(label, host_values, host_hash) if host_values else None
    if source is None and host is None:
        raise ValueError("all copies are missing; restore or explicitly retire " + label)
    if source is None:
        return host
    if host is None or digest(source) == digest(host):
        return source
    source_changed = (digest(source) != host_hash
                      or (source_hash is not None and digest(source) != source_hash))
    host_changed = digest(host) != host_hash
    if source_changed and host_changed:
        raise ValueError("mirror conflict: " + label + "; different edits found, all files preserved")
    return source if source_changed else host


def synchronize(source, project, helpers, check=False):
    package = Tree(source, helpers)
    host = None if project is None else Tree(project, helpers)
    if project is not None and (project == source or project.is_relative_to(source)):
        raise ValueError("project must be outside the plugin package")
    source_state = load_state(package)
    host_state = load_state(host) if host else None
    retired = load_retirements(package)
    package.retired_skills = set(retired)
    if host:
        host.retired_skills = set(retired)
    if host_state and (project / host_state["package"]).resolve() != source:
        raise ValueError("project mirror state points to a different package")
    source_base = source_state or {"agents": {}, "skills": {}}
    host_base = host_state or {"agents": {}, "skills": {}}
    raw_team = package.read("team.json")
    team = json.loads(raw_team) if raw_team is not None else None
    if not isinstance(team, dict) or not team:
        raise ValueError("team.json must define the studio roles")
    canonical = {}
    for role, entry in team.items():
        valid_name(role)
        name = "jgs-" + role
        if not isinstance(entry, dict) or entry.get("agent") != name:
            raise ValueError("invalid role in team.json: " + role)
        body = package.text(Path("roles") / (role + ".md"))
        if body is not None:
            canonical[name] = profile(name, entry.get("purpose"), body)
    agent_groups = [canonical, read_agents(package, Path("agents"), True),
                    read_agents(package, Path(".codex/agents"), False)]
    if host:
        agent_groups += [read_agents(host, Path(".claude/agents"), True),
                         read_agents(host, Path(".codex/agents"), False)]
    skill_groups = [read_skills(package, Path("skills"))]
    if host:
        skill_groups += [read_skills(host, Path(".claude/skills")),
                         read_skills(host, Path(".agents/skills"))]
    agents = {name: choose_mirror("agent " + name,
                                 [group.get(name) for group in agent_groups[:3]],
                                 [group.get(name) for group in agent_groups[3:]],
                                 source_base["agents"].get(name), host_base["agents"].get(name))
              for name in sorted(set(source_base["agents"]).union(host_base["agents"],
                                 *(set(g) for g in agent_groups)))}
    skills = {name: choose_mirror("skill " + name,
                                 [group.get(name) for group in skill_groups[:1]],
                                 [group.get(name) for group in skill_groups[1:]],
                                 source_base["skills"].get(name), host_base["skills"].get(name))
              for name in sorted(set(source_base["skills"]).union(host_base["skills"],
                                 *(set(g) for g in skill_groups)))
              if Path(name).parts and Path(name).parts[0] not in retired}
    # State paths are data, never paths to write without validation.
    for name in skills:
        relative = Path(name)
        if len(relative.parts) < 2 or relative.is_absolute() or ".." in relative.parts:
            raise ValueError("invalid skill state path: " + name)
        valid_name(relative.parts[0])
        if relative.parts[0] in agents:
            raise ValueError("developer role must be an agent, not a duplicate skill: " + relative.parts[0])
    plans = {}

    def plan(tree, relative, data, executable=None):
        key = (tree.root, Path(relative))
        old = tree.read(relative)
        if old != tree.reads[Path(relative)]:
            raise ValueError("file changed during sync; all files preserved: " + str(relative))
        mode = ((tree.root / relative).stat().st_mode & 0o777) if old is not None else 0o644
        desired_mode = mode if executable is None or os.name == "nt" else (0o755 if executable else 0o644)
        if old != data or mode != desired_mode:
            plans[key] = (tree, old, data, mode, desired_mode)

    retirement_trees = [(package, Path("skills"), skill_groups[0])]
    if host:
        retirement_trees += [(host, Path(".claude/skills"), skill_groups[1]),
                             (host, Path(".agents/skills"), skill_groups[2])]
    for tree, directory, files in retirement_trees:
        for name, value in files.items():
            parts = Path(name).parts
            if parts[0] in retired:
                expected = retired[parts[0]].get(Path(*parts[1:]).as_posix())
                if digest(value) != expected:
                    raise ValueError("retired role skill has local edits or unknown files; migrate them to its agent first: " + name)
                plan(tree, directory / name, None)

    updated_team = json.loads(raw_team)
    for name, value in agents.items():
        role = name[4:]
        entry = updated_team.setdefault(role, {"agent": name, "title": name, "tasks": []})
        entry["purpose"] = value["description"]
        plan(package, Path("roles") / (role + ".md"), value["instructions"].encode("utf-8"))
        plan(package, Path("agents") / (name + ".md"), render_profile(value, True))
        plan(package, Path(".codex/agents") / (name + ".toml"), render_profile(value, False))
        if host:
            plan(host, Path(".claude/agents") / (name + ".md"), render_profile(value, True))
            plan(host, Path(".codex/agents") / (name + ".toml"), render_profile(value, False))
    if updated_team != team:
        plan(package, Path("team.json"), encoded(updated_team))
    for name, value in skills.items():
        plan(package, Path("skills") / name, value.data, value.executable)
        if host:
            plan(host, Path(".claude/skills") / name, value.data, value.executable)
            plan(host, Path(".agents/skills") / name, value.data, value.executable)
    state = {"schema": 1, "package": ".", "agents": {key: digest(value) for key, value in agents.items()},
             "skills": {key: digest(value) for key, value in skills.items()}}
    plan(package, STATE, encoded(state))
    if host:
        host_record = dict(state, package=Path(os.path.relpath(source, project)).as_posix())
        plan(host, STATE, encoded(host_record))
        # Parent repositories can enforce CRLF even when the submodule uses LF.
        # Merge only the scoped mirror rules; preserve every existing byte.
        existing = host.read(".gitattributes") or b""
        normalized = existing.replace(b"\r\n", b"\n")
        if ATTRIBUTES not in normalized:
            if b"# BEGIN JAEHOGAMESTUDIO MIRRORS" in existing or b"# END JAEHOGAMESTUDIO MIRRORS" in existing:
                raise ValueError("existing studio .gitattributes block differs; all files preserved")
            separator = b"\n" if existing.endswith(b"\n") else b"\n\n" if existing else b""
            plan(host, Path(".gitattributes"), existing + separator + ATTRIBUTES)
    package.ensure_unchanged()
    if host:
        host.ensure_unchanged()
    if check:
        if plans:
            for (root, relative) in plans:
                print("out-of-sync " + ("package/" if root == source else "project/") + relative.as_posix())
            return 1
        print("up-to-date: " + str(len(agents)) + " agents, " + str(len(skills)) + " skill files")
        return 0
    publish(plans, helpers)
    # Files were explicitly retired by policy and verified before publication.
    # Remove empty containers only; never recursively remove user data/caches.
    for tree, directory, _ in retirement_trees:
        for name, files in retired.items():
            root = directory / name
            directories = {root}
            for published in files:
                directories.update(root / parent for parent in Path(published).parents)
            for relative in sorted(directories, key=lambda value: len(value.parts), reverse=True):
                try:
                    if tree.directory(relative) is not None:
                        (tree.root / relative).rmdir()
                except (OSError, ValueError):
                    pass
    for (root, relative), change in plans.items():
        print(("retired " if change[2] is None else "mirrored ")
              + ("package/" if root == source else "project/") + relative.as_posix())
    print("up-to-date: " + str(len(agents)) + " agents, " + str(len(skills)) + " skill files")
    return 0


def publish(plans, helpers):
    """Preflight every destination; rollback this transaction on a write failure."""
    for (root, relative), (tree, old, data, mode, desired_mode) in plans.items():
        if tree.read(relative) != old:
            raise ValueError("destination changed during sync: " + str(relative))
    created_dirs, written = [], []
    try:
        for (root, relative), (tree, old, data, previous_mode, desired_mode) in plans.items():
            for parent in reversed(relative.parents):
                if parent == Path("."):
                    continue
                if helpers["checked_destination"](root, parent, directory=True) is None:
                    (root / parent).mkdir()
                    created_dirs.append(root / parent)
            target = root / relative
            replace_file(tree, relative, old, data, desired_mode)
            written.append((tree, relative, old, data, previous_mode))
    except BaseException:
        for tree, relative, old, data, mode in reversed(written):
            if tree.read(relative) == data:
                if old is None:
                    (tree.root / relative).unlink()
                else:
                    replace_file(tree, relative, data, old, mode)
        for path in reversed(created_dirs):
            try:
                path.rmdir()
            except OSError:
                pass
        raise


def replace_file(tree, relative, expected, data, mode):
    target = tree.root / relative
    temporary = None
    try:
        if data is None:
            if tree.read(relative) != expected:
                raise ValueError("destination changed during sync: " + str(relative))
            target.unlink()
            return
        with tempfile.NamedTemporaryFile(dir=target.parent, prefix=".jgs-write-", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(data)
        temporary.chmod(mode)
        if tree.read(relative) != expected:
            raise ValueError("destination changed during sync: " + str(relative))
        os.replace(temporary, target)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, help="also mirror into this existing engine project")
    parser.add_argument("--check", action="store_true", help="report drift without changing any files")
    args = parser.parse_args(argv)
    if tomllib is None:
        print("error: sync-studio.py needs Python 3.11+ (tomllib); use python3.11 or newer", file=sys.stderr)
        return 1
    try:
        helpers = runpy.run_path(str(Path(__file__).with_name("init-project.py")))
        source = Path(__file__).resolve().parents[1]
        if args.project is not None:
            selected = args.project.expanduser().lstat()
            if (stat.S_ISLNK(selected.st_mode)
                    or getattr(selected, "st_file_attributes", 0)
                    & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)):
                raise ValueError("project is a symlink or junction; select its real directory")
        project = None if args.project is None else helpers["resolve_project"](args.project)
        return synchronize(source, project, helpers, args.check)
    except (OSError, ValueError, TypeError, KeyError) as error:
        # Relative component names keep ordinary success output safe for Windows
        # consoles; errors use backslash escapes if a path cannot be encoded.
        message = ("error: " + str(error)).encode(sys.stderr.encoding or "utf-8", errors="backslashreplace")
        print(message.decode(sys.stderr.encoding or "utf-8"), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
