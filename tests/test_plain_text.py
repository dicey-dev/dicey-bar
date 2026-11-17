from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
# Raw Qt text items default to AutoText, which renders markup such as <img src="http://...">.
TEXT_ITEM = re.compile(r"(?<![.\w])(Text|Label|TextEdit|TextArea|TextInput)\s*\{")
PLAIN = re.compile(r"^\s*textFormat:\s*Text\.PlainText\s*$", re.M)


def block(text, start):
    """Return the body of the object whose opening brace is at start, skipping string literals."""
    depth, index, quote = 0, start, None
    while index < len(text):
        char = text[index]
        if quote:
            if char == "\\":
                index += 1
            elif char == quote:
                quote = None
        elif char in "\"'":
            quote = char
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start + 1:index]
        index += 1
    raise ValueError("unbalanced braces")


def own_lines(body):
    """Drop nested objects so only the item's own properties remain."""
    kept, depth = [], 0
    for line in body.splitlines():
        if depth == 0:
            kept.append(line)
        depth += line.count("{") - line.count("}")
    return "\n".join(kept)


class PlainTextTest(unittest.TestCase):
    def test_every_text_item_renders_plain_text(self):
        # Tray titles, menus and window titles come from other programs, so markup must never load.
        found = 0
        for path in sorted(ROOT.rglob("*.qml")):
            text = path.read_text()
            for match in TEXT_ITEM.finditer(text):
                found += 1
                number = text.count("\n", 0, match.start()) + 1
                body = own_lines(block(text, match.end() - 1))
                self.assertRegex(body, PLAIN, f"{path.relative_to(ROOT)}:{number}: set textFormat: Text.PlainText")
        self.assertGreater(found, 0)


if __name__ == "__main__":
    unittest.main()
