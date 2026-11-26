#!/usr/bin/env python3
"""Hyprland and file I/O for the Dicey Bar Displays panel.

The panel and its hotplug service decide what each monitor should do
(displays/DisplayModel.js). This helper only carries that out:

  state                     JSON: connected monitors, HDR support, lid, nightLight, memory, ddcMemory
  apply                     stdin plan -> live `hyprctl eval`, then read back
  revert                    stdin plan -> re-read connected monitors & safely roll back
  persist                   stdin {"plan": [...]?, "memory": {...}?, "removeManagedBlock": bool?,
                                   "forgetDdcFp": "<fp>"?}
  ddc-read [--detect NAME]... NAME[:BUS][@scdc|@ddc]...
                            JSON: one read-only reading per connector, each
                            {"status": "ok"|"ambiguous"|"busy"|"no_tool", "code"?, "bus"?}
                            or, from HDMI SCDC, {"status": "ok", "signal": "scdc",
                            "scdc": "locked"|"unlocked"|"absent"}. `@scdc` reads only
                            SCDC (never ddcutil); `@ddc` reads only VCP 0x60 (never
                            I2C 0x54); without either a sysfs-linked connector is
                            checked for SCDC lock first, then VCP 0x60.
                            `ddcutil detect` only runs for connectors named by --detect.
  ddc-learn NAME FP CODE    JSON: remember CODE as FP's input on connector NAME (ddc.json)
  ddc-forget FP             JSON: drop FP's learned input from ddc.json
  nightlight on KELVIN|off  JSON: control hyprsunset temperature & notify bar

The bar decides when a monitor is paused for an input switch (DisplayModel.js);
this helper only ever reads VCP 0x60 (DDC/CI) or the read-only HDMI SCDC
Sink_Version / Config_1 / Status_Flags_0 registers (I2C 0x54), and never writes to a
monitor over I2C.

Every value written into Lua is validated or emitted as an escaped string
literal, because monitors.lua is code Hyprland runs.
"""
import contextlib
import ctypes
import errno
import fcntl
import glob
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

# scripts/dev.py rewrites `"dicey.bar"` to `"dicey.bar.dev"` in dev copies so
# development mode stays isolated from production state and monitors.lua.
PLUGIN_ID = "dicey.bar"
IS_DEV = PLUGIN_ID != ("dicey." + "bar")

BEGIN = "-- BEGIN dicey.bar displays"
END = "-- END dicey.bar displays"
FOREIGN_BLOCKS = ("im0001gt.screens",)
TAKEOVER_HEADER = "-- Monitor rules are managed by the Displays panel in Dicey Bar (block below)."
CATCH_ALL = 'hl.monitor({ output = "", mode = "preferred", position = "auto", scale = "auto" })'
HYPRCTL_TIMEOUT = 10
DDC_TIMEOUT = 4
DDC_DETECT_TIMEOUT = 20
DAY_KELVIN = 6500
CONNECTOR_RE = re.compile(r"[A-Za-z0-9._-]+")
MIRROR_RE = re.compile(r"[A-Za-z0-9._-]+")

# Linux i2c-dev ioctls (<linux/i2c-dev.h>, <linux/i2c.h>). I2C_SLAVE (never
# I2C_SLAVE_FORCE) so an address a kernel driver owns is left alone.
I2C_SLAVE = 0x0703
I2C_SMBUS = 0x0720
I2C_SMBUS_READ = 1
I2C_SMBUS_BYTE_DATA = 2
# HDMI 2.0/2.1 SCDC: read-only register reads only. Sink_Version (0x01) is
# nonzero on any SCDC sink; Config_1 (0x31, bits 3:0) gives FRL_Rate (0 = TMDS,
# 1-2 = 3-lane FRL, 3-6 = 4-lane FRL); Status_Flags_0 (0x40) holds
# Clock_Detected + Ch0..2_Locked (TMDS, 0x0F) or FRL lane locks (0x0E / 0x1E).
# None of these registers is clear-on-read.
SCDC_ADDR = 0x54
SCDC_SINK_VERSION = 0x01
SCDC_CONFIG_1 = 0x31
SCDC_STATUS_FLAGS_0 = 0x40
SCDC_LOCK_MASK = 0x1F
SCDC_TMDS_LOCK = 0x0F
SCDC_FRL_3LANE_LOCK = 0x0E
SCDC_FRL_4LANE_LOCK = 0x1E
INTERNAL_RE = re.compile(r"^(eDP|LVDS|DSI)-")
# How an input-paused monitor is kept off: "mirror" (parked lit as a mirror,
# HDMI SCDC) or "disable" (DDC/CI); "" for an older caller (disable).
PAUSE_MODES = ("", "mirror", "disable")

MCCS_INPUTS = {
    "0x01": "VGA-1",
    "0x02": "VGA-2",
    "0x03": "DVI-1",
    "0x04": "DVI-2",
    "0x05": "Composite-1",
    "0x06": "Composite-2",
    "0x07": "S-Video-1",
    "0x08": "S-Video-2",
    "0x0f": "DP-1",
    "0x10": "DP-2",
    "0x11": "HDMI-1",
    "0x12": "HDMI-2",
    "0x19": "USB-C-1",
    "0x1b": "USB-C",
}


def home() -> Path:
    return Path(os.environ.get("HOME") or Path.home())


def state_dir() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or str(home() / ".local" / "state")
    folder = "dicey-bar-dev" if IS_DEV else "dicey-bar"
    return Path(base) / folder


def memory_path() -> Path:
    return state_dir() / "displays.json"


def ddc_memory_path() -> Path:
    """Learned DDC/CI inputs live apart from displays.json so the slow, automatic
    learning path can never race or rewrite layout memory."""
    return state_dir() / "ddc.json"


def i2c_dev_root() -> Path:
    return Path(os.environ.get("DICEY_I2C_DEV_ROOT") or "/dev")


def sysfs_drm_root() -> Path:
    return Path(os.environ.get("DICEY_SYSFS_DRM") or "/sys/class/drm")


def brightness_lock_path() -> Path:
    # Same lock omarchy-brightness-display holds, so a DDC read and a brightness
    # write never share the I2C bus at once.
    return Path(os.environ.get("XDG_RUNTIME_DIR") or "/tmp") / "omarchy-brightness-display.lock"


def monitors_lua_path() -> Path:
    override = os.environ.get("DICEY_DISPLAYS_MONITORS_LUA")
    if override:
        return Path(override)
    if IS_DEV:
        return state_dir() / "monitors.lua"
    return home() / ".config" / "hypr" / "monitors.lua"


# ------------------------------------------------------------------ Lua

def lua_string(value) -> str:
    """A Lua string literal for any text: printable ASCII stays, everything
    else (quotes, backslashes, newlines, UTF-8 bytes) becomes a \\ddd escape."""
    out = ['"']
    for byte in str(value).encode("utf-8"):
        char = chr(byte)
        if 0x20 <= byte < 0x7F and char not in '"\\':
            out.append(char)
        else:
            out.append("\\%03d" % byte)
    out.append('"')
    return "".join(out)


def _int(value, low, high, what):
    if isinstance(value, bool):
        raise ValueError(f"invalid {what}")
    number = int(value)
    if float(number) != float(value) or not low <= number <= high:
        raise ValueError(f"invalid {what}: {value!r}")
    return number


def _number(value, low, high, what):
    if isinstance(value, bool):
        raise ValueError(f"invalid {what}")
    number = float(value)
    if not (low <= number <= high):
        raise ValueError(f"invalid {what}: {value!r}")
    return number


def _fmt(number: float) -> str:
    text = f"{number:.3f}".rstrip("0").rstrip(".")
    return text or "0"


def _clean_mirror_field(value, what: str) -> str:
    text = str(value or "").strip()
    if text.lower() == "none":
        text = ""
    if "\n" in text or (text and not MIRROR_RE.fullmatch(text)):
        raise ValueError(f"invalid {what}: {text!r}")
    return text


