"""The Displays helper's Lua generation, monitors.lua splicing and parsers."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HELPER = Path(__file__).resolve().parents[1] / "displays" / "display_ctl.py"
spec = importlib.util.spec_from_file_location("display_ctl", HELPER)
ctl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ctl)


def output(**overrides):
    base = {
        "name": "HDMI-A-1", "fp": "BNQ|BenQ GW2790Q|ET97T00426087",
        "selector": "desc:BNQ BenQ GW2790Q ET97T00426087",
        "enabled": True, "autoOff": False, "mode": {"w": 2560, "h": 1440, "hz": 100},
        "x": 0, "y": 0, "scale": 1, "vrr": 0, "hdr": False, "transform": 0,
    }
    base.update(overrides)
    return ctl.validate_output(base)


LAPTOP = dict(name="eDP-1", fp="LG Display|0x0753|", selector="eDP-1",
              mode={"w": 1920, "h": 1080, "hz": 60.02}, x=313, y=1440)

FOREIGN = """-- See https://wiki.hypr.land/Configuring/Basics/Monitors/
-- Managed by im0001gt.screens (Screens bar panel).

local omarchy_gdk_scale = 1
hl.env("GDK_SCALE", tostring(omarchy_gdk_scale))

-- BEGIN im0001gt.screens
hl.monitor({ output = "desc:BNQ BenQ GW2790Q ET97T00426087", mode = "2560x1440@100.00", position = "0x0", scale = 1, vrr = 0 })
hl.monitor({ output = "PROBE-1", mode = "1600x900@60", position = "4000x0", scale = 1.667, vrr = 0 })
hl.monitor({ output = "", mode = "preferred", position = "auto", scale = "auto" })
hl.config({ misc = { vrr = 0 }, render = { cm_auto_hdr = 0 } })
-- END im0001gt.screens
"""


class LuaTest(unittest.TestCase):
    def test_strings_cannot_break_out_of_the_literal(self):
        hostile = 'x" }) os.execute("rm -rf ~") hl.monitor({ output = "\n\\'
        literal = ctl.lua_string(hostile)
        self.assertTrue(literal.startswith('"') and literal.endswith('"'))
        inner = literal[1:-1]
        self.assertNotIn('"', inner)
        self.assertNotIn("\n", inner)
        self.assertNotIn("\\\\", inner.replace("\\092", ""))
        self.assertIn("\\034", inner)

    def test_utf8_is_escaped_bytewise(self):
        self.assertEqual(ctl.lua_string("é"), '"\\195\\169"')

    def test_rule_spells_out_every_field(self):
        rule = ctl.monitor_rule(output(hdr=True, vrr=2, scale=1.25, x=1920, y=-200,
                                       transform=1, sdrBrightness=1.35, sdrSaturation=1.1))
        self.assertEqual(rule, 'hl.monitor({ output = "desc:BNQ BenQ GW2790Q ET97T00426087", '
                               'disabled = false, mode = "2560x1440@100.00", position = "1920x-200", '
                               'scale = 1.25, transform = 1, vrr = 2, cm = "hdr", bitdepth = 10, '
                               'sdrbrightness = 1.35, sdrsaturation = 1.1, mirror = "" })')
        sdr_rule = ctl.monitor_rule(output(sdrBrightness=1.5))
        self.assertIn('cm = "srgb", bitdepth = 8, sdrbrightness = 1, sdrsaturation = 1, mirror = ""', sdr_rule)
        mirror_rule = ctl.monitor_rule(output(mirrorOf="eDP-1"))
        self.assertIn('mirror = "eDP-1"', mirror_rule)
        connector_rule = ctl.monitor_rule(output(), use_connector_name=True)
        self.assertIn('output = "HDMI-A-1"', connector_rule)
        self.assertNotIn("desc:", connector_rule)

    def test_disabled_rule(self):
        # Disabled rules always address exact connector name so desc: prefix
        # matching can never disable a newly hotplugged display.
        self.assertEqual(ctl.monitor_rule(output(enabled=False)),
                         'hl.monitor({ output = "HDMI-A-1", disabled = true })')
        # Unprofiled monitors omit `vrr` so Hyprland preserves fullscreen VRR policy.
        unmanaged = ctl.monitor_rule(output(vrrManaged=False))
        self.assertNotIn("vrr =", unmanaged)

    def test_validation_rejects_bad_values(self):
        for bad in ({"x": 1.5}, {"scale": 0}, {"vrr": 3}, {"transform": True},
                    {"sdrBrightness": 3.0}, {"sdrSaturation": 0.1}, {"mirrorOf": "a\nb"},
                    {"mode": {"w": "2560; os.exit()", "h": 1440, "hz": 60}}, {"selector": "a\nb"}):
            with self.subTest(bad=bad), self.assertRaises((ValueError, TypeError)):
                output(**bad)

    def test_plan_must_leave_a_display_on(self):
        with self.assertRaises(ValueError):
            ctl.validate_plan([dict(output(enabled=False))])
        with self.assertRaises(ValueError):
            ctl.validate_plan([dict(output(autoOff=True))])
        with self.assertRaises(ValueError):
            ctl.validate_plan([dict(output(mirrorOf="HDMI-A-1"))])
        with self.assertRaises(ValueError):
            ctl.validate_plan([
                dict(output(**LAPTOP)),
                dict(output(mirrorOf="eDP-1")),
                dict(output(name="DP-3", selector="DP-3", mirrorOf="HDMI-A-1")),
            ])

    def test_live_rules_order_primary_then_mirrors_then_disabled(self):
        lua = ctl.live_lua([
            output(**LAPTOP, enabled=False),
            output(name="DP-3", selector="DP-3", mirrorOf="HDMI-A-1"),
            output(),
            output(name="DP-4", selector="DP-4", autoOff=True),
        ])
        lines = lua.split("\n")
        self.assertEqual(len(lines), 3)
        # Live rules always address exact connector names, never prefix-matched desc:
        self.assertIn('output = "HDMI-A-1"', lines[0])
        self.assertNotIn("desc:", lines[0])
        self.assertIn('mirror = ""', lines[0])
        self.assertIn('output = "DP-3"', lines[1])
        self.assertIn('mirror = "HDMI-A-1"', lines[1])
        self.assertIn('output = "eDP-1", disabled = true', lines[2])


    def test_input_switched_monitor_is_turned_off_live_but_lid_is_left_alone(self):
        lua = ctl.live_lua([output(**LAPTOP, autoOff=True, enabled=False, offReason="lid"),
                            output(autoOff=True, offReason="input")])
        self.assertEqual(lua, 'hl.monitor({ output = "HDMI-A-1", disabled = true })')
        with self.assertRaises(ValueError):
            output(offReason="sleep")

    def test_safe_revert_plan_never_disables_last_connected_monitor(self):
        # Start with A on and B disabled; preview enabling B, then A is unplugged!
        previous_plan = [
            output(name="DP-1", selector="DP-1", enabled=True),
            output(name="HDMI-A-1", selector="HDMI-A-1", enabled=False),
        ]
        only_b_connected = [{"name": "HDMI-A-1", "width": 2560, "height": 1440, "refreshRate": 100}]
        safe = ctl.safe_revert_plan(previous_plan, only_b_connected, lid_is_closed=False)
        self.assertEqual(len(safe), 1)
        self.assertEqual(safe[0]["name"], "HDMI-A-1")
        self.assertTrue(safe[0]["enabled"])
        self.assertEqual(safe[0]["mirrorOf"], "")

        # Completely replaced disabled monitor reporting 0x0 still yields preferred mode
        replacement = [{"name": "DP-9", "id": 3, "width": 0, "height": 0, "refreshRate": 0, "disabled": True}]
        safe_rep = ctl.safe_revert_plan(previous_plan, replacement, lid_is_closed=False)
        self.assertEqual(len(safe_rep), 1)
        self.assertEqual(safe_rep[0]["name"], "DP-9")
        self.assertTrue(safe_rep[0]["enabled"])
        self.assertIsNone(safe_rep[0]["mode"])
        self.assertIn('mode = "preferred"', ctl.monitor_rule(safe_rep[0]))

        # Numeric mirrorOf ID ("0") resolves against connected monitor id=0 -> "eDP-1"
        mirrored_prev = [
            output(**LAPTOP, enabled=True),
            output(name="HDMI-A-1", selector="HDMI-A-1", enabled=True, mirrorOf="0"),
        ]
        live_both = [
            {"id": 0, "name": "eDP-1", "width": 1920, "height": 1080, "refreshRate": 60},
            {"id": 1, "name": "HDMI-A-1", "width": 2560, "height": 1440, "refreshRate": 100},
        ]
        safe_mir = ctl.safe_revert_plan(mirrored_prev, live_both)
        self.assertEqual(safe_mir[1]["mirrorOf"], "eDP-1")


class MonitorsLuaTest(unittest.TestCase):
    def test_takes_over_the_foreign_block_and_keeps_its_other_settings(self):
        text = ctl.splice_block(FOREIGN, [output(), output(**LAPTOP)])
        self.assertNotIn("im0001gt.screens", text)
        self.assertIn(ctl.TAKEOVER_HEADER, text)
        self.assertNotIn("PROBE-1", text)
        self.assertIn('hl.env("GDK_SCALE"', text)
        # Carried non-monitor config is hoisted OUTSIDE our managed block so
        # later rewrites can never drop or corrupt it.
        before_block = text[:text.index(ctl.BEGIN)]
        block = text[text.index(ctl.BEGIN):text.index(ctl.END)]
        self.assertIn("hl.config({ misc = { vrr = 0 }, render = { cm_auto_hdr = 0 } })", before_block)
        self.assertNotIn("hl.config(", block)
        self.assertIn('output = "eDP-1", disabled = false, mode = "1920x1080@60.02", position = "313x1440"', block)

    def test_multiline_foreign_takeover_and_double_splice_stay_valid_lua(self):
        multiline_foreign = """-- Managed by im0001gt.screens
