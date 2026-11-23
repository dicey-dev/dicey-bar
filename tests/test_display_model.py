"""Run the Displays panel's layout model through Qt's own JavaScript engine."""
from pathlib import Path
import subprocess
import tempfile
import unittest

MODEL = Path(__file__).resolve().parents[1] / "displays" / "DisplayModel.js"

QML = r"""
import QtQuick
import QtTest
import "MODEL" as Model

TestCase {
    name: "DisplayModel"

    function monitor(name, make, model, serial, w, h, hz, x, y, extra) {
        var m = {
            name: name, make: make, model: model, serial: serial,
            description: [make, model, serial].filter(function(s) { return s }).join(" "),
            width: w, height: h, refreshRate: hz, x: x, y: y, scale: 1, transform: 0,
            disabled: false, vrr: false, colorManagementPreset: "srgb",
            physicalWidth: 600, availableModes: [w + "x" + h + "@" + hz.toFixed(2) + "Hz"]
        }
        for (var k in extra || {}) m[k] = extra[k]
        return m
    }

    function laptop() { return monitor("eDP-1", "LG Display", "0x0753", "", 1920, 1080, 60.02, 313, 1440, { physicalWidth: 310 }) }
    function benq() {
        return monitor("HDMI-A-1", "BNQ", "BenQ GW2790Q", "ET97T00426087", 2560, 1440, 100, 0, 0, {
            availableModes: ["2560x1440@59.95Hz", "2560x1440@100.00Hz", "1920x1080@100.00Hz", "1920x1080@60.00Hz"]
        })
    }
    function dell(x) { return monitor("DP-3", "DEL", "U2723QE", "ABC123", 1920, 1080, 60, x === undefined ? 4480 : x, 0) }

    function byName(plan, name) {
        for (var i = 0; i < plan.length; i++) if (plan[i].name === name) return plan[i]
        return null
    }

    function test_identity_uses_edid_and_names_internal_panels_by_connector() {
        var ids = Model.identify([laptop(), benq()])
        compare(ids[0].fp, "LG Display|0x0753|")
        compare(ids[0].selector, "eDP-1")
        compare(ids[0].internal, true)
        compare(ids[1].fp, "BNQ|BenQ GW2790Q|ET97T00426087")
        compare(ids[1].selector, "desc:BNQ BenQ GW2790Q ET97T00426087")
    }

    function test_identical_monitors_are_told_apart_by_connector() {
        var a = monitor("DP-1", "ACME", "Panel", "", 1920, 1080, 60, 0, 0)
        var b = monitor("DP-2", "ACME", "Panel", "", 1920, 1080, 60, 1920, 0)
        var ids = Model.identify([a, b])
        compare(ids[0].fp, "ACME|Panel||DP-1")
        compare(ids[1].fp, "ACME|Panel||DP-2")
        compare(ids[0].selector, "DP-1")
    }

    function test_modes_split_into_resolution_and_refresh_rate() {
        var groups = Model.modeGroups(benq().availableModes)
        compare(groups.map(function(g) { return g.key }), ["2560x1440", "1920x1080"])
        compare(groups[0].rates, [100, 59.95])
        compare(groups[1].rates, [100, 60])
    }

    function test_pick_mode_keeps_choice_or_falls_back_at_the_same_resolution() {
        var modes = benq().availableModes
        compare(Model.pickMode(modes, { w: 2560, h: 1440, hz: 59.95 }), { w: 2560, h: 1440, hz: 59.95 })
        compare(Model.pickMode(modes, { w: 2560, h: 1440, hz: 144 }), { w: 2560, h: 1440, hz: 100 })
        compare(Model.pickMode(modes, { w: 3840, h: 2160, hz: 60 }), { w: 2560, h: 1440, hz: 100 })
        compare(Model.pickMode(modes, null), { w: 2560, h: 1440, hz: 100 })
    }

    function test_scales_are_clean_for_the_mode() {
        compare(Model.cleanScale(1.25, 2560, 1440), 1.25)
        compare(Model.cleanScale(1.6, 1920, 1080), 1.6)
        compare(Model.cleanScale(1.3, 1920, 1080), 1.333)
    }

    function test_attach_closes_a_gap_with_the_shortest_move() {
        var placed = [{ x: 0, y: 0, w: 1920, h: 1080 }]
        var moved = Model.attach({ x: 3840, y: 0, w: 1920, h: 1080 }, placed)
        compare([moved.x, moved.y], [1920, 0])
        var below = Model.attach({ x: 100, y: 1300, w: 1920, h: 1080 }, placed)
        compare([below.x, below.y], [100, 1080])
    }

    function test_attach_pushes_an_overlapping_monitor_out() {
        var placed = [{ x: 0, y: 0, w: 2560, h: 1440 }]
        var r = Model.attach({ x: 2400, y: 100, w: 1920, h: 1080 }, placed)
        compare([r.x, r.y], [2560, 100])
    }

    function test_attach_snaps_edges_into_line_when_close() {
        var placed = [{ x: 0, y: 0, w: 2560, h: 1440 }]
        var r = Model.attach({ x: 2560, y: 40, w: 1920, h: 1080 }, placed, 60)
        compare([r.x, r.y], [2560, 0])
        var centred = Model.attach({ x: 330, y: 1440, w: 1920, h: 1080 }, placed, 60)
        compare([centred.x, centred.y], [320, 1440])
    }

    function test_derive_without_memory_keeps_the_live_layout() {
        var plan = Model.derive([laptop(), benq()], {}, {})
        compare([byName(plan, "HDMI-A-1").x, byName(plan, "HDMI-A-1").y], [0, 0])
        compare([byName(plan, "eDP-1").x, byName(plan, "eDP-1").y], [313, 1440])
        compare(byName(plan, "HDMI-A-1").mode.hz, 100)
    }

    function test_apply_then_reconnect_restores_the_saved_layout() {
        var plan = Model.derive([laptop(), benq()], {}, {})
        byName(plan, "HDMI-A-1").mode = { w: 2560, h: 1440, hz: 59.95 }
        byName(plan, "eDP-1").scale = 1.25
        var memory = Model.remember({}, Model.solve(plan), "2026-11-23T10:00:00Z")

        // Next time Hyprland brings them up somewhere else and at defaults.
        var replug = benq()
        replug.x = 1920
        replug.refreshRate = 59.95
        var again = Model.derive([laptop(), replug], memory, {})
        compare(byName(again, "HDMI-A-1").mode.hz, 59.95)
        compare(byName(again, "eDP-1").scale, 1.25)
        var b = byName(again, "HDMI-A-1")
        var e = byName(again, "eDP-1")
        compare([b.x, b.y], [0, 0])
        compare(e.y, 1440)
    }

    function test_a_monitor_keeps_its_settings_in_a_new_combination() {
        var plan = Model.derive([laptop(), benq()], {}, {})
        byName(plan, "HDMI-A-1").mode = { w: 1920, h: 1080, hz: 100 }
        byName(plan, "HDMI-A-1").vrr = 2
        var memory = Model.remember({}, Model.solve(plan), "2026-11-23T10:00:00Z")
        var three = Model.derive([laptop(), benq(), dell()], memory, {})
        compare(byName(three, "HDMI-A-1").mode, { w: 1920, h: 1080, hz: 100 })
        compare(byName(three, "HDMI-A-1").vrr, 2)
        compare(byName(three, "DP-3").vrr, 0)
    }

    function test_three_monitors_lose_the_middle_one_and_close_the_gap() {
        var left = laptop(); left.x = 0; left.y = 0
        var middle = benq(); middle.x = 1920; middle.y = 0
        var right = dell(4480)
        var plan = Model.derive([left, middle, right], {}, {})
        var memory = Model.remember({}, plan, "2026-11-23T10:00:00Z")
        var two = Model.derive([left, right], memory, {})
        compare([byName(two, "eDP-1").x, byName(two, "DP-3").x], [0, 1920])
        compare(byName(two, "DP-3").y, 0)
    }

    function test_new_monitor_returns_beside_its_last_neighbour() {
        var left = laptop(); left.x = 0; left.y = 360
        var top = benq(); top.x = 1920; top.y = 0
        var memory = Model.remember({}, Model.derive([left, top], {}, {}), "2026-11-23T10:00:00Z")
        // Dell was last used below the BenQ, in a setup without the laptop.
        var bq = benq(); bq.x = 0; bq.y = 0
        var d = dell(0); d.y = 1440
        memory = Model.remember(memory, Model.derive([bq, d], {}, {}), "2026-11-23T11:00:00Z")
        // All three together have never been arranged.
        var three = Model.derive([left, top, dell(9000)], memory, {})
        var b = byName(three, "HDMI-A-1")
        var dp = byName(three, "DP-3")
        compare(dp.y, b.y + 1440)
        compare(dp.x, b.x)
    }

    function test_lid_shut_leaves_the_panel_alone_and_uses_the_external_layout() {
        var plan = Model.derive([laptop(), benq()], {}, {})
        var off = Model.autoOffFor(plan, true, {})
        compare(off["LG Display|0x0753|"], "lid")
        var closed = Model.derive([laptop(), benq()], {}, off)
        compare(byName(closed, "eDP-1").autoOff, true)
        compare([byName(closed, "HDMI-A-1").x, byName(closed, "HDMI-A-1").y], [0, 0])
        compare(Model.autoOffFor(Model.derive([laptop()], {}, {}), true, {}), {})
    }

    function test_monitor_switched_to_another_input_is_turned_off_until_it_returns() {
        var fp = "BNQ|BenQ GW2790Q|ET97T00426087"
        var off = Model.autoOffFor(Model.derive([laptop(), benq()], {}, {}), false, { [fp]: true })
        compare(off[fp], "input")
        var plan = Model.derive([laptop(), benq()], {}, off)
        compare(byName(plan, "HDMI-A-1").offReason, "input")
        var live = Model.planFromLive([laptop(), benq()], {}, off)
        verify(!Model.planMatchesLive(plan, live))
        var dark = benq()
        dark.disabled = true
        var moved = laptop()
        moved.x = byName(plan, "eDP-1").x
        moved.y = byName(plan, "eDP-1").y
        verify(Model.planMatchesLive(plan, Model.planFromLive([moved, dark], {}, off)))
        var lid = Model.derive([laptop(), benq()], {}, Model.autoOffFor(plan, true, {}))
        verify(Model.planMatchesLive(lid, Model.planFromLive([laptop(), benq()], {})))
    }

    function test_never_turns_every_monitor_off() {
        var plan = Model.derive([benq()], {}, { "BNQ|BenQ GW2790Q|ET97T00426087": "input" })
        compare(plan[0].autoOff, false)
        compare(plan[0].enabled, true)
    }

    function test_manual_off_is_remembered_for_that_combination() {
        var plan = Model.derive([laptop(), benq()], {}, {})
        byName(plan, "eDP-1").enabled = false
        var memory = Model.remember({}, Model.solve(plan), "t")
        var again = Model.derive([laptop(), benq()], memory, {})
        compare(byName(again, "eDP-1").enabled, false)
        compare([byName(again, "HDMI-A-1").x, byName(again, "HDMI-A-1").y], [0, 0])
    }

    function test_plan_matches_live_only_when_nothing_would_change() {
        var live = Model.planFromLive([laptop(), benq()], {})
        var plan = Model.derive([laptop(), benq()], {}, {})
        verify(Model.planMatchesLive(plan, live))
        byName(plan, "eDP-1").x += 10
        verify(!Model.planMatchesLive(plan, live))
    }

    function test_confirmation_only_for_mode_or_power_changes() {
        var live = Model.planFromLive([laptop(), benq()], {})
        var plan = Model.derive([laptop(), benq()], {}, {})
        byName(plan, "eDP-1").scale = 1.25
        verify(!Model.needsConfirmation(plan, live))
        byName(plan, "HDMI-A-1").mode = { w: 1920, h: 1080, hz: 60 }
        verify(Model.needsConfirmation(plan, live))
    }

    function test_ddc_needs_two_misses_and_recovers_on_its_own_input() {
        compare(Model.ddcStep(0, "0x0f", "0x11"), { misses: 1, off: false })
        compare(Model.ddcStep(1, null, "0x11"), { misses: 2, off: true })
        compare(Model.ddcStep(2, "0x11", "0x11"), { misses: 0, off: false })
    }

    function test_remember_keeps_a_learned_input_while_watched() {
        var plan = Model.derive([benq()], {}, {})
        plan[0].ddcWatch = true
        var memory = Model.remember({}, plan, "t")
        memory.monitors[plan[0].fp].ddcInput = "0x11"
        memory = Model.remember(memory, plan, "t2")
        compare(memory.monitors[plan[0].fp].ddcInput, "0x11")
        plan[0].ddcWatch = false
        memory = Model.remember(memory, plan, "t3")
        compare(memory.monitors[plan[0].fp].ddcInput, undefined)
    }
}
"""


class DisplayModelTest(unittest.TestCase):
    def test_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = Path(tmp) / "tst_display_model.qml"
            fixture.write_text(QML.replace("MODEL", MODEL.as_uri()))
            result = subprocess.run(
                ["/usr/lib/qt6/bin/qmltestrunner", "-input", str(fixture)],
                capture_output=True, text=True, timeout=120,
                env={"QT_QPA_PLATFORM": "offscreen", "PATH": "/usr/bin:/bin"},
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