def validate_output(output: dict, *, allow_preferred_mode: bool = False) -> dict:
    if not isinstance(output, dict):
        raise ValueError("output must be an object")
    selector = str(output.get("selector") or output.get("name") or "")
    name = str(output.get("name") or "")
    if not selector or not name or "\n" in selector or "\n" in name:
        raise ValueError("output needs a name and selector")
    mirror_raw = _clean_mirror_field(output.get("mirrorOf"), "mirrorOf")
    park_raw = _clean_mirror_field(output.get("parkMirrorOf"), "parkMirrorOf")
    clean = {
        "name": name,
        "selector": selector,
        "fp": str(output.get("fp") or name),
        "enabled": output.get("enabled") is not False,
        "autoOff": output.get("autoOff") is True,
        "offReason": str(output.get("offReason") or ""),
        "hdr": output.get("hdr") is True,
        "vrrManaged": output.get("vrrManaged") is not False,
        "mirrorOf": mirror_raw,
        "parkMirrorOf": park_raw,
        "pauseMode": str(output.get("pauseMode") or ""),
    }
    if clean["offReason"] not in ("", "lid", "input"):
        raise ValueError(f"invalid offReason: {clean['offReason']!r}")
    if clean["pauseMode"] not in PAUSE_MODES:
        raise ValueError(f"invalid pauseMode: {clean['pauseMode']!r}")
    # How a monitor is kept off only means something while it is input-paused.
    if not (clean["autoOff"] and clean["offReason"] == "input"):
        clean["pauseMode"] = ""
    mode = output.get("mode") or {}
    try:
        clean["mode"] = {
            "w": _int(mode.get("w"), 1, 32768, "width"),
            "h": _int(mode.get("h"), 1, 32768, "height"),
            "hz": _number(mode.get("hz"), 1, 1000, "refresh rate"),
        }
    except (ValueError, TypeError):
        # An output that is off (or an emergency revert entry) may have no mode.
        if clean["enabled"] and not clean["autoOff"] and not allow_preferred_mode:
            raise
        clean["mode"] = None
    clean["x"] = _int(output.get("x", 0), -100000, 100000, "x")
    clean["y"] = _int(output.get("y", 0), -100000, 100000, "y")
    clean["scale"] = _number(output.get("scale", 1), 0.1, 10, "scale")
    clean["vrr"] = _int(output.get("vrr", 0), 0, 2, "vrr")
    clean["transform"] = _int(output.get("transform", 0), 0, 7, "transform")
    clean["sdrBrightness"] = _number(output.get("sdrBrightness", 1), 0.5, 2.0, "sdrBrightness")
    clean["sdrSaturation"] = _number(output.get("sdrSaturation", 1), 0.5, 1.5, "sdrSaturation")
    return clean


def _settle_pending_mirror_pause(o: dict) -> dict:
    """A monitor that pauses by mirror-parking but has no park target is never
    disabled (that would drop the link it relocks on): it stays on, extended."""
    if (o.get("autoOff") and o.get("offReason") == "input" and o.get("pauseMode") == "mirror"
            and not o.get("parkMirrorOf")):
        o.update(enabled=True, autoOff=False, offReason="", pauseMode="")
    return o


def validate_plan(plan) -> list:
    if not isinstance(plan, list) or not plan:
        raise ValueError("plan must be a non-empty list")
    outputs = [_settle_pending_mirror_pause(validate_output(o)) for o in plan]
    if not any(o["enabled"] and not o["autoOff"] and not o["mirrorOf"] for o in outputs):
        raise ValueError("plan would leave every display off")
    by_name = {o["name"]: o for o in outputs}
    for o in outputs:
        if not o["mirrorOf"]:
            continue
        if o["mirrorOf"] == o["name"]:
            raise ValueError(f"output {o['name']} cannot mirror itself")
        target = by_name.get(o["mirrorOf"])
        if not target or not target["enabled"] or target["autoOff"]:
            raise ValueError(f"mirror target {o['mirrorOf']} is not active")
        if target["mirrorOf"]:
            raise ValueError(f"cannot mirror another mirror ({o['mirrorOf']})")
    for o in outputs:
        # Mirror-parking: an input-paused monitor kept lit (TMDS alive) as a
        # mirror of an active output, so it relocks the moment it is back.
        if not o["parkMirrorOf"]:
            continue
        if not o["autoOff"] or o["offReason"] != "input":
            raise ValueError(f"output {o['name']} is parked but not paused for an input switch")
        if o["parkMirrorOf"] == o["name"]:
            raise ValueError(f"output {o['name']} cannot park on itself")
        target = by_name.get(o["parkMirrorOf"])
        if not target or not target["enabled"] or target["autoOff"] or target["mirrorOf"]:
            raise ValueError(f"park target {o['parkMirrorOf']} is not an active output")
    return outputs


def monitor_rule(output: dict, *, keep_on: bool = False, use_connector_name: bool = False) -> str:
    """One hl.monitor call. Hyprland merges a rule into the one already set for
    that output, so every managed field is spelled out. Disabled rules always
    use the exact connector name so a prefix-matched `desc:` rule can never
    disable a future hotplugged display."""
    is_disabled = not output["enabled"] and not keep_on
    target = output["name"] if (use_connector_name or is_disabled) else output["selector"]
    selector = lua_string(target)
    if is_disabled:
        return f"hl.monitor({{ output = {selector}, disabled = true }})"
    mode = output["mode"]
    if mode is None:
        return (f'hl.monitor({{ output = {selector}, disabled = false, mode = "preferred", position = "auto", '
                f'scale = "auto", mirror = {lua_string(output.get("mirrorOf") or "")} }})')
    sdr_b = output["sdrBrightness"] if output["hdr"] else 1
    sdr_s = output["sdrSaturation"] if output["hdr"] else 1
    fields = [
        f"output = {selector}",
        "disabled = false",
        f'mode = "{mode["w"]}x{mode["h"]}@{mode["hz"]:.2f}"',
        f'position = "{output["x"]}x{output["y"]}"',
        f"scale = {_fmt(output['scale'])}",
        f"transform = {output['transform']}",
    ]
    if output.get("vrrManaged", True):
        fields.append(f"vrr = {output['vrr']}")
    fields.extend([
        'cm = "hdr"' if output["hdr"] else 'cm = "srgb"',
        f"bitdepth = {10 if output['hdr'] else 8}",
        f"sdrbrightness = {_fmt(sdr_b)}",
        f"sdrsaturation = {_fmt(sdr_s)}",
        f"mirror = {lua_string(output.get('mirrorOf') or '')}",
    ])
    return "hl.monitor({ " + ", ".join(fields) + " })"


def live_lua(outputs: list) -> str:
    """Rules for `hyprctl eval`. Always addresses outputs by exact connector
    name (never `desc:`) to avoid Hyprland prefix-match ambiguity. Non-mirroring
    enabled monitors come first so mirror sources exist and no step leaves
    nothing lit; mirrored monitors next; then input-paused monitors parked as a
    mirror (still lit, so their link stays up, but out of the layout); then
    disabled/input-switched monitors. Parking is live only: file_block never
    writes it to monitors.lua. A monitor whose pauseMode is "mirror" is never
    disabled: without a park target it stays on, extended."""
    def parked(o):
        return o["autoOff"] and o["offReason"] == "input" and bool(o.get("parkMirrorOf"))
    outputs = [_settle_pending_mirror_pause(dict(o, pauseMode=o.get("pauseMode") or "")) for o in outputs]
    primary_on = [o for o in outputs if o["enabled"] and not o["autoOff"] and not o["mirrorOf"]]
    mirror_on = [o for o in outputs if o["enabled"] and not o["autoOff"] and o["mirrorOf"]]
    parked_mirror = [o for o in outputs if parked(o)]
    off = [o for o in outputs if (not o["enabled"] and not o["autoOff"])
           or (o["offReason"] == "input" and not parked(o))]
    lines = [monitor_rule(o, use_connector_name=True) for o in primary_on]
    lines += [monitor_rule(o, use_connector_name=True) for o in mirror_on]
    lines += [monitor_rule(dict(o, enabled=True, mirrorOf=o["parkMirrorOf"]), use_connector_name=True)
              for o in parked_mirror]
    lines += [monitor_rule(dict(o, enabled=False), use_connector_name=True) for o in off]
    return "\n".join(lines)


