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

    def test_major_bump_of_alpha_requires_explicit_stability_decision(self):
        def run(root, *args):
            with patch.object(version, "ROOT", root), patch.object(sys, "argv", ["version.py", *args, "A note."]), \
                    patch("builtins.print"), patch("sys.stderr"):
                version.main()

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            changelog = "# Changelog\n\n## [0.1.0]\n\n- Previous.\n"
            (root / "manifest.json").write_text(json.dumps({"id": "example.test", "version": "0.1.0"}))
            (root / "CHANGELOG.md").write_text(changelog)
            for args in (("major",), ("minor", "--stable")):
                with self.assertRaises(SystemExit):
                    run(root, *args)
                self.assertEqual(json.loads((root / "manifest.json").read_text())["version"], "0.1.0")
                self.assertEqual((root / "CHANGELOG.md").read_text(), changelog)
            run(root, "major", "--stable")
            self.assertEqual(json.loads((root / "manifest.json").read_text())["version"], "1.0.0")
            self.assertIn("\n## [1.0.0]", (root / "CHANGELOG.md").read_text())
