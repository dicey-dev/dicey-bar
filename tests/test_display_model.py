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

    // ------------------------------------------------ DDC/CI auto-pause
    function ok(code) { return { status: "ok", code: code } }
    function amb() { return { status: "ambiguous" } }
    function busy() { return { status: "busy" } }

    // Feeds readings 5s apart from t0; returns { state, learns, t }.
    function feed(st, readings, t0, savedL, canPause, step) {
        var s = st
        var t = t0
        var learns = []
        for (var i = 0; i < readings.length; i++) {
            var r = Model.ddcTransition(s, readings[i], { fp: "fp", now: t, savedL: savedL, canPause: canPause !== false })
            s = r.state
            if (r.learn) learns.push(r.learn)
            t += step || 5000
        }
        return { state: s, learns: learns, t: t }
    }

    function watching(L, connector) {
        var r = feed(Model.ddcFresh(connector || "HDMI-A-1", 0, false), [ok(L), ok(L)], 10000, L)
        compare(r.state.state, "WATCHING")
        return r
    }

    function test_ddc_codes_reject_zero_and_label_inputs() {
        compare(Model.ddcCode("0x11"), "0x11")
        compare(Model.ddcCode("0xF"), "0x0f")
        compare(Model.ddcCode("0x00"), "")
        compare(Model.ddcCode("0x0"), "")
        compare(Model.ddcCode("17"), "")
        compare(Model.ddcCode("0x100"), "")
        compare(Model.ddcInputLabel("0x12"), "HDMI-2 (0x12)")
        compare(Model.ddcInputLabel("0x51"), "Input 0x51")
        compare(Model.ddcInputLabel(""), "")
    }

    function test_ddc_probe_waits_out_the_grace_then_learns_a_new_monitor() {
        var st = Model.ddcFresh("HDMI-A-1", 0, false)
        var early = Model.ddcTransition(st, ok("0x11"), { fp: "fp", now: 9999, savedL: "", canPause: true })
        compare(early.state.state, "UNPROBED")
        compare(early.learn, null)
        var r = feed(st, [ok("0x11"), ok("0x11")], 10000, "")
        compare(r.state.state, "WATCHING")
        compare(r.state.expected, "0x11")
        compare(r.learns, [{ fp: "fp", connector: "HDMI-A-1", code: "0x11" }])
        // A known monitor on its learned input: watched without rewriting it.
        var known = feed(Model.ddcFresh("HDMI-A-1", 0, false), [ok("0x11"), ok("0x11")], 10000, "0x11")
        compare(known.state.state, "WATCHING")
        compare(known.learns.length, 0)
        // A single read never confirms anything; differing reads restart the pair.
        var flaky = feed(Model.ddcFresh("HDMI-A-1", 0, false), [ok("0x11"), ok("0x12"), ok("0x12")], 10000, "")
        compare(flaky.state.state, "WATCHING")
        compare(flaky.state.expected, "0x12")
    }

    function test_ddc_hdmi_and_vendor_codes_on_dp_are_learned() {
        var cable = feed(Model.ddcFresh("DP-2", 0, false), [ok("0x11"), ok("0x11")], 10000, "")
        compare(cable.learns[0], { fp: "fp", connector: "DP-2", code: "0x11" })
        var vendor = feed(Model.ddcFresh("DP-1", 0, false), [ok("0x51"), ok("0x51")], 10000, "")
        compare(vendor.learns[0].code, "0x51")
        var hp = feed(Model.ddcFresh("DP-1", 0, false), [ok("0x90"), ok("0x90")], 10000, "")
        compare(hp.state.expected, "0x90")
    }

    function test_ddc_startup_on_another_input_waits_and_never_relearns() {
        var r = feed(Model.ddcFresh("HDMI-A-1", 0, false), [ok("0x12"), ok("0x12")], 10000, "0x11")
        compare(r.state.state, "UNVERIFIED")
        compare(r.state.expected, "0x11")
        compare(r.learns.length, 0)
        compare(Model.ddcOffMap({ fp: r.state }), {})
        compare(Model.ddcPollInterval({ fp: r.state }, 0), 15000)
        var waiting = feed(r.state, [ok("0x12"), ok("0x12"), ok("0x12"), ok("0x12"), amb()], r.t, "0x11")
        compare(waiting.state.state, "UNVERIFIED")
        var back = feed(waiting.state, [ok("0x11")], waiting.t, "0x11")
        compare(back.state.state, "WATCHING")
        verify(back.state.seenL)
    }

    function test_ddc_hotplug_relearns_even_with_a_saved_input() {
        var plugged = feed(Model.ddcFresh("HDMI-A-1", 0, true), [ok("0x12"), ok("0x12")], 10000, "0x11")
        compare(plugged.state.state, "WATCHING")
        compare(plugged.state.expected, "0x12")
        compare(plugged.learns, [{ fp: "fp", connector: "HDMI-A-1", code: "0x12" }])
        verify(!plugged.state.hotplugged)
    }

    function test_ddc_tv_without_ddc_is_unsupported_and_left_alone() {
        var r = feed(Model.ddcFresh("HDMI-A-2", 0, false), [amb(), busy(), amb(), busy(), amb()], 10000, "")
        compare(r.state.state, "UNSUPPORTED")
        compare(r.learns.length, 0)
        compare(Model.ddcPollInterval({ tv: r.state }, 0), 0)
        compare(Model.ddcDue({ tv: r.state }, { tv: true }, 999999), [])
        // A monitor that answered once is intermittent, not unsupported: it
        // gets 5 reads in all to give a matching pair, then stops probing.
        var unstable = feed(Model.ddcFresh("DP-1", 0, false), [ok("0x11"), amb(), ok("0x11")], 10000, "")
        compare(unstable.state.state, "PROBING")
        var gaveUp = feed(unstable.state, [amb(), ok("0x12")], unstable.t, "")
        compare(gaveUp.state.state, "UNSUPPORTED")
        compare(gaveUp.learns.length, 0)
        compare(Model.ddcStatusText(gaveUp.state, true), "Not supported by this display")
    }

    function test_ddc_intermittent_probe_still_learns_but_silent_tv_stops_fast() {
        var flaky = feed(Model.ddcFresh("DP-1", 0, false), [ok("0x11"), amb(), ok("0x11"), ok("0x11")], 10000, "")
        compare(flaky.state.state, "WATCHING")
        compare(flaky.state.expected, "0x11")
        compare(flaky.learns, [{ fp: "fp", connector: "DP-1", code: "0x11" }])
        verify(!flaky.state.probeSawValid)
        compare(flaky.state.probeAmbiguous, 0)
        // Busy reads never count; three unreadable answers (15s) still give up.
        var tv = feed(Model.ddcFresh("HDMI-A-2", 0, false), [amb(), amb(), busy(), amb()], 10000, "")
        compare(tv.state.state, "UNSUPPORTED")
        var twoAmb = feed(Model.ddcFresh("HDMI-A-2", 0, false), [amb(), amb()], 10000, "")
        compare(twoAmb.state.state, "PROBING")
        compare(twoAmb.state.probeAmbiguous, 2)
        // A hold (DPMS) during probing restarts the count.
        var held = Model.ddcHold({ p: twoAmb.state }, {}, 30000).states.p
        compare(held.state, "UNPROBED")
        compare(held.probeAmbiguous, 0)
        verify(!held.probeSawValid)
    }

    function test_ddc_detected_bus_is_kept_per_connection_and_detect_is_scoped() {
        var fresh = Model.ddcFresh("DP-5", 0, false)
        compare(fresh.detectedBus, "")
        compare(Model.ddcReadArgs([{ fp: "a", name: "DP-5" }], { a: fresh }), ["--detect", "DP-5", "DP-5"])
        var r = Model.ddcTransition(fresh, { status: "ok", code: "0x11", bus: "13" }, { fp: "a", now: 10000, savedL: "" })
        compare(r.state.detectedBus, "13")
        // A bad bus is ignored; a reading without one keeps the known bus.
        compare(Model.ddcTransition(r.state, { status: "ok", code: "0x11", bus: "1;3" }, { fp: "a", now: 15000 }).state.detectedBus, "13")
        var w = Model.ddcTransition(r.state, ok("0x11"), { fp: "a", now: 15000, savedL: "" }).state
        compare(w.state, "WATCHING")
        compare(w.detectedBus, "13")
        var sysfs = watching("0x0f", "DP-2").state
        var states = { a: w, b: sysfs, c: Model.ddcFresh("HDMI-A-1", 0, false) }
        compare(Model.ddcReadArgs([{ fp: "a", name: "DP-5" }, { fp: "b", name: "DP-2" }, { fp: "c", name: "HDMI-A-1" }], states),
                ["--detect", "HDMI-A-1", "DP-5:13@ddc", "DP-2@ddc", "HDMI-A-1"])
        // A watching DDC monitor never asks for detect or probes SCDC.
        compare(Model.ddcReadArgs([{ fp: "b", name: "DP-2" }], states), ["DP-2@ddc"])
        // Reconnect, port move and resume all forget the bus.
        var ids = Model.planFromLive([laptop(), benq()], {}, {})
        var benqFp = byName(ids, "HDMI-A-1").fp
        var kept = {}
        kept[benqFp] = Model.ddcTransition(Model.ddcFresh("HDMI-A-1", 0, false), { status: "busy", bus: "9" }, { now: 1 }).state
        compare(kept[benqFp].detectedBus, "9")
        compare(Model.ddcPrune(kept, ids, {}, 2)[benqFp].detectedBus, "9")
        compare(Model.ddcPrune(kept, ids, { "HDMI-A-1": true }, 2)[benqFp].detectedBus, "")
        compare(Model.ddcResume(kept, 5)[benqFp].detectedBus, "")
    }

    function test_ddc_failed_reads_count_as_ambiguous() {
        var targets = [{ fp: "p", name: "HDMI-A-1" }, { fp: "w", name: "DP-2" }]
        compare(Model.ddcFailedReadings(targets), { "HDMI-A-1": { status: "ambiguous" }, "DP-2": { status: "ambiguous" } })
        compare(Model.ddcFailedReadings(null), {})
        var w = watching("0x11").state
        var p = feed(w, [ok("0x12"), ok("0x12"), ok("0x12")], 40000, "0x11").state
        compare(p.state, "PAUSED")
        // Two failed helper runs wake a paused monitor (fail open)...
        var failed = Model.ddcFailedReadings(targets)
        var once = Model.ddcObserve({ p: p }, "p", failed["HDMI-A-1"], { now: 100000, savedL: "0x11" })
        compare(once.states.p.state, "PAUSED")
        var twice = Model.ddcObserve(once.states, "p", failed["HDMI-A-1"], { now: 103000, savedL: "0x11" })
        compare(twice.states.p.state, "WATCHING")
        verify(twice.offChanged)
        // ...and one breaks a half-built pause streak.
        var two = feed(w, [ok("0x12"), ok("0x12")], 40000, "0x11").state
        var broken = Model.ddcTransition(two, failed["DP-2"], { fp: "fp", now: 50000, savedL: "0x11", canPause: true }).state
        compare(broken.otherStreak, 0)
        compare(feed(broken, [ok("0x12")], 55000, "0x11").state.state, "WATCHING")
    }

    function test_ddc_connection_changes_invalidate_inflight_reads() {
        var a = Model.ddcFresh("HDMI-A-1", 0, false)
        var states = { x: a }
        compare(Model.ddcConnectionsChanged(states, { x: a }, {}), false)
        verify(Model.ddcConnectionsChanged(states, {}, {}))
        verify(Model.ddcConnectionsChanged({}, states, {}))
        verify(Model.ddcConnectionsChanged(states, { x: Model.ddcFresh("DP-1", 0, true) }, {}))
        // Same-port replug: a fresh state on the plugged connector.
        verify(Model.ddcConnectionsChanged(states, { x: Model.ddcFresh("HDMI-A-1", 5, true) }, { "HDMI-A-1": true }))
        compare(Model.ddcConnectionsChanged(states, { x: a }, { "HDMI-A-1": true }), false)
        compare(Model.ddcConnectionsChanged(states, { x: a }, { "eDP-1": true }), false)
    }

    function test_ddc_pauses_only_after_three_identical_other_reads() {
        var w = watching("0x11")
        var two = feed(w.state, [ok("0x12"), ok("0x12")], w.t, "0x11")
        compare(two.state.state, "WATCHING")
        // Seeing this PC's input resets the evidence.
        var reset = feed(two.state, [ok("0x11"), ok("0x12"), ok("0x12")], two.t, "0x11")
        compare(reset.state.state, "WATCHING")
        // An unreadable answer breaks the streak and never pauses.
        var broken = feed(w.state, [ok("0x12"), ok("0x12"), amb(), ok("0x12"), ok("0x12")], w.t, "0x11")
        compare(broken.state.state, "WATCHING")
        var allAmb = feed(w.state, [amb(), amb(), amb(), amb(), amb(), amb()], w.t, "0x11")
        compare(allAmb.state.state, "WATCHING")
        // A different other input restarts the count.
        var mixed = feed(w.state, [ok("0x12"), ok("0x0f"), ok("0x12")], w.t, "0x11")
        compare(mixed.state.state, "WATCHING")
        // Busy (brightness holds the bus) is no information at all.
        var paused = feed(w.state, [ok("0x12"), busy(), ok("0x12"), busy(), ok("0x12")], w.t, "0x11")
        compare(paused.state.state, "PAUSED")
        compare(paused.state.pausedCode, "0x12")
        compare(Model.ddcOffMap({ fp: paused.state }), { fp: true })
        compare(Model.ddcPollInterval({ fp: paused.state }, 0), 3000)
        compare(Model.ddcStatusText({ state: "PAUSED", pausedCode: "0x12" }, true), "Paused · switched to HDMI-2 (0x12)")
    }

    function test_ddc_never_pauses_when_unsafe_or_inside_the_grace() {
        var w = watching("0x11")
        var unsafe = feed(w.state, [ok("0x12"), ok("0x12"), ok("0x12"), ok("0x12")], w.t, "0x11", false)
        compare(unsafe.state.state, "WATCHING")
        // Becoming safe later pauses on the next matching read.
        var later = feed(unsafe.state, [ok("0x12")], unsafe.t, "0x11", true)
        compare(later.state.state, "PAUSED")
        // Just woken (grace restarts): evidence builds but no pause for 10s.
        var woke = Model.ddcWake({ fp: later.state }, "fp", later.t, false).fp
        compare(woke.state, "WATCHING")
        var inGrace = feed(woke, [ok("0x12"), ok("0x12"), ok("0x12")], later.t + 1000, "0x11", true, 3000)
        compare(inGrace.state.state, "WATCHING")
        var afterGrace = feed(inGrace.state, [ok("0x12")], later.t + 10000, "0x11", true)
        compare(afterGrace.state.state, "PAUSED")
    }

    function test_ddc_paused_wakes_on_its_input_or_fails_open() {
        var w = watching("0x11")
        var p = feed(w.state, [ok("0x12"), ok("0x12"), ok("0x12")], w.t, "0x11").state
        compare(p.state, "PAUSED")
        var back = feed(p, [ok("0x11")], 100000, "0x11", true, 3000)
        compare(back.state.state, "WATCHING")
        compare(back.state.readyAt, 100000)
        compare(Model.ddcOffMap({ fp: back.state }), {})
        // Another input keeps it paused (and tracks which one).
        var moved = feed(p, [ok("0x0f"), amb(), ok("0x12"), amb(), busy(), ok("0x12")], 100000, "0x11", true, 3000)
        compare(moved.state.state, "PAUSED")
        compare(moved.state.pausedCode, "0x12")
        // Two unreadable answers in a row (busy in between is neutral) wake it.
        var failOpen = feed(p, [amb(), busy(), amb()], 100000, "0x11", true, 3000)
        compare(failOpen.state.state, "WATCHING")
        // Losing ddcutil wakes it too.
        var gone = feed(p, [{ status: "no_tool" }], 100000, "0x11")
        compare(gone.state.state, "NO_TOOL")
        compare(Model.ddcOffMap({ fp: gone.state }), {})
        compare(Model.ddcPollInterval({ fp: gone.state }, 0), 0)
    }

    function test_ddc_acer_no_signal_loop_wakes_and_then_suspends() {
        // The Acer reports x00 (ambiguous) once it sleeps with no signal on the
        // other input: it is woken every time, and the 4th pause in 10 minutes
        // suspends watching instead of pausing.
        var s = watching("0x0f", "DP-1")
        var st = s.state
        var t = s.t
        for (var round = 0; round < 3; round++) {
            var paused = feed(st, [ok("0x11"), ok("0x11"), ok("0x11")], t, "0x0f")
            compare(paused.state.state, "PAUSED")
            var woke = feed(paused.state, [amb(), amb()], paused.t, "0x0f", true, 3000)
            compare(woke.state.state, "WATCHING")
            st = woke.state
            t = woke.t + 10000
        }
        compare(st.recentPauses.length, 3)
        var fourth = feed(st, [ok("0x11"), ok("0x11"), ok("0x11")], t, "0x0f")
        compare(fourth.state.state, "SUSPENDED")
        compare(Model.ddcOffMap({ fp: fourth.state }), {})
        compare(Model.ddcPollInterval({ fp: fourth.state }, 0), 0)
        // Pauses older than 10 minutes no longer count.
        var calm = feed(st, [ok("0x11"), ok("0x11"), ok("0x11")], t + 600000, "0x0f")
        compare(calm.state.state, "PAUSED")
        compare(calm.state.recentPauses.length, 1)
    }

    function test_ddc_manual_wake_snoozes_until_the_input_returns() {
        var w = watching("0x11")
        var p = feed(w.state, [ok("0x12"), ok("0x12"), ok("0x12")], w.t, "0x11").state
        var states = Model.ddcWake({ fp: p, other: w.state }, "fp", 50000, true)
        compare(states.fp.state, "SNOOZED")
        compare(states.fp.expected, "0x11")
        compare(states.other, w.state)
        compare(Model.ddcOffMap(states), {})
        compare(Model.ddcPollInterval({ fp: states.fp }, 0), 15000)
        var snoozed = feed(states.fp, [ok("0x12"), ok("0x12"), ok("0x12"), ok("0x12"), ok("0x12")], 60000, "0x11")
        compare(snoozed.state.state, "SNOOZED")
        var back = feed(snoozed.state, [ok("0x11")], snoozed.t, "0x11")
        compare(back.state.state, "WATCHING")
        compare(back.learns.length, 0)
        // Waking anything but a paused monitor changes nothing.
        compare(Model.ddcWake({ fp: w.state }, "fp", 1, true).fp, w.state)
        compare(Model.ddcStatusText(states.fp, true), "Snoozed until input returns")
    }

    function test_ddc_can_pause_guards_the_last_lit_display() {
        var plan = Model.derive([laptop(), benq(), dell()], {}, {})
        var benqFp = byName(plan, "HDMI-A-1").fp
        var dellFp = byName(plan, "DP-3").fp
        var laptopFp = byName(plan, "eDP-1").fp
        verify(Model.ddcCanPause(plan, benqFp, false, {}))
        verify(!Model.ddcCanPause(plan, laptopFp, false, {}))
        verify(!Model.ddcCanPause(plan, "missing", false, {}))
        // Lid shut: the panel does not count, but the other external does...
        verify(Model.ddcCanPause(plan, benqFp, true, {}))
        // ...unless it is paused already.
        verify(!Model.ddcCanPause(plan, benqFp, true, { [dellFp]: true }))
        // User-disabled monitors are never paused, and do not count as lit.
        var userOff = Model.derive([laptop(), benq(), dell()], {}, {})
        byName(userOff, "DP-3").enabled = false
        verify(!Model.ddcCanPause(userOff, dellFp, false, {}))
        verify(!Model.ddcCanPause(userOff, benqFp, true, {}))
        // Mirrors (either side) are never paused.
        var mirrored = Model.derive([laptop(), benq()], {}, {})
        byName(mirrored, "HDMI-A-1").mirrorOf = "eDP-1"
        verify(!Model.ddcCanPause(mirrored, benqFp, false, {}))
        var source = Model.derive([laptop(), benq()], {}, {})
        byName(source, "eDP-1").mirrorOf = "HDMI-A-1"
        verify(!Model.ddcCanPause(source, benqFp, false, {}))
        // A single external behind a closed lid stays on.
        verify(!Model.ddcCanPause(Model.derive([laptop(), benq()], {}, {}), benqFp, true, {}))
    }

    function test_ddc_can_pause_counts_only_displays_lit_right_now() {
        var mons = [laptop(), benq(), dell()]
        var plan = Model.derive(mons, {}, {})
        var benqFp = byName(plan, "HDMI-A-1").fp
        verify(Model.ddcCanPause(plan, benqFp, true, {}, mons))
        // Lid shut and the other external DPMS-asleep: nothing else is lit.
        var asleep = [laptop(), benq(), dell()]
        asleep[2].dpmsStatus = false
        verify(!Model.ddcCanPause(plan, benqFp, true, {}, asleep))
        // With the lid open the panel still counts.
        verify(Model.ddcCanPause(plan, benqFp, false, {}, asleep))
        // Disabled live, or not connected at all, does not count either.
        var off = [laptop(), benq(), dell()]
        off[2].disabled = true
        verify(!Model.ddcCanPause(plan, benqFp, true, {}, off))
        verify(!Model.ddcCanPause(plan, benqFp, true, {}, [laptop(), benq()]))
        var panelAsleep = [laptop(), benq(), dell()]
        panelAsleep[0].dpmsStatus = false
        panelAsleep[2].disabled = true
        verify(!Model.ddcCanPause(plan, benqFp, false, {}, panelAsleep))
        // Without live monitors only the plan is checked (as before).
        verify(Model.ddcCanPause(plan, benqFp, true, {}, undefined))
    }

    function test_plan_wakes_detects_a_dark_monitor_to_turn_on() {
        var mons = [laptop(), benq()]
        var memory = Model.remember({}, Model.derive(mons, {}, {}), "t")
        var benqFp = byName(Model.planFromLive(mons, memory, {}), "HDMI-A-1").fp
        var dark = [laptop(), benq()]
        dark[1].disabled = true
        var livePaused = Model.planFromLive(dark, memory, { [benqFp]: "input" })
        var wake = Model.derive(mons, memory, {})
        verify(Model.planWakes(wake, livePaused))
        verify(!Model.planWakes(Model.derive(mons, memory, { [benqFp]: "input" }), livePaused))
        verify(!Model.planWakes(wake, Model.planFromLive(mons, memory, {})))
    }

    function test_persistable_plan_keeps_edits_to_monitors_that_are_not_paused() {
        var mons = [laptop(), benq(), dell()]
        var memory = Model.remember({}, Model.derive(mons, {}, {}), "t")
        var ids = Model.planFromLive(mons, memory, {})
        var benqFp = byName(ids, "HDMI-A-1").fp
        var unpaused = Model.derive(mons, memory, {})
        var paused = Model.derive(mons, memory, { [benqFp]: "input" })
        // The user edits the Dell while the BenQ is paused.
        var edited = paused.map(function(o) { var c = {}; for (var k in o) c[k] = o[k]; return c })
        var d = byName(edited, "DP-3")
        d.scale = 1.25
        d.x = 5000
        d.y = 37
        d.mode = { w: 1920, h: 1080, hz: 60 }
        var file = Model.persistablePlan(edited, unpaused)
        compare(file.length, 3)
        verify(!file.some(function(o) { return o.offReason === "input" || o.autoOff }))
        var fd = byName(file, "DP-3")
        compare([fd.scale, fd.x, fd.y], [1.25, 5000, 37])
        compare(fd, d)
        compare(byName(file, "eDP-1"), byName(edited, "eDP-1"))
        // The paused monitor goes back to its unpaused entry, on and unpaused;
        // here its old spot is taken by the laptop, so it moves right of the others.
        var ub = byName(unpaused, "HDMI-A-1")
        var fb = byName(file, "HDMI-A-1")
        compare([fb.enabled, fb.autoOff, fb.offReason], [true, false, ""])
        compare([fb.mode, fb.scale], [ub.mode, ub.scale])
        compare(fb.x, 5000 + 1920 / 1.25)
        verify(!Model.overlaps(Model.rectOf(fb), Model.rectOf(byName(file, "eDP-1"))))
        verify(!Model.overlaps(Model.rectOf(fb), Model.rectOf(fd)))
        // With its spot free it keeps its unpaused position exactly.
        var roomy = edited.map(function(o) { var c = {}; for (var k in o) c[k] = o[k]; return c })
        byName(roomy, "eDP-1").x = 10000
        byName(roomy, "DP-3").x = 12000
        var kept = byName(Model.persistablePlan(roomy, unpaused), "HDMI-A-1")
        compare([kept.x, kept.y], [ub.x, ub.y])
        // A plan with nothing paused is passed through untouched.
        compare(Model.persistablePlan(unpaused, unpaused), unpaused)
    }

    function test_edited_draft_follows_runtime_pauses_and_wakes() {
        var mons = [laptop(), benq()]
        var memory = Model.remember({}, Model.derive(mons, {}, {}), "t")
        var benqFp = byName(Model.planFromLive(mons, memory, {}), "HDMI-A-1").fp
        var dark = [laptop(), benq()]
        dark[1].disabled = true
        var draft = Model.planFromLive(dark, memory, { [benqFp]: "input" })
        byName(draft, "eDP-1").scale = 1.5
        // Pre-apply refresh right after DDC wake: autoOff cleared, but Hyprland
        // still reports the monitor disabled. Draft waits instead of copying enabled:false.
        var preApply = Model.planFromLive(dark, memory, {})
        compare(byName(preApply, "HDMI-A-1").enabled, false)
        compare(Model.syncDraftAutoOff(draft, preApply).changed, false)
        // Explicit resolution edit on the waking monitor is preserved when it wakes,
        // and narrowing the other monitor leaves no gap after solve().
        var editedMode = draft.map(function(o) {
            var c = {}
            for (var k in o) c[k] = o[k]
            if (o.mode) c.mode = { w: o.mode.w, h: o.mode.h, hz: o.mode.hz }
            return c
        })
        byName(editedMode, "HDMI-A-1").mode = { w: 1600, h: 900, hz: 60 }
        byName(editedMode, "eDP-1").mode = { w: 1280, h: 720, hz: 60 }
        byName(editedMode, "eDP-1").scale = 1
        var woke = Model.planFromLive(mons, memory, {})
        var syncedEdited = Model.syncDraftAutoOff(editedMode, woke)
        verify(syncedEdited.changed)
        compare(byName(syncedEdited.draft, "HDMI-A-1").mode, { w: 1600, h: 900, hz: 60 })
        var solved = Model.solve(syncedEdited.draft)
        verify(Model.sharedEdge(Model.rectOf(byName(solved, "eDP-1")), Model.rectOf(byName(solved, "HDMI-A-1"))) > 0)
        // Side-by-side: narrowing the active output from 1920 to 1280 leaves no gap on wake.
        var sideA = monitor("eDP-1", "LG Display", "0x0753", "", 1920, 1080, 60, 0, 0, { physicalWidth: 310 })
        var sideB = monitor("HDMI-A-1", "BNQ", "BenQ GW2790Q", "ET97T00426087", 1920, 1080, 60, 1920, 0)
        var sideMem = Model.remember({}, Model.derive([sideA, sideB], {}, {}), "t")
        var sideFp = byName(Model.planFromLive([sideA, sideB], sideMem, {}), "HDMI-A-1").fp
        var sideDraft = Model.planFromLive([sideA, Object.assign({}, sideB, { disabled: true })], sideMem, { [sideFp]: "input" })
        byName(sideDraft, "eDP-1").mode = { w: 1280, h: 720, hz: 60 }
        var sideSynced = Model.syncDraftAutoOff(sideDraft, Model.planFromLive([sideA, sideB], sideMem, {}))
        compare(byName(sideSynced.draft, "HDMI-A-1").x, 1280)
        compare(byName(Model.solve(sideSynced.draft), "HDMI-A-1").x, 1280)
        // Three-display layout: waking a paused output attaches against the edited layout
        // without moving the other displays (preserving the 320px vertical offset).
        var mA = monitor("eDP-1", "LG Display", "0x0753", "", 1920, 1080, 60, 0, 0, { physicalWidth: 310 })
        var mB = monitor("DP-3", "DEL", "U2723QE", "ABC123", 1920, 1080, 60, 1920, 320)
        var mC = monitor("HDMI-A-1", "BNQ", "BenQ GW2790Q", "ET97T00426087", 1920, 1080, 60, 1920, 0)
        var mem3 = Model.remember({}, Model.derive([mA, mB, mC], {}, {}), "t")
        var cFp = byName(Model.planFromLive([mA, mB, mC], mem3, {}), "HDMI-A-1").fp
        var draft3 = Model.planFromLive([mA, mB, Object.assign({}, mC, { disabled: true })], mem3, { [cFp]: "input" })
        byName(draft3, "eDP-1").x = 0
        byName(draft3, "eDP-1").y = 0
        byName(draft3, "DP-3").x = 1920
        byName(draft3, "DP-3").y = 320
        var synced3 = Model.syncDraftAutoOff(draft3, Model.planFromLive([mA, mB, mC], mem3, {}))
        verify(synced3.changed)
        var sA = byName(synced3.draft, "eDP-1")
        var sB = byName(synced3.draft, "DP-3")
        var sC = byName(synced3.draft, "HDMI-A-1")
        compare(sB.y - sA.y, 320)
        verify(!Model.overlaps(Model.rectOf(sC), Model.rectOf(sA)) && !Model.overlaps(Model.rectOf(sC), Model.rectOf(sB)))
        verify(Model.sharedEdge(Model.rectOf(sC), Model.rectOf(sA)) > 0 || Model.sharedEdge(Model.rectOf(sC), Model.rectOf(sB)) > 0)
        // Solving the already-valid 3-display draft preserves the 320px offset.
        var solved3 = Model.solve(synced3.draft)
        compare(byName(solved3, "DP-3").y - byName(solved3, "eDP-1").y, 320)
        // Edited A-C-B row: C pauses then wakes at its old live position to B's right.
        // Restoring C at its draft position yields a valid A-C-B layout and keeps B in place.
        var rA = monitor("eDP-1", "LG Display", "0x0753", "", 1920, 1080, 60, 0, 0, { physicalWidth: 310 })
        var rB = monitor("DP-3", "DEL", "U2723QE", "ABC123", 1920, 1080, 60, 1920, 0)
        var rC = monitor("HDMI-A-1", "BNQ", "BenQ GW2790Q", "ET97T00426087", 1920, 1080, 60, 3840, 0)
        var memRow = Model.remember({}, Model.derive([rA, rB, rC], {}, {}), "t")
        var rcFp = byName(Model.planFromLive([rA, rB, rC], memRow, {}), "HDMI-A-1").fp
        var rowDraft = Model.planFromLive([rA, rB, rC], memRow, {})
        byName(rowDraft, "eDP-1").x = 0
        byName(rowDraft, "HDMI-A-1").x = 1920
        byName(rowDraft, "DP-3").x = 3840
        var pausedRow = Model.syncDraftAutoOff(rowDraft, Model.planFromLive([rA, rB, Object.assign({}, rC, { disabled: true })], memRow, { [rcFp]: "input" }))
        verify(pausedRow.changed)
        var wokeRow = Model.syncDraftAutoOff(pausedRow.draft, Model.planFromLive([rA, rB, rC], memRow, {}))
        verify(wokeRow.changed)
        compare([byName(wokeRow.draft, "eDP-1").x, byName(wokeRow.draft, "HDMI-A-1").x, byName(wokeRow.draft, "DP-3").x], [0, 1920, 3840])
        var solvedRow = Model.solve(wokeRow.draft)
        compare([byName(solvedRow, "eDP-1").x, byName(solvedRow, "HDMI-A-1").x, byName(solvedRow, "DP-3").x], [0, 1920, 3840])
        // Post-apply refresh confirms enabled: now draft clears pause and updates geometry.
        var woke = Model.planFromLive(mons, memory, {})
        var synced = Model.syncDraftAutoOff(draft, woke)
        verify(synced.changed)
        var b = byName(synced.draft, "HDMI-A-1")
        compare([b.autoOff, b.offReason, b.enabled], [false, "", true])
        compare([b.x, b.y], [byName(woke, "HDMI-A-1").x, byName(woke, "HDMI-A-1").y])
        // The user's edit to the other monitor stays.
        compare(byName(synced.draft, "eDP-1").scale, 1.5)
        // A fresh pause shows up in the edited draft too.
        var again = Model.syncDraftAutoOff(synced.draft, draft)
        verify(again.changed)
        compare(byName(again.draft, "HDMI-A-1").offReason, "input")
        compare(Model.syncDraftAutoOff(again.draft, draft).changed, false)
    }

    function test_lid_closed_user_disabled_and_paused_externals_never_leave_nothing_lit() {
        var plan = Model.planFromLive([laptop(), benq(), dell()], {})
        byName(plan, "DP-3").enabled = false
        var benqFp = byName(plan, "HDMI-A-1").fp
        var off = Model.autoOffFor(plan, true, { [benqFp]: true })
        compare(off["LG Display|0x0753|"], "lid")
        compare(off[benqFp], undefined)
        // With the lid open the panel stays lit, so the pause may go ahead.
        compare(Model.autoOffFor(plan, false, { [benqFp]: true })[benqFp], "input")
    }

    function test_ddc_prune_tracks_plugs_moves_and_unplugs() {
        var w = watching("0x11").state
        var ids = Model.planFromLive([laptop(), benq()], {}, {})
        var benqFp = byName(ids, "HDMI-A-1").fp
        var start = Model.ddcPrune({}, ids, {}, 500)
        compare(Object.keys(start), [benqFp])
        compare(start[benqFp].state, "UNPROBED")
        compare(start[benqFp].readyAt, 500)
        verify(!start[benqFp].hotplugged)
        var states = {}
        states[benqFp] = w
        states["gone"] = w
        var kept = Model.ddcPrune(states, ids, {}, 1000)
        compare(kept[benqFp], w)
        compare(kept["gone"], undefined)
        var plugged = Model.ddcPrune(states, ids, { "HDMI-A-1": true }, 1000)
        compare(plugged[benqFp].state, "UNPROBED")
        verify(plugged[benqFp].hotplugged)
        // Moved to another PC port: starts over and relearns, even if paused.
        var p = feed(w, [ok("0x12"), ok("0x12"), ok("0x12")], 40000, "0x11").state
        states[benqFp] = p
        var moved = benq()
        moved.name = "DP-1"
        var movedIds = Model.planFromLive([laptop(), moved], {}, {})
        var after = Model.ddcPrune(states, movedIds, {}, 2000)
        compare(after[benqFp].connector, "DP-1")
        compare(after[benqFp].state, "UNPROBED")
        verify(after[benqFp].hotplugged)
        compare(after[benqFp].recentPauses.length, 1)
        compare(Model.ddcOffMap(after), {})
    }

    function test_ddc_saved_input_is_per_connector() {
        var mem = Model.normalizeDdcMemory({ monitors: {
            fp: { input: "0x11", connector: "HDMI-A-1", learnedAt: "t" },
            zero: { input: "0x00", connector: "DP-1" }
        } })
        compare(Object.keys(mem.monitors), ["fp"])
        compare(Model.ddcSavedInput(mem, "fp", "HDMI-A-1"), "0x11")
        compare(Model.ddcSavedInput(mem, "fp", "DP-1"), "")
        compare(Model.ddcSavedInput(null, "fp", "HDMI-A-1"), "")
    }

    function test_ddc_hold_drops_evidence_while_a_monitor_cannot_be_read() {
        var w = watching("0x11").state
        var two = feed(w, [ok("0x12"), ok("0x12")], 40000, "0x11").state
        var held = Model.ddcHold({ fp: two }, { fp: false }, 60000)
        verify(held.changed)
        compare(held.states.fp.otherStreak, 0)
        verify(held.states.fp.held)
        compare(Model.ddcDue(held.states, { fp: false }, 90000), [])
        compare(Model.ddcHold(held.states, { fp: false }, 61000).changed, false)
        var back = Model.ddcHold(held.states, { fp: true }, 70000)
        verify(back.changed)
        compare(back.states.fp.readyAt, 70000)
        // A probe interrupted by DPMS starts over.
        var probing = feed(Model.ddcFresh("DP-1", 0, false), [ok("0x11")], 10000, "").state
        compare(probing.state, "PROBING")
        compare(Model.ddcHold({ p: probing }, {}, 20000).states.p.state, "UNPROBED")
        // A paused monitor is never held (it is dark on purpose).
        var p = feed(w, [ok("0x12"), ok("0x12"), ok("0x12")], 40000, "0x11").state
        compare(Model.ddcHold({ p: p }, {}, 90000).changed, false)
    }

    function test_ddc_resume_and_reprobe() {
        var w = watching("0x11").state
        var p = feed(w, [ok("0x12"), ok("0x12"), ok("0x12")], 40000, "0x11").state
        var unsupported = feed(Model.ddcFresh("HDMI-A-2", 0, false), [amb(), amb(), amb()], 10000, "").state
        var resumed = Model.ddcResume({ p: p, w: w, u: unsupported }, 900000)
        compare(resumed.p.state, "UNPROBED")
        verify(!resumed.p.hotplugged)
        compare(resumed.p.readyAt, 900000)
        compare(resumed.w.state, "UNPROBED")
        compare(resumed.u, unsupported)
        compare(Model.ddcOffMap(resumed), {})
        // After resume a monitor on another input waits instead of relearning.
        var again = feed(resumed.p, [ok("0x12"), ok("0x12")], 910000, "0x11")
        compare(again.state.state, "UNVERIFIED")
        compare(again.learns.length, 0)

        var snoozed = Model.ddcWake({ s: p }, "s", 1, true).s
        var suspended = Model.ddcCopyState(w)
        suspended.state = "SUSPENDED"
        suspended.recentPauses = [1, 2, 3]
        var reprobed = Model.ddcReprobe({ u: unsupported, s: snoozed, w: w, p: p, v: again.state, x: suspended }, 5000)
        compare(reprobed.u.state, "UNPROBED")
        verify(reprobed.u.hotplugged)
        compare(reprobed.x.state, "UNPROBED")
        verify(reprobed.x.hotplugged)
        compare(reprobed.x.recentPauses, [])
        compare(reprobed.w, w)
        compare(reprobed.p, p)
        // An explicit Re-probe of an unsupported monitor may learn the input shown now.
        var learned = feed(reprobed.u, [ok("0x12"), ok("0x12")], 20000, "0x11")
        compare(learned.learns.length, 1)
    }

    function test_ddc_reprobe_while_snoozed_or_waiting_keeps_the_learned_input() {
        var w = watching("0x11").state
        var p = feed(w, [ok("0x12"), ok("0x12"), ok("0x12")], 40000, "0x11").state
        var snoozed = Model.ddcWake({ s: p }, "s", 60000, true).s
        snoozed.lastPollAt = 70000
        var waiting = feed(Model.ddcFresh("HDMI-A-1", 0, false), [ok("0x12"), ok("0x12")], 10000, "0x11").state
        compare(waiting.state, "UNVERIFIED")
        var reprobed = Model.ddcReprobe({ s: snoozed, v: waiting }, 71000)
        compare(reprobed.s.state, "SNOOZED")
        compare(reprobed.s.expected, "0x11")
        verify(!reprobed.s.hotplugged)
        compare(reprobed.s.lastPollAt, 0)
        compare(reprobed.v.state, "UNVERIFIED")
        compare(reprobed.v.expected, "0x11")
        verify(!reprobed.v.hotplugged)
        // Read again right away, not after the slow 15s pace.
        compare(Model.ddcDue(reprobed, { s: true, v: true }, 71001).sort(), ["s", "v"])
        // Still on the other input: nothing is relearned, nothing pauses.
        var still = feed(reprobed.s, [ok("0x12"), ok("0x12"), ok("0x12"), ok("0x12")], 71000, "0x11")
        compare(still.state.state, "SNOOZED")
        compare(still.learns.length, 0)
        var back = feed(reprobed.v, [ok("0x11")], 72000, "0x11")
        compare(back.state.state, "WATCHING")
        compare(back.state.expected, "0x11")
        compare(back.learns.length, 0)
    }

    function test_ddc_poll_pacing() {
        var fresh = Model.ddcFresh("HDMI-A-1", 1000, false)
        compare(Model.ddcPollInterval({}, 0), 0)
        compare(Model.ddcPollInterval({ a: fresh }, 0), 5000)
        compare(Model.ddcDue({ a: fresh }, { a: true }, 5000), [])
        compare(Model.ddcDue({ a: fresh }, { a: true }, 11000), ["a"])
        compare(Model.ddcDue({ a: fresh }, { a: false }, 11000), [])
        var w = watching("0x11").state
        compare(Model.ddcDue({ w: w }, { w: true }, w.lastPollAt + 1000), [])
        compare(Model.ddcDue({ w: w }, { w: true }, w.lastPollAt + 5000), ["w"])
        var p = feed(w, [ok("0x12"), ok("0x12"), ok("0x12")], 40000, "0x11").state
        // Paused monitors are dark on purpose and always read, every 3s.
        compare(Model.ddcDue({ p: p }, {}, p.lastPollAt + 3000), ["p"])
        compare(Model.ddcPollInterval({ p: p, w: w }, 0), 3000)
        var s = Model.ddcWake({ p: p }, "p", 1, true).p
        compare(Model.ddcDue({ s: s }, { s: true }, s.lastPollAt + 5000), [])
        compare(Model.ddcDue({ s: s }, { s: true }, s.lastPollAt + 15000), ["s"])
    }

    function test_ddc_status_text() {
        compare(Model.ddcStatusText({ state: "WATCHING", code: "0x11" }, true), "Watching · HDMI-1 (0x11)")
        compare(Model.ddcStatusText({ state: "UNPROBED" }, true), "Checking input…")
        compare(Model.ddcStatusText({ state: "PROBING" }, true), "Checking input…")
        compare(Model.ddcStatusText({ state: "UNVERIFIED" }, true), "Waiting for this PC's input")
        compare(Model.ddcStatusText({ state: "SUSPENDED" }, true), "Suspended (unstable readings)")
        compare(Model.ddcStatusText({ state: "NO_TOOL" }, true), "Needs ddcutil")
        compare(Model.ddcStatusText({ state: "WATCHING", code: "0x11" }, false), "Apply a layout once to enable")
    }

    function test_monitor_added_event_names() {
        compare(Model.monitorAddedName("monitoradded", "HDMI-A-1"), "HDMI-A-1")
        compare(Model.monitorAddedName("monitoraddedv2", "3,DP-2,Dell Inc. U2723QE"), "DP-2")
        compare(Model.monitorAddedName("monitorremoved", "HDMI-A-1"), "HDMI-A-1")
        compare(Model.monitorAddedName("monitorremovedv2", "1,HDMI-A-1,BNQ BenQ GW2790Q ET97T00426087"), "HDMI-A-1")
        compare(Model.monitorAddedName("monitoraddedv2", ""), "")
        // Brief HPD bounce (<8s) does not count as a physical cable swap; longer unplug or new plug does.
        verify(Model.ddcIsHotplug(0, 50000))
        verify(!Model.ddcIsHotplug(50000, 51500))
        verify(!Model.ddcIsHotplug(50000, 57999))
        verify(Model.ddcIsHotplug(50000, 58000))
        // A 7.5s HPD pulse classified as a bounce at raw monitoradded stays a bounce
        // even if a stale pre-settle refresh sees the connector absent first and the
        // settled refresh arrives past 8.0s (8.7s total).
        var mons = [laptop(), benq()]
        var staleBounced = Model.ddcRetainPendingFlags({ "HDMI-A-1": true }, { "eDP-1": true })
        compare(staleBounced, { "HDMI-A-1": true })
        var pluggedBounce = Model.ddcResolvePlugged(mons, { "eDP-1": true }, {}, staleBounced,
                                                    { "HDMI-A-1": 50000 }, 58700, 0)
        compare(pluggedBounce, {})
        compare(Model.ddcRetainPendingFlags(staleBounced, { "eDP-1": true, "HDMI-A-1": true }), {})
        var prunedBounce = Model.ddcPrune({}, Model.planFromLive(mons, {}, {}), pluggedBounce, 58700)
        var benqFp = byName(Model.planFromLive(mons, {}, {}), "HDMI-A-1").fp
        var afterBounce = feed(prunedBounce[benqFp], [ok("0x12"), ok("0x12")], 70000, "0x11")
        compare([afterBounce.state.state, afterBounce.state.expected, afterBounce.learns.length],
                ["UNVERIFIED", "0x11", 0])
        // Without a bounce flag, a genuine >8s unplug+replug is marked hotplugged.
        var pluggedReal = Model.ddcResolvePlugged(mons, { "eDP-1": true }, {}, {},
                                                  { "HDMI-A-1": 50000 }, 58700, 0)
        compare(pluggedReal, { "HDMI-A-1": true })
    }

    function test_input_pause_never_changes_the_persisted_unpaused_layout() {
        var memory = Model.remember({}, Model.derive([laptop(), benq()], {}, {}), "t")
        var ids = Model.planFromLive([laptop(), benq()], memory, {})
        var benqFp = byName(ids, "HDMI-A-1").fp
        var unpaused = Model.derive([laptop(), benq()], memory, Model.autoOffFor(ids, false, {}))
        var paused = Model.derive([laptop(), benq()], memory, Model.autoOffFor(ids, false, { [benqFp]: true }))
        compare(byName(paused, "HDMI-A-1").offReason, "input")
        // The paused plan moves the laptop to 0,0 for the live session...
        compare([byName(paused, "eDP-1").x, byName(paused, "eDP-1").y], [0, 0])
        // ...while what monitors.lua gets is identical whether paused or not.
        var stillUnpaused = Model.derive([laptop(), benq()], memory, Model.autoOffFor(ids, false, {}))
        compare(JSON.stringify(stillUnpaused), JSON.stringify(unpaused))
        verify(!stillUnpaused.some(function(o) { return o.offReason === "input" }))
        compare([byName(unpaused, "eDP-1").x, byName(unpaused, "eDP-1").y], [313, 1440])
    }

    // ------------------------------------------------ Tier 1: HDMI SCDC + mirror-parking
    function scdc(v) { return { status: "ok", signal: "scdc", scdc: v } }

    function scdcWatching(connector) {
        var r = feed(Model.ddcFresh(connector || "HDMI-A-1", 0, false), [scdc("locked"), scdc("locked")], 10000, "")
        compare(r.state.state, "WATCHING")
        compare(r.state.mode, "scdc")
        return r
    }

    function parkedMonitors() {
        var mons = [laptop(), benq()]
        mons[0].id = 0
        mons[1].id = 1
        mons[1].mirrorOf = "0"
        return mons
    }

    function test_scdc_acer_lifecycle_parks_as_a_mirror_and_wakes_on_lock() {
        // Two locks while probing: watched through SCDC, nothing learned.
        var one = feed(Model.ddcFresh("HDMI-A-1", 0, false), [scdc("locked")], 10000, "0x11")
        compare(one.state.state, "PROBING")
        var w = feed(one.state, [scdc("locked")], one.t, "0x11")
        compare([w.state.state, w.state.mode, w.state.pauseMode], ["WATCHING", "scdc", "mirror"])
        compare(w.learns.length, 0)
        compare(Model.ddcPollInterval({ fp: w.state }, 0), 5000)
        compare(Model.ddcReadArgs([{ fp: "fp", name: "HDMI-A-1", mode: "scdc" }], { fp: w.state }), ["HDMI-A-1@scdc"])
        compare(Model.ddcStatusText(w.state, true), "Active · HDMI signal lock")
        // Two unlocked reads are not enough; a lock resets the evidence.
        var two = feed(w.state, [scdc("unlocked"), scdc("unlocked"), scdc("locked"), scdc("unlocked"), scdc("unlocked")], w.t, "")
        compare(two.state.state, "WATCHING")
        // Busy is neutral; an unreadable answer breaks the streak.
        var broken = feed(w.state, [scdc("unlocked"), scdc("unlocked"), amb(), scdc("unlocked")], w.t, "")
        compare(broken.state.state, "WATCHING")
        var p = feed(w.state, [scdc("unlocked"), busy(), scdc("unlocked"), scdc("unlocked")], w.t, "", true, 5000)
        compare([p.state.state, p.state.pauseMode, p.state.pausedReason], ["PAUSED", "mirror", "unlocked"])
        compare(Model.ddcPollInterval({ fp: p.state }, 0), 3000)
        compare(Model.ddcStatusText(p.state, true), "Paused · switched away or powered off")

        var mons = [laptop(), benq()]
        var memory = Model.remember({}, Model.derive(mons, {}, {}), "t")
        var benqFp = byName(Model.planFromLive(mons, memory, {}), "HDMI-A-1").fp
        var states = {}
        states[benqFp] = p.state
        compare(Model.ddcOffMap(states), { [benqFp]: true })
        var modes = Model.ddcPauseModes(states)
        compare(modes, { [benqFp]: "mirror" })
        var off = Model.autoOffFor(Model.planFromLive(mons, memory), false, Model.ddcOffMap(states))
        compare(off[benqFp], "input")
        var plan = Model.derive(mons, memory, off, modes)
        var b = byName(plan, "HDMI-A-1")
        compare([b.autoOff, b.offReason, b.parkMirrorOf, b.mirrorOf, b.pauseMode], [true, "input", "eDP-1", "", "mirror"])
        // Not parked yet (still extended): reported as it is, lit and not
        // paused (never "off" with no park), and out of step, so it is applied.
        var before = byName(Model.planFromLive(mons, memory, off, modes), "HDMI-A-1")
        compare([before.autoOff, before.offReason, before.pauseMode, before.parkMirrorOf, before.mirrorOf, before.enabled],
                [false, "", "", "", "", true])
        verify(!Model.outputMatchesLive(b, before))
        verify(Model.parkedMirrorsStale(plan, Model.planFromLive(mons, memory, off, modes)))
        // Hyprland now reports it as a mirror of eDP-1 (numeric id): the park
        // the bar recorded before applying it is shown as paused and parked,
        // never as a mirror the user set up.
        var applied = Model.recordAppliedParks({}, plan)
        compare(applied, { "HDMI-A-1": ["eDP-1"] })
        var live = Model.planFromLive(parkedMonitors(), memory, off, modes, applied)
        var lb = byName(live, "HDMI-A-1")
        compare([lb.enabled, lb.autoOff, lb.offReason, lb.mirrorOf, lb.parkMirrorOf], [true, true, "input", "", "eDP-1"])
        verify(Model.outputMatchesLive(b, lb))
        verify(!Model.parkedMirrorsStale(plan, live))
        compare(Model.activeLayoutPreset(live), "")
        // A disabled monitor is not a park (and a park is not a disable).
        var dark = [laptop(), benq()]
        dark[1].disabled = true
        verify(!Model.outputMatchesLive(b, byName(Model.planFromLive(dark, memory, off, modes), "HDMI-A-1")))
        var disablePlan = Model.derive(mons, memory, off, { [benqFp]: "disable" })
        compare(byName(disablePlan, "HDMI-A-1").parkMirrorOf, "")
        verify(!Model.outputMatchesLive(byName(disablePlan, "HDMI-A-1"), lb))
        // One lock wakes it at once, with a fresh settle grace.
        var woke = Model.ddcObserve(states, benqFp, scdc("locked"), { now: p.t, canPause: true })
        verify(woke.offChanged)
        compare([woke.states[benqFp].state, woke.states[benqFp].readyAt], ["WATCHING", p.t])
        var offW = Model.autoOffFor(Model.planFromLive(mons, memory), false, Model.ddcOffMap(woke.states))
        var modesW = Model.ddcPauseModes(woke.states)
        var wakePlan = Model.derive(parkedMonitors(), memory, offW, modesW)
        var wb = byName(wakePlan, "HDMI-A-1")
        compare([wb.autoOff, wb.mirrorOf, wb.parkMirrorOf, wb.enabled], [false, "", "", true])
        // Still parked live (the park the bar applied): never shown as the
        // user's mirror; the reconcile un-mirrors it back to the saved layout.
        compare(modesW, {})
        var parks = Model.recordAppliedParks({}, plan)
        compare(parks, { "HDMI-A-1": ["eDP-1"] })
        var stillParked = Model.planFromLive(parkedMonitors(), memory, offW, modesW, parks)
        var sp = byName(stillParked, "HDMI-A-1")
        compare([sp.mirrorOf, sp.parkMirrorOf, sp.autoOff], ["", "eDP-1", false])
        verify(!Model.planMatchesLive(wakePlan, stillParked))
        verify(Model.planWakes(wakePlan, stillParked))
        compare([wb.x, wb.y], [byName(Model.derive(mons, memory, {}), "HDMI-A-1").x, 0])
        // With no saved layout at all, the bar's own park is still never kept,
        // while the same mirror not applied by the bar is the user's and stays.
        compare(byName(Model.derive(parkedMonitors(), {}, {}, modesW, parks), "HDMI-A-1").mirrorOf, "")
        compare(byName(Model.derive(parkedMonitors(), {}, {}, modesW), "HDMI-A-1").mirrorOf, "eDP-1")
    }

    function test_scdc_tv_powered_off_reads_absent_and_wakes_on_power_on() {
        var w = scdcWatching("HDMI-A-2")
        var p = feed(w.state, [scdc("absent"), scdc("unlocked"), scdc("absent")], w.t, "", true, 2000)
        compare([p.state.state, p.state.pauseMode, p.state.pausedReason], ["PAUSED", "mirror", "absent"])
        // Off or on another input it stays parked (its link is kept up)...
        var stay = feed(p.state, [scdc("absent"), scdc("unlocked"), busy(), scdc("absent"), scdc("absent"),
                                  scdc("unlocked"), scdc("absent")], p.t, "", true, 2000)
        compare(stay.state.state, "PAUSED")
        compare(stay.state.ambiguousStreak, 0)
        // ...and powering it back on (lock) restores it immediately.
        var back = feed(stay.state, [scdc("locked")], stay.t, "", true, 2000)
        compare(back.state.state, "WATCHING")
        compare(back.state.mode, "scdc")
    }

    function test_scdc_helper_failure_while_parked_fails_open() {
        var w = scdcWatching()
        var p = feed(w.state, [scdc("unlocked"), scdc("unlocked"), scdc("unlocked")], w.t, "").state
        compare(p.state, "PAUSED")
        compare(feed(p, [amb(), amb()], 90000, "").state.state, "WATCHING")
        compare(feed(p, [amb(), busy(), amb()], 90000, "").state.state, "WATCHING")
        var failed = Model.ddcFailedReadings([{ fp: "fp", name: "HDMI-A-1" }])["HDMI-A-1"]
        compare(feed(p, [failed, failed], 90000, "").state.state, "WATCHING")
        // A malformed SCDC answer is unreadable, not "away".
        var bogus = { status: "ok", signal: "scdc", scdc: "maybe" }
        compare(feed(p, [bogus, bogus], 90000, "").state.state, "WATCHING")
        compare(feed(w.state, [bogus, bogus, bogus, bogus], w.t, "").state.state, "WATCHING")
        // A real "away" answer in between resets the fail-open count.
        compare(feed(p, [amb(), scdc("unlocked"), amb()], 90000, "").state.state, "PAUSED")
    }

    function test_scdc_manual_wake_snoozes_until_the_lock_returns() {
        var w = scdcWatching()
        var p = feed(w.state, [scdc("unlocked"), scdc("unlocked"), scdc("unlocked")], w.t, "").state
        var states = Model.ddcWake({ fp: p }, "fp", 60000, true)
        compare([states.fp.state, states.fp.mode], ["SNOOZED", "scdc"])
        compare(Model.ddcOffMap(states), {})
        // Only paused monitors have a pause mode: a woken one is never
        // treated as parked because of how it is watched.
        compare(Model.ddcPauseModes(states), {})
        compare(Model.ddcStatusText(states.fp, true), "Snoozed until HDMI lock returns")
        compare(Model.ddcPollInterval(states, 0), 5000)
        var snoozed = feed(states.fp, [scdc("unlocked"), scdc("absent"), scdc("unlocked"), scdc("absent")], 61000, "")
        compare(snoozed.state.state, "SNOOZED")
        var reprobed = Model.ddcReprobe({ s: snoozed.state }, 90000)
        compare([reprobed.s.state, reprobed.s.lastPollAt], ["SNOOZED", 0])
        var back = feed(snoozed.state, [scdc("locked")], 95000, "")
        compare([back.state.state, back.state.readyAt], ["WATCHING", 95000])
    }

    function test_scdc_flapping_suspends() {
        var s = scdcWatching()
        var st = s.state
        var t = s.t
        for (var round = 0; round < 3; round++) {
            var paused = feed(st, [scdc("unlocked"), scdc("unlocked"), scdc("unlocked")], t, "", true, 2000)
            compare(paused.state.state, "PAUSED")
            var woke = feed(paused.state, [scdc("locked")], paused.t, "", true, 2000)
            compare(woke.state.state, "WATCHING")
            st = woke.state
            t = woke.t + 10000
        }
        var fourth = feed(st, [scdc("unlocked"), scdc("unlocked"), scdc("unlocked")], t, "", true, 2000)
        compare(fourth.state.state, "SUSPENDED")
        compare(Model.ddcOffMap({ fp: fourth.state }), {})
        compare(Model.ddcPollInterval({ fp: fourth.state }, 0), 0)
        // Never pauses while unsafe or inside the settle grace either.
        var unsafe = feed(st, [scdc("unlocked"), scdc("unlocked"), scdc("unlocked"), scdc("unlocked")], t, "", false)
        compare(unsafe.state.state, "WATCHING")
        var grace = feed(st, [scdc("unlocked"), scdc("unlocked"), scdc("unlocked")], st.readyAt + 1000, "", true, 1000)
        compare(grace.state.state, "WATCHING")
    }

    function test_monitor_without_scdc_falls_back_to_ddc_vcp_60() {
        // DP / HDMI 1.4: the helper answers with VCP 0x60 (SCDC "none" falls through).
        var dp = feed(Model.ddcFresh("DP-1", 0, false), [ok("0x0f"), ok("0x0f")], 10000, "")
        compare([dp.state.state, dp.state.mode, dp.state.pauseMode], ["WATCHING", "ddc", "disable"])
        compare(dp.learns.length, 1)
        compare(Model.ddcReadArgs([{ fp: "fp", name: "DP-1", mode: "ddc" }], { fp: dp.state }), ["DP-1@ddc"])
        var p = feed(dp.state, [ok("0x11"), ok("0x11"), ok("0x11")], dp.t, "0x0f").state
        compare(p.state, "PAUSED")
        compare(Model.ddcPauseModes({ fp: p }), { fp: "disable" })
        compare(Model.ddcStatusText(p, true), "Paused · switched to HDMI-1 (0x11)")
        compare(Model.ddcPollInterval({ fp: p }, 0), 3000)
        // A lone lock among VCP reads does not count; VCP pairs still learn.
        var mixed = feed(Model.ddcFresh("HDMI-A-1", 0, false), [scdc("locked"), ok("0x11"), ok("0x11")], 10000, "")
        compare([mixed.state.state, mixed.state.mode], ["WATCHING", "ddc"])
        // A DDC-watched monitor that shows an SCDC lock moves to SCDC.
        var up = feed(mixed.state, [scdc("locked")], mixed.t, "0x11")
        compare([up.state.state, up.state.mode, up.state.pauseMode], ["WATCHING", "scdc", "mirror"])
        var pausedDdc = feed(mixed.state, [ok("0x12"), ok("0x12"), ok("0x12")], mixed.t, "0x11").state
        var upWake = feed(pausedDdc, [scdc("locked")], 90000, "0x11")
        compare([upWake.state.state, upWake.state.mode, upWake.state.readyAt], ["WATCHING", "scdc", 90000])
        // No ddcutil and no SCDC lock: needs ddcutil.
        compare(feed(Model.ddcFresh("DP-1", 0, false), [{ status: "no_tool" }], 10000, "").state.state, "NO_TOOL")
        // DDC/CI monitor with HDMI HDCP RX (0x3A) uses @ddc+hdcp, mirror-parks on power-off, and wakes on return.
        var hdcpOk = { status: "ok", code: "0x11", hdcp: true }
        var pwrOff = { status: "ok", signal: "power", power: "off" }
        var benqW = feed(Model.ddcFresh("HDMI-A-1", 0, false), [hdcpOk, hdcpOk], 10000, "")
        compare([benqW.state.state, benqW.state.mode, benqW.state.hdcpCapable, benqW.state.pauseMode],
                ["WATCHING", "ddc", true, "mirror"])
        compare(Model.ddcReadArgs([{ fp: "fp", name: "HDMI-A-1", mode: "ddc" }], { fp: benqW.state }),
                ["HDMI-A-1@ddc+hdcp"])
        // Normal watching polls every 5s; after the first off/away signal the
        // next 2 confirmation checks accelerate to 1s, then drop back to 3s once
        // paused (or 5s if unable to pause, e.g. last lit display).
        compare(Model.ddcPollInterval({ fp: benqW.state }, 0), 5000)
        var c1 = feed(benqW.state, [pwrOff], benqW.t, "0x11", true, 5000)
        compare([c1.state.state, c1.state.otherStreak, Model.ddcPollInterval({ fp: c1.state }, 0)], ["WATCHING", 1, 1000])
        var c2 = feed(c1.state, [pwrOff], c1.t, "0x11", true, 1000)
        compare([c2.state.state, c2.state.otherStreak, Model.ddcPollInterval({ fp: c2.state }, 0)], ["WATCHING", 2, 1000])
        var unsafe3 = feed(c2.state, [pwrOff], c2.t, "0x11", false, 1000)
        compare([unsafe3.state.state, unsafe3.state.otherStreak, Model.ddcPollInterval({ fp: unsafe3.state }, 0)], ["WATCHING", 3, 5000])
        var benqOff = feed(c2.state, [pwrOff], c2.t, "0x11", true, 1000)
        compare([benqOff.state.state, benqOff.state.pausedReason], ["PAUSED", "power"])
        compare(Model.ddcPollInterval({ fp: benqOff.state }, 0), 3000)
        compare(Model.ddcPauseModes({ fp: benqOff.state }), { fp: "mirror" })
        compare(Model.ddcStatusText(benqOff.state, true), "Paused · switched away or powered off")
        // Stays paused while still powered off; wakes cleanly when powered back on.
        var stillOff = feed(benqOff.state, [pwrOff, pwrOff], benqOff.t, "0x11")
        compare(stillOff.state.state, "PAUSED")
        var backOn = feed(stillOff.state, [hdcpOk], stillOff.t, "0x11")
        compare([backOn.state.state, backOn.state.pausedReason], ["WATCHING", ""])
        // Alternating power-off and the same other-input VCP reading advance the streak,
        // while a power-off between two DIFFERENT VCP codes (0x12 -> power -> 0x13) resets.
        var mixedVcp = feed(backOn.state, [ok("0x12"), pwrOff, ok("0x13")], backOn.t + 15000, "0x11", true, 1000)
        compare([mixedVcp.state.state, mixedVcp.state.otherStreak, mixedVcp.state.otherCode], ["WATCHING", 1, "0x13"])
        var alt = feed(backOn.state, [ok("0x12"), pwrOff, ok("0x12")], backOn.t + 15000, "0x11", true, 1000)
        compare([alt.state.state, alt.state.pausedReason, alt.state.pausedCode], ["PAUSED", "input", "0x12"])
        // Resume and Re-probe preserve hdcpCapable (forgetting any stale bus number)
        // and route through @ddc+hdcp; AC-off or unreachable without a sysfs bus lands
        // in UNVERIFIED with --detect enabled so it recovers as soon as it powers back on.
        var withBus = Model.ddcCopyState(benqOff.state)
        withBus.detectedBus = "9"
        var resumedHdcp = Model.ddcResume({ fp: withBus }, 500000)
        verify(resumedHdcp.fp.hdcpCapable)
        compare(resumedHdcp.fp.detectedBus, "")
        compare(Model.ddcReadArgs([{ fp: "fp", name: "HDMI-A-1" }], resumedHdcp), ["--detect", "HDMI-A-1", "HDMI-A-1@ddc+hdcp"])
        var acOffResume = feed(resumedHdcp.fp, [pwrOff], 510000, "0x11")
        compare([acOffResume.state.state, acOffResume.state.mode, acOffResume.state.expected], ["UNVERIFIED", "ddc", "0x11"])
        var wokenAfterSuspend = feed(acOffResume.state, [hdcpOk], acOffResume.t, "0x11")
        compare(wokenAfterSuspend.state.state, "WATCHING")
        var noBusResume = feed(resumedHdcp.fp, [amb(), amb(), amb()], 510000, "0x11")
        compare([noBusResume.state.state, noBusResume.state.mode, noBusResume.state.expected], ["UNVERIFIED", "ddc", "0x11"])
        compare(Model.ddcReadArgs([{ fp: "fp", name: "HDMI-A-1" }], { fp: noBusResume.state }), ["--detect", "HDMI-A-1", "HDMI-A-1@ddc+hdcp"])
        var suspHdcp = Model.ddcCopyState(withBus)
        suspHdcp.state = "SUSPENDED"
        var reprobedHdcp = Model.ddcReprobe({ fp: suspHdcp }, 520000)
        compare([reprobedHdcp.fp.hdcpCapable, reprobedHdcp.fp.detectedBus], [true, ""])
        var acOffReprobe = feed(reprobedHdcp.fp, [pwrOff], 530000, "0x11")
        compare([acOffReprobe.state.state, acOffReprobe.state.mode, acOffReprobe.state.expected], ["UNVERIFIED", "ddc", "0x11"])
        // Mid-pause hdcp:true does NOT flip a disabled pause to mirror-parked, and
        // planFromLive never mistakes a disabled paused monitor for lit/extended.
        var noHdcpW = feed(Model.ddcFresh("HDMI-A-1", 0, false), [ok("0x11"), ok("0x11")], 10000, "")
        var disPaused = feed(noHdcpW.state, [ok("0x12"), ok("0x12"), ok("0x12")], noHdcpW.t, "0x11").state
        compare([disPaused.state, disPaused.pauseMode, disPaused.hdcpCapable], ["PAUSED", "disable", false])
        var midPauseHdcp = feed(disPaused, [{ status: "ok", code: "0x12", hdcp: true }], disPaused.t, "0x11").state
        compare([midPauseHdcp.state, midPauseHdcp.pauseMode, midPauseHdcp.hdcpCapable], ["PAUSED", "disable", false])
        var disLive = Model.planFromLive([laptop(), Object.assign({}, benq(), { disabled: true })], {},
                                         { [byName(Model.planFromLive([laptop(), benq()], {}, {}), "HDMI-A-1").fp]: "input" },
                                         { [byName(Model.planFromLive([laptop(), benq()], {}, {}), "HDMI-A-1").fp]: "mirror" }, {})
        compare([byName(disLive, "HDMI-A-1").autoOff, byName(disLive, "HDMI-A-1").enabled], [true, false])
        // Explicit manual power-off edit in draft survives mirror-park pause + wake and stays dirty.
        var litMons = [laptop(), benq()]
        var memMan = Model.remember({}, Model.derive(litMons, {}, {}), "t")
        var bFp = byName(Model.planFromLive(litMons, memMan, {}), "HDMI-A-1").fp
        var manualDraft = Model.planFromLive(litMons, memMan, {})
        byName(manualDraft, "HDMI-A-1").enabled = false
        var parkedLive = Model.planFromLive([laptop(), Object.assign({}, benq(), { mirrorOf: "eDP-1" })], memMan,
                                            { [bFp]: "input" }, { [bFp]: "mirror" }, { "HDMI-A-1": ["eDP-1"] })
        var syncedMan = Model.syncDraftAutoOff(manualDraft, parkedLive)
        compare(byName(syncedMan.draft, "HDMI-A-1").enabled, false)
        compare(byName(syncedMan.draft, "HDMI-A-1").autoOff, false)
        verify(!Model.outputMatchesLive(byName(syncedMan.draft, "HDMI-A-1"), byName(parkedLive, "HDMI-A-1")))
        var wokeMan = Model.syncDraftAutoOff(syncedMan.draft, Model.planFromLive(litMons, memMan, {}))
        compare(byName(wokeMan.draft, "HDMI-A-1").enabled, false)
    }

    function test_scdc_monitor_after_resume_probes_through_scdc() {
        var w = scdcWatching()
        var p = feed(w.state, [scdc("unlocked"), scdc("unlocked"), scdc("unlocked")], w.t, "").state
        var resumed = Model.ddcResume({ fp: p }, 500000)
        compare([resumed.fp.state, resumed.fp.mode], ["UNPROBED", ""])
        verify(resumed.fp.scdcCapable)
        compare(Model.ddcOffMap(resumed), {})
        compare(Model.ddcReadArgs([{ fp: "fp", name: "HDMI-A-1" }], resumed), ["HDMI-A-1@scdc"])
        // Resumed while on another input: waits for the lock, never pauses.
        var away = feed(resumed.fp, [scdc("unlocked")], 510000, "")
        compare([away.state.state, away.state.mode], ["UNVERIFIED", "scdc"])
        compare(Model.ddcStatusText(away.state, true), "Waiting for HDMI signal lock")
        var still = feed(away.state, [scdc("unlocked"), scdc("absent"), scdc("unlocked"), scdc("unlocked")], away.t, "")
        compare(still.state.state, "UNVERIFIED")
        compare(feed(still.state, [scdc("locked")], still.t, "").state.state, "WATCHING")
        var onPc = feed(resumed.fp, [scdc("locked"), scdc("locked")], 510000, "")
        compare([onPc.state.state, onPc.state.mode], ["WATCHING", "scdc"])
        // A replug starts over without the SCDC hint.
        var ids = Model.planFromLive([laptop(), benq()], {}, {})
        var benqFp = byName(ids, "HDMI-A-1").fp
        var replug = Model.ddcPrune({ [benqFp]: w.state }, ids, { "HDMI-A-1": true }, 600000)
        verify(!replug[benqFp].scdcCapable)
    }

    function test_park_target_prefers_the_open_panel_then_an_external() {
        var mons = [laptop(), benq(), dell()]
        var plan = Model.derive(mons, {}, {})
        var benqFp = byName(plan, "HDMI-A-1").fp
        compare(Model.pickParkMirrorTarget(plan, benqFp, false), "eDP-1")
        compare(Model.pickParkMirrorTarget(plan, benqFp, true), "DP-3")
        byName(plan, "eDP-1").enabled = false
        compare(Model.pickParkMirrorTarget(plan, benqFp, false), "DP-3")
        byName(plan, "DP-3").mirrorOf = "HDMI-A-1"
        compare(Model.pickParkMirrorTarget(plan, benqFp, false), "")
        compare(Model.pickParkMirrorTarget([], benqFp, false), "")
        // Lid shut: the panel is off, so it parks on the other external.
        var lapFp = byName(plan, "eDP-1").fp
        var memory = Model.remember({}, Model.derive(mons, {}, {}), "t")
        var lidPlan = Model.derive(mons, memory, { [lapFp]: "lid", [benqFp]: "input" }, { [benqFp]: "mirror" })
        compare(byName(lidPlan, "HDMI-A-1").parkMirrorOf, "DP-3")
        // The one external left is kept lit even if it was saved off, so there
        // is always an output to park on (settleParkedMirrors covers the rest).
        var userOff = Model.derive(mons, {}, {})
        byName(userOff, "DP-3").enabled = false
        var offMem = Model.remember({}, userOff, "t")
        var forced = Model.derive(mons, offMem, { [lapFp]: "lid", [benqFp]: "input" }, { [benqFp]: "mirror" })
        compare(byName(forced, "DP-3").enabled, true)
        compare(byName(forced, "HDMI-A-1").parkMirrorOf, "DP-3")
        compare(byName(forced, "eDP-1").offReason, "lid")
        // Two parked monitors both park on a real output, never on each other.
        var both = Model.derive(mons, memory, { [benqFp]: "input", [byName(plan, "DP-3").fp]: "input" },
                                { [benqFp]: "mirror", [byName(plan, "DP-3").fp]: "mirror" })
        compare([byName(both, "HDMI-A-1").parkMirrorOf, byName(both, "DP-3").parkMirrorOf], ["eDP-1", "eDP-1"])
    }

    function test_panel_apply_settles_parks_whose_target_was_edited_away() {
        var mons = [laptop(), benq(), dell()]
        var memory = Model.remember({}, Model.derive(mons, {}, {}), "t")
        var benqFp = byName(Model.planFromLive(mons, memory, {}), "HDMI-A-1").fp
        var plan = Model.derive(mons, memory, { [benqFp]: "input" }, { [benqFp]: "mirror" })
        compare(byName(plan, "HDMI-A-1").parkMirrorOf, "eDP-1")
        compare(Model.settleParkedMirrors(plan), plan)
        byName(plan, "eDP-1").enabled = false
        var settled = Model.settleParkedMirrors(plan)
        compare(byName(settled, "HDMI-A-1").parkMirrorOf, "DP-3")
        compare(byName(plan, "HDMI-A-1").parkMirrorOf, "eDP-1")
        byName(settled, "DP-3").enabled = false
        var woken = byName(Model.settleParkedMirrors(settled), "HDMI-A-1")
        compare([woken.autoOff, woken.offReason, woken.parkMirrorOf, woken.enabled], [false, "", "", true])
        // A stray park on a monitor that is not input-paused is dropped.
        var stray = Model.derive(mons, memory, {})
        byName(stray, "DP-3").parkMirrorOf = "eDP-1"
        compare(byName(Model.settleParkedMirrors(stray), "DP-3").parkMirrorOf, "")
    }

    function test_persistable_plan_and_draft_sync_carry_parks_correctly() {
        var mons = [laptop(), benq()]
        var memory = Model.remember({}, Model.derive(mons, {}, {}), "t")
        var benqFp = byName(Model.planFromLive(mons, memory, {}), "HDMI-A-1").fp
        var unpaused = Model.derive(mons, memory, {})
        var parked = Model.derive(mons, memory, { [benqFp]: "input" }, { [benqFp]: "mirror" })
        var file = Model.persistablePlan(parked, unpaused)
        verify(!file.some(function(o) { return o.parkMirrorOf || o.autoOff }))
        compare(byName(file, "HDMI-A-1").mode, byName(unpaused, "HDMI-A-1").mode)
        // No unpaused entry: still never written parked.
        var lone = Model.persistablePlan(parked, [])
        compare(byName(lone, "HDMI-A-1").parkMirrorOf, "")
        compare(byName(parked, "HDMI-A-1").parkMirrorOf, "eDP-1")
        var stray = Model.derive(mons, memory, {})
        byName(stray, "HDMI-A-1").parkMirrorOf = "eDP-1"
        compare(byName(Model.persistablePlan(stray, unpaused), "HDMI-A-1").parkMirrorOf, "")
        // Draft sync: a park shows up in an edited draft, and a wake clears it.
        var live = Model.planFromLive(parkedMonitors(), memory, { [benqFp]: "input" }, { [benqFp]: "mirror" },
                                      Model.recordAppliedParks({}, parked))
        var draft = Model.planFromLive(mons, memory, {})
        byName(draft, "eDP-1").scale = 1.5
        var synced = Model.syncDraftAutoOff(draft, live)
        verify(synced.changed)
        var sb = byName(synced.draft, "HDMI-A-1")
        compare([sb.autoOff, sb.offReason, sb.parkMirrorOf], [true, "input", "eDP-1"])
        compare(Model.syncDraftAutoOff(synced.draft, live).changed, false)
        // Pre-apply refresh right after SCDC wake: autoOff cleared, but Hyprland
        // still shows the mirror-park. Draft waits until unparked.
        var stillParked = Model.planFromLive(parkedMonitors(), memory, {}, {}, Model.recordAppliedParks({}, parked))
        compare(Model.syncDraftAutoOff(synced.draft, stillParked).changed, false)
        var woke = Model.syncDraftAutoOff(synced.draft, Model.planFromLive(mons, memory, {}))
        verify(woke.changed)
        compare(byName(woke.draft, "HDMI-A-1").parkMirrorOf, "")
        compare(byName(woke.draft, "eDP-1").scale, 1.5)
    }

    function test_user_mirror_on_an_scdc_monitor_is_kept_and_parks_are_owned_explicitly() {
        // An SCDC-watched monitor the user mirrors onto the panel: not paused,
        // so no pause mode, and its mirror is the user's everywhere.
        var w = scdcWatching()
        var mons = parkedMonitors()
        var ids = Model.planFromLive(mons, {}, {})
        var benqFp = byName(ids, "HDMI-A-1").fp
        var states = { [benqFp]: w.state }
        var modes = Model.ddcPauseModes(states)
        compare(modes, {})
        var lp = byName(Model.planFromLive(mons, {}, {}, modes, {}), "HDMI-A-1")
        compare([lp.mirrorOf, lp.parkMirrorOf, lp.autoOff], ["eDP-1", "", false])
        compare(byName(Model.derive(mons, {}, {}, modes, {}), "HDMI-A-1").mirrorOf, "eDP-1")
        var userPlan = Model.derive(mons, {}, {}, modes, {})
        var memory = Model.remember({}, userPlan, "t")
        compare(byName(Model.derive(mons, memory, {}, modes, {}), "HDMI-A-1").mirrorOf, "eDP-1")
        // A mirror is never eligible for auto-pause.
        verify(!Model.ddcCanPause(Model.derive(mons, memory, {}, modes, {}), benqFp, false, {}, mons))
        // A park the bar applied onto another target does not own this mirror.
        compare(byName(Model.planFromLive(mons, memory, {}, modes, { "HDMI-A-1": ["DP-3"] }), "HDMI-A-1").mirrorOf, "eDP-1")
        // Applying a plan with the user's mirror hands ownership back to the user.
        compare(Model.recordAppliedParks({ "HDMI-A-1": ["eDP-1"], "DP-3": ["eDP-1"] }, userPlan), { "DP-3": ["eDP-1"] })
        // Readback prunes parks Hyprland no longer shows (numeric ids resolve).
        var still = Model.pruneAppliedParks({ "HDMI-A-1": ["eDP-1"] }, mons)
        compare([still.changed, still.parks], [false, { "HDMI-A-1": ["eDP-1"] }])
        var undone = Model.pruneAppliedParks({ "HDMI-A-1": ["eDP-1"], "DP-3": ["eDP-1"] }, [laptop(), benq()])
        compare([undone.changed, undone.parks], [true, {}])
        var dark = parkedMonitors()
        dark[1].disabled = true
        compare(Model.pruneAppliedParks({ "HDMI-A-1": ["eDP-1"] }, dark).parks, {})
        verify(!Model.liveParkOf("HDMI-A-1", "", { "HDMI-A-1": ["eDP-1"] }))
    }

    function test_forget_resume_and_apply_never_leak_an_applied_park() {
        var mons = [laptop(), benq()]
        var memory = Model.remember({}, Model.derive(mons, {}, {}), "t")
        var benqFp = byName(Model.planFromLive(mons, memory, {}), "HDMI-A-1").fp
        var unpaused = Model.derive(mons, memory, {})
        var parkPlan = Model.derive(mons, memory, { [benqFp]: "input" }, { [benqFp]: "mirror" })
        var parks = Model.recordAppliedParks({}, parkPlan)
        // Woken by hand, by resume, or by Forget: no pause mode left, yet the
        // park is still live until Hyprland shows it undone.
        var resumed = Model.ddcResume({ [benqFp]: scdcWatching().state }, 1)
        compare(Model.ddcPauseModes(resumed), {})
        var live = Model.planFromLive(parkedMonitors(), memory, {}, {}, parks)
        var lb = byName(live, "HDMI-A-1")
        compare([lb.mirrorOf, lb.parkMirrorOf, lb.autoOff], ["", "eDP-1", false])
        // Forget persists the live plan: monitors.lua gets it unparked, in its unpaused place.
        var file = Model.persistablePlan(live, unpaused)
        var fb = byName(file, "HDMI-A-1")
        compare([fb.mirrorOf, fb.parkMirrorOf, fb.x, fb.y], ["", "", byName(unpaused, "HDMI-A-1").x, byName(unpaused, "HDMI-A-1").y])
        // A panel Apply of that live draft never saves the park as a mirror.
        var applied = Model.settleParkedMirrors(live)
        compare([byName(applied, "HDMI-A-1").mirrorOf, byName(applied, "HDMI-A-1").parkMirrorOf], ["", ""])
        var saved = Model.remember(memory, applied, "t2")
        var key = Model.layoutKey(applied.map(function(o) { return o.fp }))
        var entry = saved.layouts[key].outputs[benqFp]
        verify(entry.mirrorOf === undefined && entry.mirrorTargetFp === undefined)
        compare(byName(Model.derive(parkedMonitors(), saved, {}, {}, parks), "HDMI-A-1").mirrorOf, "")
        // And the forgotten memory, re-derived with no layout, keeps no park either.
        var forgot = Model.forgetMonitor(memory, benqFp)
        compare(byName(Model.derive(parkedMonitors(), forgot, {}, {}, parks), "HDMI-A-1").mirrorOf, "")
    }

    function test_pending_mirror_pause_always_gets_a_park_or_stays_on() {
        var mons = [laptop(), benq(), dell()]
        var memory = Model.remember({}, Model.derive(mons, {}, {}), "t")
        var benqFp = byName(Model.planFromLive(mons, memory, {}), "HDMI-A-1").fp
        var plan = Model.derive(mons, memory, { [benqFp]: "input" }, { [benqFp]: "mirror" })
        // derive never leaves a mirror-pause without a park.
        plan.forEach(function(o) {
            if (o.autoOff && o.offReason === "input" && o.pauseMode === "mirror") verify(!!o.parkMirrorOf)
        })
        var pending = Model.settleParkedMirrors(plan)
        byName(pending, "HDMI-A-1").parkMirrorOf = ""
        var settled = byName(Model.settleParkedMirrors(pending), "HDMI-A-1")
        compare([settled.autoOff, settled.parkMirrorOf, settled.pauseMode], [true, "eDP-1", "mirror"])
        byName(pending, "eDP-1").enabled = false
        byName(pending, "DP-3").enabled = false
        var none = byName(Model.settleParkedMirrors(pending), "HDMI-A-1")
        compare([none.autoOff, none.offReason, none.parkMirrorOf, none.pauseMode, none.enabled], [false, "", "", "", true])
        // A DDC/CI pause keeps disabling (no park needed).
        var ddc = Model.derive(mons, memory, { [benqFp]: "input" }, { [benqFp]: "disable" })
        var dd = byName(Model.settleParkedMirrors(ddc), "HDMI-A-1")
        compare([dd.autoOff, dd.pauseMode, dd.parkMirrorOf], [true, "disable", ""])
        compare(byName(Model.settleParkedMirrors(Model.derive(mons, memory, {})), "HDMI-A-1").pauseMode, "")
        // planWakes: unparking an output lit but parked is a wake (retry guard).
        var parkedLive = [{ name: "HDMI-A-1", enabled: true, autoOff: false, mirrorOf: "", parkMirrorOf: "eDP-1" }]
        var mirroredLive = [{ name: "HDMI-A-1", enabled: true, autoOff: false, mirrorOf: "eDP-1", parkMirrorOf: "" }]
        var unpark = [{ name: "HDMI-A-1", enabled: true, autoOff: false, mirrorOf: "", parkMirrorOf: "" }]
        verify(Model.planWakes(unpark, parkedLive))
        verify(Model.planWakes(unpark, mirroredLive))
        verify(!Model.planWakes([{ name: "HDMI-A-1", enabled: true, mirrorOf: "eDP-1" }], mirroredLive))
        verify(!Model.planWakes([{ name: "HDMI-A-1", enabled: true, autoOff: true, offReason: "input", parkMirrorOf: "eDP-1" }], parkedLive))
        verify(!Model.planWakes(unpark, unpark))
    }

    function test_scdc_ambiguous_and_dpms_off_never_pause() {
        var w = scdcWatching()
        // Ambiguous (transport/permission error, floating bus) breaks the streak.
        var broken = feed(w.state, [scdc("unlocked"), scdc("unlocked"), amb(), scdc("unlocked"), scdc("unlocked")],
                          w.t, "", true, 2000)
        compare(broken.state.state, "WATCHING")
        var p = feed(w.state, [scdc("unlocked"), scdc("unlocked"), scdc("unlocked")], w.t, "", true, 2000)
        compare(p.state.state, "PAUSED")
        compare(feed(p.state, [amb(), amb()], p.t, "", true, 2000).state.state, "WATCHING")
        // DPMS-off: no evidence either way, never a wake or a pause.
        var dpms = { status: "dpms_off" }
        compare(feed(p.state, [dpms, dpms, dpms, dpms], p.t, "", true, 2000).state.state, "PAUSED")
        var held = feed(w.state, [scdc("unlocked"), scdc("unlocked"), dpms, scdc("unlocked")], w.t, "", true, 2000)
        compare(held.state.state, "WATCHING")
        compare(held.state.otherStreak, 1)
    }

    function test_global_idle_holds_evidence_and_never_wakes_or_pauses() {
        var mons = [laptop(), benq(), dell()]
        var memory = Model.remember({}, Model.derive(mons, {}, {}), "t")
        var ids = Model.planFromLive(mons, memory, {})
        var benqFp = byName(ids, "HDMI-A-1").fp
        var dellFp = byName(ids, "DP-3").fp
        var idle = mons.map(function(m) { var c = {}; for (var k in m) c[k] = m[k]; c.dpmsStatus = false; return c })
        verify(Model.ddcGlobalIdle(idle))
        verify(!Model.ddcGlobalIdle(mons))
        verify(!Model.ddcGlobalIdle([]))
        var overlay = Model.ddcLiveDpms(mons, { "eDP-1": false, "HDMI-A-1": false, "DP-3": false })
        verify(Model.ddcGlobalIdle(overlay))
        compare(mons[0].dpmsStatus, undefined)
        // The parked monitor stays paused while everything idles; one other
        // display asleep on its own still does not count as lit.
        var unpaused = Model.derive(mons, memory, {})
        verify(Model.ddcCanPause(unpaused, benqFp, false, {}, idle))
        var oneDark = [laptop(), benq(), dell()]
        oneDark[0].dpmsStatus = false
        oneDark[2].dpmsStatus = false
        verify(!Model.ddcCanPause(unpaused, benqFp, false, {}, oneDark))
        // Idle holds every unpaused monitor (its half-built streak dropped) and
        // leaves paused ones alone; back from idle, a fresh grace starts.
        var w = scdcWatching("HDMI-A-1")
        var streak = feed(w.state, [scdc("unlocked"), scdc("unlocked")], w.t, "", true, 2000)
        var p = feed(scdcWatching("DP-3").state, [scdc("unlocked"), scdc("unlocked"), scdc("unlocked")], w.t, "", true, 2000)
        compare(p.state.state, "PAUSED")
        var states = { [benqFp]: streak.state, [dellFp]: p.state }
        var h = Model.ddcHold(states, {}, 100000)
        verify(h.changed)
        compare([h.states[benqFp].held, h.states[benqFp].otherStreak, h.states[dellFp].state], [true, 0, "PAUSED"])
        var back = Model.ddcHold(h.states, { [benqFp]: true, [dellFp]: true }, 200000)
        compare([back.states[benqFp].held, back.states[benqFp].readyAt], [false, 200000])
        var after = feed(back.states[benqFp], [scdc("unlocked"), scdc("unlocked"), scdc("unlocked"), scdc("unlocked")],
                         200000, "", true, 2000)
        compare(after.state.state, "WATCHING")
    }

    function test_scdc_probing_without_ddcutil_never_hits_no_tool() {
        // locked -> unlocked -> locked -> locked: keeps probing, then watches.
        var r = feed(Model.ddcFresh("HDMI-A-1", 0, false), [scdc("locked"), scdc("unlocked"), scdc("locked")], 10000, "")
        compare([r.state.state, r.state.probeSawValid], ["PROBING", true])
        var on = feed(r.state, [scdc("locked")], r.t, "")
        compare([on.state.state, on.state.mode, on.learns.length], ["WATCHING", "scdc", 0])
        // A "needs ddcutil" after an SCDC answer is only unreadable.
        var mid = feed(Model.ddcFresh("HDMI-A-1", 0, false), [scdc("locked"), { status: "no_tool" }, scdc("locked"),
                                                               scdc("locked")], 10000, "")
        compare([mid.state.state, mid.state.mode], ["WATCHING", "scdc"])
        var known = Model.ddcFresh("HDMI-A-1", 0, false)
        known.scdcCapable = true
        compare(feed(known, [{ status: "no_tool" }], 10000, "").state.state, "PROBING")
        // An SCDC sink answering unlocked at startup enters UNVERIFIED (never
        // UNSUPPORTED and never PAUSED), and starts WATCHING once lock returns.
        var away = feed(Model.ddcFresh("HDMI-A-1", 0, false), [scdc("unlocked"), scdc("unlocked")], 10000, "")
        compare(away.state.state, "PROBING")
        var waiting = feed(away.state, [scdc("unlocked")], away.t, "")
        compare([waiting.state.state, waiting.state.mode, waiting.state.scdcCapable], ["UNVERIFIED", "scdc", true])
        var stillWaiting = feed(waiting.state, [scdc("unlocked"), scdc("unlocked"), scdc("unlocked")], waiting.t, "", true)
        compare(stillWaiting.state.state, "UNVERIFIED")
        var returned = feed(stillWaiting.state, [scdc("locked")], stillWaiting.t, "")
        compare([returned.state.state, returned.state.mode], ["WATCHING", "scdc"])
        // unlocked followed by ambiguous retries keeps polling in UNVERIFIED instead of UNSUPPORTED.
        var flaky = feed(Model.ddcFresh("HDMI-A-1", 0, false), [scdc("unlocked"), amb(), amb(), amb(), amb()], 10000, "")
        compare([flaky.state.state, flaky.state.mode, flaky.state.scdcCapable], ["UNVERIFIED", "scdc", true])
        compare(feed(flaky.state, [scdc("locked")], flaky.t, "").state.state, "WATCHING")
        // No SCDC sink and no ddcutil: needs ddcutil.
        compare(feed(Model.ddcFresh("DP-1", 0, false), [{ status: "no_tool" }], 10000, "").state.state, "NO_TOOL")
    }

    function test_persistable_plan_packs_two_put_back_monitors_without_overlap() {
        var mons = [laptop(), benq(), dell()]
        var memory = Model.remember({}, Model.derive(mons, {}, {}), "t")
        var ids = Model.planFromLive(mons, memory, {})
        var benqFp = byName(ids, "HDMI-A-1").fp
        var dellFp = byName(ids, "DP-3").fp
        var unpaused = Model.derive(mons, memory, {})
        var plan = Model.derive(mons, memory, { [benqFp]: "input", [dellFp]: "input" },
                                { [benqFp]: "mirror", [dellFp]: "mirror" })
        compare([byName(plan, "HDMI-A-1").parkMirrorOf, byName(plan, "DP-3").parkMirrorOf], ["eDP-1", "eDP-1"])
        // The user widens the primary over where both paused monitors were.
        var lap = byName(plan, "eDP-1")
        lap.x = 0
        lap.y = 0
        lap.mode = { w: 5120, h: 1440, hz: 60 }
        var file = Model.persistablePlan(plan, unpaused)
        compare([byName(file, "eDP-1").x, byName(file, "eDP-1").mode.w], [0, 5120])
        var b = byName(file, "HDMI-A-1")
        var d = byName(file, "DP-3")
        verify(!(b.x === d.x && b.y === d.y))
        var rects = file.map(function(o) { return Model.rectOf(o) })
        for (var i = 0; i < rects.length; i++)
            for (var j = i + 1; j < rects.length; j++) verify(!Model.overlaps(rects[i], rects[j]), file[i].name + " / " + file[j].name)
        verify(!file.some(function(o) { return o.autoOff || o.parkMirrorOf || o.pauseMode }))
    }

    function test_scdc_and_ddc_status_texts() {
        compare(Model.ddcStatusText({ state: "WATCHING", mode: "scdc", code: "0x11" }, true), "Active · HDMI signal lock")
        compare(Model.ddcStatusText({ state: "PAUSED", mode: "scdc" }, true), "Paused · switched away or powered off")
        compare(Model.ddcStatusText({ state: "SNOOZED", mode: "scdc" }, true), "Snoozed until HDMI lock returns")
        compare(Model.ddcStatusText({ state: "WATCHING", mode: "ddc", code: "0x11" }, true), "Watching · HDMI-1 (0x11)")
        compare(Model.ddcStatusText({ state: "SNOOZED", mode: "ddc" }, true), "Snoozed until input returns")
        compare(Model.ddcStatusText({ state: "PAUSED", mode: "scdc" }, false), "Apply a layout once to enable")
    }

    function test_remember_strips_legacy_ddc_fields_and_old_memory_still_loads() {
        var fp = "BNQ|BenQ GW2790Q|ET97T00426087"
        var legacy = { version: 1, layouts: {}, monitors: {} }
        legacy.monitors[fp] = { mode: { w: 2560, h: 1440, hz: 100 }, scale: 1, vrr: 0, hdr: false,
            ddcWatch: true, ddcInput: "0x11", ddcConnector: "HDMI-A-1" }
        var plan = Model.derive([benq()], legacy, {})
        compare(plan[0].ddcWatch, undefined)
        compare(Model.planFromLive([benq()], legacy)[0].ddcWatch, undefined)
        compare(plan[0].mode, { w: 2560, h: 1440, hz: 100 })
        var saved = Model.remember(legacy, plan, "t")
        compare(saved.monitors[fp].ddcWatch, undefined)
        compare(saved.monitors[fp].ddcInput, undefined)
        compare(saved.monitors[fp].ddcConnector, undefined)
        // An off or paused monitor keeps its profile, minus the old fields.
        var two = Model.derive([laptop(), benq()], legacy, { [fp]: "input" })
        var kept = Model.remember(legacy, two, "t2")
        compare(kept.monitors[fp].mode, { w: 2560, h: 1440, hz: 100 })
        compare(kept.monitors[fp].ddcInput, undefined)
        compare(legacy.monitors[fp].ddcInput, "0x11")
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

    // ------------------------------------------------ Round 2 regressions
    function test_live_user_mirror_blocks_a_pause_and_is_never_claimed_by_a_pending_pause() {
        var extended = [laptop(), benq(), dell(2560)]
        var memory = Model.remember({}, Model.derive(extended, {}, {}), "t")
        var ids = Model.planFromLive(extended, memory, {})
        var benqFp = byName(ids, "HDMI-A-1").fp
        // The user mirrors the SCDC-capable BenQ onto the panel live; the saved
        // layout still has it extended, so the plan alone shows no mirror.
        var live = [laptop(), benq(), dell(2560)]
        live[0].id = 0
        live[1].id = 1
        live[2].id = 2
        live[1].mirrorOf = "0"
        var plan = Model.derive(live, memory, {}, {}, {})
        compare(byName(plan, "HDMI-A-1").mirrorOf, "")
        verify(Model.ddcCanPause(plan, benqFp, false, {}, extended, {}))
        verify(!Model.ddcCanPause(plan, benqFp, false, {}, live, {}))
        verify(!Model.ddcCanPause(plan, benqFp, false, {}, live, { "HDMI-A-1": ["DP-3"] }))
        // Only the bar's own recorded park on that target is not the user's.
        verify(Model.ddcCanPause(plan, benqFp, false, {}, live, { "HDMI-A-1": ["eDP-1"] }))
        // Another monitor mirroring it live (the user's) rules a pause out too...
        var onto = [laptop(), benq(), dell(2560)]
        onto[2].mirrorOf = "HDMI-A-1"
        verify(!Model.ddcCanPause(plan, benqFp, false, {}, onto, {}))
        // ...unless that mirror is the bar's park; a live mirror is never lit
        // on its own, so with the lid shut nothing else counts.
        verify(Model.ddcCanPause(plan, benqFp, false, {}, onto, { "DP-3": ["HDMI-A-1"] }))
        var dellMirror = [laptop(), benq(), dell(2560)]
        dellMirror[2].mirrorOf = "eDP-1"
        verify(Model.ddcCanPause(plan, benqFp, true, {}, extended, {}))
        verify(!Model.ddcCanPause(plan, benqFp, true, {}, dellMirror, {}))
        // Even pending a mirror pause (PAUSED, SCDC), the live mirror is never
        // taken for a park without an explicit record.
        var modes = { [benqFp]: "mirror" }
        var off = { [benqFp]: "input" }
        compare(Model.liveParkOf("HDMI-A-1", "eDP-1", {}), "")
        compare(Model.liveParkOf("HDMI-A-1", "eDP-1", { "HDMI-A-1": ["DP-3"] }), "")
        compare(Model.liveParkOf("HDMI-A-1", "eDP-1", { "HDMI-A-1": ["DP-3", "eDP-1"] }), "eDP-1")
        compare(Model.liveParkOf("DP-3", "eDP-1", { "HDMI-A-1": ["eDP-1"] }), "")
        var lb = byName(Model.planFromLive(live, memory, off, modes, {}), "HDMI-A-1")
        compare([lb.mirrorOf, lb.parkMirrorOf, lb.autoOff], ["eDP-1", "", false])
        compare(byName(Model.derive(live, {}, {}, modes, {}), "HDMI-A-1").mirrorOf, "eDP-1")
    }

    function test_failed_repark_never_loses_the_old_bar_owned_park() {
        var mons = [laptop(), benq(), dell(2560)]
        var memory = Model.remember({}, Model.derive(mons, {}, {}), "t")
        var benqFp = byName(Model.planFromLive(mons, memory, {}), "HDMI-A-1").fp
        var off = { [benqFp]: "input" }
        var modes = { [benqFp]: "mirror" }
        var first = Model.derive(mons, memory, off, modes)
        compare(byName(first, "HDMI-A-1").parkMirrorOf, "eDP-1")
        var parks = Model.recordAppliedParks({}, first)
        compare(parks, { "HDMI-A-1": ["eDP-1"] })
        // A repark onto DP-3, recorded before the apply runs: both targets stay
        // the bar's (deduplicated, never replaced), whatever the apply does.
        var repark = Model.settleParkedMirrors(first)
        byName(repark, "HDMI-A-1").parkMirrorOf = "DP-3"
        var both = Model.recordAppliedParks(parks, repark)
        compare(both, { "HDMI-A-1": ["eDP-1", "DP-3"] })
        compare(Model.recordAppliedParks(both, repark), both)
        compare(parks, { "HDMI-A-1": ["eDP-1"] })
        compare(Model.recordAppliedParks({ "HDMI-A-1": "eDP-1" }, []), { "HDMI-A-1": ["eDP-1"] })
        // The apply failed: Hyprland still shows the old park. Readback narrows
        // to exactly that one, so it is still the bar's.
        var oldLive = [laptop(), benq(), dell(2560)]
        oldLive[0].id = 0
        oldLive[1].id = 1
        oldLive[2].id = 2
        oldLive[1].mirrorOf = "0"
        var kept = Model.pruneAppliedParks(both, oldLive)
        compare([kept.changed, kept.parks], [true, { "HDMI-A-1": ["eDP-1"] }])
        compare(Model.pruneAppliedParks(kept.parks, oldLive).changed, false)
        var livePlan = Model.planFromLive(oldLive, memory, off, modes, kept.parks)
        var lb = byName(livePlan, "HDMI-A-1")
        compare([lb.mirrorOf, lb.parkMirrorOf, lb.autoOff], ["", "eDP-1", true])
        // Never leaks into monitors.lua or displays.json as a mirror.
        var file = Model.persistablePlan(livePlan, Model.derive(oldLive, memory, {}, {}, kept.parks))
        compare([byName(file, "HDMI-A-1").mirrorOf, byName(file, "HDMI-A-1").parkMirrorOf], ["", ""])
        var saved = Model.remember(memory, Model.settleParkedMirrors(livePlan), "t2")
        for (var key in saved.layouts) {
            var entry = saved.layouts[key].outputs[benqFp]
            verify(!entry || (entry.mirrorOf === undefined && entry.mirrorTargetFp === undefined))
        }
        compare(byName(Model.derive(oldLive, {}, {}, {}, kept.parks), "HDMI-A-1").mirrorOf, "")
        // The repark took: the new target is the live park.
        var newLive = [laptop(), benq(), dell(2560)]
        newLive[1].mirrorOf = "DP-3"
        compare(Model.pruneAppliedParks(both, newLive).parks, { "HDMI-A-1": ["DP-3"] })
        // A mirror the bar never applied is the user's: ownership is dropped.
        compare(Model.pruneAppliedParks(kept.parks, newLive).parks, {})
    }

    function test_dpms_probe_runs_while_one_monitor_sleeps_on_its_own() {
        var watching = scdcWatching("HDMI-A-1").state
        var snoozed = Model.ddcFresh("DP-3", 0, false)
        snoozed.state = "SNOOZED"
        snoozed.mode = "ddc"
        var states = { benq: watching, dell: snoozed }
        // The laptop stays on while the BenQ sleeps by itself: probe for its wake.
        verify(Model.ddcNeedsDpmsProbe(states, { "eDP-1": true, "HDMI-A-1": false, "DP-3": true }, false))
        verify(Model.ddcNeedsDpmsProbe({ dell: snoozed }, { "eDP-1": true, "DP-3": false }, false))
        // A slow (snoozed, DDC) monitor never stalls the probe past 5 s.
        compare(Model.ddcPollInterval({ dell: snoozed }, 0), 15000)
        compare(Model.ddcPollInterval({ dell: snoozed }, 0, { "eDP-1": true, "DP-3": false }, false), 5000)
        compare(Model.ddcPollInterval({}, 0, {}, true), 5000)
        verify(Model.ddcNeedsDpmsProbe({}, {}, true))
        // It woke (liveDpms back on): no probe, the normal pace returns.
        verify(!Model.ddcNeedsDpmsProbe(states, { "eDP-1": true, "HDMI-A-1": true, "DP-3": true }, false))
        compare(Model.ddcPollInterval({ dell: snoozed }, 0, { "eDP-1": true, "DP-3": true }, false), 15000)
        // A paused or stopped monitor never needs it, nor does an unknown one.
        var paused = Model.ddcCopyState(watching)
        paused.state = "PAUSED"
        var gone = Model.ddcCopyState(snoozed)
        gone.state = "UNSUPPORTED"
        verify(!Model.ddcNeedsDpmsProbe({ p: paused, g: gone }, { "HDMI-A-1": false, "DP-3": false }, false))
        verify(!Model.ddcNeedsDpmsProbe(states, {}, false))
        // Held while asleep, then unheld with a fresh grace the moment it wakes.
        var held = Model.ddcHold({ benq: watching }, { benq: false }, 1000)
        verify(held.states.benq.held)
        var back = Model.ddcHold(held.states, { benq: true }, 7000)
        compare([back.states.benq.held, back.states.benq.readyAt], [false, 7000])
        // A regular batch reporting dpms_off marks the monitor held right away,
        // so a refresh seeing it awake starts a fresh 10s grace and blocks a
        // re-pause 5 seconds later.
        var sw = scdcWatching("HDMI-A-1")
        var offObs = Model.ddcObserve({ benq: sw.state }, "benq", { status: "dpms_off" }, { now: 20000, canPause: true })
        verify(offObs.states.benq.held)
        var awakeHold = Model.ddcHold(offObs.states, { benq: true }, 21000)
        compare([awakeHold.states.benq.held, awakeHold.states.benq.readyAt], [false, 21000])
        var tooSoon = feed(awakeHold.states.benq, [scdc("unlocked"), scdc("unlocked"), scdc("unlocked")], 22000, "", true, 2000)
        compare(tooSoon.state.state, "WATCHING")
    }

    function test_forget_persists_the_full_unpaused_plan_for_the_next_memory() {
        var mons = [laptop(), benq(), dell(2560)]
        var memory = Model.remember({}, Model.derive(mons, {}, {}), "t")
        var ids = Model.planFromLive(mons, memory, {})
        var benqFp = byName(ids, "HDMI-A-1").fp
        var laptopFp = byName(ids, "eDP-1").fp
        // The BenQ is paused (DDC, dark): Hyprland has the others packed.
        var paused = Model.derive(mons, memory, { [benqFp]: "input" }, { [benqFp]: "disable" })
        var live = mons.map(function(m) {
            var c = {}
            for (var k in m) c[k] = m[k]
            var o = byName(paused, m.name)
            c.x = o.x
            c.y = o.y
            if (m.name === "HDMI-A-1") c.disabled = true
            return c
        })
        var livePlan = Model.planFromLive(live, memory, { [benqFp]: "input" }, { [benqFp]: "disable" }, {})
        // Forget the panel: what DisplayService.unpausedPlan(nextMemory) persists.
        var nextMemory = Model.forgetMonitor(memory, laptopFp)
        verify(Model.hasMemory(nextMemory))
        var unpausedNext = Model.derive(live, nextMemory, Model.autoOffFor(Model.planFromLive(live, nextMemory, {}), false, {}), {}, {})
        verify(!unpausedNext.some(function(o) { return o.autoOff || o.parkMirrorOf || o.pauseMode || o.mirrorOf }))
        var rects = unpausedNext.map(function(o) { return Model.rectOf(o) })
        for (var i = 0; i < rects.length; i++)
            for (var j = i + 1; j < rects.length; j++) verify(!Model.overlaps(rects[i], rects[j]))
        // The old file plan mixed the packed live geometry of awake outputs
        // with the post-Forget layout of the paused one.
        var mixed = Model.persistablePlan(livePlan, unpausedNext)
        verify(byName(mixed, "DP-3").x !== byName(unpausedNext, "DP-3").x
               || byName(mixed, "HDMI-A-1").x !== byName(unpausedNext, "HDMI-A-1").x)
        compare([byName(unpausedNext, "HDMI-A-1").x, byName(unpausedNext, "DP-3").x], [0, 2560])
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