def file_block(outputs: list) -> str:
    """The managed block for monitors.lua. Contains strictly our own generated
    rules so subsequent rewrites can never drop or mangle user/foreign lines.
    A mirror-parked monitor is written as its plain self (never parked)."""
    lines = [BEGIN, "-- Written by the Displays panel in Dicey Bar. Changes here are replaced."]
    lines.append(CATCH_ALL)
    for output in outputs:
        lines.append(monitor_rule(output, keep_on=output["autoOff"]))
    lines.append(END)
    return "\n".join(lines)


def _match_long_bracket(text: str, pos: int):
    """If text[pos:] begins with a Lua long bracket `[===[`, return (eq_count, end_of_opener)."""
    if pos >= len(text) or text[pos] != "[":
        return None
    j = pos + 1
    while j < len(text) and text[j] == "=":
        j += 1
    if j < len(text) and text[j] == "[":
        return j - pos - 1, j + 1
    return None


def _skip_long_bracket_content(text: str, start: int, eq_count: int) -> int:
    closer = "]" + ("=" * eq_count) + "]"
    idx = text.find(closer, start)
    return (idx + len(closer)) if idx != -1 else len(text)


def strip_hl_monitor_calls(text: str) -> str:
    """Remove top-level `hl.monitor(...)` calls (single or multiline) from a
    Lua snippet while preserving all other statements (including strings that
    contain `--` or `hl.monitor(...)`, multiline `hl.config({...})`, etc.)."""
    out = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        # Quoted string literals outside hl.monitor() — preserve verbatim
        if ch in ('"', "'"):
            q = ch
            start = i
            i += 1
            while i < n:
                if text[i] == "\\":
                    i += 2
                    continue
                if text[i] == q:
                    i += 1
                    break
                i += 1
            out.append(text[start:i])
            continue
        # Long-bracket string literals `[==[...]==]` outside hl.monitor() — preserve verbatim
        lb_str = _match_long_bracket(text, i)
        if lb_str:
            end = _skip_long_bracket_content(text, lb_str[1], lb_str[0])
            out.append(text[i:end])
            i = end
            continue
        # Line or block comments
        if ch == "-" and i + 1 < n and text[i + 1] == "-":
            lb = _match_long_bracket(text, i + 2)
            if lb:
                end = _skip_long_bracket_content(text, lb[1], lb[0])
                i = end
                continue
            nl = text.find("\n", i)
            i = n if nl == -1 else nl + 1
            continue
        # Check for `hl.monitor(` call at identifier boundary
        if text.startswith("hl.monitor", i) and (i == 0 or not (text[i - 1].isalnum() or text[i - 1] == "_")):
            j = i + len("hl.monitor")
            while j < n and text[j] in " \t\r\n":
                j += 1
            if j < n and text[j] == "(":
                depth = 0
                k = j
                while k < n:
                    c = text[k]
                    if c in ('"', "'"):
                        q = c
                        k += 1
                        while k < n:
                            if text[k] == "\\":
                                k += 2
                                continue
                            if text[k] == q:
                                k += 1
                                break
                            k += 1
                        continue
                    if c == "-" and k + 1 < n and text[k + 1] == "-":
                        lb = _match_long_bracket(text, k + 2)
                        if lb:
                            k = _skip_long_bracket_content(text, lb[1], lb[0])
                        else:
                            nl = text.find("\n", k)
                            k = n if nl == -1 else nl + 1
                        continue
                    lb = _match_long_bracket(text, k)
                    if lb:
                        k = _skip_long_bracket_content(text, lb[1], lb[0])
                        continue
                    if c == "(":
                        depth += 1
                    elif c == ")":
                        depth -= 1
                        if depth == 0:
                            k += 1
                            break
                    k += 1
                if depth != 0:
                    raise ValueError("unbalanced hl.monitor call in foreign block")
                # Consume trailing spaces/semicolon/newline belonging to the stripped call
                while k < n and text[k] in " \t;":
                    k += 1
                if k < n and text[k] == "\n":
                    k += 1
                i = k
                continue
        out.append(ch)
        i += 1
    cleaned_lines = [ln for ln in "".join(out).splitlines() if ln.strip()]
    return "\n".join(cleaned_lines)


def _block_span(lines: list, begin: str, end: str):
    """Find the innermost valid BEGIN..END span: tracks the most recent BEGIN
    before END so a stray BEGIN earlier in the file never swallows user lines."""
    start = None
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped == begin:
            start = index
        elif start is not None and stripped == end:
            return start, index
    return None


def validate_lua_syntax(text: str) -> None:
    """Run `luac -p -` when available to refuse writing broken Lua."""
    luac = shutil.which("luac")
    if not luac:
        return
    result = subprocess.run([luac, "-p", "-"], input=text, capture_output=True, text=True, timeout=5)
    if result.returncode != 0:
        err = (result.stderr or result.stdout or "").strip()
        raise ValueError(f"generated monitors.lua failed syntax check: {err}")


def splice_block(text: str, outputs: list) -> str:
    """monitors.lua with the managed block replaced. On first takeover of a
    known foreign display manager, any non-monitor settings it held (including
    multiline `hl.config({...})`) are preserved OUTSIDE our managed markers so
    later rewrites only ever touch our own generated lines."""
    lines = text.split("\n") if text else []
    span = _block_span(lines, BEGIN, END)
    carried_outside = []
    if span is None:
        for owner in FOREIGN_BLOCKS:
            span = _block_span(lines, f"-- BEGIN {owner}", f"-- END {owner}")
            if span is not None:
                body = "\n".join(lines[span[0] + 1:span[1]])
                preserved = strip_hl_monitor_calls(body)
                if preserved:
                    carried_outside = preserved.split("\n")
                lines = [TAKEOVER_HEADER if line.strip().startswith(f"-- Managed by {owner}") else line
                         for line in lines]
                break
    else:
        # Backward compatibility: if an older dicey.bar block already held
        # non-monitor lines inside its markers, hoist them cleanly outside.
        body = "\n".join(lines[span[0] + 1:span[1]])
        preserved = strip_hl_monitor_calls(body)
        if preserved:
            carried_outside = preserved.split("\n")

    block = file_block(outputs).split("\n")
    replacement = (carried_outside + [""] + block) if carried_outside else block
    if span is None:
        while lines and lines[-1] == "":
            lines.pop()
        if lines:
            lines.append("")
        lines.extend(replacement)
    else:
        lines[span[0]:span[1] + 1] = replacement
    result = "\n".join(lines).rstrip("\n") + "\n"
    validate_lua_syntax(result)
    return result


def remove_managed_block(text: str) -> str:
    """Remove our managed block from monitors.lua (used when all memory is forgotten)."""
    lines = text.split("\n") if text else []
    span = _block_span(lines, BEGIN, END)
    if span is None:
        return text
    body = "\n".join(lines[span[0] + 1:span[1]])
    preserved = strip_hl_monitor_calls(body)
    replacement = preserved.split("\n") if preserved else []
    lines[span[0]:span[1] + 1] = replacement
    result = "\n".join(lines).rstrip("\n")
    result = (result + "\n") if result else ""
    if result:
        validate_lua_syntax(result)
    return result


