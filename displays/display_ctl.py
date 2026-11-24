#!/usr/bin/env python3
"""Hyprland and file I/O for the Dicey Bar Displays panel.

The panel and its hotplug service decide what each monitor should do
(displays/DisplayModel.js). This helper only carries that out:

  state                     JSON: connected monitors, HDR support, lid, nightLight, memory
  apply                     stdin plan -> live `hyprctl eval`, then read back
  revert                    stdin plan -> re-read connected monitors & safely roll back
  persist                   stdin {"plan": [...]?, "memory": {...}?, "removeManagedBlock": bool?}
  ddc-poll NAME...          JSON: each connector's DDC/CI input source code
  ddc-inputs NAME           JSON: supported VCP 0x60 inputs + current input for NAME
  ddc-set-input NAME CODE   JSON: switch monitor NAME to VCP 0x60 input CODE
  ddc-relearn NAME FP       JSON: re-read VCP 0x60 for NAME and store under saved FP
  nightlight on KELVIN|off  JSON: control hyprsunset temperature & notify bar

Every value written into Lua is validated or emitted as an escaped string
literal, because monitors.lua is code Hyprland runs.
"""
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
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
DDC_CAP_TIMEOUT = 8
DDC_DETECT_MIN_INTERVAL = 60
DAY_KELVIN = 6500

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

DEFAULT_INPUT_CODES = ("0x0f", "0x10", "0x11", "0x12", "0x1b")


def home() -> Path:
    return Path(os.environ.get("HOME") or Path.home())


def state_dir() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or str(home() / ".local" / "state")
    folder = "dicey-bar-dev" if IS_DEV else "dicey-bar"
    return Path(base) / folder


def memory_path() -> Path:
    return state_dir() / "displays.json"


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


def validate_output(output: dict) -> dict:
    if not isinstance(output, dict):
        raise ValueError("output must be an object")
    selector = str(output.get("selector") or output.get("name") or "")
    name = str(output.get("name") or "")
    if not selector or not name or "\n" in selector or "\n" in name:
        raise ValueError("output needs a name and selector")
    mirror_raw = str(output.get("mirrorOf") or "").strip()
    if mirror_raw.lower() == "none":
        mirror_raw = ""
    if "\n" in mirror_raw or (mirror_raw and not re.fullmatch(r"[A-Za-z0-9._-]+", mirror_raw)):
        raise ValueError(f"invalid mirrorOf: {mirror_raw!r}")
    clean = {
        "name": name,
        "selector": selector,
        "fp": str(output.get("fp") or name),
        "enabled": output.get("enabled") is not False,
        "autoOff": output.get("autoOff") is True,
        "offReason": str(output.get("offReason") or ""),
        "hdr": output.get("hdr") is True,
        "ddcWatch": output.get("ddcWatch") is True,
        "mirrorOf": mirror_raw,
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
    clean["sdrBrightness"] = _number(output.get("sdrBrightness", 1), 0.5, 2.0, "sdrBrightness")
    clean["sdrSaturation"] = _number(output.get("sdrSaturation", 1), 0.5, 1.5, "sdrSaturation")
    return clean


def validate_plan(plan) -> list:
    if not isinstance(plan, list) or not plan:
        raise ValueError("plan must be a non-empty list")
    outputs = [validate_output(o) for o in plan]
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
    return outputs


def monitor_rule(output: dict, *, keep_on: bool = False, use_connector_name: bool = False) -> str:
    """One hl.monitor call. Hyprland merges a rule into the one already set for
    that output, so every field is spelled out: a rule that left out
    `disabled`, `bitdepth`, or `mirror` would keep the old value."""
    target = output["name"] if use_connector_name else output["selector"]
    selector = lua_string(target)
    if not output["enabled"] and not keep_on:
        return f"hl.monitor({{ output = {selector}, disabled = true }})"
    mode = output["mode"]
    if mode is None:
        return f'hl.monitor({{ output = {selector}, disabled = false, mode = "preferred", position = "auto", scale = "auto", mirror = "" }})'
    sdr_b = output["sdrBrightness"] if output["hdr"] else 1
    sdr_s = output["sdrSaturation"] if output["hdr"] else 1
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
        f"sdrbrightness = {_fmt(sdr_b)}",
        f"sdrsaturation = {_fmt(sdr_s)}",
        f"mirror = {lua_string(output.get('mirrorOf') or '')}",
    ]
    return "hl.monitor({ " + ", ".join(fields) + " })"


