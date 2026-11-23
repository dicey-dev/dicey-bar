#!/usr/bin/env python3
"""Hyprland and file I/O for the Dicey Bar Displays panel.

The panel and its hotplug service decide what each monitor should do
(displays/DisplayModel.js). This helper only carries that out:

  state                 JSON: connected monitors, HDR support, lid, memory
  apply                 stdin plan -> live `hyprctl eval`, then read back
  persist               stdin {"plan": [...], "memory": {...}?} -> write the
                        memory file and the managed block in monitors.lua
  ddc-poll NAME...      JSON: each connector's DDC/CI input source code

Every value written into Lua is validated or emitted as an escaped string
literal, because monitors.lua is code Hyprland runs.
"""
import glob
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

BEGIN = "-- BEGIN dicey.bar displays"
END = "-- END dicey.bar displays"
FOREIGN_BLOCKS = ("im0001gt.screens",)
TAKEOVER_HEADER = "-- Monitor rules are managed by the Displays panel in Dicey Bar (block below)."
CATCH_ALL = 'hl.monitor({ output = "", mode = "preferred", position = "auto", scale = "auto" })'
HYPRCTL_TIMEOUT = 10
DDC_TIMEOUT = 4


def home() -> Path:
    return Path(os.environ.get("HOME") or Path.home())


def memory_path() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or str(home() / ".local" / "state")
    return Path(base) / "dicey-bar" / "displays.json"


def monitors_lua_path() -> Path:
    override = os.environ.get("DICEY_DISPLAYS_MONITORS_LUA")
    return Path(override) if override else home() / ".config" / "hypr" / "monitors.lua"


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


def validate_output(output: dict) -> dict:
    if not isinstance(output, dict):
        raise ValueError("output must be an object")
    selector = str(output.get("selector") or output.get("name") or "")
    name = str(output.get("name") or "")
    if not selector or not name or "\n" in selector or "\n" in name:
        raise ValueError("output needs a name and selector")
    clean = {
        "name": name,
        "selector": selector,
        "fp": str(output.get("fp") or name),
        "enabled": output.get("enabled") is not False,
        "autoOff": output.get("autoOff") is True,
        "offReason": str(output.get("offReason") or ""),
        "hdr": output.get("hdr") is True,
        "ddcWatch": output.get("ddcWatch") is True,
    }
    if clean["offReason"] not in ("", "lid", "input"):
        raise ValueError(f"invalid offReason: {clean['offReason']!r}")
    mode = output.get("mode") or {}
    try:
        clean["mode"] = {
            "w": _int(mode.get("w"), 1, 32768, "width"),
            "h": _int(mode.get("h"), 1, 32768, "height"),
            "hz": _number(mode.get("hz"), 1, 1000, "refresh rate"),
        }
    except (ValueError, TypeError):
        # An output that is off may report no usable mode; it gets none.
        if clean["enabled"] and not clean["autoOff"]:
            raise
        clean["mode"] = None
    clean["x"] = _int(output.get("x", 0), -100000, 100000, "x")
    clean["y"] = _int(output.get("y", 0), -100000, 100000, "y")
    clean["scale"] = _number(output.get("scale", 1), 0.1, 10, "scale")
    clean["vrr"] = _int(output.get("vrr", 0), 0, 2, "vrr")
    clean["transform"] = _int(output.get("transform", 0), 0, 7, "transform")
    return clean


def validate_plan(plan) -> list:
    if not isinstance(plan, list) or not plan:
        raise ValueError("plan must be a non-empty list")
    outputs = [validate_output(o) for o in plan]
    if not any(o["enabled"] and not o["autoOff"] for o in outputs):
        raise ValueError("plan would leave every display off")
    return outputs


