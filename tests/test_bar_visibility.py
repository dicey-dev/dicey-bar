"""Run the actual island visibility bindings through Qt's hide/show lifecycle."""
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

SOURCE = Path(os.environ.get("DICEY_BAR_SOURCE", Path(__file__).resolve().parents[1] / "Bar.qml"))

class BarVisibilityTest(unittest.TestCase):
    def test_islands_reappear_after_remap(self):
        source = SOURCE.read_text()
        for region in ("left", "center", "right"):
            with self.subTest(region=region):
                expression = re.search(r"id: " + region + r"Island\s+visible: ([^\n]+)", source).group(1)
                child = region + "Modules"
                qml = """
import QtQuick
import QtTest
Item {
    id: host
    width: 200; height: 40
    property bool mapped: true
    Item {
        visible: host.mapped
        Rectangle {
            id: island
            visible: EXPRESSION
            Item {
                id: CHILD
                property var entries: [1]
                property bool layoutVisible: true
                visible: layoutVisible && entries.length > 0
            }
        }
    }
    TestCase {
        name: "IslandRemapping"
        when: windowShown
        function test_visibility() {
            compare(island.visible, true)
            for (var i = 0; i < 3; i++) {
                host.mapped = false
                compare(island.visible, false)
                host.mapped = true
                compare(island.visible, true)
            }
            CHILD.entries = []
            compare(island.visible, false)
            CHILD.entries = [1]
            compare(island.visible, true)
        }
    }
}
""".replace("EXPRESSION", expression).replace("CHILD", child)
                with tempfile.TemporaryDirectory() as directory:
                    fixture = Path(directory) / "tst_island.qml"
                    fixture.write_text(qml)
                    result = subprocess.run(
                        ["/usr/lib/qt6/bin/qmltestrunner", "-input", str(fixture)],
                        env={**os.environ, "QT_QPA_PLATFORM": "offscreen", "QT_QUICK_BACKEND": "software"},
                        capture_output=True, text=True,
                    )
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_empty_islands_do_not_paint_while_widgets_load(self):
        source = SOURCE.read_text()
        for region in ("left", "center", "right"):
            with self.subTest(region=region):
                block = re.search(r"id: " + region + r"Island\n(.*?)\n\n", source, re.S)
                self.assertIsNotNone(block, f"{region}Island block not found in Bar.qml")
                visible = re.search(r"visible: ([^\n]+)", block.group(1))
                color = re.search(r"color: ([^\n]+)", block.group(1))
                self.assertIsNotNone(visible, f"{region}Island has no visible binding")
                self.assertIsNotNone(color, f"{region}Island has no color binding")
                visible = visible.group(1)
                color = color.group(1).replace("root.islandBackground", '"#202020"')
                child = region + "Modules"
                # The child sizes itself from its widget's effective visibility,
                # as ModuleSlot does, so unmapping drops its width to zero.
                qml = """
import QtQuick
import QtTest
Item {
    id: host
    width: 200; height: 40
    property bool mapped: true
    Item {
        visible: host.mapped
        Rectangle {
            id: island
            visible: VISIBLE
            color: COLOR
            Item {
                id: CHILD
                property var entries: [1]
                property bool loaded: false
                width: loaded && widget.visible ? 30 : 0
                Item { id: widget }
            }
        }
    }
    TestCase {
        name: "IslandPaint"
        when: windowShown
        function test_paint() {
            compare(island.visible, true)
            compare(Qt.colorEqual(island.color, "transparent"), true)
            CHILD.loaded = true
            compare(Qt.colorEqual(island.color, "#202020"), true)
            for (var i = 0; i < 3; i++) {
                host.mapped = false
                host.mapped = true
                compare(island.visible, true)
                compare(CHILD.width, 30)
                compare(Qt.colorEqual(island.color, "#202020"), true)
            }
            CHILD.loaded = false
            compare(Qt.colorEqual(island.color, "transparent"), true)
        }
    }
}
""".replace("VISIBLE", visible).replace("COLOR", color).replace("CHILD", child)
                with tempfile.TemporaryDirectory() as directory:
                    fixture = Path(directory) / "tst_island_paint.qml"
                    fixture.write_text(qml)
                    result = subprocess.run(
                        ["/usr/lib/qt6/bin/qmltestrunner", "-input", str(fixture)],
                        env={**os.environ, "QT_QPA_PLATFORM": "offscreen", "QT_QUICK_BACKEND": "software"},
                        capture_output=True, text=True,
                    )
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

if __name__ == "__main__":
    unittest.main()
