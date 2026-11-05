"""Project setup keeps definitions in a real submodule directory."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from test_mirror_sync import snapshot, write_claude, write_codex

try:
    import tomllib
except ImportError:
    tomllib = None

SOURCE = Path(__file__).resolve().parents[1]


@unittest.skipIf(tomllib is None, "Python 3.11+ required")
class ProjectReferenceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="jgs-reference-")
        self.addCleanup(temporary.cleanup)
        self.project = Path(temporary.name).resolve() / "프로젝트 with spaces"
        self.package = self.project / "tools" / "studio package"
        shutil.copytree(SOURCE, self.package,
                        ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc"))

    def sync(self, *arguments):
        return subprocess.run([sys.executable, str(self.package / "scripts/sync-studio.py"),
                               "--project", str(self.project), *arguments],
                              capture_output=True, timeout=20)

    def expect_ok(self, result):
        self.assertEqual(result.returncode, 0, (result.stdout + result.stderr).decode("utf-8"))

    def assert_references_only(self):
        for directory in (".claude/agents", ".codex/agents", ".agents/skills", ".claude/skills"):
            path = self.project / directory
            self.assertFalse(path.exists() and any(p.is_file() for p in path.rglob("*")), directory)
        self.assertFalse(any(p.is_symlink() for p in self.project.rglob("*")))
        self.assertTrue((self.package / "skills/jgs-sync/SKILL.md").is_file())
        self.assertFalse((self.project / ".jgs-mirror.json").exists())
        self.assertFalse((self.project / ".codex").exists())

    def seed_legacy_copies(self):
        self.expect_ok(self.sync())
        team = json.loads((self.package / "team.json").read_text(encoding="utf-8"))
        for role, entry in team.items():
            body = (self.package / "roles" / (role + ".md")).read_text(encoding="utf-8")
            write_claude(self.project / ".claude/agents" / (entry["agent"] + ".md"), entry["agent"], entry["purpose"], body)
            write_codex(self.project / ".codex/agents" / (entry["agent"] + ".toml"), entry["agent"], entry["purpose"], body)
        for directory in (".agents/skills", ".claude/skills"):
            shutil.copytree(self.package / "skills", self.project / directory)
        state = json.loads((self.package / ".jgs-mirror.json").read_text(encoding="utf-8"))
        state["package"] = "tools/studio package"
        (self.project / ".jgs-mirror.json").write_text(json.dumps(state), encoding="utf-8")

    def test_default_setup_registers_native_paths_and_never_copies_definitions(self):
        self.expect_ok(self.sync())
        self.assert_references_only()
        catalog = json.loads((self.project / ".agents/plugins/marketplace.json").read_text(encoding="utf-8"))
        self.assertEqual(catalog["plugins"][0]["source"]["path"], "./tools/studio package")
        profiles = list((self.package / "codex-agents").glob("jgs-*.toml"))
        self.assertEqual(len(profiles), 7)
        for path in profiles:
            self.assertEqual(set(tomllib.loads(path.read_text(encoding="utf-8"))), {"developer_instructions"})
        settings = json.loads((self.project / ".claude/settings.json").read_text(encoding="utf-8"))
        self.assertEqual(settings["extraKnownMarketplaces"]["jaeho-studio"]["source"]["path"],
                         "./tools/studio package")

    def test_repeated_setup_and_check_do_not_touch_files(self):
        self.expect_ok(self.sync())
        before = snapshot(self.project)
        self.expect_ok(self.sync())
        self.expect_ok(self.sync("--check"))
        self.assertEqual(snapshot(self.project), before)

    def test_former_root_path_record_is_removed_once_without_recreation(self):
        self.expect_ok(self.sync())
        state = self.project / ".jgs-mirror.json"
        state.write_bytes((json.dumps({"schema": 2, "mode": "references", "package": "tools/studio package"}) + "\r\n").encode("utf-8"))
        before = snapshot(self.project)
        self.assertEqual(self.sync("--check").returncode, 1)
        self.assertEqual(snapshot(self.project), before)
        self.expect_ok(self.sync())
        self.assert_references_only()
        self.assertTrue((self.package / ".jgs-mirror.json").is_file())
        after = snapshot(self.project)
        self.expect_ok(self.sync())
        self.expect_ok(self.sync("--check"))
        self.assertEqual(snapshot(self.project), after)

    def test_unrecognized_root_metadata_is_preserved_before_any_changes(self):
        state = self.project / ".jgs-mirror.json"
        state.write_bytes(b'{"custom": "keep"}\n')
        before = snapshot(self.project)
        result = self.sync()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"invalid former project registration", result.stderr)
        self.assertEqual(snapshot(self.project), before)

    def test_former_managed_codex_folder_is_removed_and_not_recreated(self):
        config = self.project / ".codex/config.toml"
        config.parent.mkdir()
        config.write_text('# Codex agent paths\n\n# BEGIN JAEHOGAMESTUDIO REFERENCES\n[agents.jgs-core]\nconfig_file = "../tools/studio package/codex-agents/jgs-core.toml"\n# END JAEHOGAMESTUDIO REFERENCES\n', encoding="utf-8")
        self.expect_ok(self.sync())
        self.assert_references_only()
        before = snapshot(self.project)
        self.expect_ok(self.sync())
        self.expect_ok(self.sync("--check"))
        self.assertEqual(snapshot(self.project), before)

    def test_crlf_checkout_keeps_reference_checks_and_setup_read_only(self):
        self.expect_ok(self.sync())
        for name in (".claude/settings.json", ".agents/plugins/marketplace.json"):
            path = self.project / name
            path.write_bytes(path.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
        before = snapshot(self.project)
        self.expect_ok(self.sync("--check"))
        self.expect_ok(self.sync())
        self.assertEqual(snapshot(self.project), before)

    def test_ordinary_skill_and_role_edits_need_no_project_definition_update(self):
        self.expect_ok(self.sync())
        paths = (".claude/settings.json", ".agents/plugins/marketplace.json")
        before = {name: (self.project / name).read_bytes() for name in paths}
        skill = self.package / "skills/jgs-sync/SKILL.md"
        skill.write_text(skill.read_text(encoding="utf-8") + "\nReference layout regression.\n", encoding="utf-8")
        role = self.package / "roles/core.md"
        role.write_text(role.read_text(encoding="utf-8") + "\nRole body update.\n", encoding="utf-8")
        self.expect_ok(self.sync())
        self.assertEqual({name: (self.project / name).read_bytes() for name in paths}, before)
        self.assert_references_only()

    def test_new_agent_stays_in_package_without_project_codex(self):
        self.expect_ok(self.sync())
        write_claude(self.package / "agents/jgs-build.md", "jgs-build", "Build role", "Build instructions.\n")
        self.expect_ok(self.sync())
        profile = tomllib.loads((self.package / "codex-agents/jgs-build.toml").read_text(encoding="utf-8"))
        self.assertEqual(profile["developer_instructions"], "Build instructions.\n")
        self.assert_references_only()

    def test_unchanged_old_copies_are_migrated_and_cannot_be_recreated(self):
        self.seed_legacy_copies()
        self.expect_ok(self.sync())
        self.assert_references_only()
        before = snapshot(self.project)
        self.assertNotEqual(self.sync("--legacy-copies").returncode, 0)
        self.assertEqual(snapshot(self.project), before)

    def test_local_edits_stop_migration_without_changing_files(self):
        self.seed_legacy_copies()
        path = self.project / ".claude/agents/jgs-core.md"
        path.write_text(path.read_text(encoding="utf-8") + "\nKeep local changes.\n", encoding="utf-8")
        before = snapshot(self.project)
        result = self.sync()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"local agent edits", result.stderr)
        self.assertEqual(snapshot(self.project), before)

    def test_preserves_unrelated_settings_agents_and_context(self):
        originals = {".codex/config.toml": b"[agents.user-role]\ndescription = 'Keep me'\n\n[plugins.'other-plugin@user-market']\nenabled = false\n",
                     ".codex/agents/user-role.toml": b"custom native profile\n",
                     ".claude/settings.json": b'{"permissions":{"deny":["Read(private)"]}}\n',
                     ".jaeho-studio/project.md": b"Keep existing context\n"}
        for name, data in originals.items():
            path = self.project / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        self.expect_ok(self.sync())
        for name in (".codex/agents/user-role.toml", ".jaeho-studio/project.md"):
            self.assertEqual((self.project / name).read_bytes(), originals[name])
        config = tomllib.loads((self.project / ".codex/config.toml").read_text(encoding="utf-8"))
        self.assertEqual(config["agents"]["user-role"]["description"], "Keep me")
        self.assertEqual(config["plugins"], {"other-plugin@user-market": {"enabled": False}})
        settings = json.loads((self.project / ".claude/settings.json").read_text(encoding="utf-8"))
        self.assertEqual(settings["permissions"], {"deny": ["Read(private)"]})

    def test_check_is_read_only_before_initial_setup(self):
        before = snapshot(self.project)
        self.assertEqual(self.sync("--check").returncode, 1)
        self.assertEqual(snapshot(self.project), before)


if __name__ == "__main__":
    unittest.main()
