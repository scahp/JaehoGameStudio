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
        text = data.decode("utf-8")
        fields = tomllib.loads(text)
        # config_file reads a regular Codex configuration layer. Metadata is
        # kept in comments so it cannot become unknown runtime config keys.
        if set(fields) == {"developer_instructions"}:
            for key, value in re.findall(r"^# (name|description) = (.+)$", text, re.MULTILINE):
                if key in fields:
                    raise ValueError("duplicate role metadata: " + str(relative))
                fields[key] = json.loads(value)
        if set(fields) != {"name", "description", "developer_instructions"}:
            raise ValueError("managed Codex agents support name, description and developer_instructions only: "
                             + str(relative))
        result = profile(fields["name"], fields["description"], fields["developer_instructions"])
    if result["name"] != relative.stem:
        raise ValueError("agent filename and name differ: " + str(relative))
    return result


def render_profile(value, claude, config_layer=False):
    if claude:
        text = ("---\nname: " + quoted(value["name"]) + "\ndescription: "
                + quoted(value["description"]) + "\n---\n\n" + value["instructions"])
    else:
        prefix = "# " if config_layer else ""
        text = (prefix + "name = " + quoted(value["name"]) + "\n" + prefix + "description = "
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
        # Reject aliases before traversing shared skill resources.
        tree.directory(path.relative_to(tree.root))
        name = valid_name(path.name)
        entry = path.relative_to(tree.root) / "SKILL.md"
        data = tree.read(entry)
        if data is None:
            raise ValueError("skill directory has no SKILL.md: " + str(entry))
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


def choose(label, candidates, baseline):
    values = {digest(value): value for value in candidates if value is not None}
    if not values:
        raise ValueError("all copies are missing; restore or explicitly retire " + label)
    changed = {key: value for key, value in values.items() if key != baseline}
    if len(changed) > 1:
        raise ValueError("mirror conflict: " + label + "; different edits found, all files preserved")
    return next(iter((changed or values).values()))


def synchronize(source, helpers, check=False):
    """Mirror native formats inside the package; never distribute project copies."""
    package = Tree(source, helpers)
    baseline = load_state(package) or {"agents": {}, "skills": {}}
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
    groups = [canonical, read_agents(package, Path("agents"), True),
              read_agents(package, Path("codex-agents"), False)]
    agents = {name: choose("agent " + name, [group.get(name) for group in groups],
                           baseline["agents"].get(name))
              for name in sorted(set(baseline["agents"]).union(*(set(group) for group in groups)))}
    current_skills = read_skills(package, Path("skills"))
    skills = {name: choose("skill " + name, [current_skills.get(name)], baseline["skills"].get(name))
              for name in sorted(set(baseline["skills"]).union(current_skills))}
    for name in skills:
        relative = Path(name)
        if len(relative.parts) < 2 or relative.is_absolute() or relative.drive or ".." in relative.parts:
            raise ValueError("invalid skill state path: " + name)
        valid_name(relative.parts[0])
        if relative.parts[0] in agents:
            raise ValueError("developer role must be an agent, not a duplicate skill: " + relative.parts[0])
    plans = {}

    def plan(relative, data):
        relative = Path(relative)
        old = package.read(relative)
        if old != package.reads[relative]:
            raise ValueError("file changed during sync; all files preserved: " + str(relative))
        mode = package.modes[relative] if old is not None else 0o644
        if old != data:
            plans[(source, relative)] = (package, old, data, mode, mode)

    updated_team = json.loads(raw_team)
    for name, value in agents.items():
        role = name[4:]
        entry = updated_team.setdefault(role, {"agent": name, "title": name, "tasks": []})
        entry["purpose"] = value["description"]
        plan(Path("roles") / (role + ".md"), value["instructions"].encode("utf-8"))
        plan(Path("agents") / (name + ".md"), render_profile(value, True))
        plan(Path("codex-agents") / (name + ".toml"), render_profile(value, False, config_layer=True))
    if updated_team != team:
        plan("team.json", encoded(updated_team))
    plan(STATE, encoded({"schema": 1, "package": ".",
                         "agents": {name: digest(value) for name, value in agents.items()},
                         "skills": {name: digest(value) for name, value in skills.items()}}))
    package.ensure_unchanged()
    if check:
        for _, relative in plans:
            print("out-of-sync package/" + relative.as_posix())
        if plans:
            return 1
    else:
        publish(plans, helpers)
        for _, relative in plans:
            print("mirrored package/" + relative.as_posix())
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
    parser.add_argument("--project", type=Path, help="register package paths in this engine project")
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
        if project is not None:
            connector = runpy.run_path(str(Path(__file__).with_name("connect-project.py")))
            # Validate migration before changing even the package's own mirrors.
            connector["connect"](source, project, globals(), helpers, check=True, quiet=True)
            package_result = synchronize(source, helpers, args.check)
            project_result = connector["connect"](source, project, globals(), helpers, check=args.check)
            return max(package_result, project_result)
        return synchronize(source, helpers, args.check)
    except (OSError, ValueError, TypeError, KeyError) as error:
        # Relative component names keep ordinary success output safe for Windows
        # consoles; errors use backslash escapes if a path cannot be encoded.
        message = ("error: " + str(error)).encode(sys.stderr.encoding or "utf-8", errors="backslashreplace")
        print(message.decode(sys.stderr.encoding or "utf-8"), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