def monitor_rule(output: dict, *, keep_on: bool = False) -> str:
    """One hl.monitor call. Hyprland merges a rule into the one already set for
    that output, so every field is spelled out: a rule that left out
    `disabled` or `bitdepth` would keep the old value."""
    selector = lua_string(output["selector"])
    if not output["enabled"] and not keep_on:
        return f"hl.monitor({{ output = {selector}, disabled = true }})"
    mode = output["mode"]
    if mode is None:
        return f'hl.monitor({{ output = {selector}, disabled = false, mode = "preferred", position = "auto", scale = "auto" }})'
    fields = [
        f"output = {selector}",
        "disabled = false",
        f'mode = "{mode["w"]}x{mode["h"]}@{mode["hz"]:.2f}"',
        f'position = "{output["x"]}x{output["y"]}"',
        f"scale = {_fmt(output['scale'])}",
        f"transform = {output['transform']}",
        f"vrr = {output['vrr']}",
        'cm = "hdr"' if output["hdr"] else 'cm = "srgb"',
        f"bitdepth = {10 if output['hdr'] else 8}",
    ]
    return "hl.monitor({ " + ", ".join(fields) + " })"


def live_lua(outputs: list) -> str:
    """Rules for `hyprctl eval`. A monitor off because the lid is shut is
    skipped: Omarchy's clamshell handling owns it. One switched to another
    input is turned off here. Monitors switched on come first so no step
    leaves nothing lit."""
    on = [o for o in outputs if o["enabled"] and not o["autoOff"]]
    off = [o for o in outputs if (not o["enabled"] and not o["autoOff"]) or o["offReason"] == "input"]
    return "\n".join([monitor_rule(o) for o in on] +
                     [monitor_rule(dict(o, enabled=False)) for o in off])


def file_block(outputs: list, carried: list) -> str:
    """The managed block for monitors.lua. A monitor that is off only for now
    (lid, input) is written as on, so it comes back when that ends."""
    lines = [BEGIN, "-- Written by the Displays panel in Dicey Bar. Changes here are replaced."]
    lines.extend(carried)
    lines.append(CATCH_ALL)
    for output in outputs:
        lines.append(monitor_rule(output, keep_on=output["autoOff"]))
    lines.append(END)
    return "\n".join(lines)


def _block_span(lines: list, begin: str, end: str):
    start = None
    for index, line in enumerate(lines):
        stripped = line.strip()
        if start is None and stripped == begin:
            start = index
        elif start is not None and stripped == end:
            return start, index
    return None


def splice_block(text: str, outputs: list) -> str:
    """monitors.lua with the managed block replaced. On first use a block left
    by a known display manager is replaced in place; its rules for monitors go,
    and any other settings it held are kept inside the new block."""
    lines = text.split("\n") if text else []
    span = _block_span(lines, BEGIN, END)
    carried = []
    if span is None:
        for owner in FOREIGN_BLOCKS:
            span = _block_span(lines, f"-- BEGIN {owner}", f"-- END {owner}")
            if span is not None:
                for line in lines[span[0] + 1:span[1]]:
                    stripped = line.strip()
                    if stripped and not stripped.startswith("hl.monitor(") and not stripped.startswith("--"):
                        carried.append(stripped)
                # Its header line would now point at a block that is gone.
                lines = [TAKEOVER_HEADER if line.strip().startswith(f"-- Managed by {owner}") else line
                         for line in lines]
                break
    else:
        for line in lines[span[0] + 1:span[1]]:
            stripped = line.strip()
            if stripped.startswith("hl.config("):
                carried.append(stripped)
    block = file_block(outputs, carried).split("\n")
    if span is None:
        while lines and lines[-1] == "":
            lines.pop()
        if lines:
            lines.append("")
        lines.extend(block)
    else:
        lines[span[0]:span[1] + 1] = block
    return "\n".join(lines).rstrip("\n") + "\n"


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


def write_monitors_lua(outputs: list, path: Path = None) -> bool:
    """Writes the managed block. The file is left alone when nothing changes,
    because every write makes Hyprland reload its config. The first write keeps
    a one-time backup of the file as it was."""
    path = path or monitors_lua_path()
    if path.is_symlink():
        raise ValueError(f"refusing to write through a symlink: {path}")
    original = path.read_text(encoding="utf-8") if path.exists() else ""
    updated = splice_block(original, outputs)
    if updated == original:
        return False
    backup = path.with_name(path.name + ".dicey-bar-backup")
    if original and BEGIN not in original and not backup.exists():
        atomic_write(backup, original)
    atomic_write(path, updated)
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
    if not re.fullmatch(r"[A-Za-z0-9._-]+", name or ""):
        return []
    return sorted(glob.glob(f"/sys/class/drm/card*-{name}"))


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


