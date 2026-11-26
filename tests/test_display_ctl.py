"""The Displays helper's Lua generation, monitors.lua splicing and parsers."""
import contextlib
import errno
import importlib.util
import io
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
        # 0x00 is what sleeping / no-signal monitors report; never an input.
        self.assertIsNone(ctl.parse_vcp_input("VCP 60 SNC x00"))
        self.assertIsNone(ctl.parse_vcp_input("VCP 60 SNC x0"))
        self.assertEqual(ctl.parse_vcp_input("VCP 60 SNC x51"), "0x51")
        self.assertIsNone(ctl.parse_vcp_input(""))

    def test_nightlight_parser(self):
        self.assertEqual(ctl.parse_hyprsunset_temp("4000\n"), 4000)
        self.assertIsNone(ctl.parse_hyprsunset_temp("Couldn't connect"))


def completed(stdout="", returncode=0):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr="")


class _DdcReadBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.drm = root / "drm"
        self.runtime = root / "run"
        self.drm.mkdir()
        self.runtime.mkdir()
        devices = root / "devices" / "pci0000:00" / "0000:00:02.0"
        for connector, bus in (("HDMI-A-1", 7), ("DP-2", 12)):
            card = devices / f"card1-{connector}"
            (devices / f"i2c-{bus}").mkdir(parents=True, exist_ok=True)
            card.mkdir(parents=True)
            (card / "ddc").symlink_to(devices / f"i2c-{bus}")
            (self.drm / f"card1-{connector}").symlink_to(card)
        # A connector without a ddc link (e.g. some docks) cannot be placed by sysfs.
        (devices / "card1-DP-5").mkdir()
        (self.drm / "card1-DP-5").symlink_to(devices / "card1-DP-5")
        # An empty /dev stand-in: no test ever opens a real I2C bus.
        self.dev = root / "dev"
        self.dev.mkdir()
        self.env = mock.patch.dict(os.environ, {"DICEY_SYSFS_DRM": str(self.drm),
                                                "XDG_RUNTIME_DIR": str(self.runtime),
                                                "DICEY_I2C_DEV_ROOT": str(self.dev)})
        self.env.start()
        self.which = mock.patch.object(ctl.shutil, "which",
                                       side_effect=lambda tool: "/usr/bin/ddcutil" if tool == "ddcutil" else None)
        self.which.start()
        # Hyprland's live power state: only the laptop panel, lit, unless a test
        # sets it (an unknown state reads nothing at all).
        self.dpms = mock.patch.object(ctl, "live_dpms_snapshot",
                                      return_value=[{"name": "eDP-1", "dpmsStatus": True}])
        self.dpms.start()
        self.suspend_clock = mock.patch.object(ctl, "suspend_clock_ms", return_value=0)
        self.suspend_clock.start()

    def tearDown(self):
        self.suspend_clock.stop()
        self.dpms.stop()
        self.which.stop()
        self.env.stop()
        self.tmp.cleanup()


