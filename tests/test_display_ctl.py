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
        rule = ctl.monitor_rule(output(hdr=True, vrr=2, scale=1.25, x=1920, y=-200))
        self.assertEqual(rule, 'hl.monitor({ output = "desc:BNQ BenQ GW2790Q ET97T00426087", '
                               'disabled = false, mode = "2560x1440@100.00", position = "1920x-200", '
                               'scale = 1.25, transform = 0, vrr = 2, cm = "hdr", bitdepth = 10 })')
        self.assertIn('cm = "srgb", bitdepth = 8', ctl.monitor_rule(output()))

    def test_disabled_rule(self):
        self.assertEqual(ctl.monitor_rule(output(enabled=False)),
                         'hl.monitor({ output = "desc:BNQ BenQ GW2790Q ET97T00426087", disabled = true })')

    def test_validation_rejects_bad_values(self):
        for bad in ({"x": 1.5}, {"scale": 0}, {"vrr": 3}, {"transform": True},
                    {"mode": {"w": "2560; os.exit()", "h": 1440, "hz": 60}}, {"selector": "a\nb"}):
            with self.subTest(bad=bad), self.assertRaises((ValueError, TypeError)):
                output(**bad)

    def test_plan_must_leave_a_display_on(self):
        with self.assertRaises(ValueError):
            ctl.validate_plan([dict(output(enabled=False))])
        with self.assertRaises(ValueError):
            ctl.validate_plan([dict(output(autoOff=True))])

    def test_live_rules_skip_auto_off_and_turn_monitors_on_first(self):
        lua = ctl.live_lua([output(**LAPTOP, enabled=False), output(), output(name="DP-3", selector="DP-3", autoOff=True)])
        lines = lua.split("\n")
        self.assertEqual(len(lines), 2)
        self.assertIn("desc:BNQ", lines[0])
        self.assertIn('output = "eDP-1", disabled = true', lines[1])


    def test_input_switched_monitor_is_turned_off_live_but_lid_is_left_alone(self):
        lua = ctl.live_lua([output(**LAPTOP, autoOff=True, enabled=False, offReason="lid"),
                            output(autoOff=True, offReason="input")])
        self.assertEqual(lua, 'hl.monitor({ output = "desc:BNQ BenQ GW2790Q ET97T00426087", disabled = true })')
        with self.assertRaises(ValueError):
            output(offReason="sleep")


class MonitorsLuaTest(unittest.TestCase):
    def test_takes_over_the_foreign_block_and_keeps_its_other_settings(self):
        text = ctl.splice_block(FOREIGN, [output(), output(**LAPTOP)])
        self.assertNotIn("im0001gt.screens", text)
        self.assertIn(ctl.TAKEOVER_HEADER, text)
        self.assertNotIn("PROBE-1", text)
        self.assertIn('hl.env("GDK_SCALE"', text)
        block = text[text.index(ctl.BEGIN):text.index(ctl.END)]
        self.assertIn("hl.config({ misc = { vrr = 0 }, render = { cm_auto_hdr = 0 } })", block)
        self.assertIn('output = "eDP-1", disabled = false, mode = "1920x1080@60.02", position = "313x1440"', block)

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


class PersistTest(unittest.TestCase):
    def test_persist_writes_memory_and_learns_watched_inputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            lua = Path(tmp) / "hypr" / "monitors.lua"
            env = {"XDG_STATE_HOME": str(Path(tmp) / "state"), "DICEY_DISPLAYS_MONITORS_LUA": str(lua)}
            fp = "BNQ|BenQ GW2790Q|ET97T00426087"
            plan = [dict(output(ddcWatch=True))]
            memory = {"version": 1, "monitors": {fp: {"ddcWatch": True}}, "layouts": {}}
            with mock.patch.dict(os.environ, env), \
                    mock.patch.object(ctl, "ddc_poll", return_value={"HDMI-A-1": "0x11"}) as poll:
                result = ctl.persist({"plan": plan, "memory": memory})
                saved = json.loads((Path(tmp) / "state" / "dicey-bar" / "displays.json").read_text())
                poll.assert_called_once_with(["HDMI-A-1"])
                # Already learned: no second read.
                ctl.persist({"plan": plan, "memory": saved})
                poll.assert_called_once()
            self.assertTrue(result["monitorsLuaChanged"])
            self.assertEqual(saved["monitors"][fp]["ddcInput"], "0x11")
            self.assertEqual(oct((Path(tmp) / "state" / "dicey-bar" / "displays.json").stat().st_mode & 0o777), "0o600")
            self.assertIn(ctl.BEGIN, lua.read_text())

    def test_cli_reports_invalid_input_as_json(self):
        result = subprocess.run([sys.executable, "-I", str(HELPER), "apply"], input="[]",
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 1)
        self.assertFalse(json.loads(result.stdout)["ok"])


if __name__ == "__main__":
    unittest.main()