def live_lua(outputs: list) -> str:
    """Rules for `hyprctl eval`. Always addresses outputs by exact connector
    name (never `desc:`) to avoid Hyprland prefix-match ambiguity. Non-mirroring
    enabled monitors come first so mirror sources exist and no step leaves
    nothing lit; mirrored monitors next; then disabled/input-switched monitors."""
    primary_on = [o for o in outputs if o["enabled"] and not o["autoOff"] and not o["mirrorOf"]]
    mirror_on = [o for o in outputs if o["enabled"] and not o["autoOff"] and o["mirrorOf"]]
    off = [o for o in outputs if (not o["enabled"] and not o["autoOff"]) or o["offReason"] == "input"]
    lines = [monitor_rule(o, use_connector_name=True) for o in primary_on]
    lines += [monitor_rule(o, use_connector_name=True) for o in mirror_on]
    lines += [monitor_rule(dict(o, enabled=False), use_connector_name=True) for o in off]
    return "\n".join(lines)


def file_block(outputs: list) -> str:
    """The managed block for monitors.lua. Contains strictly our own generated
    rules so subsequent rewrites can never drop or mangle user/foreign lines."""
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
    Lua snippet while preserving all other statements (e.g. `hl.config({...})`,
    `hl.workspace_rule(...)`) intact."""
    out = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
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
    return {
        "monitors": connected_monitors(),
        "lidClosed": lid_closed(),
        "nightLight": nightlight_state(),
        "memory": load_memory(),
    }


# ------------------------------------------------------------------ apply & safe revert

def _verify_some_display_enabled(outputs: list) -> list:
    """Confirm at least one real connected display remained enabled after apply."""
    try:
        live = connected_monitors()
    except (OSError, ValueError, subprocess.SubprocessError):
        return []
    if not live:
        return []
    if any(not m.get("disabled") for m in live):
        return []
    # Emergency recovery: re-enable the first connected monitor so the user is
    # never stranded on a black screen.
    rescue = next((m for m in live if re.match(r"^(eDP|LVDS|DSI)-", m["name"])), live[0])
    rule = f'hl.monitor({{ output = {lua_string(rescue["name"])}, disabled = false, mode = "preferred", position = "auto", scale = "auto", mirror = "" }})'
    subprocess.run(["hyprctl", "eval", rule], capture_output=True, text=True, timeout=HYPRCTL_TIMEOUT)
    return [f"No display stayed enabled; restored {rescue['name']} safely"]


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
    kept = []
    for raw in plan:
        try:
            o = validate_output(raw)
        except (ValueError, TypeError):
            continue
        if o["name"] in live_by_name:
            kept.append(o)
    if not kept:
        # Topology changed completely: leave all currently connected monitors on.
        return [
            validate_output({
                "name": m["name"],
                "selector": m["name"],
                "enabled": True,
                "autoOff": False,
                "mode": {"w": m["width"], "h": m["height"], "hz": m["refreshRate"]}
                if (m.get("width") and m.get("height") and m.get("refreshRate")) else None,
                "x": m.get("x", 0),
                "y": m.get("y", 0),
                "scale": m.get("scale", 1) or 1,
                "transform": m.get("transform", 0) or 0,
            })
            for m in current_monitors if m.get("name")
        ]
    # Clear any mirror targeting a monitor that is no longer connected/active.
    kept_by_name = {o["name"]: o for o in kept}
    for o in kept:
        t = kept_by_name.get(o["mirrorOf"])
        if o["mirrorOf"] and (not t or not t["enabled"] or t["autoOff"] or t["mirrorOf"]):
            o["mirrorOf"] = ""
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
        if chosen["mode"] is None:
            m = live_by_name.get(chosen["name"]) or {}
            if m.get("width") and m.get("height") and m.get("refreshRate"):
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

def input_label(code: str) -> str:
    norm = "0x%02x" % int(code, 16) if isinstance(code, str) else ""
    return MCCS_INPUTS.get(norm, f"Input {norm.upper()}")


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


def parse_vcp60_capabilities(text: str) -> list:
    """Extract supported VCP 0x60 input codes from `ddcutil capabilities`."""
    lines = (text or "").splitlines()
    in_60 = False
    in_values = False
    seen = []
    for raw in lines:
        stripped = raw.strip()
        if re.match(r"Feature:\s*60\b", stripped, re.IGNORECASE):
            in_60 = True
            in_values = False
            continue
        if in_60:
            if re.match(r"Feature:\s*[0-9a-fA-F]{2}\b", stripped, re.IGNORECASE):
                break
            if stripped.startswith("Values:"):
                in_values = True
                continue
            if in_values:
                match = re.match(r"([0-9a-fA-F]{2})\s*:\s*(.*)$", stripped)
                if match:
                    code = "0x%02x" % int(match.group(1), 16)
                    raw_label = match.group(2).strip()
                    label = MCCS_INPUTS.get(code) or raw_label or f"Input {code.upper()}"
                    if not any(item["code"] == code for item in seen):
                        seen.append({"code": code, "label": label})
                elif stripped and not stripped.startswith("("):
                    break
    return seen


def ddc_input(bus: str):
    if not re.fullmatch(r"\d+", str(bus or "")):
        return None
    try:
        result = subprocess.run(["ddcutil", "--bus", str(bus), "--skip-ddc-checks", "getvcp", "60", "--brief"],
                                capture_output=True, text=True, timeout=DDC_TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        return None
    return parse_vcp_input(result.stdout)


def bus_cache_path() -> Path:
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if runtime:
        base = Path(runtime) / "dicey-bar"
    else:
        cache_home = os.environ.get("XDG_CACHE_HOME") or str(home() / ".cache")
        base = Path(cache_home) / "dicey-bar"
    try:
        base.mkdir(parents=True, exist_ok=True)
        os.chmod(base, 0o700)
    except OSError:
        pass
    return base / "ddc-buses.json"


def _clean_bus_map(raw) -> dict:
    if not isinstance(raw, dict):
        return {}
    return {
        str(k): str(v)
        for k, v in raw.items()
        if re.fullmatch(r"[A-Za-z0-9._-]+", str(k)) and re.fullmatch(r"\d+", str(v))
    }


def cached_buses(names, *, force_refresh: bool = False, now: float = None) -> dict:
    path = bus_cache_path()
    current_time = time.time() if now is None else float(now)
    detected_at = 0.0
    buses = {}
    if not path.is_symlink():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict) and "buses" in raw:
                buses = _clean_bus_map(raw.get("buses"))
                detected_at = float(raw.get("detectedAt") or 0.0)
            elif isinstance(raw, dict):
                buses = _clean_bus_map(raw)
        except (OSError, ValueError, TypeError):
            buses = {}
    missing = any(name not in buses for name in names)
    due = (current_time - detected_at) >= DDC_DETECT_MIN_INTERVAL
    if force_refresh or (missing and due):
        buses = _clean_bus_map(ddc_buses())
        try:
            atomic_write(path, json.dumps({"detectedAt": current_time, "buses": buses}), 0o600)
        except OSError:
            pass
    return buses


def drop_cached_bus(name: str) -> None:
    path = bus_cache_path()
    if path.is_symlink() or not path.exists():
        return
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        buses = _clean_bus_map(raw.get("buses") if isinstance(raw, dict) and "buses" in raw else raw)
        if name in buses:
            del buses[name]
            detected_at = float(raw.get("detectedAt") or 0.0) if isinstance(raw, dict) else 0.0
            atomic_write(path, json.dumps({"detectedAt": detected_at, "buses": buses}), 0o600)
    except (OSError, ValueError, TypeError):
        pass


def ddc_poll(names: list, *, force_refresh: bool = False) -> dict:
    names = [n for n in names if re.fullmatch(r"[A-Za-z0-9._-]+", n)]
    buses = cached_buses(names, force_refresh=force_refresh)
    readings = {}
    for name in names:
        val = ddc_input(buses[name]) if name in buses else None
        if val is None and name in buses:
            drop_cached_bus(name)
        readings[name] = val
    return readings


def ddc_inputs(name: str) -> dict:
    if not re.fullmatch(r"[A-Za-z0-9._-]+", name or ""):
        raise ValueError(f"invalid monitor name: {name!r}")
    buses = cached_buses([name], force_refresh=True)
    bus = buses.get(name)
    if not bus:
        return {"ok": False, "name": name, "current": None, "inputs": [], "errors": ["No DDC/CI bus found for display"]}
    current = ddc_input(bus)
    inputs = []
    try:
        cap = subprocess.run(["ddcutil", "--bus", bus, "--skip-ddc-checks", "capabilities"],
                             capture_output=True, text=True, timeout=DDC_CAP_TIMEOUT)
        if cap.returncode == 0:
            inputs = parse_vcp60_capabilities(cap.stdout)
    except (OSError, subprocess.SubprocessError):
        inputs = []
    if not inputs:
        inputs = [{"code": c, "label": input_label(c)} for c in DEFAULT_INPUT_CODES]
    if current and not any(item["code"] == current for item in inputs):
        inputs.insert(0, {"code": current, "label": input_label(current)})
    return {"ok": True, "name": name, "current": current, "inputs": inputs}


def ddc_set_input(name: str, code: str) -> dict:
    if not re.fullmatch(r"[A-Za-z0-9._-]+", name or ""):
        raise ValueError(f"invalid monitor name: {name!r}")
    if not re.fullmatch(r"0x[0-9a-fA-F]{1,2}", str(code or "")):
        raise ValueError(f"invalid VCP input code: {code!r}")
    norm = "0x%02x" % int(code, 16)
    # Always refresh connector->bus mapping before a write so a dock reconnect
    # can never switch the wrong monitor's input.
    buses = cached_buses([name], force_refresh=True)
    bus = buses.get(name)
    if not bus:
        return {"ok": False, "errors": [f"No DDC/CI bus found for {name}"]}
    result = subprocess.run(["ddcutil", "--bus", bus, "--skip-ddc-checks", "setvcp", "60", norm],
                            capture_output=True, text=True, timeout=DDC_TIMEOUT)
    if result.returncode != 0:
        err = (result.stderr or result.stdout or "").strip() or f"ddcutil setvcp exited {result.returncode}"
        return {"ok": False, "errors": [err]}
    return {"ok": True, "name": name, "code": norm, "label": input_label(norm)}


def ddc_relearn(name: str, fp: str) -> dict:
    if not re.fullmatch(r"[A-Za-z0-9._-]+", name or ""):
        raise ValueError(f"invalid monitor name: {name!r}")
    memory = load_memory()
    monitors = memory.get("monitors") if isinstance(memory.get("monitors"), dict) else {}
    profile = monitors.get(fp)
    if not isinstance(profile, dict) or not profile.get("ddcWatch"):
        return {"ok": False, "errors": ["Apply Auto-Disable first before re-learning input"]}
    reading = ddc_poll([name], force_refresh=True).get(name)
    if not reading:
        return {"ok": False, "errors": [f"Could not read DDC/CI input for {name}"]}
    profile["ddcInput"] = reading
    atomic_write(memory_path(), json.dumps(memory, indent=2, sort_keys=True) + "\n", 0o600)
    return {"ok": True, "ddcInput": reading, "label": input_label(reading), "memory": memory}


# ------------------------------------------------------------------ persist

def persist(payload: dict) -> dict:
    plan_raw = payload.get("plan")
    remove_block = payload.get("removeManagedBlock") is True
    outputs = validate_plan(plan_raw) if plan_raw is not None else None
    memory = payload.get("memory")
    if outputs is None and memory is None and not remove_block:
        raise ValueError("persist requires plan, memory, or removeManagedBlock")
    if memory is not None and not isinstance(memory, dict):
        raise ValueError("memory must be an object")

    # Write monitors.lua FIRST so any failure (symlink, permissions, syntax
    # check) aborts BEFORE committing the new memory to displays.json.
    changed = False
    if remove_block:
        changed = write_monitors_lua([], remove_block=True)
    elif outputs is not None:
        changed = write_monitors_lua(outputs)

    if memory is not None:
        monitors = memory.get("monitors") if isinstance(memory.get("monitors"), dict) else {}
        if outputs is not None:
            learn = [o for o in outputs if o["ddcWatch"] and not (monitors.get(o["fp"]) or {}).get("ddcInput")]
            if learn:
                readings = ddc_poll([o["name"] for o in learn], force_refresh=True)
                for output in learn:
                    profile = monitors.get(output["fp"])
                    if isinstance(profile, dict) and readings.get(output["name"]):
                        profile["ddcInput"] = readings[output["name"]]
        atomic_write(memory_path(), json.dumps(memory, indent=2, sort_keys=True) + "\n", 0o600)
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
        elif command == "revert":
            result = revert(read_stdin_json())
        elif command == "persist":
            payload = read_stdin_json()
            if not isinstance(payload, dict):
                raise ValueError("persist expects an object")
            result = persist(payload)
        elif command == "ddc-poll":
            result = ddc_poll(argv[1:])
        elif command == "ddc-inputs":
            if len(argv) < 2:
                raise ValueError("ddc-inputs expects monitor name")
            result = ddc_inputs(argv[1])
        elif command == "ddc-set-input":
            if len(argv) < 3:
                raise ValueError("ddc-set-input expects monitor name and code")
            result = ddc_set_input(argv[1], argv[2])
        elif command == "ddc-relearn":
            if len(argv) < 3:
                raise ValueError("ddc-relearn expects monitor name and fingerprint")
            result = ddc_relearn(argv[1], argv[2])
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
