"""Black-box mirror regressions; all writes stay in disposable package copies."""

import json
import os
from pathlib import Path
import runpy
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

try:
    import tomllib
except ImportError:
    tomllib = None


SOURCE = Path(__file__).resolve().parents[1]


def snapshot(root):
    """Include directory entries and links as well as file bytes and mtimes."""
    result = {}

    def visit(path):
        name = path.relative_to(root).as_posix()
        metadata = path.lstat()
        stamp = (metadata.st_mtime_ns, stat.S_IMODE(metadata.st_mode))
        if path.is_symlink():
            result[name] = ("symlink", os.readlink(path), stamp)
        elif path.is_dir():
            result[name] = ("directory", stamp)
            for child in sorted(path.iterdir()):
                visit(child)
        else:
            result[name] = ("file", path.read_bytes(), stamp)

    visit(root)
    return result


def read_claude(path):
    """Read the small native front matter without adding a YAML dependency."""
    text = path.read_bytes().decode("utf-8")
    if not text.startswith("---\n"):
        raise AssertionError("missing Claude front matter: " + str(path))
    header, body = text[4:].split("\n---\n", 1)
    fields = {}
    for line in header.splitlines():
        name, value = line.split(":", 1)
        value = value.strip()
        if value.startswith('"'):
            value = json.loads(value)
        elif value.startswith("'") and value.endswith("'"):
            value = value[1:-1].replace("''", "'")
        fields[name.strip()] = value
    if body.startswith("\n"):
        body = body[1:]
    return fields, body


def write_claude(path, name, description, body):
    path.parent.mkdir(parents=True, exist_ok=True)
    header = "---\nname: {}\ndescription: {}\n---\n\n".format(
        json.dumps(name, ensure_ascii=False),
        json.dumps(description, ensure_ascii=False),
    )
    path.write_bytes((header + body).encode("utf-8"))


def write_codex(path, name, description, body):
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = {"name": name, "description": description, "developer_instructions": body}
    path.write_bytes("".join(
        key + " = " + json.dumps(value, ensure_ascii=False) + "\n"
        for key, value in fields.items()
    ).encode("utf-8"))


