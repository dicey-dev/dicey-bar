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
                match = re.search(r"id: " + region + r"Island\s+visible: ([^\n]+)", source)
                self.assertIsNotNone(match, f"{region}Island visible binding not found in Bar.qml")
                expression = match.group(1)
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

    def test_tooltip_waits_for_bar_scale_before_revealing(self):
        source = SOURCE.read_text()
        popup = re.search(r"id: tooltipWindow\n(.*?)\n\s+anchor\s*\{", source, re.S)
        self.assertIsNotNone(popup, "tooltipWindow block not found in Bar.qml")
        scale_ready = re.search(r"readonly property bool scaleReady: ([^\n]+)", popup.group(1))
        on_visible = re.search(r"onVisibleChanged: ([^\n]+)", popup.group(1))
        on_scale_ready = re.search(r"onScaleReadyChanged: (.*?\n\s+\}\))", popup.group(1), re.S)
        bubble = re.search(r"id: tooltipBubble\n(.*?)\n\s+Text\s*\{", source, re.S)
        self.assertIsNotNone(scale_ready, "tooltipWindow has no scaleReady binding")
        self.assertIsNotNone(on_visible, "tooltipWindow has no onVisibleChanged handler")
        self.assertIsNotNone(on_scale_ready, "tooltipWindow has no onScaleReadyChanged handler")
        self.assertIsNotNone(bubble, "tooltipBubble block not found in Bar.qml")
        bubble_opacity = re.search(r"opacity: ([^\n]+)", bubble.group(1))
        self.assertIsNotNone(bubble_opacity, "tooltipBubble has no opacity binding")
        qml = """
import QtQuick
import QtTest
Item {
    id: barWindow
    width: 200; height: 40
    property real devicePixelRatio: 5 / 3

    Item {
        id: tooltipWindow
        visible: false
        property real devicePixelRatio: 1
        readonly property bool scaleReady: __SCALE_READY__
        property bool revealed: false
        onVisibleChanged: __ON_VISIBLE__
        onScaleReadyChanged: __ON_SCALE_READY__

        Item {
            id: tooltipBubble
            opacity: __BUBBLE_OPACITY__
        }
    }

    TestCase {
        name: "TooltipScaleGate"
        when: windowShown
        function test_fractional_and_integer_scales() {
            // Fractional monitor: stays hidden while popup opens at scale 1,
            // then reveals after matching barWindow's fractional scale.
            tooltipWindow.visible = true
            wait(0)
            compare(tooltipWindow.revealed, false)
            compare(tooltipBubble.opacity, 0)

            tooltipWindow.devicePixelRatio = 5 / 3
            compare(tooltipWindow.scaleReady, true)
            wait(0)
            compare(tooltipWindow.revealed, true)
            compare(tooltipBubble.opacity, 1)

            // Hiding resets revealed so the next show re-arms the gate.
            tooltipWindow.visible = false
            compare(tooltipWindow.revealed, false)
            compare(tooltipBubble.opacity, 0)

            // Rapid hide before Qt.callLater runs must not reveal a hidden tooltip.
            tooltipWindow.devicePixelRatio = 5 / 3
            tooltipWindow.visible = true
            tooltipWindow.visible = false
            wait(0)
            compare(tooltipWindow.revealed, false)
            compare(tooltipBubble.opacity, 0)

            // Scale dropping before Qt.callLater runs must keep a visible tooltip hidden.
            tooltipWindow.devicePixelRatio = 1
            tooltipWindow.visible = true
            tooltipWindow.devicePixelRatio = 5 / 3
            tooltipWindow.devicePixelRatio = 1
            wait(0)
            compare(tooltipWindow.revealed, false)
            compare(tooltipBubble.opacity, 0)
            tooltipWindow.visible = false

            // Whole-number scale (2x): a post-first-frame dip to 1 must not re-hide it.
            barWindow.devicePixelRatio = 2
            tooltipWindow.devicePixelRatio = 2
            tooltipWindow.visible = true
            wait(0)
            compare(tooltipWindow.revealed, true)
            compare(tooltipBubble.opacity, 1)

            tooltipWindow.devicePixelRatio = 1
            wait(0)
            compare(tooltipWindow.scaleReady, false)
            compare(tooltipWindow.revealed, true)
            compare(tooltipBubble.opacity, 1)
        }
    }
}
"""
        qml = (
            qml.replace("__SCALE_READY__", scale_ready.group(1))
            .replace("__ON_VISIBLE__", on_visible.group(1))
            .replace("__ON_SCALE_READY__", on_scale_ready.group(1))
            .replace("__BUBBLE_OPACITY__", bubble_opacity.group(1))
        )
        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(directory) / "tst_tooltip_scale.qml"
            fixture.write_text(qml)
            result = subprocess.run(
                ["/usr/lib/qt6/bin/qmltestrunner", "-input", str(fixture)],
                env={**os.environ, "QT_QPA_PLATFORM": "offscreen", "QT_QUICK_BACKEND": "software"},
                capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

if __name__ == "__main__":
    unittest.main()
