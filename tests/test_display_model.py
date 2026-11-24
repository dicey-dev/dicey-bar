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

    function test_a_reload_undoing_an_automatic_apply_gets_one_retry() {
        var first = Model.autoApplyStep(null, "plan", 0)
        var retry = Model.autoApplyStep(first.last, "plan", 2000)
        var third = Model.autoApplyStep(retry.last, "plan", 4000)
        verify(first.allowed && retry.allowed && !third.allowed)
        verify(Model.autoApplyStep(third.last, "plan", 21000).allowed)
        verify(Model.autoApplyStep(third.last, "other", 5000).allowed)
    }

    function test_fullscreen_vrr_survives_hyprland_reporting_it_inactive() {
        var plan = Model.derive([benq()], {}, {})
        plan[0].vrr = 2
        var memory = Model.remember({}, plan, "t")
        var live = Model.planFromLive([benq()], memory)
        compare(live[0].vrr, 2)
        verify(Model.planMatchesLive(Model.derive([benq()], memory, {}), live))
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
        // Explicit different input: 2 misses -> off
        compare(Model.ddcStep(0, "0x0f", "0x11"), { misses: 2, off: false })
        compare(Model.ddcStep(2, "0x0f", "0x11"), { misses: 4, off: true })
        compare(Model.ddcStep(4, "0x11", "0x11"), { misses: 0, off: false })

        // No reply (e.g. I2C bus contention): requires 4 consecutive misses -> off
        compare(Model.ddcStep(0, null, "0x11"), { misses: 1, off: false })
        compare(Model.ddcStep(1, null, "0x11"), { misses: 2, off: false })
        compare(Model.ddcStep(2, null, "0x11"), { misses: 3, off: false })
        compare(Model.ddcStep(3, null, "0x11"), { misses: 4, off: true })
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

    function test_per_monitor_brightness_and_per_setup_text_size_are_remembered() {
        var dockedPlan = Model.derive([laptop(), benq()], {}, {})
        byName(dockedPlan, "eDP-1").brightness = 55
        byName(dockedPlan, "HDMI-A-1").brightness = 82
        var memory = Model.remember({}, dockedPlan, "t", { textSize: 11 })

        var undockedPlan = Model.derive([laptop()], memory, {})
        memory = Model.remember(memory, undockedPlan, "t2", { textSize: 14 })

        var docked = Model.derive([laptop(), benq()], memory, {})
        compare(byName(docked, "eDP-1").brightness, 55)
        compare(byName(docked, "HDMI-A-1").brightness, 82)
        compare(Model.layoutTextSize(memory, docked), 11)

        var undocked = Model.derive([laptop()], memory, {})
        compare(byName(undocked, "eDP-1").brightness, 55)
        compare(Model.layoutTextSize(memory, undocked), 14)
    }

    function test_rotation_persists_and_rotates_canvas_geometry() {
        var plan = Model.derive([laptop(), benq()], {}, {})
        byName(plan, "HDMI-A-1").transform = 1
        var solved = Model.solve(plan)
        var b = byName(solved, "HDMI-A-1")
        compare(Model.logicalSize(b), { w: 1440, h: 2560 })
        var live = Model.planFromLive([laptop(), benq()], {})
        verify(!Model.planMatchesLive(solved, live))
        verify(Model.needsConfirmation(solved, live))

        var memory = Model.remember({}, solved, "2026-11-23T12:00:00Z")
        var restored = Model.derive([laptop(), benq()], memory, {})
        compare(byName(restored, "HDMI-A-1").transform, 1)
    }

    function test_layout_presets_and_mirror_invariants() {
        var base = Model.derive([laptop(), benq()], {}, {})
        compare(Model.activeLayoutPreset(base), "extend")

        var extOnly = Model.applyLayoutPreset(base, "external")
        compare(byName(extOnly, "eDP-1").enabled, false)
        compare(byName(extOnly, "HDMI-A-1").enabled, true)
        compare(Model.activeLayoutPreset(extOnly), "external")

        var lapOnly = Model.applyLayoutPreset(base, "internal")
        compare(byName(lapOnly, "eDP-1").enabled, true)
        compare(byName(lapOnly, "HDMI-A-1").enabled, false)
        compare(Model.activeLayoutPreset(lapOnly), "internal")

        var mirrored = Model.applyLayoutPreset(base, "mirror")
        compare(byName(mirrored, "eDP-1").mirrorOf, "")
        compare(byName(mirrored, "HDMI-A-1").mirrorOf, "eDP-1")
        compare(Model.activeLayoutPreset(mirrored), "mirror")

        // Remember mirror by target fingerprint and restore on reconnect
        var memory = Model.remember({}, mirrored, "2026-11-23T12:00:00Z")
        var restored = Model.derive([laptop(), benq()], memory, {})
        compare(byName(restored, "HDMI-A-1").mirrorOf, "eDP-1")
        compare(Model.activeLayoutPreset(restored), "mirror")

        // Sanitizer blocks self-mirror and mirror-of-mirror chains
        var broken = Model.derive([laptop(), benq(), dell()], {}, {})
        byName(broken, "eDP-1").mirrorOf = "eDP-1"
        byName(broken, "HDMI-A-1").mirrorOf = "eDP-1"
        byName(broken, "DP-3").mirrorOf = "HDMI-A-1"
        var clean = Model.solve(broken)
        compare(byName(clean, "eDP-1").mirrorOf, "")
        compare(byName(clean, "HDMI-A-1").mirrorOf, "eDP-1")
        compare(byName(clean, "DP-3").mirrorOf, "")
    }

    function test_hdr_sdr_tuning_remembered_and_diffed() {
        var hdrMonitor = benq()
        hdrMonitor.hdrCapable = true
        hdrMonitor.colorManagementPreset = "hdr"
        hdrMonitor.sdrBrightness = 1.0
        hdrMonitor.sdrSaturation = 1.0
        var live = Model.planFromLive([hdrMonitor], {})
        var plan = Model.derive([hdrMonitor], {}, {})
        plan[0].hdr = true
        plan[0].sdrBrightness = 1.35
        plan[0].sdrSaturation = 1.1
        verify(!Model.planMatchesLive(plan, live))
        var memory = Model.remember({}, plan, "2026-11-23T12:00:00Z")
        var restored = Model.derive([hdrMonitor], memory, {})
        compare(restored[0].hdr, true)
        compare(restored[0].sdrBrightness, 1.35)
        compare(restored[0].sdrSaturation, 1.1)
    }

    function test_per_setup_night_light_and_forget_monitor() {
        var docked = Model.derive([laptop(), benq()], {}, {})
        var memory = Model.remember({}, docked, "t1", {
            textSize: 12,
            nightLight: { enabled: true, kelvin: 3200 }
        })
        var undocked = Model.derive([laptop()], memory, {})
        memory = Model.remember(memory, undocked, "t2", {
            textSize: 14,
            nightLight: { enabled: false, kelvin: 4000 }
        })

        compare(Model.layoutNightLight(memory, docked), { enabled: true, kelvin: 3200 })
        compare(Model.layoutNightLight(memory, undocked), { enabled: false, kelvin: 4000 })

        var benqFp = byName(docked, "HDMI-A-1").fp
        var afterForget = Model.forgetMonitor(memory, benqFp)
        compare(afterForget.monitors[benqFp], undefined)
        compare(Model.layoutNightLight(afterForget, docked), null)
        // Unrelated laptop-only layout survives forgetting BenQ
        compare(Model.layoutNightLight(afterForget, undocked), { enabled: false, kelvin: 4000 })
    }

    function test_desc_prefix_collision_falls_back_to_connector_names() {
        var m1 = monitor("DP-1", "ACME", "Pro", "1", 1920, 1080, 60, 0, 0)
        var m2 = monitor("DP-2", "ACME", "Pro", "10", 1920, 1080, 60, 1920, 0)
        var ids = Model.identify([m1, m2])
        // "ACME Pro 1" is a prefix of "ACME Pro 10" — Hyprland desc: would match both!
        compare(ids[0].selector, "DP-1")
        compare(ids[1].selector, "DP-2")
    }

    function test_unprofiled_monitor_preserves_live_mode_and_flags_on_hotplug() {
        var tv = monitor("HDMI-A-2", "SONY", "TV", "TV1", 3840, 2160, 60, 1920, 0, {
            vrr: true,
            colorManagementPreset: "hdr",
            hdrCapable: true,
            availableModes: ["4096x2160@60.00Hz", "3840x2160@60.00Hz", "1920x1080@120.00Hz"]
        })
        var existingMemory = Model.remember({}, Model.derive([laptop()], {}, {}), "t1")
        var plan = Model.derive([laptop(), tv], existingMemory, {})
        var tvPlan = byName(plan, "HDMI-A-2")
        // Never force 4096x2160 or strip VRR/HDR on a display the user hasn't configured
        compare(tvPlan.mode, { w: 3840, h: 2160, hz: 60 })
        compare(tvPlan.vrr, 1)
        compare(tvPlan.hdr, true)
    }

    function test_lid_closed_never_auto_offs_last_external_monitor() {
        var plan = Model.derive([laptop(), benq()], {}, {})
        var fp = byName(plan, "HDMI-A-1").fp
        var off = Model.autoOffFor(plan, true, { [fp]: true })
        compare(off["LG Display|0x0753|"], "lid")
        compare(off[fp], undefined)
    }

    function test_remember_keeps_existing_night_light_when_not_edited() {
        var docked = Model.derive([laptop(), benq()], {}, {})
        var memory = Model.remember({}, docked, "t1", {
            textSize: 12,
            nightLight: { enabled: true, kelvin: 3200 }
        })
        // Re-applying without options.nightLight or options.textSize preserves prior values
        var memory2 = Model.remember(memory, docked, "t2", {})
        compare(Model.layoutTextSize(memory2, docked), 12)
        compare(Model.layoutNightLight(memory2, docked), { enabled: true, kelvin: 3200 })
    }

    function test_ensure_enabled_mode_recovers_zero_mode_disabled_monitor() {
        var off = benq()
        off.disabled = true
        off.width = 0
        off.height = 0
        off.refreshRate = 0
        var live = Model.planFromLive([laptop(), off], {})
        var recovered = Model.ensureEnabledMode(byName(live, "HDMI-A-1"), off.availableModes, null)
        compare(recovered, { w: 2560, h: 1440, hz: 100 })
    }

    function test_numeric_mirror_id_resolves_to_connector_name() {
        var m0 = laptop(); m0.id = 0
        var m1 = benq(); m1.id = 1; m1.mirrorOf = "0"
        var live = Model.planFromLive([m0, m1], {})
        compare(byName(live, "HDMI-A-1").mirrorOf, "eDP-1")
    }

    function test_unprofiled_monitor_leaves_vrr_unmanaged_and_memory_prefix_checked() {
        var m1 = monitor("DP-1", "ACME", "Pro", "1", 1920, 1080, 60, 0, 0)
        var mem = Model.remember({}, Model.derive([m1], {}, {}), "t1")
        // Plugging in "ACME Pro 10" later must detect prefix collision against remembered "ACME Pro 1"
        var m2 = monitor("DP-2", "ACME", "Pro", "10", 1920, 1080, 60, 1920, 0)
        var plan = Model.derive([m2], mem, {})
        compare(plan[0].selector, "DP-2")
        compare(plan[0].vrrManaged, false)
    }

    function test_attach_with_guides_snaps_closest_edge_and_hv_centers() {
        var placed = [{ x: 0, y: 0, w: 2560, h: 1440 }]
        // Horizontal center snap (below): centerX = 1280, rect x = 320
        var hCent = Model.attachWithGuides({ x: 340, y: 1440, w: 1920, h: 1080 }, placed, 60)
        compare([hCent.rect.x, hCent.rect.y], [320, 1440])
        compare(hCent.snap.axis, "x")
        compare(hCent.snap.kind, "h-center")
        compare(hCent.snap.guidePos, 1280)

        // Vertical center snap (to right): centerY = 720, rect y = 180
        var vCent = Model.attachWithGuides({ x: 2560, y: 195, w: 1920, h: 1080 }, placed, 60)
        compare([vCent.rect.x, vCent.rect.y], [2560, 180])
        compare(vCent.snap.axis, "y")
        compare(vCent.snap.kind, "v-center")
        compare(vCent.snap.guidePos, 720)

        // Bottom edge snap picks closest over center
        var bot = Model.attachWithGuides({ x: 2560, y: 345, w: 1920, h: 1080 }, placed, 60)
        compare([bot.rect.x, bot.rect.y], [2560, 360])
        compare(bot.snap.kind, "bottom")
        compare(bot.snap.guidePos, 1440)

        // Free move (alignWithin = 0) allows exact 1px fine positioning without jumping
        var free = Model.attachWithGuides({ x: 347, y: 1440, w: 1920, h: 1080 }, placed, 0)
        compare([free.rect.x, free.rect.y], [347, 1440])
        compare(free.snap, null)
    }

    function test_attach_with_guides_preserves_valid_placement_when_snap_obstructed() {
        var placed = [
            { x: 0, y: 0, w: 300, h: 240 },
            { x: 70, y: 240, w: 160, h: 400 },
            { x: 300, y: 60, w: 280, h: 210 }
        ]
        var res = Model.attachWithGuides({ x: 240, y: 270, w: 140, h: 380 }, placed, 30)
        compare([res.rect.x, res.rect.y], [240, 270])
        for (var i = 0; i < placed.length; i++) verify(!Model.overlaps(res.rect, placed[i]))
    }

    function test_pointer_anchored_drag_prevents_zoom_feedback_and_min_tile_jump() {
        // Two 1920x1080 monitors on a 600x184 canvas (usableH = 162, pad = 14)
        var spanX0 = 3840, spanY0 = 1080
        var k0 = Math.min((600 - 28) / (spanX0 * 1.24), (162 - 28) / (spanY0 * 1.20))
        var start = { x: 1920, y: 0, w: 1920, h: 1080 }
        var p0 = { x: 400, y: 80 }
        var anchor = Model.createDragAnchor(p0, start, k0)

        // Drag down to y ~= 500
        var p1 = { x: 400, y: 80 + 500 * k0 }
        var w1 = Model.dragWantedRect(anchor, p1)
        compare(w1.y, 500)

        // Bounds expand vertically to [0..1580], reducing k
        var k1 = Math.min((600 - 28) / (3840 * 1.24), (162 - 28) / (1580 * 1.20))
        verify(k1 < k0)
        anchor = Model.rebaseDragAnchor(anchor, p1, w1, k1)

        // Next 1px pointer movement must advance by ~1/k1 (~14px), never jump to ~996
        var p2 = { x: 400, y: p1.y + 1 }
        var w2 = Model.dragWantedRect(anchor, p2)
        verify(w2.y > 500 && w2.y < 530)

        // Minimum-sized tile (200%-scaled laptop 960x540 beside 4K 3840x2160):
        // grabbing anywhere preserves exact start position with zero jump on first Shift-drag
        var smallK = Math.min((600 - 28) / (4800 * 1.24), (162 - 28) / (2160 * 1.20))
        var laptopRect = { x: 0, y: 0, w: 960, h: 540 }
        var smallAnchor = Model.createDragAnchor({ x: 80, y: 65 }, laptopRect, smallK)
        compare(Model.dragWantedRect(smallAnchor, { x: 80, y: 65 }), laptopRect)
        var shifted = Model.dragWantedRect(smallAnchor, { x: 81, y: 65 })
        compare(shifted.y, 0)
        compare(shifted.x, Math.round(1 / smallK))
    }

    function test_ddc_quick_port_swap_legacy_migration_and_backoff_retry() {
        var fp = "BNQ|BenQ GW2790Q|ET97T00426087"
        var movedBenq = benq()
        movedBenq.name = "DP-1"
        var mem = { version: 1, monitors: {}, layouts: {} }
        mem.monitors[fp] = {
            mode: { w: 2560, h: 1440, hz: 100 }, scale: 1, vrr: 0, hdr: false,
            ddcWatch: true, ddcInput: "0x11", ddcConnector: "HDMI-A-1"
        }

        // Quick swap while ddcOff was set: pruneDdcState clears stale off/misses
        var identified = Model.planFromLive([laptop(), movedBenq], mem, {})
        var pruned = Model.pruneDdcState(identified, mem, { [fp]: true }, { [fp]: 4 }, { [fp]: { connector: "HDMI-A-1", inFlight: false, failures: 2, nextRetryAt: 99999 } })
        compare(pruned.ddcOff, {})
        compare(pruned.ddcMisses, {})
        compare(pruned.relearnState, {})
        verify(Model.ddcNeedsRelearn(mem.monitors[fp], "DP-1"))
        verify(!Model.ddcNeedsRelearn(mem.monitors[fp], "HDMI-A-1"))

        // With stale ddcOff cleared, derive keeps the moved monitor enabled
        var autoOff = Model.autoOffFor(identified, false, pruned.ddcOff)
        compare(autoOff, {})
        var derived = Model.derive([laptop(), movedBenq], mem, autoOff)
        compare(byName(derived, "DP-1").autoOff, false)
        compare(byName(derived, "DP-1").enabled, true)

        // Legacy profile (no ddcConnector) requires migration re-learn on any connector
        var legacy = { ddcWatch: true, ddcInput: "0x11" }
        verify(Model.ddcNeedsRelearn(legacy, "HDMI-A-1"))
        verify(Model.ddcNeedsRelearn(legacy, "DP-1"))

        // Transient failure backoff, retry, connector transition reset, and Re-probe clear
        var b1 = Model.ddcRelearnBegin({}, fp, "DP-1", 1000)
        verify(b1.allowed)
        verify(!Model.ddcRelearnBegin(b1.state, fp, "DP-1", 1001).allowed)
        var f1 = Model.ddcRelearnFinish(b1.state, fp, "DP-1", false, 1500)
        verify(!Model.ddcRelearnBegin(f1, fp, "DP-1", 6499).allowed)
        var b2 = Model.ddcRelearnBegin(f1, fp, "DP-1", 6500)
        verify(b2.allowed)
        var f2 = Model.ddcRelearnFinish(b2.state, fp, "DP-1", false, 7000)
        verify(!Model.ddcRelearnBegin(f2, fp, "DP-1", 16999).allowed)
        // Re-probe clears backoff immediately
        var cleared = Model.ddcRelearnClearBackoff(f2)
        verify(Model.ddcRelearnBegin(cleared, fp, "DP-1", 7100).allowed)
        // Connector change invalidates old backoff immediately
        verify(Model.ddcRelearnBegin(f2, fp, "DP-2", 7100).allowed)
        // Success removes tracking entry
        compare(Model.ddcRelearnFinish(b2.state, fp, "DP-1", true, 7000), {})
    }

    function test_sync_draft_brightness_reconciles_slider_scroll_and_low_steps() {
        var draft = Model.derive([laptop(), benq()], {}, {})
        byName(draft, "eDP-1").brightness = 40
        // Bar-icon scroll moves hardware 40% -> 45%; draft syncs before Apply
        var s1 = Model.syncDraftBrightness(draft, "eDP-1", 45)
        verify(s1.changed)
        compare(byName(s1.draft, "eDP-1").brightness, 45)
        var mem = Model.remember({}, s1.draft, "t1")
        compare(mem.monitors["LG Display|0x0753|"].brightness, 45)

        // Low-brightness step (Omarchy +5% at 2% steps to 3%) syncs exact actual value
        var s2 = Model.syncDraftBrightness(s1.draft, "eDP-1", 3)
        verify(s2.changed)
        compare(byName(s2.draft, "eDP-1").brightness, 3)
        var mem2 = Model.remember(mem, s2.draft, "t2")
        compare(mem2.monitors["LG Display|0x0753|"].brightness, 3)
    }

    function test_merge_actual_brightness_preserves_closed_panel_scroll_on_apply() {
        // Saved brightness is 40%; user scrolled to 45% while panel was closed,
        // then opened panel and applied an unrelated layout change.
        var plan = Model.derive([laptop(), benq()], {}, {})
        byName(plan, "eDP-1").brightness = 40
        var merged = Model.mergeActualBrightness(plan, { "eDP-1": 45 })
        compare(byName(merged, "eDP-1").brightness, 45)
        compare(byName(merged, "HDMI-A-1").brightness, null)
        var mem = Model.remember({}, merged, "t1")
        compare(mem.monitors["LG Display|0x0753|"].brightness, 45)
    }

    function test_brightness_queue_serializes_baseline_generations_and_steps() {
        // Panel opens on A; baseline reads are queued for A and B at gen 0.
        var q = []
        var gens = {}
        var rA = Model.brightnessQueueRead(q, gens, "eDP-1", true)
        var rB = Model.brightnessQueueRead(rA.queue, gens, "HDMI-A-1", true)
        compare(rB.queue.length, 2)
        compare(rB.queue[1].baseline, true)
        compare(rB.queue[1].gen, 0)

        // Scrolling unvisited B preserves B's queued baseline read BEFORE the step,
        // bumps B's generation to 1, and appends an afterStep readback at gen 1.
        var sB = Model.brightnessQueueStep(rB.queue, gens, {}, "HDMI-A-1", 5)
        compare(sB.gen, 1)
        compare(sB.queue.length, 4)
        compare(sB.queue[1].baseline, true)
        compare(sB.queue[1].gen, 0)
        compare(sB.queue[2].delta, 5)
        compare(sB.queue[2].gen, 1)
        compare(sB.queue[3].afterStep, true)
        compare(sB.queue[3].gen, 1)

        // Setting brightness via slider/Discard bumps generation to 2, drops obsolete
        // step/afterStep for B, while still preserving unrun baseline read.
        var wB = Model.brightnessQueueSet(sB.queue, sB.gens, "HDMI-A-1", 60)
        compare(wB.gen, 2)
        compare(wB.queue.length, 3)
        compare(wB.queue[1].baseline, true)
        compare(wB.queue[2].pct, 60)
        compare(wB.queue[2].gen, 2)
        // A stale read with gen 0 or 1 no longer matches current gen 2.
        verify(rB.queue[1].gen !== wB.gens["HDMI-A-1"])

        // Scrolling an unread monitor with no queued baseline automatically prepends baseline.
        var sFresh = Model.brightnessQueueStep([], {}, {}, "DP-1", 5)
        compare(sFresh.queue.length, 3)
        compare(sFresh.queue[0].baseline, true)
        compare(sFresh.queue[1].delta, 5)
        compare(sFresh.queue[2].afterStep, true)
    }

    function test_ddc_relearn_cancel_queued_only_drops_queued_entries() {
        var b1 = Model.ddcRelearnBegin({}, "fp1", "DP-1", 1000)
        var b2 = Model.ddcRelearnBegin(b1.state, "fp2", "DP-2", 1000)
        var after = Model.ddcRelearnCancelQueued(b2.state, ["fp2"])
        verify(!!after["fp1"])
        verify(!after["fp2"])
    }

    function test_brightness_queue_discard_supersedes_pending_and_inflight_steps() {
        // Panel open on eDP-1 (baseline 40%), user scrolls +5% then immediately clicks Discard.
        var step = Model.brightnessQueueStep([], {}, { "eDP-1": 40 }, "eDP-1", 5)
        // Simulate step[0] (delta +5, gen 1) already running in brightnessProc,
        // and step[1] (afterStep, gen 1) still in queue.
        var active = step.queue[0]
        var remaining = step.queue.slice(1)
        var disc = Model.brightnessQueueDiscard(remaining, step.gens, active, { "eDP-1": 40 })
        // Generation bumped past active/afterStep so late callbacks are ignored
        verify(disc.gens["eDP-1"] > active.gen)
        // Queued afterStep dropped, restore write to 40% queued at new gen
        compare(disc.queue.length, 1)
        compare(disc.queue[0].name, "eDP-1")
        compare(disc.queue[0].pct, 40)
        compare(disc.queue[0].gen, disc.gens["eDP-1"])
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
