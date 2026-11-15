from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
BARE_COLOR = re.compile(r"(?<![.\w])Color\.")


class ThemeTokenTest(unittest.TestCase):
    def test_palette_is_qualified_for_qt_6_12(self):
        # Qt 6.12 has its own Color type, so the shell reference requires Commons.Color.
        for path in sorted(ROOT.rglob("*.qml")):
            text = path.read_text()
            for number, line in enumerate(text.splitlines(), 1):
                code = line.split("//", 1)[0]
                self.assertIsNone(BARE_COLOR.search(code), f"{path.relative_to(ROOT)}:{number}: use Commons.Color")
            if "Commons.Color" in text:
                self.assertIn("import qs.Commons as Commons", text, path.relative_to(ROOT))