-- BEGIN im0001gt.screens
hl.monitor({
    output = "DP-1",
    mode = "1920x1080@60",
    position = "0x0",
})
hl.config({
    misc = { vrr = 0 },
    render = { cm_auto_hdr = 0 },
})
hl.workspace_rule({ workspace = 1, monitor = "DP-1" })
hl.env("TEST_FLAG", "foo --bar hl.monitor({})")
-- END im0001gt.screens
"""
        first = ctl.splice_block(multiline_foreign, [output()])
        second = ctl.splice_block(first, [output(x=1920)])
        self.assertNotIn('output = "DP-1"', second)
        self.assertIn("hl.config({", second)
        self.assertIn("render = { cm_auto_hdr = 0 }", second)
        self.assertIn("hl.workspace_rule(", second)
        self.assertIn('hl.env("TEST_FLAG", "foo --bar hl.monitor({})")', second)
        self.assertIn('position = "1920x0"', second)
        ctl.validate_lua_syntax(second)

    def test_stray_begin_marker_does_not_swallow_user_lines(self):
        with_stray = (
            f"{ctl.BEGIN}\n"
            'hl.env("KEEP_ME", "1")\n'
            f"{ctl.BEGIN}\n"
            f"{ctl.CATCH_ALL}\n"
            f"{ctl.END}\n"
        )
        updated = ctl.splice_block(with_stray, [output()])
        self.assertIn('hl.env("KEEP_ME", "1")', updated)

    def test_rewrites_its_own_block_in_place(self):
        first = ctl.splice_block(FOREIGN, [output()])
        second = ctl.splice_block(first + "\n-- user line\n", [output(x=1920)])
        self.assertEqual(second.count(ctl.BEGIN), 1)
        self.assertIn('position = "1920x0"', second)
        self.assertIn("-- user line", second)
        self.assertEqual(second.count("hl.config("), 1)

    def test_appends_to_a_file_without_a_block(self):
        text = ctl.splice_block('hl.monitor({ output = "", mode = "preferred" })\n', [output()])
        self.assertTrue(text.startswith('hl.monitor({ output = "", mode = "preferred" })\n\n' + ctl.BEGIN))

    def test_auto_off_monitor_is_written_as_on(self):
        text = ctl.splice_block("", [output(), output(**LAPTOP, autoOff=True, enabled=False)])
        self.assertIn('output = "eDP-1", disabled = false', text)

    def test_write_backs_up_once_and_skips_unchanged_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "monitors.lua"
            path.write_text(FOREIGN)
            self.assertTrue(ctl.write_monitors_lua([output()], path))
            backup = path.with_name("monitors.lua.dicey-bar-backup")
            self.assertEqual(backup.read_text(), FOREIGN)
            self.assertFalse(ctl.write_monitors_lua([output()], path))
            self.assertTrue(ctl.write_monitors_lua([output(x=5)], path))
            self.assertEqual(backup.read_text(), FOREIGN)

    def test_refuses_to_write_through_a_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "real.lua"
            target.write_text("")
            link = Path(tmp) / "monitors.lua"
            link.symlink_to(target)
            with self.assertRaises(ValueError):
                ctl.write_monitors_lua([output()], link)


class ParserTest(unittest.TestCase):
    def test_edid_hdr_static_metadata_block(self):
        edid = bytearray(256)
        edid[126] = 1
        ext = edid[128:]
        ext[0], ext[1], ext[2] = 0x02, 0x03, 8
        ext[4], ext[5], ext[6], ext[7] = (7 << 5) | 3, 6, 0x05, 0x01
        edid[128:] = ext
        self.assertTrue(ctl.edid_hdr_capable(bytes(edid)))
        ext[5] = 5  # colorimetry block, not HDR
        edid[128:] = ext
        self.assertFalse(ctl.edid_hdr_capable(bytes(edid)))
        self.assertFalse(ctl.edid_hdr_capable(bytes(128)))

    def test_ddc_detect_and_input_source(self):
        detect = """Display 1
   I2C bus:          /dev/i2c-3
   DRM connector:    card1-HDMI-A-1
   Monitor:          BNQ:BenQ GW2790Q:ET97T00426087