@unittest.skipIf(tomllib is None, "mirror synchronization requires Python 3.11+ tomllib")
class MirrorSyncTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="jgs-mirror-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.package = self.root / "plugin copy"
        shutil.copytree(SOURCE, self.package, symlinks=True,
                        ignore=shutil.ignore_patterns(".git", ".jgs-mirror.json", "__pycache__", "*.pyc"))
        self.project = self.root / "프로젝트 with spaces"
        self.project.mkdir()
        self.home = self.root / "unrelated home"
        (self.home / ".codex").mkdir(parents=True)
        (self.home / ".codex" / "config.toml").write_bytes(b"existing_global = true\r\n")
        self.outside = self.root / "outside"
        self.outside.mkdir()
        (self.outside / "keep.txt").write_bytes(b"outside must not change\r\n")
        self.team = json.loads((self.package / "team.json").read_text(encoding="utf-8"))
        self.home_before = snapshot(self.home)

    def run_sync(self, project=False, check=False):
        command = [sys.executable, str(self.package / "scripts" / "sync-studio.py")]
        if project is not False and project is not None:
            command.extend(["--project", str(self.project if project is True else project)])
        if check:
            command.append("--check")
        env = os.environ.copy()
        env.update({
            "HOME": str(self.home),
            "USERPROFILE": str(self.home),
            "CODEX_HOME": str(self.home / ".codex"),
            "XDG_CONFIG_HOME": str(self.home / "config"),
            "XDG_DATA_HOME": str(self.home / "data"),
            "XDG_CACHE_HOME": str(self.home / "cache"),
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONUTF8": "1",
        })
        return subprocess.run(command, cwd=self.outside, env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)

    def assert_ok(self, result):
        self.assertEqual(result.returncode, 0,
                         (result.stdout + result.stderr).decode("utf-8", errors="replace"))
        self.assertEqual(snapshot(self.home), self.home_before)

    def initialize(self, project=False):
        self.assert_ok(self.run_sync(project=project))


    def assert_rejected_without_changes(self, project=False, check=False):
        before = snapshot(self.root)
        result = self.run_sync(project=project, check=check)
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertEqual(snapshot(self.root), before)
        return result

    def assert_role(self, role, body=None, description=None, project=False):
        team = json.loads((self.package / "team.json").read_text(encoding="utf-8"))
        name = "jgs-" + role
        self.assertEqual(team[role]["agent"], name)
        if body is None:
            body = (self.package / "roles" / (role + ".md")).read_bytes().decode("utf-8")
        if description is None:
            description = team[role]["purpose"]
        self.assertEqual(team[role]["purpose"], description)
        self.assertEqual((self.package / "roles" / (role + ".md")).read_bytes().decode("utf-8"), body)
        locations = [(self.package, "agents")]
        if project:
            locations.append((self.project, ".claude/agents"))
        for root, claude_directory in locations:
            fields, actual_body = read_claude(root / claude_directory / (name + ".md"))
            # Model, reasoning, permissions and tools inherit from the host.
            self.assertEqual(fields, {"name": name, "description": description})
            self.assertEqual(actual_body, body)
            text = (root / ("codex-agents" if root == self.package else ".codex/agents") / (name + ".toml")).read_text(encoding="utf-8")
            codex = tomllib.loads(text)
            if root == self.package:
                self.assertEqual(set(codex), {"developer_instructions"})
                for line in text.splitlines():
                    if line.startswith(("# name = ", "# description = ")):
                        key, value = line[2:].split(" = ", 1)
                        codex[key] = json.loads(value)
            self.assertEqual(codex, {"name": name, "description": description,
                                     "developer_instructions": body})

    def skill_files(self, root, name):
        return {path.relative_to(root / name).as_posix(): path.read_bytes()
                for path in (root / name).rglob("*") if path.is_file()}

    def assert_skill(self, name, expected=None):
        source = self.skill_files(self.package / "skills", name)
        if expected is not None:
            self.assertEqual(source, expected)

    def add_resource(self, root, name="jgs-sync", relative="resources/nested/원본.bin", data=b"\x00\xff\r\nresource"):
        path = root / name / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def link(self, destination, target, directory=True):
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            destination.symlink_to(target, target_is_directory=directory)
        except (OSError, NotImplementedError) as error:
            self.skipTest("symlinks unavailable: " + str(error))

    def assert_mid_scan_edit_preserved(self, scanner, scanned_directory, target, edited):
        # run_path returns a copy of the namespace; patch the actual function
        # globals so the edit happens after a successful, deterministic read.
        self.addCleanup(setattr, sys, "dont_write_bytecode", sys.dont_write_bytecode)
        module = runpy.run_path(str(self.package / "scripts/sync-studio.py"),
                                run_name="jgs_mirror_regression")
        helpers = runpy.run_path(str(self.package / "scripts/init-project.py"),
                                 run_name="jgs_helpers_regression")
        synchronize = module["synchronize"]
        namespace = synchronize.__globals__
        original = namespace[scanner]
        source = self.package.resolve()
        project = self.project.resolve()
        before = snapshot(self.root)
        injected = {}

        def edit_after_scan(tree, relative, *args):
            values = original(tree, relative, *args)
            if tree.root == source and Path(relative) == Path(scanned_directory) and not injected:
                target.write_bytes(edited)
                injected.update(snapshot(self.root))
            return values

        environment = {
            "HOME": str(self.home), "USERPROFILE": str(self.home),
            "CODEX_HOME": str(self.home / ".codex"),
            "XDG_CONFIG_HOME": str(self.home / "config"),
            "XDG_DATA_HOME": str(self.home / "data"),
            "XDG_CACHE_HOME": str(self.home / "cache"),
        }
        with mock.patch.dict(os.environ, environment), mock.patch.dict(namespace, {scanner: edit_after_scan}):
            with self.assertRaisesRegex(ValueError, "changed during sync"):
                synchronize(source, helpers, check=False)
        self.assertTrue(injected, "the requested scan was never reached")
        self.assertEqual(snapshot(self.root), injected)
        self.assertEqual(target.read_bytes(), edited)
        relative = target.relative_to(self.root).as_posix()
        self.assertEqual({key: value for key, value in injected.items() if key != relative},
                         {key: value for key, value in before.items() if key != relative})
        self.assertEqual(snapshot(self.home), self.home_before)

    def test_source_only_bootstraps_missing_native_agents(self):
        for directory in (self.package / "agents", self.package / "codex-agents"):
            if directory.exists():
                shutil.rmtree(directory)
        before_roles = snapshot(self.package / "roles")
        before_skills = snapshot(self.package / "skills")
        self.initialize(project=False)
        for role in self.team:
            self.assert_role(role, project=False)
        self.assertTrue((self.package / ".jgs-mirror.json").is_file())
        self.assertEqual(snapshot(self.package / "roles"), before_roles)
        self.assertEqual(snapshot(self.package / "skills"), before_skills)
        self.assertEqual(list(self.project.iterdir()), [])


    def test_state_has_hash_collections_without_clock_fields(self):
        self.initialize()
        forbidden = {"timestamp", "created_at", "updated_at", "synced_at", "last_sync"}

        def check_keys(value):
            if isinstance(value, dict):
                self.assertTrue(forbidden.isdisjoint(value))
                for child in value.values():
                    check_keys(child)
            elif isinstance(value, list):
                for child in value:
                    check_keys(child)

        for root in (self.package,):
            state = json.loads((root / ".jgs-mirror.json").read_text(encoding="utf-8"))
            self.assertTrue({"schema", "package", "agents", "skills"}.issubset(state))
            self.assertIsInstance(state["agents"], dict)
            self.assertIsInstance(state["skills"], dict)
            self.assertGreaterEqual(len(state["agents"]), len(self.team))
            self.assertGreaterEqual(len(state["skills"]), 1)
            check_keys(state)

    def test_repeat_install_and_clean_check_preserve_bytes_and_mtimes(self):
        self.initialize()
        before = snapshot(self.root)
        self.assert_ok(self.run_sync())
        self.assertEqual(snapshot(self.root), before)
        self.assert_ok(self.run_sync(check=True))
        self.assertEqual(snapshot(self.root), before)


    def test_check_before_initial_install_reports_drift_without_writing(self):
        self.assert_rejected_without_changes(check=True)

    def test_check_after_edit_reports_drift_without_writing(self):
        self.initialize()
        path = self.package / "agents/jgs-core.md"
        path.write_bytes(path.read_bytes() + "\n검사만 하는 변경.\n".encode("utf-8"))
        self.assert_rejected_without_changes(check=True)

    def test_source_role_and_description_flow_to_both_formats(self):
        self.initialize()
        role = self.package / "roles/core.md"
        body = role.read_text(encoding="utf-8") + '\nQuotes """ and \\slashes\t 한글.\n'
        role.write_bytes(body.encode("utf-8"))
        team_path = self.package / "team.json"
        team = json.loads(team_path.read_text(encoding="utf-8"))
        description = '새 설명: "코어" \\경로'
        team["core"]["purpose"] = description
        team_path.write_bytes((json.dumps(team, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
        self.assert_ok(self.run_sync())
        self.assert_role("core", body, description)

    def test_claude_edit_flows_back_to_source_and_codex(self):
        self.initialize()
        body = '# 변경된 코어\n\n""" quoted \\path\t한글 내용.\n'
        description = 'Claude에서 바꾼 설명: "안전한 수명"'
        write_claude(self.package / "agents/jgs-core.md", "jgs-core", description, body)
        self.assert_ok(self.run_sync())
        self.assert_role("core", body, description)

    def test_codex_edit_flows_back_to_source_and_claude(self):
        self.initialize()
        body = '# Updated core\n\nCodex says """ and \\path; 한글.\n'
        description = "Codex에서 수정한 코어 설명."
        write_codex(self.package / "codex-agents/jgs-core.toml", "jgs-core", description, body)
        self.assert_ok(self.run_sync())
        self.assert_role("core", body, description)

    def test_source_native_agent_edit_syncs_without_project(self):
        self.initialize(project=False)
        body = "# Source native\n\n원본 Claude agent 본문.\n"
        description = "원본 native 편집."
        write_claude(self.package / "agents/jgs-core.md", "jgs-core", description, body)
        self.assert_ok(self.run_sync(project=False))
        self.assert_role("core", body, description, project=False)

    def test_identical_edits_in_two_copies_are_not_a_conflict(self):
        self.initialize()
        body = "# Same edit\n\n같은 변경은 하나로 수렴한다.\n"
        description = self.team["core"]["purpose"]
        (self.package / "roles/core.md").write_bytes(body.encode("utf-8"))
        write_codex(self.package / "codex-agents/jgs-core.toml", "jgs-core", description, body)
        self.assert_ok(self.run_sync())
        self.assert_role("core", body, description)


    def test_agent_edit_during_scan_is_preserved_without_sync_writes(self):
        self.initialize()
        target = self.package / "roles/core.md"
        self.assert_mid_scan_edit_preserved("read_agents", "agents", target,
                                           target.read_bytes() + b"\nMid-scan agent edit B.\n")

    def test_skill_resource_edit_during_scan_is_preserved_without_sync_writes(self):
        target = self.add_resource(self.package / "skills")
        self.initialize()
        self.assert_mid_scan_edit_preserved("read_skills", "skills", target,
                                           b"\x00\xffmid-scan resource B\r\n")

    def test_scanned_input_outside_write_plan_is_checked_before_publication(self):
        self.initialize()
        target = self.package / "team.json"
        team = json.loads(target.read_text(encoding="utf-8"))
        team["core"]["purpose"] = "Mid-scan team edit B."
        edited = (json.dumps(team, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        self.assert_mid_scan_edit_preserved("read_skills", "skills", target, edited)

    def test_unsupported_native_model_or_permission_fields_reject_all_writes(self):
        self.initialize()
        cases = (
            ("agents/jgs-core.md", 'model: "fixed-model"'),
            ("agents/jgs-core.md", 'permissionMode: "bypassPermissions"'),
            ("codex-agents/jgs-core.toml", 'model = "fixed-model"'),
            ("codex-agents/jgs-core.toml", 'model_reasoning_effort = "high"'),
            ("codex-agents/jgs-core.toml", 'sandbox_mode = "danger-full-access"'),
            ("codex-agents/jgs-core.toml", 'approval_policy = "never"'),
        )
        pending = self.package / "roles/particles.md"
        pending.write_bytes(pending.read_bytes() + b"\nPending valid update.\n")
        for relative, extra in cases:
            with self.subTest(relative=relative, extra=extra):
                path = self.package / relative
                original = path.read_bytes()
                if relative.endswith(".md"):
                    changed = original.replace(b"\n---\n", ("\n" + extra + "\n---\n").encode("utf-8"), 1)
                else:
                    changed = original + (extra + "\n").encode("utf-8")
                path.write_bytes(changed)
                self.assert_rejected_without_changes()
                path.write_bytes(original)

    def test_new_native_agents_are_discovered_in_package(self):
        self.initialize()
        cases = (
            (self.package / "agents/jgs-audio.md", write_claude, "audio"),
            (self.package / "codex-agents/jgs-build.toml", write_codex, "build"),
        )
        for path, writer, role in cases:
            writer(path, "jgs-" + role, "새 역할 " + role, "# " + role + "\n\n동적 역할 본문.\n")
        self.assert_ok(self.run_sync())
        for _, _, role in cases:
            self.assert_role(role, "# " + role + "\n\n동적 역할 본문.\n", "새 역할 " + role)

    def test_new_team_role_and_new_skills_are_discovered(self):
        self.initialize()
        team_path = self.package / "team.json"
        team = json.loads(team_path.read_text(encoding="utf-8"))
        team["audio"] = {"agent": "jgs-audio", "title": "오디오", "purpose": "소리 처리.", "tasks": []}
        team_path.write_bytes((json.dumps(team, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
        body = "# 오디오\n\n소리의 수명을 관리한다.\n"
        (self.package / "roles/audio.md").write_bytes(body.encode("utf-8"))
        origins = (self.package / "skills", self.package / "skills", self.package / "skills")
        for index, origin in enumerate(origins):
            name = "local-helper-" + str(index)
            skill = origin / name
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_bytes(("---\nname: " + name + "\ndescription: 새 도구\n---\n\n# 도구\n").encode("utf-8"))
            self.add_resource(origin, name=name)
        self.assert_ok(self.run_sync())
        self.assert_role("audio", body, "소리 처리.")
        for index in range(len(origins)):
            self.assert_skill("local-helper-" + str(index))

    def test_agent_conflict_rejects_all_pending_agent_and_skill_writes(self):
        self.initialize()
        source = self.package / "roles/particles.md"
        source.write_bytes(source.read_bytes() + b"\nSource edit.\n")
        host = self.package / "agents/jgs-particles.md"
        host.write_bytes(host.read_bytes() + b"\nDifferent host edit.\n")
        pending = self.package / "roles/core.md"
        pending.write_bytes(pending.read_bytes() + b"\nUnrelated pending edit.\n")
        self.add_resource(self.package / "skills", data=b"pending skill")
        self.assert_rejected_without_changes()


    def test_check_reports_missing_mirror_without_recreating_it(self):
        self.initialize()
        (self.package / "codex-agents/jgs-core.toml").unlink()
        self.assert_rejected_without_changes(check=True)

    def test_project_symlink_alias_is_rejected(self):
        alias = self.root / "project alias"
        self.link(alias, self.project)
        self.assert_rejected_without_changes(project=alias)


    def test_late_native_file_symlink_is_rejected_before_any_write(self):
        path = self.package / "codex-agents/jgs-particles.toml"
        path.unlink()
        self.link(path, self.outside / "keep.txt", directory=False)
        self.assert_rejected_without_changes()

    def test_source_resource_symlink_is_rejected_even_with_internal_target(self):
        path = self.package / "skills/jgs-sync/resources/alias.md"
        self.link(path, self.package / "roles/core.md", directory=False)
        self.assert_rejected_without_changes()

    def test_mirror_state_symlink_is_rejected_before_any_write(self):
        self.link(self.package / ".jgs-mirror.json", self.outside / "keep.txt", directory=False)
        self.assert_rejected_without_changes()

    def test_invalid_team_id_cannot_escape_package_or_project(self):
        team_path = self.package / "team.json"
        team = json.loads(team_path.read_text(encoding="utf-8"))
        team["particles"]["agent"] = "../../outside/escape"
        team_path.write_bytes(json.dumps(team).encode("utf-8"))
        self.assert_rejected_without_changes()

    def test_invalid_native_name_cannot_escape_package_or_project(self):
        write_claude(self.package / "agents/jgs-escape.md", "jgs-../../outside/escape",
                     "invalid identifier", "# Invalid\n")
        self.assert_rejected_without_changes()

    def test_missing_or_file_project_is_rejected_without_creating_source_mirrors(self):
        for project in (self.root / "does not exist", self.outside / "keep.txt"):
            with self.subTest(project=project):
                self.assert_rejected_without_changes(project=project)


if __name__ == "__main__":
    unittest.main()