class DdcReadTest(_DdcReadBase):
    def test_sysfs_resolves_the_connector_bus(self):
        self.assertEqual(ctl.sysfs_bus("HDMI-A-1"), "7")
        self.assertEqual(ctl.sysfs_bus("DP-2"), "12")
        self.assertIsNone(ctl.sysfs_bus("DP-5"))
        self.assertIsNone(ctl.sysfs_bus("DP-9"))
        self.assertIsNone(ctl.sysfs_bus("../etc"))

    def test_reads_over_the_sysfs_bus_and_never_runs_detect(self):
        with mock.patch.object(ctl.subprocess, "run", return_value=completed("VCP 60 SNC x11\n")) as run, \
                mock.patch.object(ctl, "ddc_buses") as detect:
            result = ctl.ddc_read(["HDMI-A-1"], detect=["HDMI-A-1"])
        self.assertEqual(result, {"ok": True, "dpmsKnown": True, "globalIdle": False, "dpms": {"eDP-1": True},
                                  "readings": {"HDMI-A-1": {"status": "ok", "code": "0x11"}}, "suspendMs": 0})
        self.assertEqual(run.call_args[0][0],
                         ["ddcutil", "--bus", "7", "--skip-ddc-checks", "getvcp", "60", "--brief"])
        detect.assert_not_called()

    def test_hdmi_and_vendor_codes_on_dp_are_valid(self):
        for out, code in (("VCP 60 SNC x12", "0x12"), ("VCP 60 SNC x51", "0x51"), ("VCP 60 SNC x90", "0x90")):
            with self.subTest(out=out), mock.patch.object(ctl.subprocess, "run", return_value=completed(out)):
                self.assertEqual(ctl.ddc_read(["DP-2"])["readings"]["DP-2"], {"status": "ok", "code": code})

    def test_anything_but_a_clean_nonzero_answer_is_ambiguous(self):
        cases = [
            completed("VCP 60 SNC x00\n"),
            completed("VCP 60 ERR\n"),
            completed(""),
            completed("VCP 60 SNC x11\n", returncode=1),
            subprocess.TimeoutExpired(cmd="ddcutil", timeout=4),
            OSError("no such device"),
        ]
        for case in cases:
            kwargs = {"side_effect": case} if isinstance(case, BaseException) else {"return_value": case}
            with self.subTest(case=case), mock.patch.object(ctl.subprocess, "run", **kwargs):
                self.assertEqual(ctl.ddc_read(["HDMI-A-1"])["readings"]["HDMI-A-1"], {"status": "ambiguous"})

    def test_no_ddcutil_reports_no_tool(self):
        with mock.patch.object(ctl.shutil, "which", return_value=None), \
                mock.patch.object(ctl.subprocess, "run") as run:
            result = ctl.ddc_read(["HDMI-A-1", "DP-2"], detect=["HDMI-A-1", "DP-2"])
        self.assertEqual(result["readings"], {"HDMI-A-1": {"status": "no_tool"}, "DP-2": {"status": "no_tool"}})
        run.assert_not_called()

    def test_held_brightness_lock_is_busy_and_skips_the_bus(self):
        import fcntl
        lock = self.runtime / "omarchy-brightness-display.lock"
        self.assertEqual(ctl.brightness_lock_path(), lock)
        with open(lock, "w") as held:
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with mock.patch.object(ctl.subprocess, "run") as run:
                result = ctl.ddc_read(["HDMI-A-1"])
            run.assert_not_called()
        self.assertEqual(result["readings"]["HDMI-A-1"], {"status": "busy"})
        # Released: the same read goes through and leaves the lock free again.
        with mock.patch.object(ctl.subprocess, "run", return_value=completed("VCP 60 SNC x0f")):
            self.assertEqual(ctl.ddc_read(["HDMI-A-1"])["readings"]["HDMI-A-1"]["code"], "0x0f")
        with open(lock, "w") as again:
            fcntl.flock(again, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def test_detect_runs_once_only_when_allowed_and_sysfs_cannot_place(self):
        with mock.patch.object(ctl.subprocess, "run", return_value=completed("VCP 60 SNC x11")), \
                mock.patch.object(ctl, "ddc_buses", return_value={"DP-5": "4"}) as detect:
            without = ctl.ddc_read(["DP-5", "DP-9"])
            self.assertEqual(without["readings"], {"DP-5": {"status": "ambiguous"}, "DP-9": {"status": "ambiguous"}})
            detect.assert_not_called()
            allowed = ctl.ddc_read(["DP-5", "DP-9", "HDMI-A-1"], detect=["DP-5", "DP-9", "HDMI-A-1"])
        self.assertEqual(detect.call_count, 1)
        # A detected bus is reported back so the bar can pass it as a hint.
        self.assertEqual(allowed["readings"]["DP-5"], {"status": "ok", "code": "0x11", "bus": "4"})
        self.assertEqual(allowed["readings"]["DP-9"], {"status": "ambiguous"})
        self.assertEqual(allowed["readings"]["HDMI-A-1"], {"status": "ok", "code": "0x11"})

    def test_detect_is_scoped_to_probing_connectors(self):
        with mock.patch.object(ctl.subprocess, "run", return_value=completed("VCP 60 SNC x11")), \
                mock.patch.object(ctl, "ddc_buses", return_value={"DP-5": "4", "DP-9": "6"}) as detect:
            # DP-9 is watching (not named by --detect): never triggers detect.
            watching = ctl.ddc_read(["DP-9"], detect=["DP-5"])
            detect.assert_not_called()
            self.assertEqual(watching["readings"], {"DP-9": {"status": "ambiguous"}})
            probing = ctl.ddc_read(["DP-5", "DP-9"], detect=["DP-5"])
        self.assertEqual(detect.call_count, 1)
        self.assertEqual(probing["readings"]["DP-5"], {"status": "ok", "code": "0x11", "bus": "4"})
        self.assertEqual(probing["readings"]["DP-9"], {"status": "ambiguous"})

    def test_bus_hint_is_used_after_sysfs_and_never_detects(self):
        with mock.patch.object(ctl.subprocess, "run", return_value=completed("VCP 60 SNC x11")) as run, \
                mock.patch.object(ctl, "ddc_buses") as detect:
            hinted = ctl.ddc_read(["DP-5:13", "HDMI-A-1:99"], detect=["DP-5"])
        detect.assert_not_called()
        self.assertEqual(hinted["readings"]["DP-5"], {"status": "ok", "code": "0x11", "bus": "13"})
        # sysfs wins over a hint; its bus is not echoed.
        self.assertEqual(hinted["readings"]["HDMI-A-1"], {"status": "ok", "code": "0x11"})
        buses = [call[0][0][2] for call in run.call_args_list]
        self.assertEqual(sorted(buses), ["13", "7"])
        for bad in ("DP-5:", "DP-5:1a", "DP-5:../1", "a;b:3", "DP-5:1:2"):
            with self.subTest(spec=bad):
                self.assertIsNone(ctl.parse_ddc_spec(bad))
        self.assertEqual(ctl.parse_ddc_spec("DP-2:13"), ("DP-2", "13", False))
        self.assertEqual(ctl.parse_ddc_spec("DP-2"), ("DP-2", None, False))
        self.assertEqual(ctl.parse_ddc_spec("DP-2@scdc"), ("DP-2", None, True))
        self.assertEqual(ctl.parse_ddc_spec("DP-2:13@scdc"), ("DP-2", "13", True))
        for bad in ("@scdc", "DP-2@scd", "DP-2@scdc:3", "DP-2:@scdc"):
            with self.subTest(spec=bad):
                self.assertIsNone(ctl.parse_ddc_spec(bad))

    def test_cli_args_name_detect_targets_explicitly(self):
        self.assertEqual(ctl.parse_ddc_read_args(["--detect", "DP-5", "DP-5", "HDMI-A-1:7"]),
                         (["DP-5", "HDMI-A-1:7"], ["DP-5"]))

    def test_detect_takes_the_brightness_lock_and_is_busy_when_held(self):
        import fcntl
        lock = self.runtime / "omarchy-brightness-display.lock"
        with open(lock, "w") as held:
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with mock.patch.object(ctl.subprocess, "run") as run, \
                    mock.patch.object(ctl, "ddc_buses") as detect:
                result = ctl.ddc_read(["DP-5", "DP-9"], detect=["DP-5"])
            detect.assert_not_called()
            run.assert_not_called()
        self.assertEqual(result["readings"], {"DP-5": {"status": "busy"}, "DP-9": {"status": "ambiguous"}})
        # The lock is released after a detect.
        with mock.patch.object(ctl.subprocess, "run", return_value=completed("VCP 60 SNC x11")), \
                mock.patch.object(ctl, "ddc_buses", return_value={"DP-5": "4"}):
            self.assertEqual(ctl.ddc_read(["DP-5"], detect=["DP-5"])["readings"]["DP-5"]["bus"], "4")
        with open(lock, "w") as again:
            fcntl.flock(again, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def test_untrusted_lock_file_is_ambiguous_never_unlocked_or_busy_forever(self):
        # An unusable lock file may never clear, so it fails open (ambiguous),
        # unlike real contention (busy); the bus is never touched either way.
        lock = self.runtime / "omarchy-brightness-display.lock"
        target = self.runtime / "elsewhere"
        target.write_text("")
        lock.symlink_to(target)
        with mock.patch.object(ctl.subprocess, "run") as run, mock.patch.object(ctl, "ddc_buses") as detect:
            result = ctl.ddc_read(["HDMI-A-1", "DP-5"], detect=["DP-5"])
        run.assert_not_called()
        detect.assert_not_called()
        self.assertEqual(result["readings"], {"HDMI-A-1": {"status": "ambiguous"}, "DP-5": {"status": "ambiguous"}})
        lock.unlink()
        lock.mkdir()
        self.assertEqual(ctl._try_lock(), "ambiguous")
        lock.rmdir()
        lock.write_text("")
        with mock.patch.object(ctl.os, "getuid", return_value=os.getuid() + 1):
            self.assertEqual(ctl._try_lock(), "ambiguous")
        with mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": str(self.runtime / "missing")}), \
                mock.patch.object(ctl.subprocess, "run") as run:
            self.assertEqual(ctl.read_vcp_input("7"), {"status": "ambiguous"})
        run.assert_not_called()
        # flock failing for any reason but contention is unusable too.
        with mock.patch.object(ctl.fcntl, "flock", side_effect=OSError(9, "EBADF")):
            self.assertEqual(ctl._try_lock(), "ambiguous")
        with mock.patch.object(ctl.fcntl, "flock", side_effect=BlockingIOError(11, "EAGAIN")):
            self.assertEqual(ctl._try_lock(), "busy")
        fd = ctl._try_lock()
        self.assertIsInstance(fd, int)
        ctl._unlock(fd)

    def test_invalid_connector_names_are_dropped(self):
        with mock.patch.object(ctl.subprocess, "run") as run:
            self.assertEqual(ctl.ddc_read(["a;b", "../x"])["readings"], {})
        run.assert_not_called()


class FakeScdc:
    """Stands in for /dev/i2c-N + the SCDC sink: register -> byte, or an
    OSError to raise. Records every register read and fails on any other use."""
    def __init__(self, registers=None, open_error=None):
        self.registers = dict(registers or {})
        self.open_error = open_error
        self.reads = []
        self.opened = []

    def open(self, bus):
        self.opened.append(bus)
        if self.open_error:
            raise self.open_error
        return os.open(os.devnull, os.O_RDONLY)

    def read(self, fd, register):
        self.reads.append(register)
        value = self.registers.get(register, OSError(121, "Remote I/O error"))
        if isinstance(value, BaseException):
            raise value
        return value

    def patch(self):
        stack = contextlib.ExitStack()
        stack.enter_context(mock.patch.object(ctl, "_open_i2c", side_effect=self.open))
        stack.enter_context(mock.patch.object(ctl, "_smbus_read_byte_data", side_effect=self.read))
        return stack


class ScdcTest(_DdcReadBase):
    """HDMI SCDC (I2C 0x54) signal-lock probe and its place in ddc-read."""

    def test_locked_unlocked_and_frl_flags(self):
        # TMDS (Config_1 FRL_Rate=0): requires all 4 bits (0x0F).
        # 3-lane FRL (FRL_Rate=1..2): requires lanes 0..2 (0x0E).
        # 4-lane FRL (FRL_Rate=3..6): requires lanes 0..3 (0x1E).
        ok_cases = (
            (0x00, 0x0F, "locked"),
            (0x00, 0x00, "unlocked"),
            (0x00, 0x20, "unlocked"),
            (0x00, 0xE0, "unlocked"),
            (0x01, 0x0E, "locked"),
            (0x02, 0x0E, "locked"),
            (0x01, 0x00, "unlocked"),
            (0x03, 0x1E, "locked"),
            (0x06, 0x1E, "locked"),
            (0x06, 0x20, "unlocked"),
        )
        for cfg1, flags, want in ok_cases:
            fake = FakeScdc({0x01: 0x01, 0x31: cfg1, 0x40: flags})
            with self.subTest(cfg1=hex(cfg1), flags=hex(flags)), fake.patch():
                self.assertEqual(ctl.read_scdc_status("3"), {"status": "ok", "signal": "scdc", "scdc": want})
                # Only Sink_Version, Config_1, and Status_Flags_0, in that order:
                # never a clear-on-read/update register.
                self.assertEqual(fake.reads, [0x01, 0x31, 0x40])
        # Clock-only, partial TMDS channels, partial FRL lanes, and reserved FRL
        # rates are ambiguous (never full lock and never away).
        partial_cases = (
            (0x00, 0x01),  # TMDS clock-only
            (0x00, 0x07),  # TMDS missing Ch2
            (0x00, 0x0E),  # TMDS missing Clock_Detected
            (0x00, 0x10),  # stray lane bit in TMDS
            (0x01, 0x02),  # 3-lane FRL single lane
            (0x02, 0x06),  # 3-lane FRL 2/3 lanes
            (0x03, 0x0E),  # 4-lane FRL 3/4 lanes
            (0x04, 0x10),  # 4-lane FRL single lane
            (0x07, 0x1E),  # reserved FRL_Rate
        )
        for cfg1, flags in partial_cases:
            fake = FakeScdc({0x01: 0x01, 0x31: cfg1, 0x40: flags})
            with self.subTest(partial=(hex(cfg1), hex(flags))), fake.patch():
                self.assertEqual(ctl.read_scdc_status("3"), {"status": "ambiguous"})

    def test_only_a_nack_or_version_zero_is_no_scdc_sink(self):
        cases = [
            FakeScdc({}),                                   # 0x54 NACKs (EREMOTEIO)
            FakeScdc({0x01: OSError(6, "ENXIO")}),
            FakeScdc({0x01: 0x00, 0x31: 0x00, 0x40: 0x0F}), # Sink_Version 0
            FakeScdc({0x01: 0x01, 0x31: OSError(121, "EREMOTEIO")}),
            FakeScdc({0x01: 0x01, 0x31: 0x00, 0x40: OSError(121, "EREMOTEIO")}),
        ]
        for fake in cases:
            with self.subTest(regs=fake.registers, err=fake.open_error), fake.patch():
                self.assertEqual(ctl.read_scdc_status("3"), {"status": "none"})
        version_zero = FakeScdc({0x01: 0x00, 0x31: 0x00, 0x40: 0x0F})
        with version_zero.patch():
            ctl.read_scdc_status("3")
        self.assertEqual(version_zero.reads, [0x01])
        for bad in ("", None, "3a", "../3", "1234567"):
            with self.subTest(bus=bad), mock.patch.object(ctl, "_open_i2c") as opener:
                self.assertEqual(ctl.read_scdc_status(bad), {"status": "ambiguous"})
            opener.assert_not_called()

    def test_transport_permission_and_floating_bus_errors_are_ambiguous(self):
        cases = [
            FakeScdc(open_error=OSError(13, "EACCES")),
            FakeScdc(open_error=OSError(1, "EPERM")),
            FakeScdc(open_error=OSError(2, "ENOENT")),
            FakeScdc(open_error=OSError(16, "EBUSY")),     # a kernel driver owns 0x54
            FakeScdc(open_error=OSError("not a character device")),
            FakeScdc({0x01: OSError(110, "ETIMEDOUT")}),
            FakeScdc({0x01: OSError(5, "EIO")}),
            FakeScdc({0x01: OSError(16, "EBUSY")}),
            FakeScdc({0x01: 0x01, 0x31: 0x00, 0x40: OSError(110, "ETIMEDOUT")}),
            FakeScdc({0x01: 0xFF, 0x31: 0x00, 0x40: 0x0F}), # floating bus
            FakeScdc({0x01: 0x01, 0x31: 0xFF, 0x40: 0x0F}),
            FakeScdc({0x01: 0x01, 0x31: 0x00, 0x40: 0xFF}),
        ]
        for fake in cases:
            with self.subTest(regs=fake.registers, err=fake.open_error), fake.patch():
                self.assertEqual(ctl.read_scdc_status("7"), {"status": "ambiguous"})
                # Watched through SCDC, a transport error is never "absent" (no signal).
                self.assertEqual(ctl.ddc_read(["HDMI-A-1@scdc"])["readings"]["HDMI-A-1"], {"status": "ambiguous"})
        for nack in (FakeScdc({}), FakeScdc({0x01: OSError(6, "ENXIO")})):
            with self.subTest(nack=nack.registers), nack.patch():
                self.assertEqual(ctl.ddc_read(["HDMI-A-1@scdc"])["readings"]["HDMI-A-1"],
                                 {"status": "ok", "signal": "scdc", "scdc": "absent"})

    def test_held_lock_is_busy_untrusted_is_ambiguous_and_neither_opens_the_bus(self):
        import fcntl
        lock = self.runtime / "omarchy-brightness-display.lock"
        fake = FakeScdc({0x01: 0x01, 0x31: 0x00, 0x40: 0x0F})
        with open(lock, "w") as held:
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with fake.patch(), mock.patch.object(ctl.subprocess, "run") as run:
                self.assertEqual(ctl.read_scdc_status("7"), {"status": "busy"})
                self.assertEqual(ctl.ddc_read(["HDMI-A-1@scdc", "DP-2"])["readings"],
                                 {"HDMI-A-1": {"status": "busy"}, "DP-2": {"status": "busy"}})
            run.assert_not_called()
        self.assertEqual(fake.opened, [])
        # Released afterwards: the next read takes and frees the lock again.
        with fake.patch():
            self.assertEqual(ctl.read_scdc_status("7")["scdc"], "locked")
        with open(lock, "w") as again:
            fcntl.flock(again, fcntl.LOCK_EX | fcntl.LOCK_NB)
        lock.unlink()
        lock.symlink_to(self.runtime / "elsewhere")
        with fake.patch():
            self.assertEqual(ctl.read_scdc_status("7"), {"status": "ambiguous"})
            self.assertEqual(ctl.ddc_read(["HDMI-A-1@scdc"])["readings"]["HDMI-A-1"], {"status": "ambiguous"})
        self.assertEqual(fake.opened, ["7"])

    def test_real_open_refuses_symlinks_and_non_character_devices(self):
        (self.dev / "i2c-3").write_text("")
        (self.dev / "i2c-4").symlink_to(os.devnull)
        with mock.patch.object(ctl.fcntl, "ioctl") as ioctl:
            for bus in ("3", "4", "5"):
                with self.subTest(bus=bus):
                    with self.assertRaises(OSError):
                        ctl._open_i2c(bus)
                    self.assertEqual(ctl.read_scdc_status(bus), {"status": "ambiguous"})
        ioctl.assert_not_called()

    def test_real_open_uses_i2c_slave_never_force(self):
        calls = []
        real_open = os.open

        def fake_fstat(fd):
            return os.stat_result((0o20666,) + (0,) * 9)

        def fake_open(path, flags, *args):
            calls.append((path, flags))
            return real_open(os.devnull, os.O_RDONLY)

        with mock.patch.object(ctl.os, "open", side_effect=fake_open), \
                mock.patch.object(ctl.os, "fstat", side_effect=fake_fstat), \
                mock.patch.object(ctl.fcntl, "ioctl") as ioctl:
            fd = ctl._open_i2c("3")
            os.close(fd)
        self.assertEqual(calls[0][0], str(self.dev / "i2c-3"))
        self.assertTrue(calls[0][1] & os.O_NOFOLLOW and calls[0][1] & os.O_CLOEXEC and calls[0][1] & os.O_RDWR)
        self.assertEqual(ioctl.call_args[0][1:], (0x0703, 0x54))

    def test_smbus_read_builds_a_read_byte_data_transfer(self):
        seen = {}

        def fake_ioctl(fd, request, args):
            seen["request"] = request
            seen["rw"] = args.read_write
            seen["command"] = args.command
            seen["size"] = args.size
            args.data.contents.byte = 0x0F
            return 0

        with mock.patch.object(ctl.fcntl, "ioctl", side_effect=fake_ioctl):
            self.assertEqual(ctl._smbus_read_byte_data(5, 0x40), 0x0F)
        self.assertEqual(seen, {"request": 0x0720, "rw": 1, "command": 0x40, "size": 2})

    def test_locked_scdc_answers_without_ddcutil(self):
        fake = FakeScdc({0x01: 0x01, 0x31: 0x00, 0x40: 0x0F})
        with fake.patch(), mock.patch.object(ctl.subprocess, "run") as run, \
                mock.patch.object(ctl, "ddc_buses") as detect:
            result = ctl.ddc_read(["HDMI-A-1"], detect=["HDMI-A-1"])
        self.assertEqual(result["readings"]["HDMI-A-1"], {"status": "ok", "signal": "scdc", "scdc": "locked"})
        self.assertEqual(fake.opened, ["7"])
        run.assert_not_called()
        detect.assert_not_called()

    def test_unlocked_or_missing_scdc_falls_through_to_vcp_60(self):
        for fake in (FakeScdc({0x01: 0x01, 0x31: 0x00, 0x40: 0x00}), FakeScdc({})):
            with self.subTest(regs=fake.registers), fake.patch(), \
                    mock.patch.object(ctl.subprocess, "run", return_value=completed("VCP 60 SNC x11")) as run:
                result = ctl.ddc_read(["HDMI-A-1"])
            self.assertEqual(result["readings"]["HDMI-A-1"], {"status": "ok", "code": "0x11"})
            self.assertEqual(run.call_count, 1)

    def test_unlocked_scdc_is_kept_when_vcp_60_fails(self):
        fake = FakeScdc({0x01: 0x01, 0x31: 0x00, 0x40: 0x00})
        with fake.patch(), mock.patch.object(ctl.subprocess, "run", return_value=completed("VCP 60 ERR\n")):
            result = ctl.ddc_read(["HDMI-A-1"])
        self.assertEqual(result["readings"]["HDMI-A-1"], {"status": "ok", "signal": "scdc", "scdc": "unlocked"})

    def test_scdc_is_only_tried_on_sysfs_linked_connectors(self):
        fake = FakeScdc({0x01: 0x01, 0x31: 0x00, 0x40: 0x0F})
        with fake.patch(), mock.patch.object(ctl.subprocess, "run", return_value=completed("VCP 60 SNC x11")), \
                mock.patch.object(ctl, "ddc_buses", return_value={}):
            result = ctl.ddc_read(["DP-5:13", "DP-9"], detect=["DP-9"])
        self.assertEqual(fake.opened, [])
        self.assertEqual(result["readings"]["DP-5"], {"status": "ok", "code": "0x11", "bus": "13"})

    def test_scdc_target_never_runs_ddcutil_even_when_it_is_missing(self):
        for flags, want in ((0x0F, "locked"), (0x00, "unlocked")):
            fake = FakeScdc({0x01: 0x01, 0x31: 0x00, 0x40: flags})
            with self.subTest(want=want), fake.patch(), \
                    mock.patch.object(ctl.shutil, "which", return_value=None), \
                    mock.patch.object(ctl.subprocess, "run") as run, mock.patch.object(ctl, "ddc_buses") as detect:
                result = ctl.ddc_read(["HDMI-A-1@scdc"], detect=["HDMI-A-1"])
            self.assertEqual(result["readings"]["HDMI-A-1"], {"status": "ok", "signal": "scdc", "scdc": want})
            run.assert_not_called()
            detect.assert_not_called()
        # No SCDC answer while watched through SCDC: a TV in standby -> absent.
        with FakeScdc({}).patch(), mock.patch.object(ctl.subprocess, "run") as run:
            result = ctl.ddc_read(["HDMI-A-1@scdc", "DP-2:12@scdc"])
        run.assert_not_called()
        self.assertEqual(result["readings"], {
            "HDMI-A-1": {"status": "ok", "signal": "scdc", "scdc": "absent"},
            "DP-2": {"status": "ok", "signal": "scdc", "scdc": "absent"},
        })
        # A hint bus is used when sysfs has none; no bus at all is ambiguous.
        fake = FakeScdc({0x01: 0x01, 0x31: 0x00, 0x40: 0x0F})
        with fake.patch():
            result = ctl.ddc_read(["DP-5:13@scdc", "DP-9@scdc"])
        self.assertEqual(fake.opened, ["13"])
        self.assertEqual(result["readings"]["DP-5"]["scdc"], "locked")
        self.assertEqual(result["readings"]["DP-9"], {"status": "ambiguous"})

    def test_no_ddcutil_still_reports_an_scdc_lock(self):
        with FakeScdc({0x01: 0x01, 0x31: 0x00, 0x40: 0x0F}).patch(), mock.patch.object(ctl.shutil, "which", return_value=None):
            result = ctl.ddc_read(["HDMI-A-1", "DP-5"])
        self.assertEqual(result["readings"], {
            "HDMI-A-1": {"status": "ok", "signal": "scdc", "scdc": "locked"},
            "DP-5": {"status": "no_tool"},
        })

    def test_no_ddcutil_watches_an_scdc_sink_through_scdc_alone(self):
        (self.dev / "i2c-7").write_text("")
        for regs, want in (({0x01: 0x01, 0x31: 0x00, 0x40: 0x0F}, {"status": "ok", "signal": "scdc", "scdc": "locked"}),
                           ({0x01: 0x01, 0x31: 0x00, 0x40: 0x00}, {"status": "ok", "signal": "scdc", "scdc": "unlocked"}),
                           ({}, {"status": "no_tool"}),
                           ({0x01: OSError(110, "ETIMEDOUT")}, {"status": "ambiguous"})):
            with self.subTest(want=want), FakeScdc(regs).patch(), \
                    mock.patch.object(ctl.shutil, "which", return_value=None), \
                    mock.patch.object(ctl.subprocess, "run") as run:
                self.assertEqual(ctl.ddc_read(["HDMI-A-1"])["readings"]["HDMI-A-1"], want)
            run.assert_not_called()
        # No /dev/i2c node at all (i2c-dev not loaded): this needs ddcutil.
        with FakeScdc(open_error=OSError(2, "ENOENT")).patch(), mock.patch.object(ctl.shutil, "which", return_value=None):
            self.assertEqual(ctl.ddc_read(["DP-2"])["readings"]["DP-2"], {"status": "no_tool"})

    def test_global_idle_skips_every_bus_and_dpms_off_monitors_are_not_read(self):
        live = [{"name": "eDP-1", "dpmsStatus": False}, {"name": "HDMI-A-1", "dpmsStatus": False},
                {"name": "DP-2", "disabled": True, "dpmsStatus": True}]
        fake = FakeScdc({0x01: 0x01, 0x31: 0x00, 0x40: 0x0F})
        with fake.patch(), mock.patch.object(ctl, "live_dpms_snapshot", return_value=live), \
                mock.patch.object(ctl.subprocess, "run") as run, mock.patch.object(ctl, "ddc_buses") as detect:
            idle = ctl.ddc_read(["HDMI-A-1@scdc", "DP-2", "DP-5"], detect=["HDMI-A-1", "DP-2", "DP-5"])
        self.assertEqual(idle, {"ok": True, "dpmsKnown": True, "globalIdle": True,
                                "dpms": {"eDP-1": False, "HDMI-A-1": False},
                                "readings": {}, "suspendMs": 0})
        self.assertEqual(fake.opened, [])
        run.assert_not_called()
        detect.assert_not_called()
        # One monitor DPMS-off on its own: only that one is skipped. A disabled
        # (DDC-paused) monitor is still read so it can wake.
        live[0]["dpmsStatus"] = True
        fake = FakeScdc({0x01: 0x01, 0x31: 0x00, 0x40: 0x0F})
        with fake.patch(), mock.patch.object(ctl, "live_dpms_snapshot", return_value=live), \
                mock.patch.object(ctl.subprocess, "run", return_value=completed("VCP 60 SNC x11")):
            some = ctl.ddc_read(["HDMI-A-1@scdc", "DP-2"])
        self.assertFalse(some["globalIdle"])
        self.assertEqual(some["dpms"], {"eDP-1": True, "HDMI-A-1": False})
        self.assertEqual(some["readings"]["HDMI-A-1"], {"status": "dpms_off"})
        self.assertEqual(some["readings"]["DP-2"]["scdc"], "locked")
        self.assertEqual(fake.opened, ["12"])
        # Every enabled monitor off counts; nothing enabled is not idle.
        self.assertEqual(ctl._dpms_view([{"name": "DP-2", "disabled": True}]), ({}, False))
        self.assertEqual(ctl._dpms_view(None), ({}, False))

    def test_live_dpms_snapshot_fails_soft(self):
        self.dpms.stop()
        try:
            for error in (OSError("no hyprctl"), subprocess.TimeoutExpired("hyprctl", 10), ValueError("bad json")):
                with self.subTest(error=error), mock.patch.object(ctl, "hyprctl_json", side_effect=error):
                    self.assertIsNone(ctl.live_dpms_snapshot())
            with mock.patch.object(ctl, "hyprctl_json", return_value=[{"name": "FALLBACK"}, {"name": "DP-2"}, "x"]):
                self.assertEqual(ctl.live_dpms_snapshot(), [{"name": "DP-2"}])
        finally:
            self.dpms.start()

    def test_unknown_dpms_state_reads_nothing_and_is_never_awake(self):
        self.dpms.stop()
        try:
            for raw in ([], [{"name": "FALLBACK"}], "x"):
                with self.subTest(raw=raw), mock.patch.object(ctl, "hyprctl_json", return_value=raw):
                    self.assertIsNone(ctl.live_dpms_snapshot())
        finally:
            self.dpms.start()
        fake = FakeScdc({0x01: 0x01, 0x31: 0x00, 0x40: 0x0F})
        with fake.patch(), mock.patch.object(ctl, "live_dpms_snapshot", return_value=None), \
                mock.patch.object(ctl.subprocess, "run") as run, mock.patch.object(ctl, "ddc_buses") as detect, \
                mock.patch.object(ctl, "sysfs_bus") as sysfs:
            result = ctl.ddc_read(["HDMI-A-1@scdc", "DP-2", "DP-5"], detect=["HDMI-A-1", "DP-2", "DP-5"])
        self.assertEqual(result, {"ok": True, "dpmsKnown": False, "globalIdle": False, "dpms": {},
                                  "readings": {}, "suspendMs": 0})
        self.assertEqual(fake.opened, [])
        run.assert_not_called()
        detect.assert_not_called()
        sysfs.assert_not_called()

    def test_no_specs_is_a_pure_power_state_probe(self):
        live = [{"name": "eDP-1", "dpmsStatus": True}, {"name": "HDMI-A-1", "dpmsStatus": False}]
        fake = FakeScdc({0x01: 0x01, 0x31: 0x00, 0x40: 0x0F})
        with fake.patch(), mock.patch.object(ctl, "live_dpms_snapshot", return_value=live), \
                mock.patch.object(ctl.subprocess, "run") as run, mock.patch.object(ctl, "ddc_buses") as detect:
            probe = ctl.ddc_read([])
            live[1]["dpmsStatus"] = True
            woke = ctl.ddc_read([])
        self.assertEqual(probe, {"ok": True, "dpmsKnown": True, "globalIdle": False,
                                 "dpms": {"eDP-1": True, "HDMI-A-1": False}, "readings": {}, "suspendMs": 0})
        self.assertEqual(woke["dpms"], {"eDP-1": True, "HDMI-A-1": True})
        self.assertEqual(fake.opened, [])
        run.assert_not_called()
        detect.assert_not_called()
        with mock.patch.object(ctl, "ddc_read", return_value=probe) as read, \
                mock.patch("sys.stdout", new=io.StringIO()):
            self.assertEqual(ctl.main(["ddc-read"]), 0)
        read.assert_called_once_with([], detect=[])

    def test_suspend_clock_ms_reports_boottime_minus_monotonic(self):
        self.suspend_clock.stop()
        try:
            def fake_clock(clk):
                return 101.25 if clk == ctl.time.CLOCK_BOOTTIME else 100.0
            with mock.patch.object(ctl.time, "clock_gettime", side_effect=fake_clock):
                self.assertEqual(ctl.suspend_clock_ms(), 1250)
                self.assertEqual(ctl.ddc_read([])["suspendMs"], 1250)
            # A batch that spans a suspend discards readings and reports the post-I/O clock.
            fake = FakeScdc({0x01: 0x01, 0x31: 0x00, 0x40: 0x0F})
            with fake.patch(), mock.patch.object(ctl, "suspend_clock_ms", side_effect=[1000, 2600]):
                spanned = ctl.ddc_read(["HDMI-A-1"])
            self.assertEqual(spanned["suspendMs"], 2600)
            self.assertEqual(spanned["readings"], {})
        finally:
            self.suspend_clock.start()

    def test_pre_transfer_nxio_is_ambiguous_but_a_transfer_nack_is_none(self):
        # Opening the node or addressing 0x54 transfers nothing: never a NACK.
        for err in (OSError(errno.ENXIO, "ENXIO"), OSError(errno.EREMOTEIO, "EREMOTEIO")):
            fake = FakeScdc(open_error=err)
            with self.subTest(open_error=err), fake.patch():
                self.assertEqual(ctl.read_scdc_status("7"), {"status": "ambiguous"})
                self.assertEqual(ctl.ddc_read(["HDMI-A-1@scdc"])["readings"]["HDMI-A-1"], {"status": "ambiguous"})
                with mock.patch.object(ctl.shutil, "which", return_value=None):
                    # Not "no_tool" either: the node exists, it just did not open.
                    (self.dev / "i2c-7").write_text("")
                    try:
                        self.assertEqual(ctl.ddc_read(["HDMI-A-1"])["readings"]["HDMI-A-1"], {"status": "ambiguous"})
                    finally:
                        (self.dev / "i2c-7").unlink()
        # The real open: an ENXIO from the I2C_SLAVE ioctl is ambiguous too.
        real_open, real_fstat = os.open, os.fstat
        nodes = set()

        def fake_open(path, flags, *args):
            if "i2c-" not in str(path):
                return real_open(path, flags, *args)
            fd = real_open(os.devnull, os.O_RDONLY)
            nodes.add(fd)
            return fd

        def fake_fstat(fd):
            return os.stat_result((0o20666,) + (0,) * 9) if fd in nodes else real_fstat(fd)

        with mock.patch.object(ctl.os, "open", side_effect=fake_open), \
                mock.patch.object(ctl.os, "fstat", side_effect=fake_fstat), \
                mock.patch.object(ctl.fcntl, "ioctl", side_effect=OSError(errno.ENXIO, "ENXIO")) as ioctl:
            self.assertEqual(ctl.read_scdc_status("7"), {"status": "ambiguous"})
        self.assertEqual(ioctl.call_args[0][1:], (0x0703, 0x54))
        # The SMBus register reads themselves: EREMOTEIO/ENXIO is the sink's NACK.
        for regs in ({0x01: OSError(errno.ENXIO, "ENXIO")}, {0x01: OSError(errno.EREMOTEIO, "EREMOTEIO")},
                     {0x01: 0x01, 0x31: OSError(errno.ENXIO, "ENXIO")},
                     {0x01: 0x01, 0x31: 0x00, 0x40: OSError(errno.ENXIO, "ENXIO")},
                     {0x01: 0x01, 0x31: 0x00, 0x40: OSError(errno.EREMOTEIO, "EREMOTEIO")}):
            fake = FakeScdc(regs)
            with self.subTest(regs=regs), fake.patch():
                self.assertEqual(ctl.read_scdc_status("7"), {"status": "none"})
                self.assertEqual(ctl.ddc_read(["HDMI-A-1@scdc"])["readings"]["HDMI-A-1"],
                                 {"status": "ok", "signal": "scdc", "scdc": "absent"})

    def test_cli_passes_scdc_specs_through(self):
        self.assertEqual(ctl.parse_ddc_read_args(["HDMI-A-1:7@scdc", "--detect", "DP-5", "DP-5"]),
                         (["HDMI-A-1:7@scdc", "DP-5"], ["DP-5"]))


class MirrorParkTest(unittest.TestCase):
    def parked(self, **extra):
        fields = dict(name="HDMI-A-1", selector="HDMI-A-1", autoOff=True, offReason="input",
                      parkMirrorOf="eDP-1", x=1920)
        fields.update(extra)
        return output(**fields)

    def test_live_lua_parks_after_primaries_and_mirrors(self):
        lua = ctl.live_lua([
            self.parked(),
            output(name="DP-4", selector="DP-4", autoOff=True, offReason="input"),
            output(name="DP-3", selector="DP-3", mirrorOf="eDP-1"),
            output(**LAPTOP),
        ])
        lines = lua.split("\n")
        self.assertEqual(len(lines), 4)
        self.assertIn('output = "eDP-1"', lines[0])
        self.assertIn('output = "DP-3"', lines[1])
        self.assertIn('output = "HDMI-A-1", disabled = false, mode = "2560x1440@100.00"', lines[2])
        self.assertIn('mirror = "eDP-1"', lines[2])
        self.assertEqual(lines[3], 'hl.monitor({ output = "DP-4", disabled = true })')
        self.assertNotIn("parkMirrorOf", lua)

    def test_parked_without_a_mode_still_mirrors(self):
        parked = ctl.validate_output({"name": "HDMI-A-1", "autoOff": True, "offReason": "input",
                                      "parkMirrorOf": "eDP-1"})
        self.assertIsNone(parked["mode"])
        lua = ctl.live_lua([output(**LAPTOP), parked])
        self.assertIn('output = "HDMI-A-1", disabled = false, mode = "preferred"', lua)
        self.assertIn('mirror = "eDP-1"', lua.split("\n")[1])

    def test_file_block_never_writes_a_park(self):
        block = ctl.file_block([output(**LAPTOP), self.parked()])
        self.assertNotIn("parkMirrorOf", block)
        line = [ln for ln in block.split("\n") if '"HDMI-A-1"' in ln][0]
        self.assertIn("disabled = false", line)
        self.assertIn('mirror = ""', line)
        self.assertIn('position = "1920x0"', line)

    def test_validate_plan_accepts_only_parks_on_an_active_output(self):
        plan = ctl.validate_plan([dict(output(**LAPTOP)), dict(self.parked())])
        self.assertEqual(plan[1]["parkMirrorOf"], "eDP-1")
        self.assertEqual(plan[0]["parkMirrorOf"], "")
        bad_plans = [
            [output(**LAPTOP), output(name="HDMI-A-1", selector="HDMI-A-1", parkMirrorOf="eDP-1")],
            [output(**LAPTOP), output(name="HDMI-A-1", autoOff=True, offReason="lid", parkMirrorOf="eDP-1")],
            [output(**LAPTOP), output(name="HDMI-A-1", autoOff=True, offReason="input", parkMirrorOf="HDMI-A-1")],
            [output(**LAPTOP), self.parked(parkMirrorOf="DP-9")],
            [output(**LAPTOP, enabled=False), output(name="DP-3", selector="DP-3"), self.parked()],
            [output(**LAPTOP, autoOff=True, offReason="lid"), output(name="DP-3", selector="DP-3"), self.parked()],
            [output(name="DP-3", selector="DP-3"), output(**LAPTOP, mirrorOf="DP-3"), self.parked()],
            [output(**LAPTOP), self.parked(),
             output(name="DP-4", selector="DP-4", autoOff=True, offReason="input", parkMirrorOf="HDMI-A-1")],
        ]
        for plan in bad_plans:
            with self.subTest(plan=[(o["name"], o["parkMirrorOf"]) for o in plan]):
                with self.assertRaises(ValueError):
                    ctl.validate_plan([dict(o) for o in plan])
        for hostile in ('eDP-1" })', "eDP-1\nx", "a b"):
            with self.subTest(hostile=hostile), self.assertRaises(ValueError):
                output(parkMirrorOf=hostile)
        self.assertEqual(output(parkMirrorOf="none")["parkMirrorOf"], "")

    def test_safe_revert_keeps_a_park_explicit_and_never_disables_a_parked_monitor(self):
        live = [{"id": 0, "name": "eDP-1", "width": 1920, "height": 1080, "refreshRate": 60},
                {"id": 1, "name": "HDMI-A-1", "width": 2560, "height": 1440, "refreshRate": 100}]
        safe = ctl.safe_revert_plan([output(**LAPTOP), self.parked(pauseMode="mirror")], live)
        hdmi = [o for o in safe if o["name"] == "HDMI-A-1"][0]
        # Still the bar's live park, never turned into a mirror the user set up.
        self.assertEqual((hdmi["parkMirrorOf"], hdmi["mirrorOf"], hdmi["autoOff"], hdmi["offReason"]),
                         ("eDP-1", "", True, "input"))
        lua = ctl.live_lua(safe)
        self.assertNotIn("disabled = true", lua)
        self.assertIn('mirror = "eDP-1"', lua.split("\n")[1])
        self.assertNotIn("parkMirrorOf", ctl.file_block(safe))
        self.assertIn('mirror = ""', ctl.file_block(safe))
        # Numeric target ids resolve; a target that is gone unparks to extended.
        safe_id = ctl.safe_revert_plan([output(**LAPTOP), self.parked(parkMirrorOf="0")], live)
        self.assertEqual((safe_id[1]["parkMirrorOf"], safe_id[1]["mirrorOf"]), ("eDP-1", ""))
        gone = ctl.safe_revert_plan([output(**LAPTOP), self.parked()], live[1:])
        self.assertEqual([(o["name"], o["enabled"], o["autoOff"], o["mirrorOf"], o["parkMirrorOf"]) for o in gone],
                         [("HDMI-A-1", True, False, "", "")])

    def test_safe_revert_revalidates_park_targets_against_the_lid_and_the_plan(self):
        live = [{"id": 0, "name": "eDP-1", "width": 1920, "height": 1080, "refreshRate": 60},
                {"id": 1, "name": "HDMI-A-1", "width": 2560, "height": 1440, "refreshRate": 100},
                {"id": 2, "name": "DP-3", "width": 1920, "height": 1080, "refreshRate": 60}]
        dp3 = output(name="DP-3", selector="DP-3", x=4480)
        # Lid shut: never parked behind it; it comes back on, extended.
        shut = ctl.safe_revert_plan([output(**LAPTOP), dp3, self.parked(pauseMode="mirror")], live, True)
        hdmi = [o for o in shut if o["name"] == "HDMI-A-1"][0]
        self.assertEqual((hdmi["enabled"], hdmi["autoOff"], hdmi["offReason"], hdmi["parkMirrorOf"],
                          hdmi["pauseMode"], hdmi["mirrorOf"]), (True, False, "", "", "", ""))
        self.assertNotIn("disabled = true", ctl.live_lua(shut))
        # Lid open: the same park is kept.
        open_lid = ctl.safe_revert_plan([output(**LAPTOP), dp3, self.parked()], live, False)
        self.assertEqual([o["parkMirrorOf"] for o in open_lid if o["name"] == "HDMI-A-1"], ["eDP-1"])
        # A target the plan leaves off, paused or mirrored is no target either.
        for target in (output(**dict(LAPTOP, enabled=False)), output(**dict(LAPTOP, mirrorOf="DP-3")),
                       output(**dict(LAPTOP, autoOff=True, offReason="lid"))):
            with self.subTest(target=(target["enabled"], target["mirrorOf"], target["autoOff"])):
                safe = ctl.safe_revert_plan([target, dp3, self.parked()], live)
                hdmi = [o for o in safe if o["name"] == "HDMI-A-1"][0]
                self.assertEqual((hdmi["enabled"], hdmi["autoOff"], hdmi["parkMirrorOf"]), (True, False, ""))
                self.assertNotIn('output = "HDMI-A-1", disabled = true', ctl.live_lua(safe))
        # A park on a monitor that is not input-paused (woken, still parked live) unparks.
        stray = ctl.safe_revert_plan([output(**LAPTOP), output(name="HDMI-A-1", selector="HDMI-A-1",
                                                                 parkMirrorOf="eDP-1")], live)
        self.assertEqual((stray[1]["parkMirrorOf"], stray[1]["mirrorOf"], stray[1]["enabled"]), ("", "", True))
        # A pending mirror-pause with no target comes back on, never disabled.
        pending = ctl.safe_revert_plan([output(**LAPTOP), self.parked(parkMirrorOf="", pauseMode="mirror")], live)
        self.assertEqual((pending[1]["autoOff"], pending[1]["enabled"]), (False, True))
        self.assertNotIn("disabled = true", ctl.live_lua(pending))

    def test_pending_mirror_pause_is_never_disabled(self):
        pending = output(name="HDMI-A-1", selector="HDMI-A-1", autoOff=True, offReason="input",
                         pauseMode="mirror", x=1920)
        self.assertEqual(pending["pauseMode"], "mirror")
        lua = ctl.live_lua([output(**LAPTOP), pending])
        self.assertNotIn("disabled = true", lua)
        self.assertIn('output = "HDMI-A-1", disabled = false, mode = "2560x1440@100.00", position = "1920x0"', lua)
        self.assertIn('mirror = ""', lua.split("\n")[1])
        plan = ctl.validate_plan([dict(output(**LAPTOP)), dict(pending)])
        self.assertEqual((plan[1]["enabled"], plan[1]["autoOff"], plan[1]["offReason"], plan[1]["pauseMode"]),
                         (True, False, "", ""))
        self.assertNotIn("disabled = true", ctl.live_lua(plan))
        # A DDC/CI pause (or an older caller with no pauseMode) still disables.
        for mode in ("disable", ""):
            with self.subTest(mode=mode):
                ddc = output(name="DP-4", selector="DP-4", autoOff=True, offReason="input", pauseMode=mode)
                self.assertIn('hl.monitor({ output = "DP-4", disabled = true })',
                              ctl.live_lua(ctl.validate_plan([dict(output(**LAPTOP)), dict(ddc)])))
        # pauseMode is validated, and dropped from anything not input-paused.
        with self.assertRaises(ValueError):
            output(pauseMode="park")
        self.assertEqual(output(pauseMode="mirror")["pauseMode"], "")
        self.assertEqual(output(autoOff=True, offReason="lid", pauseMode="mirror")["pauseMode"], "")

    def test_apply_cli_parks_live(self):
        plan = [output(**LAPTOP), self.parked()]
        with mock.patch.object(ctl.subprocess, "run", return_value=completed("ok")) as run, \
                mock.patch.object(ctl, "_verify_some_display_enabled", return_value=[]), \
                mock.patch.object(ctl, "fallbacks", return_value=[]):
            result = ctl.apply(ctl.validate_plan(plan))
        self.assertTrue(result["ok"])
        lua = run.call_args[0][0][2]
        self.assertIn('mirror = "eDP-1"', lua)
        self.assertNotIn("disabled = true", lua)


class DdcMemoryTest(unittest.TestCase):
    FP = "BNQ|BenQ GW2790Q|ET97T00426087"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = Path(self.tmp.name) / "state"
        self.env = mock.patch.dict(os.environ, {"XDG_STATE_HOME": str(self.state)})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def ddc_file(self):
        return self.state / "dicey-bar" / "ddc.json"

    def test_learn_writes_ddc_json_privately_and_atomically(self):
        result = ctl.ddc_learn("HDMI-A-1", self.FP, "0x11")
        self.assertTrue(result["ok"])
        self.assertEqual(result["ddcInput"], "0x11")
        self.assertEqual(result["ddcConnector"], "HDMI-A-1")
        self.assertEqual(result["label"], "HDMI-1")
        saved = json.loads(self.ddc_file().read_text())
        self.assertEqual(saved["version"], 1)
        self.assertIs(saved["migrated"], True)
        self.assertEqual(saved["monitors"][self.FP]["input"], "0x11")
        self.assertEqual(saved["monitors"][self.FP]["connector"], "HDMI-A-1")
        self.assertTrue(saved["monitors"][self.FP]["learnedAt"])
        self.assertEqual(result["ddcMemory"], saved)
        self.assertEqual(oct(self.ddc_file().stat().st_mode & 0o777), "0o600")
        # Learning never touches displays.json.
        self.assertFalse((self.state / "dicey-bar" / "displays.json").exists())
        self.assertEqual(ctl.ddc_learn("DP-2", self.FP, "0x0F")["ddcMemory"]["monitors"][self.FP]["input"], "0x0f")

    def test_learn_rejects_bad_names_fingerprints_and_codes(self):
        for args in (("HDMI-A-1", self.FP, "0x00"), ("HDMI-A-1", self.FP, "0x0"), ("HDMI-A-1", self.FP, "17"),
                     ("HDMI-A-1", self.FP, "0x100"), ("HDMI;A", self.FP, "0x11"), ("HDMI-A-1", "", "0x11"),
                     ("HDMI-A-1", "a\nb", "0x11")):
            with self.subTest(args=args), self.assertRaises(ValueError):
                ctl.ddc_learn(*args)
        self.assertFalse(self.ddc_file().exists())

    def test_legacy_inline_input_migrates_only_when_complete_and_nonzero(self):
        legacy = {"monitors": {
            "a": {"ddcWatch": True, "ddcInput": "0x11", "ddcConnector": "HDMI-A-1"},
            "b": {"ddcWatch": True, "ddcInput": "0x00", "ddcConnector": "DP-1"},
            "c": {"ddcWatch": True, "ddcInput": "0x0f"},
            "d": {"ddcWatch": True, "ddcConnector": "DP-2"},
            "e": {"ddcInput": "0x12", "ddcConnector": "DP-3"},
        }}
        mem = ctl.load_ddc_memory(legacy)
        self.assertEqual(sorted(mem["monitors"]), ["a", "e"])
        self.assertEqual(mem["monitors"]["a"], {"input": "0x11", "connector": "HDMI-A-1", "learnedAt": "legacy"})
        # ddc.json wins over a stale legacy entry.
        ctl.ddc_learn("DP-1", "a", "0x0f")
        self.assertEqual(ctl.load_ddc_memory(legacy)["monitors"]["a"]["input"], "0x0f")

    def test_corrupt_or_hostile_ddc_json_is_ignored(self):
        self.ddc_file().parent.mkdir(parents=True)
        self.ddc_file().write_text(json.dumps({"monitors": {
            self.FP: {"input": "0x00", "connector": "HDMI-A-1"},
            "ok": {"input": "0x11", "connector": "HDMI-A-1"},
            "bad": {"input": "0x11", "connector": "x; rm"},
        }}))
        self.assertEqual(sorted(ctl.load_ddc_memory()["monitors"]), ["ok"])
        self.ddc_file().write_text("{not json")
        self.assertEqual(ctl.load_ddc_memory(), {"version": 1, "monitors": {}})

    def test_forget_never_resurrects_a_legacy_inline_input(self):
        memory_file = self.state / "dicey-bar" / "displays.json"
        memory_file.parent.mkdir(parents=True)
        memory_file.write_text(json.dumps({"monitors": {
            self.FP: {"ddcInput": "0x11", "ddcConnector": "HDMI-A-1"},
            "other": {"ddcInput": "0x0f", "ddcConnector": "DP-2"},
        }}))
        # Before ddc.json exists, the inline entries still load.
        self.assertEqual(sorted(ctl.load_ddc_memory(ctl.load_memory())["monitors"]), sorted(["other", self.FP]))
        result = ctl.ddc_forget(self.FP)
        self.assertEqual(sorted(result["ddcMemory"]["monitors"]), ["other"])
        saved = json.loads(self.ddc_file().read_text())
        self.assertIs(saved["migrated"], True)
        # displays.json still holds the stale inline input; it stays forgotten.
        self.assertEqual(sorted(ctl.load_ddc_memory(ctl.load_memory())["monitors"]), ["other"])
        with mock.patch.object(ctl, "connected_monitors", return_value=[]), \
                mock.patch.object(ctl, "lid_closed", return_value=False), \
                mock.patch.object(ctl, "nightlight_state", return_value={"enabled": False, "kelvin": 6500}):
            self.assertNotIn(self.FP, ctl.state()["ddcMemory"]["monitors"])
        # Learning another monitor later does not bring it back either.
        ctl.ddc_learn("DP-3", "third", "0x12")
        self.assertEqual(sorted(ctl.load_ddc_memory(ctl.load_memory())["monitors"]), ["other", "third"])

    def test_ddc_json_writes_take_a_private_state_lock(self):
        import fcntl
        ctl.ddc_learn("HDMI-A-1", self.FP, "0x11")
        lock = self.state / "dicey-bar" / ".ddc.lock"
        self.assertTrue(lock.is_file())
        self.assertEqual(oct(lock.stat().st_mode & 0o777), "0o600")
        # While another helper holds the lock, learn waits for it.
        calls = []
        real_flock = fcntl.flock
        def flock(fd, op):
            calls.append(op)
            return real_flock(fd, op)
        with mock.patch.object(ctl.fcntl, "flock", side_effect=flock):
            ctl.ddc_forget(self.FP)
            ctl.persist({"memory": {"version": 1, "monitors": {}, "layouts": {}}, "forgetDdcFp": "x"})
        self.assertEqual(calls, [fcntl.LOCK_EX, fcntl.LOCK_EX])
        # A planted symlink lock is refused rather than followed.
        lock.unlink()
        lock.symlink_to(self.state / "elsewhere")
        with self.assertRaises(OSError):
            ctl.ddc_learn("HDMI-A-1", self.FP, "0x11")
        self.assertFalse((self.state / "elsewhere").exists())

    def test_forget_removes_only_that_monitor(self):
        ctl.ddc_learn("HDMI-A-1", self.FP, "0x11")
        ctl.ddc_learn("DP-2", "other", "0x0f")
        result = ctl.ddc_forget(self.FP)
        self.assertEqual(sorted(result["ddcMemory"]["monitors"]), ["other"])
        self.assertEqual(sorted(json.loads(self.ddc_file().read_text())["monitors"]), ["other"])

    def test_state_reports_ddc_memory(self):
        memory_file = self.state / "dicey-bar" / "displays.json"
        memory_file.parent.mkdir(parents=True)
        memory_file.write_text(json.dumps({"monitors": {self.FP: {"ddcInput": "0x11", "ddcConnector": "HDMI-A-1"}}}))
        with mock.patch.object(ctl, "connected_monitors", return_value=[]), \
                mock.patch.object(ctl, "lid_closed", return_value=False), \
                mock.patch.object(ctl, "nightlight_state", return_value={"enabled": False, "kelvin": 6500}):
            result = ctl.state()
        self.assertEqual(result["ddcMemory"]["monitors"][self.FP]["input"], "0x11")

    def test_cli_learn_rejects_zero_code(self):
        result = subprocess.run([sys.executable, "-I", str(HELPER), "ddc-learn", "HDMI-A-1", self.FP, "0x00"],
                                capture_output=True, text=True, timeout=30,
                                env={**os.environ, "XDG_STATE_HOME": str(self.state)})
        self.assertEqual(result.returncode, 1)
        self.assertFalse(json.loads(result.stdout)["ok"])
        self.assertFalse(self.ddc_file().exists())


class RescueTest(unittest.TestCase):
    LIVE = [
        {"name": "eDP-1", "disabled": True, "dpmsStatus": True},
        {"name": "HDMI-A-1", "disabled": True, "dpmsStatus": True},
    ]

    def test_rescue_prefers_external_with_lid_closed_and_internal_with_lid_open(self):
        self.assertEqual(ctl.pick_rescue_monitor(self.LIVE, True)["name"], "HDMI-A-1")
        self.assertEqual(ctl.pick_rescue_monitor(self.LIVE, False)["name"], "eDP-1")
        self.assertEqual(ctl.pick_rescue_monitor([self.LIVE[0]], True)["name"], "eDP-1")

    def test_emergency_restore_lights_the_external_behind_a_closed_lid(self):
        with mock.patch.object(ctl, "connected_monitors", side_effect=[self.LIVE, [dict(self.LIVE[1], disabled=False)]]), \
                mock.patch.object(ctl, "lid_closed", return_value=True), \
                mock.patch.object(ctl.subprocess, "run", return_value=completed("ok")) as run:
            errors = ctl._verify_some_display_enabled([])
        commands = [call[0][0] for call in run.call_args_list]
        self.assertIn('output = "HDMI-A-1", disabled = false', commands[0][2])
        # The rescued monitor's DPMS is woken too (best effort).
        self.assertEqual(commands[1], ["hyprctl", "dispatch", "dpms", "on", "HDMI-A-1"])
        self.assertEqual(errors, ["No display stayed enabled; restored HDMI-A-1 safely"])

    def test_lit_check_ignores_a_shut_panel_dpms_off_and_mirrors(self):
        panel = {"name": "eDP-1", "disabled": False, "dpmsStatus": True, "mirrorOf": "none"}
        asleep = {"name": "HDMI-A-1", "disabled": False, "dpmsStatus": False, "mirrorOf": "none"}
        mirror = {"name": "DP-2", "disabled": False, "dpmsStatus": True, "mirrorOf": "eDP-1"}
        lit = {"name": "DP-2", "disabled": False, "dpmsStatus": True, "mirrorOf": "none"}
        # Lid shut: the panel behind it and a DPMS-off external are not lit.
        self.assertFalse(ctl.some_display_lit([panel, asleep], True))
        self.assertTrue(ctl.some_display_lit([panel, asleep], False))
        self.assertFalse(ctl.some_display_lit([dict(panel, disabled=True), mirror], False))
        self.assertTrue(ctl.some_display_lit([panel, asleep, lit], True))
        # A laptop with only its own panel still counts it behind a closed lid.
        self.assertTrue(ctl.some_display_lit([panel], True))
        self.assertFalse(ctl.some_display_lit([dict(panel, dpmsStatus=False)], True))

    def test_lid_closed_and_dpms_off_external_triggers_rescue(self):
        live = [{"name": "eDP-1", "disabled": False, "dpmsStatus": True, "mirrorOf": "none"},
                {"name": "HDMI-A-1", "disabled": False, "dpmsStatus": False, "mirrorOf": "none"}]
        woke = [live[0], dict(live[1], dpmsStatus=True)]
        with mock.patch.object(ctl, "connected_monitors", side_effect=[live, woke]), \
                mock.patch.object(ctl, "lid_closed", return_value=True), \
                mock.patch.object(ctl.subprocess, "run", return_value=completed("ok")) as run:
            errors = ctl._verify_some_display_enabled([])
        commands = [call[0][0] for call in run.call_args_list]
        self.assertIn('output = "HDMI-A-1"', commands[0][2])
        self.assertEqual(commands[1], ["hyprctl", "dispatch", "dpms", "on", "HDMI-A-1"])
        self.assertEqual(errors, ["No display stayed enabled; restored HDMI-A-1 safely"])

    def test_global_dpms_idle_with_enabled_display_skips_rescue(self):
        idle = [{"name": "eDP-1", "disabled": False, "dpmsStatus": False, "mirrorOf": "none"},
                {"name": "HDMI-A-1", "disabled": False, "dpmsStatus": False, "mirrorOf": "none"}]
        with mock.patch.object(ctl, "connected_monitors", return_value=idle), \
                mock.patch.object(ctl, "lid_closed", return_value=False), \
                mock.patch.object(ctl.subprocess, "run") as run:
            self.assertEqual(ctl._verify_some_display_enabled([]), [])
        run.assert_not_called()
        # Only a closed-lid internal panel enabled (even if DPMS-off) still triggers rescue.
        lid_only = [{"name": "eDP-1", "disabled": False, "dpmsStatus": False, "mirrorOf": "none"},
                    {"name": "HDMI-A-1", "disabled": True, "dpmsStatus": False, "mirrorOf": "none"}]
        woke = [lid_only[0], {"name": "HDMI-A-1", "disabled": False, "dpmsStatus": True, "mirrorOf": "none"}]
        with mock.patch.object(ctl, "connected_monitors", side_effect=[lid_only, woke]), \
                mock.patch.object(ctl, "lid_closed", return_value=True), \
                mock.patch.object(ctl.subprocess, "run", return_value=completed("ok")) as run:
            errors = ctl._verify_some_display_enabled([])
        self.assertEqual(errors, ["No display stayed enabled; restored HDMI-A-1 safely"])
        self.assertEqual(run.call_count, 2)


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

    def test_persist_never_reads_ddc_and_ignores_legacy_watch_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            lua = Path(tmp) / "hypr" / "monitors.lua"
            env = {"XDG_STATE_HOME": str(Path(tmp) / "state"), "DICEY_DISPLAYS_MONITORS_LUA": str(lua)}
            plan = [dict(output(), ddcWatch=True)]
            self.assertNotIn("ddcWatch", ctl.validate_output(plan[0]))
            memory = {"version": 1, "monitors": {"x": {"mode": {"w": 1, "h": 1, "hz": 60}}}, "layouts": {}}
            boom = mock.Mock(side_effect=AssertionError("persist must never touch DDC/CI"))
            with mock.patch.dict(os.environ, env), mock.patch.object(ctl, "ddc_read", boom), \
                    mock.patch.object(ctl, "read_vcp_input", boom), mock.patch.object(ctl, "ddc_buses", boom):
                result = ctl.persist({"plan": plan, "memory": memory})
            self.assertTrue(result["ok"])
            self.assertTrue(result["monitorsLuaChanged"])

    def test_persist_carries_legacy_inputs_to_ddc_json_before_memory_drops_them(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "state"
            env = {"XDG_STATE_HOME": str(state)}
            fp = "BNQ|BenQ GW2790Q|ET97T00426087"
            legacy = {"version": 1, "monitors": {fp: {"ddcWatch": True, "ddcInput": "0x11",
                                                      "ddcConnector": "HDMI-A-1"}}, "layouts": {}}
            with mock.patch.dict(os.environ, env):
                ctl.persist({"memory": legacy})
                self.assertFalse((state / "dicey-bar" / "ddc.json").exists())
                # The next save (remember() strips the old fields) moves the input over first.
                ctl.persist({"memory": {"version": 1, "monitors": {fp: {}}, "layouts": {}}})
                ddc = json.loads((state / "dicey-bar" / "ddc.json").read_text())
                self.assertEqual(ddc["monitors"][fp]["input"], "0x11")
                self.assertEqual(ctl.load_ddc_memory(ctl.load_memory())["monitors"][fp]["connector"], "HDMI-A-1")

    def test_persist_forget_drops_the_learned_input_in_the_same_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "state"
            fp = "BNQ|BenQ GW2790Q|ET97T00426087"
            with mock.patch.dict(os.environ, {"XDG_STATE_HOME": str(state)}):
                ctl.ddc_learn("HDMI-A-1", fp, "0x11")
                ctl.ddc_learn("DP-2", "keep", "0x0f")
                result = ctl.persist({"memory": {"version": 1, "monitors": {}, "layouts": {}}, "forgetDdcFp": fp})
                self.assertEqual(sorted(result["ddcMemory"]["monitors"]), ["keep"])
                self.assertEqual(sorted(json.loads((state / "dicey-bar" / "ddc.json").read_text())["monitors"]), ["keep"])
                # Forget alone is a valid persist; a bad fingerprint is not.
                self.assertTrue(ctl.persist({"forgetDdcFp": "keep"})["ok"])
                with self.assertRaises(ValueError):
                    ctl.persist({"forgetDdcFp": "a\nb"})

    def test_cli_reports_invalid_input_as_json(self):
        result = subprocess.run([sys.executable, "-I", str(HELPER), "apply"], input="[]",
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 1)
        self.assertFalse(json.loads(result.stdout)["ok"])


if __name__ == "__main__":
    unittest.main()