def atomic_write(path: Path, text: str, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp = tempfile.mkstemp(prefix="." + path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temp, mode)
        os.replace(temp, path)
    except BaseException:
        try:
            os.unlink(temp)
        except FileNotFoundError:
            pass
        raise


def write_monitors_lua(outputs: list, path: Path = None, *, remove_block: bool = False) -> bool:
    """Writes or removes the managed block. Left alone when unchanged so
    Hyprland doesn't reload needlessly. Keeps a one-time backup and preserves
    existing file permissions."""
    path = path or monitors_lua_path()
    if path.is_symlink():
        raise ValueError(f"refusing to write through a symlink: {path}")
    existed = path.exists()
    original = path.read_text(encoding="utf-8") if existed else ""
    file_mode = (os.stat(path).st_mode & 0o777) if existed else 0o644
    updated = remove_managed_block(original) if remove_block else splice_block(original, outputs)
    if updated == original:
        return False
    backup = path.with_name(path.name + ".dicey-bar-backup")
    if original and BEGIN not in original and not backup.exists():
        atomic_write(backup, original, file_mode)
    atomic_write(path, updated, file_mode)
    return True


# ------------------------------------------------------------------ state

def edid_hdr_capable(edid: bytes) -> bool:
    """True when the EDID's CTA-861 extension carries an HDR static metadata
    block (extended tag 6)."""
    for start in range(128, len(edid) - 127, 128):
        block = edid[start:start + 128]
        if block[0] != 0x02:
            continue
        end = block[2]
        if end < 4 or end > 127:
            end = 127
        index = 4
        while index < end:
            header = block[index]
            tag, length = header >> 5, header & 0x1F
            if tag == 7 and length >= 1 and index + 1 < 128 and block[index + 1] == 6:
                return True
            index += 1 + length
    return False


def connector_paths(name: str) -> list:
    if not CONNECTOR_RE.fullmatch(name or ""):
        return []
    return sorted(glob.glob(str(sysfs_drm_root() / f"card*-{name}")))


def hdr_capable(name: str) -> bool:
    for path in connector_paths(name):
        try:
            data = Path(path, "edid").read_bytes()
        except OSError:
            continue
        if data:
            return edid_hdr_capable(data)
    return False


def lid_closed() -> bool:
    # Prefer Omarchy's own lid detector (handles ACPI + logind/Apple Silicon).
    helper = shutil.which("omarchy-hw-laptop-closed")
    if helper:
        try:
            result = subprocess.run([helper], capture_output=True, timeout=3)
            return result.returncode == 0
        except (OSError, subprocess.SubprocessError):
            pass
    for path in glob.glob("/proc/acpi/button/lid/*/state"):
        try:
            if "closed" in Path(path).read_text():
                return True
        except OSError:
            pass
    return False


def hyprctl_json(*args):
    result = subprocess.run(["hyprctl", "-j", *args], capture_output=True, text=True,
                            timeout=HYPRCTL_TIMEOUT, check=True)
    return json.loads(result.stdout)


def load_memory() -> dict:
    try:
        data = json.loads(memory_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def parse_hyprsunset_temp(text: str):
    match = re.search(r"\b(\d{4})\b", text or "")
    if not match:
        return None
    temp = int(match.group(1))
    return temp if 1000 <= temp <= 10000 else None


def nightlight_state() -> dict:
    try:
        result = subprocess.run(["hyprctl", "hyprsunset", "temperature"],
                                capture_output=True, text=True, timeout=2)
    except (OSError, subprocess.SubprocessError):
        return {"enabled": False, "kelvin": DAY_KELVIN}
    if result.returncode != 0:
        return {"enabled": False, "kelvin": DAY_KELVIN}
    temp = parse_hyprsunset_temp(result.stdout)
    if temp is None:
        return {"enabled": False, "kelvin": DAY_KELVIN}
    return {"enabled": temp < DAY_KELVIN, "kelvin": temp}


def set_nightlight(enabled: bool, kelvin: int = 4000) -> dict:
    errors = []
    if enabled:
        target = _int(kelvin, 1500, 6400, "kelvin")
        running = subprocess.run(["pgrep", "-x", "hyprsunset"], capture_output=True).returncode == 0
        if not running:
            subprocess.Popen(["hyprsunset"], stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)
            for _ in range(15):
                time.sleep(0.1)
                probe = subprocess.run(["hyprctl", "hyprsunset", "temperature"],
                                       capture_output=True, text=True, timeout=2)
                if probe.returncode == 0:
                    break
        result = subprocess.run(["hyprctl", "hyprsunset", "temperature", str(target)],
                                capture_output=True, text=True, timeout=4)
        ok = result.returncode == 0
        if not ok:
            errors.append((result.stderr or result.stdout or "").strip() or "hyprctl hyprsunset failed")
    else:
        running = subprocess.run(["pgrep", "-x", "hyprsunset"], capture_output=True).returncode == 0
        ok = True
        if running:
            r1 = subprocess.run(["hyprctl", "hyprsunset", "temperature", str(DAY_KELVIN)],
                                capture_output=True, text=True, timeout=4)
            r2 = subprocess.run(["hyprctl", "hyprsunset", "identity"],
                                capture_output=True, text=True, timeout=4)
            if r1.returncode != 0 and r2.returncode != 0:
                ok = False
                errors.append((r2.stderr or r1.stderr or "").strip() or "failed to disable hyprsunset")
    # Refresh Omarchy's nightlight service indicator using Omarchy's real CLI syntax.
    try:
        subprocess.run(["omarchy-shell", "-q", "nightlight", "refresh"],
                       capture_output=True, text=True, timeout=2)
    except (OSError, subprocess.SubprocessError):
        pass
    actual = nightlight_state()
    return {"ok": ok, "errors": errors, "nightLight": actual}


def connected_monitors() -> list:
    raw = hyprctl_json("monitors", "all")
    id_to_name = {}
    for m in raw:
        name = str(m.get("name") or "")
        if name and name != "FALLBACK" and m.get("id") is not None:
            id_to_name[str(m["id"])] = name
    monitors = []
    for monitor in raw:
        name = str(monitor.get("name") or "")
        # Hyprland's stand-in once nothing else is left; never a real monitor.
        if not name or name == "FALLBACK":
            continue
        # Normalize Hyprland 0.56+ numeric monitor ID strings in mirrorOf to connector names.
        mirror_of = str(monitor.get("mirrorOf") or "").strip()
        if mirror_of in id_to_name:
            monitor["mirrorOf"] = id_to_name[mirror_of]
        monitor["hdrCapable"] = hdr_capable(name)
        monitors.append(monitor)
    return monitors


def suspend_clock_ms() -> int:
    """Milliseconds spent suspended since boot (CLOCK_BOOTTIME - CLOCK_MONOTONIC)."""
    try:
        return max(0, int(round((time.clock_gettime(time.CLOCK_BOOTTIME)
                                 - time.clock_gettime(time.CLOCK_MONOTONIC)) * 1000)))
    except (AttributeError, OSError):
        return 0


def state() -> dict:
    mem = load_memory()
    return {
        "monitors": connected_monitors(),
        "lidClosed": lid_closed(),
        "nightLight": nightlight_state(),
        "memory": mem,
        "ddcMemory": load_ddc_memory(mem),
        "suspendMs": suspend_clock_ms(),
    }


# ------------------------------------------------------------------ apply & safe revert

def _live_mirror_of(monitor: dict) -> str:
    text = str(monitor.get("mirrorOf") or "").strip()
    return "" if text.lower() == "none" else text


def some_display_lit(live: list, lid_is_closed: bool, *, require_dpms: bool = True) -> bool:
    """True when a connected display is enabled, (when `require_dpms`) DPMS-on,
    not a mirror, and not an internal panel behind a closed lid. A laptop with
    nothing but its own panel counts that panel even with the lid shut."""
    def on(m):
        return not m.get("disabled") and (not require_dpms or m.get("dpmsStatus", True) is not False)
    names = [str(m.get("name") or "") for m in live]
    only_internal = bool(names) and all(INTERNAL_RE.match(n) for n in names)
    if lid_is_closed and only_internal:
        return any(on(m) for m in live)
    return any(on(m) and not _live_mirror_of(m)
               and not (lid_is_closed and INTERNAL_RE.match(str(m.get("name") or "")))
               for m in live)


def _verify_some_display_enabled(outputs: list) -> list:
    """Confirm at least one real connected, DPMS-on display remained lit after apply."""
    try:
        live = connected_monitors()
    except (OSError, ValueError, subprocess.SubprocessError):
        return []
    if not live:
        return []
    lid_is_closed = lid_closed()
    if some_display_lit(live, lid_is_closed):
        return []
    # If the whole desktop entered global DPMS idle during apply while at least
    # one valid non-mirrored display stayed enabled, intentional blanking is not
    # a display failure.
    if _dpms_view(live)[1] and some_display_lit(live, lid_is_closed, require_dpms=False):
        return []
    # Emergency recovery: re-enable one connected monitor so the user is never
    # stranded on a black screen. Like safe_revert_plan, prefer an external
    # monitor while the lid is shut (the panel is behind it) and the internal
    # panel while it is open.
    rescue = pick_rescue_monitor(live, lid_is_closed)
    rule = f'hl.monitor({{ output = {lua_string(rescue["name"])}, disabled = false, mode = "preferred", position = "auto", scale = "auto", mirror = "" }})'
    res = subprocess.run(["hyprctl", "eval", rule], capture_output=True, text=True, timeout=HYPRCTL_TIMEOUT)
    reply = (res.stdout + res.stderr).strip()
    if res.returncode != 0 or (reply and reply != "ok"):
        return [f"No display stayed enabled; emergency restore of {rescue['name']} failed: {reply or res.returncode}"]
    # A rescued monitor that is DPMS-asleep is still dark: wake it, best effort.
    try:
        subprocess.run(["hyprctl", "dispatch", "dpms", "on", rescue["name"]],
                       capture_output=True, text=True, timeout=HYPRCTL_TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        after = connected_monitors()
        if some_display_lit(after, lid_is_closed):
            return [f"No display stayed enabled; restored {rescue['name']} safely"]
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    return [f"No display stayed enabled; could not verify recovery of {rescue['name']}"]


def pick_rescue_monitor(live: list, lid_is_closed: bool) -> dict:
    internal = [m for m in live if INTERNAL_RE.match(str(m.get("name") or ""))]
    external = [m for m in live if not INTERNAL_RE.match(str(m.get("name") or ""))]
    ordered = (external + internal) if lid_is_closed else (internal + external)
    return ordered[0] if ordered else live[0]


def apply(outputs: list) -> dict:
    lua = live_lua(outputs)
    errors = []
    if lua:
        result = subprocess.run(["hyprctl", "eval", lua], capture_output=True, text=True,
                                timeout=HYPRCTL_TIMEOUT)
        reply = (result.stdout + result.stderr).strip()
        if result.returncode != 0 or (reply and reply != "ok"):
            errors.append(reply or f"hyprctl eval exited {result.returncode}")
    errors.extend(_verify_some_display_enabled(outputs))
    return {"ok": not errors, "errors": errors, "fallbacks": fallbacks(outputs)}


def safe_revert_plan(plan: list, current_monitors: list, lid_is_closed: bool = False) -> list:
    """Filter a saved revert plan to monitors that are STILL connected right now,
    and guarantee at least one connected display stays enabled & non-mirrored."""
    live_by_name = {m["name"]: m for m in current_monitors if m.get("name")}
    id_to_name = {str(m["id"]): m["name"] for m in current_monitors if m.get("name") and m.get("id") is not None}
    kept = []
    for raw in plan:
        try:
            o = validate_output(raw, allow_preferred_mode=True)
        except (ValueError, TypeError):
            continue
        if o["mirrorOf"] in id_to_name:
            o["mirrorOf"] = id_to_name[o["mirrorOf"]]
        if o["parkMirrorOf"] in id_to_name:
            o["parkMirrorOf"] = id_to_name[o["parkMirrorOf"]]
        if o["name"] in live_by_name:
            kept.append(o)
    if not kept:
        # Topology changed completely: leave all currently connected monitors on,
        # allowing `mode = None` ("preferred") if a disabled replacement reports 0x0.
        return [
            validate_output({
                "name": m["name"],
                "selector": m["name"],
                "enabled": True,
                "autoOff": False,
                "mode": {"w": int(m["width"]), "h": int(m["height"]), "hz": float(m["refreshRate"])}
                if (int(m.get("width") or 0) > 0 and int(m.get("height") or 0) > 0 and float(m.get("refreshRate") or 0) > 0)
                else None,
                "x": m.get("x", 0),
                "y": m.get("y", 0),
                "scale": m.get("scale", 1) or 1,
                "transform": m.get("transform", 0) or 0,
            }, allow_preferred_mode=True)
            for m in current_monitors if m.get("name")
        ]
    # Clear any mirror targeting a monitor that is no longer connected/active.
    kept_by_name = {o["name"]: o for o in kept}
    for o in kept:
        t = kept_by_name.get(o["mirrorOf"])
        if o["mirrorOf"] and (not t or not t["enabled"] or t["autoOff"] or t["mirrorOf"]):
            o["mirrorOf"] = ""
    # A mirror-park stays a park (live only, never a mirror the user set up),
    # but only onto a target that is still connected, left on by this plan
    # (enabled, not a mirror, not paused) and not an internal panel behind a
    # closed lid. Otherwise the parked monitor comes back on, extended: never
    # `disabled`, which would drop the link it relocks on.
    for o in kept:
        if not o["parkMirrorOf"] and not (o["autoOff"] and o["offReason"] == "input" and o["pauseMode"] == "mirror"):
            continue
        target_name = o["parkMirrorOf"]
        t = kept_by_name.get(target_name)
        valid = (bool(target_name) and o["autoOff"] and o["offReason"] == "input" and target_name != o["name"]
                 and target_name in live_by_name and t is not None and t["enabled"] and not t["autoOff"]
                 and not t["mirrorOf"] and not t["parkMirrorOf"]
                 and not (lid_is_closed and INTERNAL_RE.match(target_name)))
        if not valid:
            o.update(parkMirrorOf="", autoOff=False, offReason="", pauseMode="", enabled=True)
    if not any(o["enabled"] and not o["autoOff"] and not o["mirrorOf"] for o in kept):
        # Unplugging monitor A during preview of B must never let revert disable B.
        def _prefer(o):
            is_int = bool(re.match(r"^(eDP|LVDS|DSI)-", o["name"]))
            return (1 if (is_int and lid_is_closed) else 0, 0 if is_int else 1)
        chosen = sorted(kept, key=_prefer)[0]
        chosen["enabled"] = True
        chosen["autoOff"] = False
        chosen["offReason"] = ""
        chosen["mirrorOf"] = ""
        chosen["parkMirrorOf"] = ""
        chosen["pauseMode"] = ""
        if chosen["mode"] is None:
            m = live_by_name.get(chosen["name"]) or {}
            if int(m.get("width") or 0) > 0 and int(m.get("height") or 0) > 0 and float(m.get("refreshRate") or 0) > 0:
                chosen["mode"] = {"w": int(m["width"]), "h": int(m["height"]), "hz": float(m["refreshRate"])}
    return kept


def revert(plan: list) -> dict:
    current = connected_monitors()
    if not current:
        return {"ok": True, "errors": [], "fallbacks": []}
    safe = safe_revert_plan(plan if isinstance(plan, list) else [], current, lid_closed())
    return apply(safe)


def fallbacks(outputs: list) -> list:
    """Monitors Hyprland did not give the asked-for mode, usually because a
    dock or cable lacks the bandwidth. Reported, never saved as the choice."""
    try:
        live = {m.get("name"): m for m in hyprctl_json("monitors", "all")}
    except (OSError, ValueError, subprocess.SubprocessError):
        return []
    out = []
    for output in outputs:
        monitor = live.get(output["name"])
        if not monitor or output["autoOff"] or not output["enabled"] or output["mode"] is None \
                or monitor.get("disabled"):
            continue
        mode = output["mode"]
        if (monitor.get("width"), monitor.get("height")) != (mode["w"], mode["h"]) or \
                abs(float(monitor.get("refreshRate") or 0) - mode["hz"]) >= 0.05:
            out.append({
                "name": output["name"],
                "wanted": f'{mode["w"]}x{mode["h"]}@{mode["hz"]:.2f}',
                "actual": f'{monitor.get("width")}x{monitor.get("height")}@{float(monitor.get("refreshRate") or 0):.2f}',
            })
    return out


# ------------------------------------------------------------------ DDC/CI

def normalize_input_code(code):
    """'0x01'..'0xff' normalized to two lowercase hex digits, else None. 0x00 is
    what many monitors answer while asleep or with no signal, so it is never a
    real input."""
    text = str(code or "").strip()
    if not re.fullmatch(r"0x[0-9a-fA-F]{1,2}", text):
        return None
    value = int(text, 16)
    return "0x%02x" % value if 0x01 <= value <= 0xFF else None


def input_label(code: str) -> str:
    norm = normalize_input_code(code)
    if not norm:
        return "Unknown input"
    return MCCS_INPUTS.get(norm, f"Input {norm.upper().replace('0X', '0x')}")


def parse_ddc_detect(text: str) -> dict:
    buses = {}
    bus = None
    for line in text.splitlines():
        line = line.strip()
        match = re.match(r"I2C bus:\s*/dev/i2c-(\d+)", line)
        if match:
            bus = match.group(1)
            continue
        match = re.match(r"DRM connector:\s*card\d+-(\S+)", line)
        if match and bus is not None:
            buses[match.group(1)] = bus
            bus = None
    return buses


def parse_vcp_input(text: str):
    """The VCP 0x60 code from `getvcp 60 --brief`, or None (0x00 included)."""
    match = re.search(r"VCP\s+60\s+SNC\s+x([0-9a-fA-F]{1,2})\b", text or "")
    return normalize_input_code("0x" + match.group(1)) if match else None


def sysfs_bus(name: str):
    """I2C bus number for a DRM connector from its `ddc` symlink, or None."""
    for path in connector_paths(name):
        link = Path(path, "ddc")
        if not os.path.lexists(link):
            continue
        found = re.findall(r"i2c-(\d+)", os.path.realpath(link))
        if found:
            return found[-1]
    return None


def ddc_buses() -> dict:
    """Connector name -> I2C bus number, from one `ddcutil detect`."""
    try:
        result = subprocess.run(["ddcutil", "--skip-ddc-checks", "detect", "--brief"],
                                capture_output=True, text=True, timeout=DDC_DETECT_TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        return {}
    return parse_ddc_detect(result.stdout or "")


LOCK_BUSY = "busy"
LOCK_UNUSABLE = "ambiguous"


def _try_lock():
    """A non-blocking hold on Omarchy's brightness lock: the fd (an int), else
    the reading to report without touching the bus. `busy` (LOCK_BUSY) only
    while someone else holds it, which passes; `ambiguous` (LOCK_UNUSABLE) when
    the lock file cannot be trusted (a symlink, not a regular file, someone
    else's, unopenable), which may never pass, so it fails open instead of
    reading as busy forever. The I2C bus is never touched without the lock."""
    try:
        fd = os.open(str(brightness_lock_path()),
                     os.O_RDWR | os.O_CREAT | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
    except OSError:
        return LOCK_UNUSABLE
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
            os.close(fd)
            return LOCK_UNUSABLE
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as error:
        os.close(fd)
        if isinstance(error, BlockingIOError) or error.errno in (errno.EAGAIN, errno.EWOULDBLOCK):
            return LOCK_BUSY
        return LOCK_UNUSABLE
    return fd


def _locked(fd) -> bool:
    return isinstance(fd, int) and not isinstance(fd, bool)


def _unlock(fd) -> None:
    try:
        os.close(fd)
    except OSError:
        pass


def read_vcp_input(bus) -> dict:
    """One VCP 0x60 read. Anything short of a clean, non-zero answer is
    `ambiguous`; a held brightness lock is `busy` (no information at all)."""
    if not re.fullmatch(r"\d+", str(bus or "")):
        return {"status": "ambiguous"}
    fd = _try_lock()
    if not _locked(fd):
        return {"status": fd}
    try:
        result = subprocess.run(["ddcutil", "--bus", str(bus), "--skip-ddc-checks", "getvcp", "60", "--brief"],
                                capture_output=True, text=True, timeout=DDC_TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        return {"status": "ambiguous"}
    finally:
        _unlock(fd)
    if result.returncode != 0:
        return {"status": "ambiguous"}
    code = parse_vcp_input(result.stdout)
    return {"status": "ok", "code": code} if code else {"status": "ambiguous"}


def _open_i2c(bus):
    """An fd on /dev/i2c-BUS addressed to the SCDC slave, or raises OSError.
    Never follows a symlink and only accepts a character device."""
    fd = os.open(str(i2c_dev_root() / f"i2c-{bus}"), os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        if not stat.S_ISCHR(os.fstat(fd).st_mode):
            raise OSError(f"/dev/i2c-{bus} is not a character device")
        fcntl.ioctl(fd, I2C_SLAVE, SCDC_ADDR)
    except BaseException:
        os.close(fd)
        raise
    return fd


class _SmbusData(ctypes.Union):
    # union i2c_smbus_data: byte, word, block[I2C_SMBUS_BLOCK_MAX + 2]
    _fields_ = [("byte", ctypes.c_uint8), ("word", ctypes.c_uint16), ("block", ctypes.c_uint8 * 34)]


class _SmbusIoctlData(ctypes.Structure):
    # struct i2c_smbus_ioctl_data
    _fields_ = [("read_write", ctypes.c_uint8), ("command", ctypes.c_uint8),
                ("size", ctypes.c_uint32), ("data", ctypes.POINTER(_SmbusData))]


def _smbus_read_byte_data(fd, register: int) -> int:
    """One SMBus "read byte data" transfer (a read; nothing is written to the
    sink's registers)."""
    data = _SmbusData()
    args = _SmbusIoctlData(I2C_SMBUS_READ, register & 0xFF, I2C_SMBUS_BYTE_DATA, ctypes.pointer(data))
    fcntl.ioctl(fd, I2C_SMBUS, args)
    return int(data.byte) & 0xFF


# An I2C NACK: nothing answers at 0x54, i.e. no SCDC sink (or one powered down).
SCDC_ABSENT_ERRNOS = (errno.EREMOTEIO, errno.ENXIO)


def read_scdc_status(bus) -> dict:
    """Read-only HDMI SCDC probe: Sink_Version (0x01), Config_1 (0x31), and
    Status_Flags_0 (0x40) at I2C 0x54. `locked` while all lock bits for the
    negotiated link mode (TMDS 0x0F, 3-lane FRL 0x0E, 4-lane FRL 0x1E) are set,
    `unlocked` while it answers with no lock bits set (switched away), `none`
    only when the sink NACKs (EREMOTEIO/ENXIO) an actual SMBus register read or
    reports Sink_Version 0 (no SCDC sink), `busy` while the brightness lock is
    held. Anything else (clock-only or partial lock, reserved FRL rate, no
    /dev/i2c node, permissions, a driver owning the address, timeouts, a
    floating bus reading 0xFF, an unusable lock file) is `ambiguous`."""
    if not re.fullmatch(r"\d{1,6}", str(bus or "")):
        return {"status": "ambiguous"}
    lock = _try_lock()
    if not _locked(lock):
        return {"status": lock}
    try:
        # Opening the node and addressing 0x54 (I2C_SLAVE) transfer nothing on
        # the wire, so no errno there (not even ENXIO) is a sink's NACK.
        try:
            fd = _open_i2c(bus)
        except OSError:
            return {"status": "ambiguous"}
        try:
            version = _smbus_read_byte_data(fd, SCDC_SINK_VERSION)
            if version == 0xFF:
                # A floating bus reads all ones; it says nothing about a sink.
                return {"status": "ambiguous"}
            if version == 0x00:
                return {"status": "none"}
            config1 = _smbus_read_byte_data(fd, SCDC_CONFIG_1)
            if config1 == 0xFF:
                return {"status": "ambiguous"}
            flags = _smbus_read_byte_data(fd, SCDC_STATUS_FLAGS_0)
        except OSError as error:
            # Only the SMBus transfer itself can be NACKed.
            if error.errno in SCDC_ABSENT_ERRNOS:
                return {"status": "none"}
            return {"status": "ambiguous"}
        finally:
            os.close(fd)
    finally:
        _unlock(lock)
    if flags == 0xFF:
        # A floating bus reads all ones; never mistake it for a lock.
        return {"status": "ambiguous"}
    frl_rate = config1 & 0x0F
    if frl_rate == 0:
        required = SCDC_TMDS_LOCK
    elif frl_rate in (1, 2):
        required = SCDC_FRL_3LANE_LOCK
    elif frl_rate in (3, 4, 5, 6):
        required = SCDC_FRL_4LANE_LOCK
    else:
        return {"status": "ambiguous"}
    if (flags & SCDC_LOCK_MASK) == 0:
        return {"status": "ok", "signal": "scdc", "scdc": "unlocked"}
    if (flags & required) == required and (frl_rate != 0 or (flags & 0x10) == 0):
        return {"status": "ok", "signal": "scdc", "scdc": "locked"}
    # Clock-only or partial channel/lane lock is never a full lock or away.
    return {"status": "ambiguous"}


def parse_ddc_spec(spec):
    """`NAME`, `NAME:BUS`, `NAME[@scdc|@ddc]` or `NAME:BUS[@scdc|@ddc]`
    (BUS digits only) -> (name, bus or None, mode), else None."""
    text = str(spec or "")
    mode = ""
    if text.endswith("@scdc"):
        mode = "scdc"
        text = text[:-len("@scdc")]
    elif text.endswith("@ddc"):
        mode = "ddc"
        text = text[:-len("@ddc")]
    name, sep, bus = text.partition(":")
    if not CONNECTOR_RE.fullmatch(name):
        return None
    if sep and not re.fullmatch(r"\d{1,6}", bus):
        return None
    return name, (bus if sep else None), mode


def live_dpms_snapshot():
    """Hyprland's monitors right now (for their power state), or None when
    the power state is unknown: hyprctl cannot answer, or lists no monitor.
    Unknown is never taken for awake (ddc_read then reads nothing)."""
    try:
        raw = hyprctl_json("monitors", "all")
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    if not isinstance(raw, list):
        return None
    live = [m for m in raw if isinstance(m, dict) and str(m.get("name") or "") not in ("", "FALLBACK")]
    return live or None


def _dpms_view(live):
    """(dpms {name: on}, globalIdle) for the enabled monitors in `live`.
    globalIdle: every enabled monitor is DPMS-off (the desktop idled)."""
    if not live:
        return {}, False
    enabled = [m for m in live if not m.get("disabled")]
    dpms = {str(m["name"]): m.get("dpmsStatus", True) is not False for m in enabled}
    return dpms, bool(enabled) and all(m.get("dpmsStatus", True) is False for m in enabled)


def ddc_read(specs: list, *, detect=()) -> dict:
    """Read-only readings, one per connector. `NAME@scdc` (a monitor already
    watched through HDMI SCDC) only reads SCDC, never ddcutil; a NACK there is
    `absent` (a TV that powers its SCDC down in standby). Otherwise a
    sysfs-linked connector is checked for an SCDC lock first (no ddcutil when
    locked), then falls through to VCP 0x60 (keeping an SCDC `unlocked` reading
    if VCP 0x60 cannot answer); without ddcutil an SCDC sink still answers
    `locked`/`unlocked`, and only a connector with no SCDC sink is `no_tool`.
    Each connector's bus comes from sysfs, else from the `NAME:BUS` hint an
    earlier detect gave, else, only for connectors named in `detect` (still
    probing), from one locked `ddcutil detect`. A bus that did not come from
    sysfs is reported back as `bus` so the caller can pass it as a hint.

    Hyprland's live power state is checked first: while every enabled monitor
    is DPMS-off (`globalIdle`) no bus is touched at all, and an enabled monitor
    that is DPMS-off on its own reads `dpms_off`. A disabled monitor (paused
    through DDC/CI) is still read, so it can wake. When the power state is
    unknown (`dpmsKnown: false`) nothing is read at all. With no specs it is a
    pure power-state probe: no bus is touched."""
    suspend_before = suspend_clock_ms()
    parsed = []
    seen = set()
    for spec in specs:
        item = parse_ddc_spec(spec)
        if item and item[0] not in seen:
            seen.add(item[0])
            parsed.append(item)
    live = live_dpms_snapshot()
    if live is None:
        return {"ok": True, "dpmsKnown": False, "globalIdle": False, "dpms": {}, "readings": {},
                "suspendMs": suspend_clock_ms()}
    dpms, global_idle = _dpms_view(live)
    if global_idle or not parsed:
        return {"ok": True, "dpmsKnown": True, "globalIdle": global_idle, "dpms": dpms, "readings": {},
                "suspendMs": suspend_clock_ms()}
    readings = {}
    sysfs = {}
    scdc_unlocked = {}
    pending = []
    has_ddcutil = shutil.which("ddcutil") is not None
    for name, hint, mode in parsed:
        if dpms.get(name) is False:
            readings[name] = {"status": "dpms_off"}
            continue
        sysfs[name] = sysfs_bus(name)
        if mode == "scdc":
            bus = sysfs[name] if sysfs[name] is not None else hint
            if bus is None:
                readings[name] = {"status": "ambiguous"}
                continue
            reading = read_scdc_status(bus)
            if reading["status"] == "none":
                reading = {"status": "ok", "signal": "scdc", "scdc": "absent"}
            readings[name] = reading
            continue
        if mode != "ddc" and sysfs[name] is not None:
            reading = read_scdc_status(sysfs[name])
            if reading["status"] == "busy" or reading.get("scdc") == "locked":
                readings[name] = reading
                continue
            if not has_ddcutil:
                # No ddcutil: an SCDC sink is still watched through SCDC alone.
                # No SCDC sink, or no /dev/i2c node at all (i2c-dev, which
                # ddcutil sets up, is not loaded): this needs ddcutil.
                no_node = not os.path.lexists(i2c_dev_root() / f"i2c-{sysfs[name]}")
                if reading["status"] == "none" or (reading["status"] == "ambiguous" and no_node):
                    reading = {"status": "no_tool"}
                readings[name] = reading
                continue
            if reading.get("scdc") == "unlocked":
                scdc_unlocked[name] = reading
        pending.append((name, hint))
    names = [name for name, _ in pending]
    if not has_ddcutil:
        readings.update({n: {"status": "no_tool"} for n in names})
        suspend_after = suspend_clock_ms()
        if suspend_after - suspend_before >= 500:
            readings = {}
        return {"ok": True, "dpmsKnown": True, "globalIdle": False, "dpms": dpms, "readings": readings,
                "suspendMs": suspend_after}
    may_detect = {str(n) for n in (detect or ())} & set(names)
    buses = {}
    reported = set()
    for name, hint in pending:
        bus = sysfs[name]
        if bus is None and hint is not None:
            bus = hint
            reported.add(name)
        buses[name] = bus
    missing = [n for n in names if buses[n] is None and n in may_detect]
    unread = {}
    if missing:
        # detect talks to every bus, so it takes the brightness lock too.
        fd = _try_lock()
        if not _locked(fd):
            unread = {n: fd for n in missing}
        else:
            try:
                detected = ddc_buses()
            finally:
                _unlock(fd)
            for n in missing:
                bus = detected.get(n)
                if bus is not None and re.fullmatch(r"\d+", str(bus)):
                    buses[n] = str(bus)
                    reported.add(n)
    for n in names:
        if n in unread:
            readings[n] = {"status": unread[n]}
        elif buses[n] is None:
            readings[n] = scdc_unlocked.get(n, {"status": "ambiguous"})
        else:
            reading = read_vcp_input(buses[n])
            if reading.get("status") not in ("ok", "busy") and n in scdc_unlocked:
                readings[n] = scdc_unlocked[n]
            else:
                if n in reported:
                    reading = dict(reading, bus=buses[n])
                readings[n] = reading
    suspend_after = suspend_clock_ms()
    if suspend_after - suspend_before >= 500:
        # Readings that spanned a suspend/resume are discarded so stale bus
        # answers can never be observed.
        readings = {}
    return {"ok": True, "dpmsKnown": True, "globalIdle": False, "dpms": dpms, "readings": readings,
            "suspendMs": suspend_after}


def _valid_fp(fp) -> bool:
    return isinstance(fp, str) and 0 < len(fp) <= 512 and "\n" not in fp and "\r" not in fp


def _clean_ddc_entry(entry):
    if not isinstance(entry, dict):
        return None
    code = normalize_input_code(entry.get("input"))
    connector = str(entry.get("connector") or "")
    if not code or not CONNECTOR_RE.fullmatch(connector):
        return None
    return {"input": code, "connector": connector, "learnedAt": str(entry.get("learnedAt") or "")}


@contextlib.contextmanager
def ddc_memory_lock():
    """Blocking lock serializing every ddc.json read-modify-write (learn,
    forget, persist's migration), so concurrent helpers never lose an update."""
    folder = state_dir()
    folder.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(folder / ".ddc.lock"), os.O_RDWR | os.O_CREAT | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError("ddc.json lock is not a regular file")
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def _read_ddc_file():
    """(monitors, migrated) from ddc.json. `migrated` is True once ddc.json was
    written by this helper, after which inline displays.json inputs are dead."""
    path = ddc_memory_path()
    if path.is_symlink():
        return {}, False
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}, False
    monitors = raw.get("monitors") if isinstance(raw, dict) else None
    out = {}
    for fp, entry in (monitors.items() if isinstance(monitors, dict) else []):
        clean = _clean_ddc_entry(entry)
        if _valid_fp(fp) and clean:
            out[fp] = clean
    return out, isinstance(raw, dict) and raw.get("migrated") is True


def load_ddc_memory(legacy_memory=None) -> dict:
    """ddc.json, plus, until ddc.json has been written once, any learned input
    an older displays.json kept inline (`ddcInput` + `ddcConnector`) for a
    monitor ddc.json does not know yet. Once migrated, a forgotten input can
    never come back from a stale inline entry."""
    monitors, migrated = _read_ddc_file()
    legacy = legacy_memory.get("monitors") if isinstance(legacy_memory, dict) and not migrated else None
    for fp, profile in (legacy.items() if isinstance(legacy, dict) else []):
        if fp in monitors or not _valid_fp(fp) or not isinstance(profile, dict):
            continue
        clean = _clean_ddc_entry({"input": profile.get("ddcInput"), "connector": profile.get("ddcConnector"),
                                  "learnedAt": "legacy"})
        if clean:
            monitors[fp] = clean
    out = {"version": 1, "monitors": monitors}
    if migrated:
        out["migrated"] = True
    return out


def write_ddc_memory(ddc_mem: dict) -> dict:
    path = ddc_memory_path()
    if path.is_symlink():
        raise ValueError(f"refusing to write through a symlink: {path}")
    saved = {"version": 1, "migrated": True, "monitors": dict(ddc_mem.get("monitors") or {})}
    atomic_write(path, json.dumps(saved, indent=2, sort_keys=True) + "\n", 0o600)
    return saved


def ddc_learn(name: str, fp: str, code: str) -> dict:
    if not CONNECTOR_RE.fullmatch(name or ""):
        raise ValueError(f"invalid monitor name: {name!r}")
    if not _valid_fp(fp):
        raise ValueError("invalid monitor fingerprint")
    norm = normalize_input_code(code)
    if not norm:
        raise ValueError(f"invalid VCP input code: {code!r}")
    with ddc_memory_lock():
        ddc_mem = load_ddc_memory(load_memory())
        ddc_mem["monitors"][fp] = {
            "input": norm,
            "connector": name,
            "learnedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        saved = write_ddc_memory(ddc_mem)
    return {"ok": True, "ddcInput": norm, "ddcConnector": name, "label": input_label(norm), "ddcMemory": saved}


def _ddc_forget_locked(fp: str, legacy_memory=None) -> dict:
    ddc_mem = load_ddc_memory(load_memory() if legacy_memory is None else legacy_memory)
    ddc_mem["monitors"].pop(fp, None)
    return write_ddc_memory(ddc_mem)


def ddc_forget(fp: str, legacy_memory=None) -> dict:
    if not _valid_fp(fp):
        raise ValueError("invalid monitor fingerprint")
    with ddc_memory_lock():
        saved = _ddc_forget_locked(fp, legacy_memory)
    return {"ok": True, "ddcMemory": saved}


# ------------------------------------------------------------------ persist

def persist(payload: dict) -> dict:
    """Writes monitors.lua and/or displays.json (and drops a forgotten monitor's
    learned input from ddc.json). Never talks to a monitor: no ddcutil here."""
    plan_raw = payload.get("plan")
    remove_block = payload.get("removeManagedBlock") is True
    outputs = validate_plan(plan_raw) if plan_raw is not None else None
    memory = payload.get("memory")
    forget_fp = payload.get("forgetDdcFp")
    if forget_fp is not None and not _valid_fp(forget_fp):
        raise ValueError("invalid forgetDdcFp")
    if outputs is None and memory is None and not remove_block and forget_fp is None:
        raise ValueError("persist requires plan, memory, removeManagedBlock, or forgetDdcFp")
    if memory is not None and not isinstance(memory, dict):
        raise ValueError("memory must be an object")

    # Write monitors.lua FIRST so any failure (symlink, permissions, syntax
    # check) aborts BEFORE committing the new memory to displays.json.
    changed = False
    if remove_block:
        changed = write_monitors_lua([], remove_block=True)
    elif outputs is not None:
        changed = write_monitors_lua(outputs)

    result = {"ok": True, "monitorsLuaChanged": changed}
    if memory is None and forget_fp is None:
        return result
    with ddc_memory_lock():
        if memory is not None:
            # Carry inputs an older displays.json learned inline over to ddc.json
            # before the new memory (which no longer holds them) replaces it.
            on_disk = load_ddc_memory(load_memory())
            if on_disk["monitors"] != _read_ddc_file()[0]:
                write_ddc_memory(on_disk)
            atomic_write(memory_path(), json.dumps(memory, indent=2, sort_keys=True) + "\n", 0o600)
        if forget_fp is not None:
            result["ddcMemory"] = _ddc_forget_locked(forget_fp)
    return result


def parse_ddc_read_args(args: list):
    """ddc-read's arguments -> (specs, detect names)."""
    specs, detect = [], []
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == "--detect":
            if index + 1 < len(args):
                detect.append(args[index + 1])
            index += 1
        else:
            specs.append(arg)
        index += 1
    return specs, detect


def read_stdin_json():
    return json.loads(sys.stdin.read() or "null")


def main(argv: list) -> int:
    if not argv:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    command = argv[0]
    try:
        if command == "state":
            result = state()
        elif command == "apply":
            result = apply(validate_plan(read_stdin_json()))
        elif command == "revert":
            result = revert(read_stdin_json())
        elif command == "persist":
            payload = read_stdin_json()
            if not isinstance(payload, dict):
                raise ValueError("persist expects an object")
            result = persist(payload)
        elif command == "ddc-read":
            specs, detect = parse_ddc_read_args(argv[1:])
            result = ddc_read(specs, detect=detect)
        elif command == "ddc-learn":
            if len(argv) < 4:
                raise ValueError("ddc-learn expects monitor name, fingerprint, and VCP code")
            result = ddc_learn(argv[1], argv[2], argv[3])
        elif command == "ddc-forget":
            if len(argv) < 2:
                raise ValueError("ddc-forget expects a fingerprint")
            result = ddc_forget(argv[1])
        elif command == "nightlight":
            if len(argv) < 2 or argv[1] not in ("on", "off"):
                raise ValueError("nightlight expects 'on <kelvin>' or 'off'")
            if argv[1] == "on":
                kelvin = int(argv[2]) if len(argv) >= 3 else 4000
                result = set_nightlight(True, kelvin)
            else:
                result = set_nightlight(False)
        else:
            print(f"unknown command: {command}", file=sys.stderr)
            return 2
    except (ValueError, TypeError, OSError, subprocess.SubprocessError) as error:
        print(json.dumps({"ok": False, "errors": [str(error)]}))
        return 1
    print(json.dumps(result))
    return 0 if result.get("ok", True) is not False else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
