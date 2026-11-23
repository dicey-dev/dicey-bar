"""Run BarModel's layout helpers through Qt's own JavaScript engine."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

MODEL = Path(os.environ.get("DICEY_BAR_MODEL", Path(__file__).resolve().parents[1] / "BarModel.js"))

QML = """
import QtQuick
import QtTest
import "MODEL" as BarModel

TestCase {
    name: "BarModelSections"

    function test_center_island_from_current_sections() {
        var island = BarModel.centerIsland({
            centerBegin: [{ id: "omarchy.indicators" }],
            center: [{ id: "omarchy.clock" }],
            centerEnd: [{ id: "omarchy.weather" }, { id: "omarchy.elsewhen" }]
        }, "")
        compare(island.anchor.map(BarModel.entryId), ["omarchy.clock"])
        compare(island.before.entries.map(BarModel.entryId), ["omarchy.indicators"])
        compare(island.before.regions, ["centerBegin"])
        compare(island.after.entries.map(BarModel.entryId), ["omarchy.weather", "omarchy.elsewhen"])
        compare(island.after.regions, ["centerEnd", "centerEnd"])
    }

    function test_legacy_center_anchor_keeps_its_flanks() {
        var island = BarModel.centerIsland({
            center: [{ id: "omarchy.indicators" }, { id: "omarchy.clock" }, { id: "omarchy.weather" }]
        }, "omarchy.clock")
        compare(island.anchor.map(BarModel.entryId), ["omarchy.clock"])
        compare(island.before.entries.map(BarModel.entryId), ["omarchy.indicators"])
        compare(island.before.regions, ["center"])
        compare(island.after.entries.map(BarModel.entryId), ["omarchy.weather"])
        compare(island.after.regions, ["center"])
    }

    function test_legacy_anchor_and_current_sections_combine_in_reading_order() {
        var island = BarModel.centerIsland({
            centerBegin: ["a"], center: ["b", "clock", "c"], centerEnd: ["d"]
        }, "clock")
        compare(island.before.entries, ["a", "b"])
        compare(island.before.regions, ["centerBegin", "center"])
        compare(island.anchor, ["clock"])
        compare(island.after.entries, ["c", "d"])
        compare(island.after.regions, ["center", "centerEnd"])
    }

    function test_missing_anchor_shows_the_whole_center_section() {
        var island = BarModel.centerIsland({ center: ["a", "b"] }, "omarchy.clock")
        compare(island.anchor, ["a", "b"])
        compare(island.before.entries, [])
        compare(island.after.entries, [])
    }

    function test_structural_change_in_a_new_section_is_not_a_settings_delta() {
        var current = { left: [], centerBegin: [], center: ["clock"], centerEnd: [], right: [], tray: [] }
        var next = { left: [], centerBegin: ["omarchy.indicators"], center: ["clock"], centerEnd: [], right: [], tray: [] }
        compare(BarModel.inlineSettingsDelta(current, next), null)
        var moved = { left: [], centerBegin: [], center: ["clock"], centerEnd: [], right: [], tray: ["omarchy.weather"] }
        compare(BarModel.inlineSettingsDelta(current, moved), null)
    }

    function test_settings_change_in_a_new_section_is_a_delta() {
        var current = { left: [], centerBegin: [], center: [], centerEnd: [{ id: "omarchy.weather", unit: "c" }], right: [], tray: [] }
        var next = { left: [], centerBegin: [], center: [], centerEnd: [{ id: "omarchy.weather", unit: "f" }], right: [], tray: [] }
        var delta = BarModel.inlineSettingsDelta(current, next)
        compare(delta.length, 1)
        compare(delta[0].region, "centerEnd")
        compare(delta[0].index, 0)
    }

    function test_center_sections_navigate_as_one() {
        compare(BarModel.navigationRegions("centerEnd"), ["centerBegin", "center", "centerEnd"])
        compare(BarModel.navigationRegions("center"), ["centerBegin", "center", "centerEnd"])
        compare(BarModel.navigationRegions("right"), ["right"])
        compare(BarModel.navigationRegions("tray"), ["tray"])
    }

    function test_fullscreen_only_counts_real_fullscreen_on_that_workspace() {
        var toplevels = [
            { workspace: { id: 1 }, lastIpcObject: { fullscreen: 1 } },
            { workspace: { id: 2 }, lastIpcObject: { fullscreen: 2 } },
            { workspace: null, lastIpcObject: { fullscreen: 2 } }
        ]
        compare(BarModel.fullscreenOn(toplevels, 1), false)
        compare(BarModel.fullscreenOn(toplevels, 2), true)
        compare(BarModel.fullscreenOn(toplevels, 3), false)
        compare(BarModel.fullscreenOn(null, 2), false)
    }
}
"""


class BarModelTest(unittest.TestCase):
    def test_layout_helpers(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(directory) / "tst_bar_model.qml"
            fixture.write_text(QML.replace("MODEL", MODEL.as_uri()))
            result = subprocess.run(
                ["/usr/lib/qt6/bin/qmltestrunner", "-input", str(fixture)],
                env={**os.environ, "QT_QPA_PLATFORM": "offscreen", "QT_QUICK_BACKEND": "software"},
                capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
