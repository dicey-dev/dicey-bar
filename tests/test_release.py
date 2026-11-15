import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("release_version", ROOT / "scripts/version.py")
version = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(version)


class ReleaseTest(unittest.TestCase):
    def test_semantic_bumps_and_invalid_versions(self):
        self.assertEqual(version.bumped("0.1.2", "patch"), "0.1.3")
        self.assertEqual(version.bumped("0.1.2", "minor"), "0.2.0")
        self.assertEqual(version.bumped("0.1.2", "major"), "1.0.0")
        for invalid in ("1.2", "01.2.3", "1.2.3-extra", "garbage"):
            with self.assertRaises(ValueError):
                version.bumped(invalid, "patch")

    def test_check_requires_release_notes_and_allows_unreleased(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "manifest.json").write_text(json.dumps({"id": "example.test", "version": "0.1.0"}))
            (root / "CHANGELOG.md").write_text("# Changelog\n\n## [Unreleased]\n\n- WIP.\n\n## [0.1.0]\n\n- Baseline.\n")
            with patch.object(version, "ROOT", root), patch.object(sys, "argv", ["version.py", "check"]), \
                    patch("sys.stderr"), self.assertRaises(SystemExit):
                version.main()  # Every released version needs committed release notes.
            (root / "docs/releases").mkdir(parents=True)
            (root / "docs/releases/v0.1.0.md").write_text("# v0.1.0\n")
            with patch.object(version, "ROOT", root), patch.object(sys, "argv", ["version.py", "check"]), patch("builtins.print") as output:
                version.main()
            self.assertIn("unreleased development; last release v0.1.0", output.call_args.args[0])
            with patch.object(version, "ROOT", root), patch.object(sys, "argv", ["version.py", "minor", "A feature."]), \
                    patch("builtins.print"):
                version.main()
            changelog = (root / "CHANGELOG.md").read_text()
            self.assertLess(changelog.index("## [Unreleased]"), changelog.index("## [0.2.0]"))
            self.assertLess(changelog.index("## [0.2.0]"), changelog.index("## [0.1.0]"))
            self.assertIn("- A feature.", (root / "docs/releases/v0.2.0.md").read_text())

    def test_repository_has_notes_for_every_release(self):
        releases = version.re.findall(r"^## \[(\d+\.\d+\.\d+)\]", (ROOT / "CHANGELOG.md").read_text(), version.re.MULTILINE)
        self.assertTrue(releases)
        for release in releases:
            self.assertTrue((ROOT / "docs/releases" / f"v{release}.md").is_file(), release)

    def test_bump_updates_manifest_and_changelog_without_publishing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "manifest.json").write_text(json.dumps({"id": "example.test", "version": "0.1.0"}))
            (root / "CHANGELOG.md").write_text("# Changelog\n\n## [0.1.0]\n\n- Previous.\n")
            with patch.object(version, "ROOT", root), patch.object(sys, "argv", ["version.py", "minor", "A feature."]), \
                    patch("builtins.print"):
                version.main()
            self.assertEqual(json.loads((root / "manifest.json").read_text())["version"], "0.2.0")
            self.assertIn("\n## [0.2.0]", (root / "CHANGELOG.md").read_text())
            self.assertIn("\n## [0.1.0]", (root / "CHANGELOG.md").read_text())

    def test_distributed_license_and_release_metadata(self):
        manifest = json.loads((ROOT / "manifest.json").read_text())
        self.assertEqual(manifest["license"], "Apache-2.0 AND MIT")
        self.assertTrue(manifest["url"].startswith("https://github.com/dicey-dev/"))
        self.assertIn("Copyright (c) David Heinemeier Hansson", (ROOT / "THIRD_PARTY_NOTICES.md").read_text())
        self.assertIn("Apache License", (ROOT / "LICENSE").read_text())
