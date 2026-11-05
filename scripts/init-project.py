#!/usr/bin/env python3
"""Create per-project JaehoGameStudio context files without replacing existing files."""
import argparse
import json
import os
from pathlib import Path
import stat
import sys


ROLES = ("director", "graphics", "editor", "core", "network", "devrel", "particles")


def resolve_project(path):
    """Resolve the explicitly selected existing project, never the user's home."""
    project = Path(path).expanduser().resolve(strict=True)
    if not project.is_dir():
        raise ValueError("project must be an existing directory")
    return project


def checked_destination(project, relative, directory=False):
    """Reject links/reparse points below the selected canonical project root.

    Windows junctions are reparse points even on Python 3.9, where pathlib has
    no is_junction(). Reject links inside the project too: destination aliases
    make preservation and file ownership ambiguous. Nothing is created here.
    """
    relative = Path(relative)
    if relative.is_absolute() or relative.drive or not relative.parts or ".." in relative.parts:
        raise ValueError("destination must be relative to the project")
    current = project
    for index, part in enumerate(relative.parts):
        current = current / part
        try:
            info = current.lstat()
        except FileNotFoundError:
            return None
        attributes = getattr(info, "st_file_attributes", 0)
        if stat.S_ISLNK(info.st_mode) or attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
            raise ValueError("destination contains a symlink or junction: " + relative.as_posix())
        if not current.resolve(strict=True).is_relative_to(project):
            raise ValueError("destination escapes the project: " + relative.as_posix())
        needs_directory = directory or index < len(relative.parts) - 1
        if needs_directory and not stat.S_ISDIR(info.st_mode):
            raise ValueError("destination parent is not a directory: " + relative.as_posix())
        if not needs_directory and not stat.S_ISREG(info.st_mode):
            raise ValueError("destination is not a regular file: " + relative.as_posix())
    return info


def uses_studio_references(project):
    """Identify the existing native registration without a separate state file."""
    marketplace = Path(".agents/plugins/marketplace.json")
    if checked_destination(project, marketplace) is not None:
        catalog = json.loads((project / marketplace).read_text(encoding="utf-8"))
        if any(entry.get("name") == "jaeho-game-studio" for entry in catalog.get("plugins", [])):
            return True
    relative = Path(".codex/config.toml")
    if checked_destination(project, relative) is None:
        return False
    lines = (project / relative).read_text(encoding="utf-8").splitlines()
    return ("# BEGIN JAEHOGAMESTUDIO REFERENCES" in lines
            and "# END JAEHOGAMESTUDIO REFERENCES" in lines)


def _same_file(path, identity):
    try:
        actual = path.lstat()
        attributes = getattr(actual, "st_file_attributes", 0)
        return (not stat.S_ISLNK(actual.st_mode)
                and not attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
                and os.path.samestat(actual, identity))
    except OSError:
        return False


def publish_files(project, files, directories=(), require_identical=False):
    """Preflight the complete plan, then exclusively create missing files.

    Existing context files are preserved byte-for-byte. Agent profiles instead
    require identical bytes: a known conflict aborts before any directory or
    file creation. Newly created files/directories are rolled back on failure.
    """
    files = {Path(name): data for name, data in files.items()}
    parents = {Path(name) for name in directories}
    for name in tuple(parents):
        parents.update(parent for parent in name.parents if parent != Path("."))
    for name in files:
        parents.update(parent for parent in name.parents if parent != Path("."))
    ordered_parents = sorted(parents, key=lambda path: (len(path.parts), path.as_posix()))
    for parent in ordered_parents:
        checked_destination(project, parent, directory=True)
    for name, expected in files.items():
        if not isinstance(expected, bytes):
            raise TypeError("file contents must be encoded before publication")
        existing = checked_destination(project, name)
        if existing is not None and require_identical and (project / name).read_bytes() != expected:
            raise ValueError("existing agent differs; all files preserved: " + name.as_posix())

    created_files, created_directories, result = [], [], []
    try:
        for parent in ordered_parents:
            if checked_destination(project, parent, directory=True) is None:
                target = project / parent
                try:
                    target.mkdir()
                except FileExistsError:
                    checked_destination(project, parent, directory=True)
                else:
                    created_directories.append((target, target.lstat()))
                    checked_destination(project, parent, directory=True)
        for name, expected in files.items():
            target = project / name
            existing = checked_destination(project, name)
            if existing is not None:
                if require_identical and target.read_bytes() != expected:
                    raise ValueError("existing agent changed during setup; files preserved: " + name.as_posix())
                result.append(("up-to-date" if require_identical else "preserved", name))
                continue
            try:
                # xb provides O_EXCL/no-overwrite on POSIX and Windows. UTF-8
                # bytes with explicit LF are independent of Windows locale.
                stream = target.open("xb")
            except FileExistsError:
                if checked_destination(project, name) is None:
                    raise OSError("destination changed during setup: " + name.as_posix())
                if require_identical and target.read_bytes() != expected:
                    raise ValueError("existing agent changed during setup; files preserved: " + name.as_posix())
                result.append(("up-to-date" if require_identical else "preserved", name))
            else:
                with stream:
                    created_files.append((target, os.fstat(stream.fileno())))
                    stream.write(expected)
                result.append(("created", name))
    except BaseException:
        for path, identity in reversed(created_files):
            if _same_file(path, identity):
                try:
                    path.unlink()
                except OSError:
                    pass
        for path, identity in reversed(created_directories):
            if _same_file(path, identity):
                try:
                    path.rmdir()  # Only empty directories created by this call.
                except OSError:
                    pass
        raise
    return result


def initialize_project(project):
    base = Path(".jaeho-studio")
    text = {
        base / "project.md": (
            "# Project\n\nRepository: " + str(project) + "\n\n"
            "Status: project selected; language, platforms, graphics API and milestones are not decided.\n"
        ),
        base / "contracts.md": (
            "# Shared contracts\n\nNo shared API contracts have been approved yet. "
            "Record ownership, lifetime, threading, errors, versioning and performance constraints when agreed.\n"
        ),
    }
    for role in ROLES:
        text[base / "roles" / role / "checkpoint.md"] = (
            "# " + role + " checkpoint\n\nStatus: no engine implementation task has been assigned. "
            "Read project.md and contracts.md before starting.\n"
        )
    files = {name: content.encode("utf-8") for name, content in text.items()}
    return publish_files(project, files, (base / "tasks", base / "decisions"))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project", type=Path, help="existing engine project directory")
    args = parser.parse_args(argv)
    try:
        project = resolve_project(args.project)
        for status, name in initialize_project(project):
            # Relative ASCII paths also work with non-UTF8 Windows consoles.
            print(status + " " + name.as_posix())
    except (OSError, ValueError) as error:
        print("error: " + str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
