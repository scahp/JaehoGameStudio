"""Retired role skills never return from an older package or project mirror."""

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from test_mirror_sync import snapshot


SOURCE = Path(__file__).resolve().parents[1]
ROLES = {"director", "graphics", "editor", "core", "network", "devrel", "particles"}


@unittest.skipUnless(sys.version_info >= (3, 11), "mirror synchronization requires Python 3.11+")
class RoleSkillRetirementTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="jgs-retirement-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.package = self.root / "package copy"
        shutil.copytree(SOURCE, self.package, symlinks=True,
                        ignore=shutil.ignore_patterns(".git", ".jgs-mirror.json", "__pycache__", "*.pyc"))
        self.project = self.root / "이전 프로젝트"
        self.project.mkdir()
        self.home = self.root / "unrelated home"
        (self.home / ".codex").mkdir(parents=True)
        (self.home / ".codex/config.toml").write_bytes(b"preserve_global = true\r\n")
        self.home_before = snapshot(self.home)
        policy_path = self.package / "retired-skills.json"
        self.policy = json.loads(policy_path.read_text(encoding="utf-8"))
        self.assertEqual(self.policy["schema"], 1)
        self.assertEqual(set(self.policy["skills"]), {"jgs-" + role for role in ROLES})
        # Use known legacy bytes without depending on removed files or a
        # particular Git history. Only this disposable policy is adjusted.
        self.legacy = {}
        for name in self.policy["skills"]:
            data = ("---\nname: " + name + "\ndescription: Legacy role\n---\n\n# "
                    + name + "\n\nLegacy developer instructions.\n").encode("utf-8")
            self.legacy[name] = data
            self.policy["skills"][name] = {"SKILL.md": hashlib.sha256(data + b"\x00regular").hexdigest()}
        policy_path.write_bytes((json.dumps(self.policy, indent=2, sort_keys=True) + "\n").encode("utf-8"))

    def run_sync(self, check=False):
        command = [sys.executable, str(self.package / "scripts/sync-studio.py"),
                   "--project", str(self.project)]
        if check:
            command.append("--check")
        environment = os.environ.copy()
        environment.update({
            "HOME": str(self.home), "USERPROFILE": str(self.home),
            "CODEX_HOME": str(self.home / ".codex"),
            "XDG_CONFIG_HOME": str(self.home / "config"),
            "XDG_DATA_HOME": str(self.home / "data"),
            "XDG_CACHE_HOME": str(self.home / "cache"),
            "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUTF8": "1",
        })
        return subprocess.run(command, cwd=self.root, env=environment,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)

    def assert_success(self, result):
        self.assertEqual(result.returncode, 0,
                         (result.stdout + result.stderr).decode("utf-8", errors="replace"))
        self.assertEqual(snapshot(self.home), self.home_before)

    def assert_unchanged_rejection(self, check=False):
        before = snapshot(self.root)
        result = self.run_sync(check=check)
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertEqual(snapshot(self.root), before)

    def skill_roots(self):
        return (self.package / "skills", self.project / ".claude/skills", self.project / ".agents/skills")

    def seed_old_manifests(self):
        retired_hashes = {name + "/" + relative: value
                          for name, files in self.policy["skills"].items()
                          for relative, value in files.items()}
        for root in (self.package, self.project):
            path = root / ".jgs-mirror.json"
            state = json.loads(path.read_text(encoding="utf-8"))
            state["skills"].update(retired_hashes)
            path.write_bytes((json.dumps(state, indent=2, sort_keys=True) + "\n").encode("utf-8"))

    def seed_old_copies(self, include_source=False):
        for root in self.skill_roots()[0 if include_source else 1:]:
            for name, data in self.legacy.items():
                target = root / name / "SKILL.md"
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
                target.chmod(0o644)
        self.seed_old_manifests()

    def native_snapshots(self):
        paths = (self.package / "agents", self.package / ".codex/agents",
                 self.project / ".claude/agents", self.project / ".codex/agents")
        return {path: snapshot(path) for path in paths}

    def assert_retired_absent(self):
        for root in self.skill_roots():
            for name in self.legacy:
                self.assertFalse((root / name).exists(), str(root / name))
            self.assertTrue((root / "jgs-sync/SKILL.md").is_file())
        for root in (self.package, self.project):
            state = json.loads((root / ".jgs-mirror.json").read_text(encoding="utf-8"))
            self.assertEqual({name.split("/", 1)[0] for name in state["skills"]}, {"jgs-sync"})

    def test_fresh_package_keeps_seven_native_agents_and_one_task_skill(self):
        self.assertEqual({path.name for path in (self.package / "skills").iterdir() if path.is_dir()},
                         {"jgs-sync"})
        self.assert_success(self.run_sync())
        expected = {"jgs-" + role for role in ROLES}
        for root, relative, suffix in ((self.package, "agents", ".md"),
                                       (self.package, ".codex/agents", ".toml"),
                                       (self.project, ".claude/agents", ".md"),
                                       (self.project, ".codex/agents", ".toml")):
            self.assertEqual({path.stem for path in (root / relative).glob("jgs-*" + suffix)}, expected)
        for root in self.skill_roots():
            self.assertEqual({path.name for path in root.iterdir() if path.is_dir()}, {"jgs-sync"})
        self.assert_retired_absent()

    def test_unchanged_old_copies_are_removed_without_touching_native_agents_or_returning(self):
        self.assert_success(self.run_sync())
        agents = self.native_snapshots()
        for include_source in (False, True):
            with self.subTest(include_source=include_source):
                self.seed_old_copies(include_source=include_source)
                self.assert_success(self.run_sync())
                self.assert_retired_absent()
                self.assertEqual(self.native_snapshots(), agents)
                before = snapshot(self.root)
                self.assert_success(self.run_sync())
                self.assert_success(self.run_sync(check=True))
                self.assertEqual(snapshot(self.root), before)

    def test_modified_retired_role_is_preserved_and_blocks_all_writes(self):
        self.assert_success(self.run_sync())
        self.seed_old_copies()
        target = self.project / ".claude/skills/jgs-core/SKILL.md"
        target.write_bytes(target.read_bytes() + b"\nUser customized instructions.\n")
        pending = self.package / "roles/particles.md"
        pending.write_bytes(pending.read_bytes() + b"\nPending valid agent edit.\n")
        self.assert_unchanged_rejection()

    def test_unknown_retired_resource_is_preserved_and_blocks_all_writes(self):
        self.assert_success(self.run_sync())
        self.seed_old_copies()
        target = self.project / ".agents/skills/jgs-core/resources/user-notes.md"
        target.parent.mkdir()
        target.write_bytes(b"Private user resource must survive.\r\n")
        self.assert_unchanged_rejection()

    def test_check_reports_old_copies_without_deleting_or_rewriting_anything(self):
        self.assert_success(self.run_sync())
        self.seed_old_copies(include_source=True)
        self.assert_unchanged_rejection(check=True)

    def test_old_manifest_entries_without_files_do_not_resurrect_or_block_retirement(self):
        self.assert_success(self.run_sync())
        self.seed_old_manifests()
        self.assert_success(self.run_sync())
        self.assert_retired_absent()
        before = snapshot(self.root)
        self.assert_success(self.run_sync())
        self.assert_success(self.run_sync(check=True))
        self.assertEqual(snapshot(self.root), before)


if __name__ == "__main__":
    unittest.main()
