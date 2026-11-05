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

    def run_sync(self, project=True, check=False):
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

    def initialize(self, project=True):
        self.assert_ok(self.run_sync(project=project))

    def run_git(self, *arguments):
        # Ignore inherited Git redirection and configuration, and never write
        # identity settings into a real user's global configuration.
        environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
        environment.update({
            "HOME": str(self.home), "USERPROFILE": str(self.home),
            "XDG_CONFIG_HOME": str(self.home / "config"),
            "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_ATTR_NOSYSTEM": "1", "GIT_TERMINAL_PROMPT": "0",
        })
        command = ["git", "-c", "user.name=StudioTest", "-c", "user.email=test@invalid",
                   "-c", "commit.gpgsign=false", "-c", "core.autocrlf=true",
                   "-c", "core.safecrlf=false", *arguments]
        result = subprocess.run(command, cwd=self.project, env=environment,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
        self.assert_ok(result)
        return result

    def assert_rejected_without_changes(self, project=True, check=False):
        before = snapshot(self.root)
        result = self.run_sync(project=project, check=check)
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertEqual(snapshot(self.root), before)
        return result

    def assert_role(self, role, body=None, description=None, project=True):
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
            codex = tomllib.loads((root / ".codex/agents" / (name + ".toml")).read_text(encoding="utf-8"))
            self.assertEqual(codex, {"name": name, "description": description,
                                     "developer_instructions": body})

    def skill_files(self, root, name):
        return {path.relative_to(root / name).as_posix(): path.read_bytes()
                for path in (root / name).rglob("*") if path.is_file()}

    def assert_skill(self, name, expected=None):
        source = self.skill_files(self.package / "skills", name)
        if expected is not None:
            self.assertEqual(source, expected)
        for mirror in (".agents/skills", ".claude/skills"):
            self.assertEqual(self.skill_files(self.project / mirror, name), source)

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
                synchronize(source, project, helpers, check=False)
        self.assertTrue(injected, "the requested scan was never reached")
        self.assertEqual(snapshot(self.root), injected)
        self.assertEqual(target.read_bytes(), edited)
        relative = target.relative_to(self.root).as_posix()
        self.assertEqual({key: value for key, value in injected.items() if key != relative},
                         {key: value for key, value in before.items() if key != relative})
        self.assertEqual(snapshot(self.home), self.home_before)

    def test_source_only_bootstraps_missing_native_agents(self):
        for directory in (self.package / "agents", self.package / ".codex/agents"):
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

    def test_install_preserves_user_configuration_context_and_other_agents(self):
        existing = {
            ".codex/config.toml": b"custom = true\r\n",
            ".codex/agents/my-agent.toml": b"user agent\r\n",
            ".claude/settings.json": b'{"custom": true}\r\n',
            ".claude/agents/my-agent.md": b"user Claude agent\r\n",
            ".jaeho-studio/project.md": b"\xff\x00private project\r\n",
            ".jaeho-studio/roles/core/checkpoint.md": b"current private work\r\n",
        }
        for relative, data in existing.items():
            path = self.project / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        original = snapshot(self.project)
        self.add_resource(self.package / "skills")
        self.initialize()
        for role in self.team:
            self.assert_role(role)
        for name in original:
            if original[name][0] == "file":
                self.assertEqual(snapshot(self.project)[name], original[name])
        for name in (self.package / "skills").iterdir():
            if name.is_dir():
                self.assert_skill(name.name)

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

        for root in (self.package, self.project):
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

    def test_existing_gitattributes_prefix_is_preserved_and_repeat_is_unchanged(self):
        path = self.project / ".gitattributes"
        for ending in (b"\r\n", b""):
            with self.subTest(ending=ending):
                original = b"# owner \xff custom rules\r\n*.md text eol=crlf\r\n*.png -text" + ending
                path.write_bytes(original)
                self.initialize()
                installed = path.read_bytes()
                self.assertTrue(installed.startswith(original))
                self.assertEqual(installed.count(b"# BEGIN JAEHOGAMESTUDIO MIRRORS"), 1)
                self.assertEqual(installed.count(b"# END JAEHOGAMESTUDIO MIRRORS"), 1)
                before = snapshot(self.root)
                self.assert_ok(self.run_sync())
                self.assert_ok(self.run_sync(check=True))
                self.assertEqual(snapshot(self.root), before)

    def test_crlf_git_checkout_keeps_mirror_bytes_and_check_clean(self):
        (self.project / ".gitattributes").write_bytes(
            b"*.md text eol=crlf\r\n*.json text eol=crlf\r\n")
        ordinary = self.project / "ordinary.md"
        ordinary.write_bytes(b"# Ordinary project text\n\nExisting CRLF policy applies.\n")
        self.add_resource(self.package / "skills", relative="resources/example.json",
                          data=b'{"resource": "LF text"}\n')
        self.initialize()
        managed = [self.project / ".jgs-mirror.json"]
        for directory in (".claude/agents", ".codex/agents", ".claude/skills", ".agents/skills"):
            managed.extend(path for path in (self.project / directory).rglob("*") if path.is_file())
        expected = {path.relative_to(self.project): path.read_bytes() for path in managed}
        for relative, data in expected.items():
            self.assertNotIn(b"\r\n", data, str(relative))
        self.run_git("init", "--quiet")
        self.run_git("add", "--all")
        self.run_git("commit", "--quiet", "-m", "Mirror fixture")
        # Force materialization instead of letting Git reuse the index's
        # up-to-date working-tree stat entries.
        for path in managed + [ordinary]:
            path.unlink()
        self.run_git("checkout-index", "-a", "-f")
        # Verify conversion really happened, while the scoped overrides kept
        # native agents, skill resources, and state exactly as synchronized.
        self.assertIn(b"\r\n", ordinary.read_bytes())
        for relative, data in expected.items():
            self.assertEqual((self.project / relative).read_bytes(), data, str(relative))
        before = snapshot(self.root)
        self.assert_ok(self.run_sync(check=True))
        self.assertEqual(snapshot(self.root), before)

    def test_gitattributes_symlink_is_rejected_before_any_write(self):
        self.link(self.project / ".gitattributes", self.outside / "keep.txt", directory=False)
        self.assert_rejected_without_changes()

    def test_different_existing_gitattributes_managed_block_rejects_all_writes(self):
        (self.project / ".gitattributes").write_bytes(
            b"# owner rules\r\n*.md text eol=crlf\r\n"
            b"# BEGIN JAEHOGAMESTUDIO MIRRORS\n"
            b"/.claude/skills/** text eol=crlf\n"
            b"# END JAEHOGAMESTUDIO MIRRORS\n")
        self.assert_rejected_without_changes()

    def test_check_before_initial_install_reports_drift_without_writing(self):
        self.assert_rejected_without_changes(check=True)

    def test_check_after_edit_reports_drift_without_writing(self):
        self.initialize()
        path = self.project / ".claude/agents/jgs-core.md"
        path.write_bytes(path.read_bytes() + "\n검사만 하는 변경.\n".encode("utf-8"))
        self.assert_rejected_without_changes(check=True)

    def test_source_role_and_team_description_flow_to_both_hosts(self):
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
        write_claude(self.project / ".claude/agents/jgs-core.md", "jgs-core", description, body)
        self.assert_ok(self.run_sync())
        self.assert_role("core", body, description)

    def test_codex_edit_flows_back_to_source_and_claude(self):
        self.initialize()
        body = '# Updated core\n\nCodex says """ and \\path; 한글.\n'
        description = "Codex에서 수정한 코어 설명."
        write_codex(self.project / ".codex/agents/jgs-core.toml", "jgs-core", description, body)
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
        write_codex(self.project / ".codex/agents/jgs-core.toml", "jgs-core", description, body)
        self.assert_ok(self.run_sync())
        self.assert_role("core", body, description)

    def test_skill_source_and_each_host_preserve_nested_binary_resources(self):
        self.add_resource(self.package / "skills")
        self.initialize()
        for index, origin in enumerate((self.package / "skills", self.project / ".agents/skills",
                                        self.project / ".claude/skills")):
            with self.subTest(origin=origin):
                path = origin / "jgs-sync/SKILL.md"
                path.write_bytes(path.read_bytes() + ("\n수정 단계 {}.\n".format(index)).encode("utf-8"))
                self.add_resource(origin, relative="resources/nested/원본.bin", data=bytes([index, 255, 13, 10]))
                expected = self.skill_files(origin, "jgs-sync")
                self.assert_ok(self.run_sync())
                self.assert_skill("jgs-sync", expected)

    @unittest.skipUnless(os.name == "posix", "executable mode bits require POSIX")
    def test_executable_skill_resource_and_mode_only_edits_round_trip(self):
        relative = "scripts/check-core.sh"
        source = self.add_resource(self.package / "skills", relative=relative,
                                   data=b"#!/bin/sh\nprintf 'core check\\n'\n")
        source.chmod(0o755)
        self.initialize()
        paths = [root / "jgs-sync" / relative for root in
                 (self.package / "skills", self.project / ".agents/skills",
                  self.project / ".claude/skills")]
        for path in paths:
            self.assertEqual(stat.S_IMODE(path.stat().st_mode) & 0o111, 0o111)
            self.assertEqual(path.read_bytes(), source.read_bytes())
        initial_state = (self.package / ".jgs-mirror.json").read_bytes()
        paths[1].chmod(0o644)  # No content or mtime change accompanies this edit.
        self.assert_ok(self.run_sync())
        for path in paths:
            self.assertEqual(stat.S_IMODE(path.stat().st_mode) & 0o111, 0)
        self.assertNotEqual((self.package / ".jgs-mirror.json").read_bytes(), initial_state)
        source.chmod(0o755)
        self.assert_ok(self.run_sync())
        for path in paths:
            self.assertEqual(stat.S_IMODE(path.stat().st_mode) & 0o111, 0o111)

    def test_new_source_state_updates_project_with_older_host_state(self):
        self.initialize()
        host_before = snapshot(self.project)
        role = self.package / "roles/core.md"
        body = role.read_text(encoding="utf-8") + "\n새로 받은 원본 업데이트.\n"
        role.write_bytes(body.encode("utf-8"))
        self.add_resource(self.package / "skills", data=b"updated upstream resource")
        self.assert_ok(self.run_sync(project=False))
        self.assert_role("core", body, project=False)
        self.assertEqual(snapshot(self.project), host_before)
        self.assertNotEqual((self.package / ".jgs-mirror.json").read_bytes(),
                            (self.project / ".jgs-mirror.json").read_bytes())
        self.assert_ok(self.run_sync())
        self.assert_role("core", body)
        self.assert_skill("jgs-sync")
        self.assert_ok(self.run_sync(check=True))

    def test_stale_host_baseline_never_overwrites_explicit_source_agent_revert(self):
        self.initialize()
        native = self.package / "agents/jgs-core.md"
        original_native = native.read_bytes()
        body_a = (self.package / "roles/core.md").read_text(encoding="utf-8")
        description = self.team["core"]["purpose"]
        host_a = snapshot(self.project)
        body_b = body_a + "\n원본에서 먼저 동기화한 B.\n"
        write_claude(native, "jgs-core", description, body_b)
        self.assert_ok(self.run_sync(project=False))
        self.assert_role("core", body_b, description, project=False)
        self.assertEqual(snapshot(self.project), host_a)

        # This explicit edit to A is newer than the source's B baseline. The
        # host still holding A must not make it look like an untouched copy.
        native.write_bytes(original_native)
        before = snapshot(self.root)
        result = self.run_sync()
        if result.returncode:
            self.assertEqual(snapshot(self.root), before)
        else:
            self.assert_ok(result)
            self.assert_role("core", body_a, description)
            self.assert_ok(self.run_sync(check=True))

    def test_divergent_host_edit_against_new_source_baseline_is_atomic_conflict(self):
        self.initialize()
        description = self.team["core"]["purpose"]
        body_a = (self.package / "roles/core.md").read_text(encoding="utf-8")
        body_b = body_a + "\n먼저 적용된 원본 B.\n"
        host_a = snapshot(self.project)
        write_claude(self.package / "agents/jgs-core.md", "jgs-core", description, body_b)
        self.assert_ok(self.run_sync(project=False))
        self.assert_role("core", body_b, description, project=False)
        self.assertEqual(snapshot(self.project), host_a)

        body_c = body_a + "\n이전 프로젝트에서 별도로 편집한 C.\n"
        write_claude(self.project / ".claude/agents/jgs-core.md", "jgs-core", description, body_c)
        self.assert_rejected_without_changes()

    def test_stale_host_baseline_never_overwrites_explicit_source_skill_revert(self):
        self.add_resource(self.package / "skills")
        self.initialize()
        skill = self.package / "skills/jgs-sync/SKILL.md"
        original = skill.read_bytes()
        expected_a = self.skill_files(self.package / "skills", "jgs-sync")
        host_a = snapshot(self.project)
        skill.write_bytes(original + "\n원본에서 먼저 동기화한 스킬 B.\n".encode("utf-8"))
        self.assert_ok(self.run_sync(project=False))
        self.assertEqual(snapshot(self.project), host_a)

        skill.write_bytes(original)
        before = snapshot(self.root)
        result = self.run_sync()
        if result.returncode:
            self.assertEqual(snapshot(self.root), before)
        else:
            self.assert_ok(result)
            self.assert_skill("jgs-sync", expected_a)
            self.assert_ok(self.run_sync(check=True))

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
            (".claude/agents/jgs-core.md", 'model: "fixed-model"'),
            (".claude/agents/jgs-core.md", 'permissionMode: "bypassPermissions"'),
            (".codex/agents/jgs-core.toml", 'model = "fixed-model"'),
            (".codex/agents/jgs-core.toml", 'model_reasoning_effort = "high"'),
            (".codex/agents/jgs-core.toml", 'sandbox_mode = "danger-full-access"'),
            (".codex/agents/jgs-core.toml", 'approval_policy = "never"'),
        )
        pending = self.package / "roles/particles.md"
        pending.write_bytes(pending.read_bytes() + b"\nPending valid update.\n")
        for relative, extra in cases:
            with self.subTest(relative=relative, extra=extra):
                path = self.project / relative
                original = path.read_bytes()
                if relative.endswith(".md"):
                    changed = original.replace(b"\n---\n", ("\n" + extra + "\n---\n").encode("utf-8"), 1)
                else:
                    changed = original + (extra + "\n").encode("utf-8")
                path.write_bytes(changed)
                self.assert_rejected_without_changes()
                path.write_bytes(original)

    def test_new_native_agents_are_discovered_in_host_and_source(self):
        self.initialize()
        cases = (
            (self.project / ".claude/agents/jgs-audio.md", write_claude, "audio"),
            (self.package / ".codex/agents/jgs-build.toml", write_codex, "build"),
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
        origins = (self.package / "skills", self.project / ".agents/skills", self.project / ".claude/skills")
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
        host = self.project / ".claude/agents/jgs-particles.md"
        host.write_bytes(host.read_bytes() + b"\nDifferent host edit.\n")
        pending = self.package / "roles/core.md"
        pending.write_bytes(pending.read_bytes() + b"\nUnrelated pending edit.\n")
        self.add_resource(self.package / "skills", data=b"pending skill")
        self.assert_rejected_without_changes()

    def test_skill_conflict_rejects_all_pending_agent_and_skill_writes(self):
        self.initialize()
        for mirror, suffix in ((".claude/skills", b"\nClaude edit.\n"),
                               (".agents/skills", b"\nCodex edit.\n")):
            path = self.project / mirror / "jgs-sync/SKILL.md"
            path.write_bytes(path.read_bytes() + suffix)
        role = self.package / "roles/core.md"
        role.write_bytes(role.read_bytes() + b"\nPending agent.\n")
        self.assert_rejected_without_changes()

    def test_deletions_restore_agents_source_roles_and_whole_skill_trees(self):
        self.add_resource(self.package / "skills")
        self.initialize()
        expected_body = (self.package / "roles/core.md").read_text(encoding="utf-8")
        expected_skill = self.skill_files(self.package / "skills", "jgs-sync")
        (self.package / "roles/core.md").unlink()
        (self.package / ".codex/agents/jgs-core.toml").unlink()
        (self.project / ".claude/agents/jgs-core.md").unlink()
        shutil.rmtree(self.package / "skills/jgs-sync")
        shutil.rmtree(self.project / ".agents/skills/jgs-sync")
        self.assert_ok(self.run_sync())
        self.assert_role("core", expected_body)
        self.assert_skill("jgs-sync", expected_skill)

    def test_check_reports_missing_mirror_without_recreating_it(self):
        self.initialize()
        (self.project / ".codex/agents/jgs-core.toml").unlink()
        self.assert_rejected_without_changes(check=True)

    def test_project_symlink_alias_is_rejected(self):
        alias = self.root / "project alias"
        self.link(alias, self.project)
        self.assert_rejected_without_changes(project=alias)

    def test_managed_directory_symlinks_are_rejected_before_any_write(self):
        for relative in (".claude", ".codex", ".agents"):
            with self.subTest(relative=relative):
                destination = self.project / relative
                self.link(destination, self.outside)
                self.assert_rejected_without_changes()
                destination.unlink()

    def test_late_native_file_symlink_is_rejected_before_any_write(self):
        path = self.project / ".codex/agents/jgs-particles.toml"
        self.link(path, self.outside / "keep.txt", directory=False)
        self.assert_rejected_without_changes()

    def test_source_resource_symlink_is_rejected_even_with_internal_target(self):
        path = self.package / "skills/jgs-sync/resources/alias.md"
        self.link(path, self.package / "roles/core.md", directory=False)
        self.assert_rejected_without_changes()

    def test_mirror_state_symlink_is_rejected_before_any_write(self):
        self.link(self.project / ".jgs-mirror.json", self.outside / "keep.txt", directory=False)
        self.assert_rejected_without_changes()

    def test_invalid_team_id_cannot_escape_package_or_project(self):
        team_path = self.package / "team.json"
        team = json.loads(team_path.read_text(encoding="utf-8"))
        team["particles"]["agent"] = "../../outside/escape"
        team_path.write_bytes(json.dumps(team).encode("utf-8"))
        self.assert_rejected_without_changes()

    def test_invalid_native_name_cannot_escape_package_or_project(self):
        write_claude(self.project / ".claude/agents/jgs-escape.md", "jgs-../../outside/escape",
                     "invalid identifier", "# Invalid\n")
        self.assert_rejected_without_changes()

    def test_missing_or_file_project_is_rejected_without_creating_source_mirrors(self):
        for project in (self.root / "does not exist", self.outside / "keep.txt"):
            with self.subTest(project=project):
                self.assert_rejected_without_changes(project=project)


if __name__ == "__main__":
    unittest.main()