Invalid display
   I2C bus:          /dev/i2c-11
   DRM connector:    card1-eDP-1
"""
        self.assertEqual(ctl.parse_ddc_detect(detect), {"HDMI-A-1": "3", "eDP-1": "11"})
        self.assertEqual(ctl.parse_vcp_input("VCP 60 SNC x11\n"), "0x11")
        self.assertEqual(ctl.parse_vcp_input("VCP 60 SNC x0F"), "0x0f")
        self.assertIsNone(ctl.parse_vcp_input("VCP 60 ERR"))

    def test_nightlight_parser(self):
        self.assertEqual(ctl.parse_hyprsunset_temp("4000\n"), 4000)
        self.assertIsNone(ctl.parse_hyprsunset_temp("Couldn't connect"))


class PersistTest(unittest.TestCase):
    def test_persist_memory_only_skips_monitors_lua(self):
        with tempfile.TemporaryDirectory() as tmp:
            lua = Path(tmp) / "hypr" / "monitors.lua"
            env = {"XDG_STATE_HOME": str(Path(tmp) / "state"), "DICEY_DISPLAYS_MONITORS_LUA": str(lua)}
            memory = {"version": 1, "monitors": {}, "layouts": {}}
            with mock.patch.dict(os.environ, env):
                result = ctl.persist({"memory": memory})
            self.assertTrue(result["ok"])
            self.assertFalse(result["monitorsLuaChanged"])
            self.assertFalse(lua.exists())
            self.assertEqual(json.loads((Path(tmp) / "state" / "dicey-bar" / "displays.json").read_text()), memory)

    def test_ddc_relearn_requires_saved_watch_and_updates_memory(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {"XDG_STATE_HOME": str(Path(tmp) / "state")}
            fp = "BNQ|BenQ GW2790Q|ET97T00426087"
            with mock.patch.dict(os.environ, env), \
                    mock.patch.object(ctl, "ddc_poll", return_value={"HDMI-A-1": "0x0f"}):
                # Refuses before ddcWatch is actually applied & saved
                denied = ctl.ddc_relearn("HDMI-A-1", fp)
                self.assertFalse(denied["ok"])
                # Once ddcWatch is saved, relearn updates ddcInput cleanly
                ctl.persist({"memory": {"version": 1, "monitors": {fp: {"ddcWatch": True}}, "layouts": {}}})
                result = ctl.ddc_relearn("HDMI-A-1", fp)
                saved = json.loads((Path(tmp) / "state" / "dicey-bar" / "displays.json").read_text())
            self.assertTrue(result["ok"])
            self.assertEqual(result["ddcInput"], "0x0f")
            self.assertEqual(saved["monitors"][fp]["ddcInput"], "0x0f")
            self.assertEqual(saved["monitors"][fp]["ddcConnector"], "HDMI-A-1")

    def test_cached_buses_rate_limits_detect_for_powered_off_monitor(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {"XDG_RUNTIME_DIR": tmp}
            with mock.patch.dict(os.environ, env), \
                    mock.patch.object(ctl, "ddc_buses", return_value={"HDMI-A-1": "3"}) as detect:
                ctl.cached_buses(["HDMI-A-1", "DP-2"], now=1000.0)
                # DP-2 is missing (powered off); polling again 5s later must NOT re-run detect
                ctl.cached_buses(["HDMI-A-1", "DP-2"], now=1005.0)
                self.assertEqual(detect.call_count, 1)
                # After 60s, a fresh detect is allowed
                ctl.cached_buses(["HDMI-A-1", "DP-2"], now=1065.0)
                self.assertEqual(detect.call_count, 2)

    def test_ddc_poll_keeps_bus_across_transient_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {"XDG_RUNTIME_DIR": tmp}
            with mock.patch.dict(os.environ, env), \
                    mock.patch.object(ctl, "ddc_buses", return_value={"HDMI-A-1": "3"}) as detect, \
                    mock.patch.object(ctl, "ddc_input", side_effect=[None, "0x11"]) as read:
                r1 = ctl.ddc_poll(["HDMI-A-1"], now=1000.0)
                # Next poll 5s later must still use cached bus "3" and recover immediately
                r2 = ctl.ddc_poll(["HDMI-A-1"], now=1005.0)
            self.assertEqual(r1, {"HDMI-A-1": None})
            self.assertEqual(r2, {"HDMI-A-1": "0x11"})
            self.assertEqual(detect.call_count, 1)
            self.assertEqual(read.call_count, 2)

    def test_persist_aborts_before_memory_write_if_monitors_lua_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "real.lua"
            target.write_text("")
            link = Path(tmp) / "monitors.lua"
            link.symlink_to(target)
            state_home = Path(tmp) / "state"
            env = {"XDG_STATE_HOME": str(state_home), "DICEY_DISPLAYS_MONITORS_LUA": str(link)}
            with mock.patch.dict(os.environ, env):
                with self.assertRaises(ValueError):
                    ctl.persist({"plan": [dict(output())], "memory": {"version": 1, "monitors": {"x": {}}, "layouts": {}}})
            self.assertFalse((state_home / "dicey-bar" / "displays.json").exists())

    def test_persist_writes_memory_and_learns_watched_inputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            lua = Path(tmp) / "hypr" / "monitors.lua"
            env = {"XDG_STATE_HOME": str(Path(tmp) / "state"), "DICEY_DISPLAYS_MONITORS_LUA": str(lua)}
            fp = "BNQ|BenQ GW2790Q|ET97T00426087"
            plan = [dict(output(ddcWatch=True))]
            memory = {"version": 1, "monitors": {fp: {"ddcWatch": True}}, "layouts": {}}
            with mock.patch.dict(os.environ, env), \
                    mock.patch.object(ctl, "ddc_poll", side_effect=[{"HDMI-A-1": "0x11"}, {"DP-1": "0x0f"}]) as poll:
                result = ctl.persist({"plan": plan, "memory": memory})
                saved = json.loads((Path(tmp) / "state" / "dicey-bar" / "displays.json").read_text())
                self.assertEqual(saved["monitors"][fp]["ddcInput"], "0x11")
                self.assertEqual(saved["monitors"][fp]["ddcConnector"], "HDMI-A-1")
                poll.assert_called_once_with(["HDMI-A-1"], force_refresh=True)
                # Already learned on same connector: no second read.
                ctl.persist({"plan": plan, "memory": saved})
                self.assertEqual(poll.call_count, 1)
                # Same monitor moved to a different PC port (DP-1): auto-relearns input!
                moved_plan = [dict(output(name="DP-1", ddcWatch=True))]
                ctl.persist({"plan": moved_plan, "memory": saved})
                saved_moved = json.loads((Path(tmp) / "state" / "dicey-bar" / "displays.json").read_text())
                self.assertEqual(poll.call_count, 2)
                poll.assert_called_with(["DP-1"], force_refresh=True)
            self.assertTrue(result["monitorsLuaChanged"])
            self.assertEqual(saved_moved["monitors"][fp]["ddcInput"], "0x0f")
            self.assertEqual(saved_moved["monitors"][fp]["ddcConnector"], "DP-1")
            self.assertEqual(oct((Path(tmp) / "state" / "dicey-bar" / "displays.json").stat().st_mode & 0o777), "0o600")
            self.assertIn(ctl.BEGIN, lua.read_text())

    def test_cli_reports_invalid_input_as_json(self):
        result = subprocess.run([sys.executable, "-I", str(HELPER), "apply"], input="[]",
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 1)
        self.assertFalse(json.loads(result.stdout)["ok"])


if __name__ == "__main__":
    unittest.main()
