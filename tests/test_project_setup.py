"""Isolated CLI regressions; never install into a real engine or user profile."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

try:
    import tomllib
except ImportError:  # Python 3.9/3.10: standard-library-only operation still works.
    tomllib = None


SOURCE = Path(__file__).resolve().parents[1]
ROLES = ("director", "graphics", "editor", "core", "network", "devrel", "particles")


def snapshot(root):
    return {str(path.relative_to(root)): (path.read_bytes(), path.stat().st_mtime_ns)
            for path in root.rglob("*") if path.is_file() and not path.is_symlink()}


class ProjectSetupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="jgs-setup-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.package = self.root / "plugin copy"
        (self.package / "scripts").mkdir(parents=True)
        (self.package / "roles").mkdir()
        for name in ("init-project.py", "install-codex-agents.py"):
            shutil.copyfile(SOURCE / "scripts" / name, self.package / "scripts" / name)
        shutil.copyfile(SOURCE / "team.json", self.package / "team.json")
        for role in ROLES:
            shutil.copyfile(SOURCE / "roles" / (role + ".md"), self.package / "roles" / (role + ".md"))
        self.project = self.root / "프로젝트 with spaces"
        self.project.mkdir()
        self.outside = self.root / "outside"
        self.outside.mkdir()
        (self.outside / "keep.txt").write_bytes(b"outside must not change\r\n")
        self.home = self.root / "unrelated home"
        (self.home / ".codex").mkdir(parents=True)
        (self.home / ".codex" / "config.toml").write_bytes(b"existing = true\r\n")

    def run_cli(self, name, project=None, extra_env=None):
        env = os.environ.copy()
        # A fake home catches accidental home-relative/global configuration work.
        env["HOME"] = str(self.home)
        env["USERPROFILE"] = str(self.home)
        env["CODEX_HOME"] = str(self.home / ".codex")
        if extra_env:
            env.update(extra_env)
        return subprocess.run([sys.executable, str(self.package / "scripts" / name),
                               str(self.project if project is None else project)],
                              cwd=self.outside, env=env, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, timeout=20)

    def link(self, destination, target, directory=True):
        try:
            destination.symlink_to(target, target_is_directory=directory)
        except (OSError, NotImplementedError) as error:
            self.skipTest("symlinks unavailable: " + str(error))

    def assert_rejected_without_changes(self, script, outside_before=None):
        before = snapshot(self.project)
        outside_before = snapshot(self.outside) if outside_before is None else outside_before
        result = self.run_cli(script)
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertEqual(snapshot(self.outside), outside_before)
        self.assertEqual(snapshot(self.project), before)
        return result

    def test_initializer_utf8_lf_nonascii_path_and_idempotence(self):
        result = self.run_cli("init-project.py", extra_env={"PYTHONIOENCODING": "cp1252"})
        self.assertEqual(result.returncode, 0, result.stderr)
        base = self.project / ".jaeho-studio"
        created = list(base.rglob("*.md"))
        self.assertEqual(len(created), 9)
        self.assertIn(str(self.project), (base / "project.md").read_bytes().decode("utf-8"))
        for path in created:
            self.assertNotIn(b"\r", path.read_bytes())
            path.read_bytes().decode("utf-8")
        self.assertTrue((base / "tasks").is_dir())
        self.assertTrue((base / "decisions").is_dir())
        before = snapshot(self.project)
        self.assertEqual(self.run_cli("init-project.py").returncode, 0)
        self.assertEqual(snapshot(self.project), before)

    def test_initializer_preserves_arbitrary_existing_bytes(self):
        base = self.project / ".jaeho-studio"
        (base / "roles" / "core").mkdir(parents=True)
        (base / "project.md").write_bytes(b"\xff\x00private project\r\n")
        (base / "contracts.md").write_bytes(b"custom contracts\r\n")
        (base / "roles" / "core" / "checkpoint.md").write_bytes(b"current private work\r\n")
        (base / "sessions.json").write_bytes(b'{"private":"session"}\n')
        before = snapshot(base)
        result = self.run_cli("init-project.py")
        self.assertEqual(result.returncode, 0, result.stderr)
        after = snapshot(base)
        for name, value in before.items():
            self.assertEqual(after[name], value)

    def test_initializer_rejects_symlink_base(self):
        self.link(self.project / ".jaeho-studio", self.outside)
        self.assert_rejected_without_changes("init-project.py")

    def test_initializer_preflights_late_symlink_parent(self):
        base = self.project / ".jaeho-studio"
        (base / "roles").mkdir(parents=True)
        self.link(base / "roles" / "particles", self.outside)
        self.assert_rejected_without_changes("init-project.py")
        self.assertFalse((base / "project.md").exists())

    def test_initializer_rejects_existing_symlink_file(self):
        base = self.project / ".jaeho-studio"
        base.mkdir()
        self.link(base / "project.md", self.outside / "keep.txt", directory=False)
        self.assert_rejected_without_changes("init-project.py")

    def test_initializer_rejects_tasks_directory_escape_before_writing(self):
        base = self.project / ".jaeho-studio"
        base.mkdir()
        self.link(base / "tasks", self.outside)
        self.assert_rejected_without_changes("init-project.py")

    def test_initializer_rejects_wrong_parent_type(self):
        (self.project / ".jaeho-studio").write_bytes(b"user file")
        self.assert_rejected_without_changes("init-project.py")

    def test_installer_creates_only_seven_local_profiles(self):
        before_package, before_home = snapshot(self.package), snapshot(self.home / ".codex")
        result = self.run_cli("install-codex-agents.py", extra_env={"PYTHONIOENCODING": "cp949"})
        self.assertEqual(result.returncode, 0, result.stderr)
        expected = {".codex/agents/jgs-" + role + ".toml" for role in ROLES}
        self.assertEqual({path.as_posix() for path in self.project.rglob("*") if path.is_file()},
                         {(self.project / path).as_posix() for path in expected})
        for path in (self.project / ".codex" / "agents").glob("*.toml"):
            data = path.read_bytes()
            self.assertNotIn(b"\r", data)
            data.decode("utf-8")
            self.assertEqual({line.split(b" = ", 1)[0] for line in data.splitlines()},
                             {b"name", b"description", b"developer_instructions"})
        self.assertFalse((self.project / ".jaeho-studio").exists())
        self.assertFalse((self.project / ".codex" / "config.toml").exists())
        self.assertEqual(snapshot(self.package), before_package)  # No source __pycache__.
        # Apple's Python 3.9 may emit its own stdlib cache below HOME/Library;
        # compare the global Codex configuration, independently of that runtime.
        self.assertEqual(snapshot(self.home / ".codex"), before_home)

    def test_installer_leaves_existing_context_and_custom_profiles_untouched(self):
        context = self.project / ".jaeho-studio" / "roles" / "core"
        context.mkdir(parents=True)
        (context / "checkpoint.md").write_bytes(b"private current state\r\n")
        config = self.project / ".codex"
        (config / "agents").mkdir(parents=True)
        (config / "config.toml").write_bytes(b"custom project config\r\n")
        (config / "agents" / "my-agent.toml").write_bytes(b"custom agent\r\n")
        before = snapshot(self.project)
        result = self.run_cli("install-codex-agents.py")
        self.assertEqual(result.returncode, 0, result.stderr)
        for name, value in before.items():
            self.assertEqual(snapshot(self.project)[name], value)

    def test_installer_up_to_date_does_not_rewrite(self):
        self.assertEqual(self.run_cli("install-codex-agents.py").returncode, 0)
        before = snapshot(self.project)
        again = self.run_cli("install-codex-agents.py")
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertEqual(again.stdout.count(b"up-to-date"), 7)
        self.assertEqual(snapshot(self.project), before)

    def test_installer_late_conflict_preserves_all_and_creates_nothing(self):
        agents = self.project / ".codex" / "agents"
        agents.mkdir(parents=True)
        (agents / "jgs-particles.toml").write_bytes(b"user customized last role\r\n")
        self.assert_rejected_without_changes("install-codex-agents.py")
        self.assertEqual(len(list(agents.iterdir())), 1)

    def test_installer_changed_source_preserves_existing_installation(self):
        self.assertEqual(self.run_cli("install-codex-agents.py").returncode, 0)
        source = self.package / "roles" / "particles.md"
        source.write_bytes(source.read_bytes() + b"\nUpdated role contract.\n")
        self.assert_rejected_without_changes("install-codex-agents.py")

    def test_installer_rejects_symlink_codex(self):
        self.link(self.project / ".codex", self.outside)
        self.assert_rejected_without_changes("install-codex-agents.py")

    def test_installer_rejects_symlink_agents(self):
        (self.project / ".codex").mkdir()
        self.link(self.project / ".codex" / "agents", self.outside)
        self.assert_rejected_without_changes("install-codex-agents.py")

    def test_installer_rejects_late_symlink_file(self):
        agents = self.project / ".codex" / "agents"
        agents.mkdir(parents=True)
        self.link(agents / "jgs-particles.toml", self.outside / "keep.txt", directory=False)
        self.assert_rejected_without_changes("install-codex-agents.py")
        self.assertEqual(len(list(agents.iterdir())), 1)

    def test_installer_rejects_even_internal_destination_alias(self):
        internal = self.project / "other agents"
        internal.mkdir()
        self.link(self.project / ".codex", internal)
        self.assert_rejected_without_changes("install-codex-agents.py")
        self.assertEqual(list(internal.iterdir()), [])

    def test_missing_role_source_preflights_before_project_changes(self):
        (self.package / "roles" / "particles.md").unlink()
        self.assert_rejected_without_changes("install-codex-agents.py")
        self.assertFalse((self.project / ".codex").exists())

    def test_invalid_team_id_does_not_escape_or_write(self):
        team_path = self.package / "team.json"
        team = json.loads(team_path.read_text(encoding="utf-8"))
        team["particles"]["agent"] = "../../outside/escape"
        team_path.write_bytes(json.dumps(team).encode("utf-8"))
        self.assert_rejected_without_changes("install-codex-agents.py")

    def test_missing_or_file_project_is_rejected(self):
        for script in ("init-project.py", "install-codex-agents.py"):
            for project in (self.root / "does-not-exist", self.outside / "keep.txt"):
                with self.subTest(script=script, project=project):
                    result = self.run_cli(script, project)
                    self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.root / "does-not-exist").exists())

    @unittest.skipIf(tomllib is None, "tomllib is available with Python 3.11+")
    def test_toml_exact_fields_source_text_and_escaping(self):
        role_path = self.package / "roles" / "core.md"
        role_path.write_bytes(role_path.read_bytes() + '\nQuotes """ and \\slashes\t / DEL\x7f / 한글.\n'.encode("utf-8"))
        team = json.loads((self.package / "team.json").read_text(encoding="utf-8"))
        self.assertEqual(self.run_cli("install-codex-agents.py").returncode, 0)
        for role in ROLES:
            path = self.project / ".codex" / "agents" / ("jgs-" + role + ".toml")
            data = tomllib.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(set(data), {"name", "description", "developer_instructions"})
            self.assertEqual(data["name"], team[role]["agent"])
            self.assertEqual(data["description"], team[role]["purpose"])
            self.assertEqual(data["developer_instructions"],
                             (self.package / "roles" / (role + ".md")).read_text(encoding="utf-8"))

    @unittest.skipUnless(os.name == "nt", "actual Windows junction regression")
    def test_windows_junction_destinations_are_rejected(self):
        for script, relative in (("init-project.py", ".jaeho-studio"),
                                 ("install-codex-agents.py", ".codex")):
            project = self.root / ("junction-" + relative.lstrip("."))
            project.mkdir()
            junction = project / relative
            result = subprocess.run(["cmd", "/c", "mklink", "/J", str(junction), str(self.outside)],
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            try:
                before = snapshot(self.outside)
                rejected = self.run_cli(script, project)
                self.assertNotEqual(rejected.returncode, 0)
                self.assertEqual(snapshot(self.outside), before)
            finally:
                junction.rmdir()  # Remove the junction itself, never its target.


if __name__ == "__main__":
    unittest.main()