def connected_monitors() -> list:
    monitors = []
    for monitor in hyprctl_json("monitors", "all"):
        name = str(monitor.get("name") or "")
        # Hyprland's stand-in once nothing else is left; never a real monitor.
        if not name or name == "FALLBACK":
            continue
        monitor["hdrCapable"] = hdr_capable(name)
        monitors.append(monitor)
    return monitors


def state() -> dict:
    return {"monitors": connected_monitors(), "lidClosed": lid_closed(), "memory": load_memory()}


# ------------------------------------------------------------------ apply

def apply(outputs: list) -> dict:
    lua = live_lua(outputs)
    errors = []
    if lua:
        result = subprocess.run(["hyprctl", "eval", lua], capture_output=True, text=True,
                                timeout=HYPRCTL_TIMEOUT)
        reply = (result.stdout + result.stderr).strip()
        if result.returncode != 0 or (reply and reply != "ok"):
            errors.append(reply or f"hyprctl eval exited {result.returncode}")
    return {"ok": not errors, "errors": errors, "fallbacks": fallbacks(outputs)}


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

def ddc_buses() -> dict:
    """Connector name -> I2C bus number, from `ddcutil detect`."""
    try:
        result = subprocess.run(["ddcutil", "--skip-ddc-checks", "detect", "--brief"],
                                capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return {}
    return parse_ddc_detect(result.stdout)


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
    match = re.search(r"VCP\s+60\s+SNC\s+x([0-9a-fA-F]{1,2})\b", text or "")
    return "0x%02x" % int(match.group(1), 16) if match else None


def ddc_input(bus: str):
    try:
        result = subprocess.run(["ddcutil", "--bus", bus, "--skip-ddc-checks", "getvcp", "60", "--brief"],
                                capture_output=True, text=True, timeout=DDC_TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        return None
    return parse_vcp_input(result.stdout)


def bus_cache_path() -> Path:
    return Path(os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir()) / "dicey-bar-ddc-buses.json"


def cached_buses(names) -> dict:
    path = bus_cache_path()
    try:
        cache = json.loads(path.read_text())
    except (OSError, ValueError):
        cache = {}
    if not isinstance(cache, dict) or any(name not in cache for name in names):
        cache = ddc_buses()
        try:
            atomic_write(path, json.dumps(cache), 0o600)
        except OSError:
            pass
    return cache


def ddc_poll(names: list) -> dict:
    names = [n for n in names if re.fullmatch(r"[A-Za-z0-9._-]+", n)]
    buses = cached_buses(names)
    return {name: (ddc_input(buses[name]) if name in buses else None) for name in names}


# ------------------------------------------------------------------ persist

def persist(payload: dict) -> dict:
    outputs = validate_plan(payload.get("plan"))
    memory = payload.get("memory")
    if memory is not None:
        if not isinstance(memory, dict):
            raise ValueError("memory must be an object")
        monitors = memory.get("monitors") if isinstance(memory.get("monitors"), dict) else {}
        # Learn the input a newly watched monitor is on, so a later switch to
        # another input can be told apart from it.
        learn = [o for o in outputs if o["ddcWatch"] and not (monitors.get(o["fp"]) or {}).get("ddcInput")]
        if learn:
            readings = ddc_poll([o["name"] for o in learn])
            for output in learn:
                profile = monitors.get(output["fp"])
                if isinstance(profile, dict) and readings.get(output["name"]):
                    profile["ddcInput"] = readings[output["name"]]
        atomic_write(memory_path(), json.dumps(memory, indent=2, sort_keys=True) + "\n", 0o600)
    changed = write_monitors_lua(outputs)
    return {"ok": True, "monitorsLuaChanged": changed}


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
        elif command == "persist":
            payload = read_stdin_json()
            if not isinstance(payload, dict):
                raise ValueError("persist expects an object")
            result = persist(payload)
        elif command == "ddc-poll":
            result = ddc_poll(argv[1:])
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
