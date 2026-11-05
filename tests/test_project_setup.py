"""Isolated CLI regressions; never install into a real engine or user profile."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

SOURCE = Path(__file__).resolve().parents[1]


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
        shutil.copyfile(SOURCE / "scripts/init-project.py", self.package / "scripts/init-project.py")
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
        self.assertIn(str(self.project.resolve()), (base / "project.md").read_bytes().decode("utf-8"))
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


    def test_missing_or_file_project_is_rejected(self):
        for script in ("init-project.py",):
            for project in (self.root / "does-not-exist", self.outside / "keep.txt"):
                with self.subTest(script=script, project=project):
                    result = self.run_cli(script, project)
                    self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.root / "does-not-exist").exists())


    @unittest.skipUnless(os.name == "nt", "actual Windows junction regression")
    def test_windows_junction_destinations_are_rejected(self):
        for script, relative in (("init-project.py", ".jaeho-studio"),):
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
