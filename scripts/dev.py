#!/usr/bin/env python3
"""Opt-in development deployment; never write the production plugin folder."""
import argparse
import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ID = "dicey.bar"
KIND = "bar"
EXCLUDED = {".git", ".dev", ".venv", ".serena", ".opencode", "__pycache__",
            "docs", "tests", "scripts", "mockups", "preview", "node_modules"}
MARKER = ".dicey-development"


def command(*args):
    # A plugin reload tears down and rebuilds every bar and panel across all
    # monitors, which can exceed omarchy-shell's 2s default IPC timeout.
    env = os.environ.copy()
    env.setdefault("OMARCHY_SHELL_IPC_TIMEOUT", "15s")
    return subprocess.run(args, check=True, capture_output=True, text=True, timeout=60, env=env).stdout.strip()


def snapshot(root):
    """Read runtime files only; reject symlinks rather than follow them."""
    files = {}
    for directory, dirs, names in os.walk(root, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d not in EXCLUDED and not d.startswith("."))
        for name in dirs + names:
            path = Path(directory) / name
            if path.is_symlink():
                raise ValueError(f"Symlink is not allowed: {path}")
        for name in sorted(names):
            path = Path(directory) / name
            if not name.startswith(".") and (path.suffix in {".qml", ".js", ".py", ".json"} or name in {"LICENSE", "THIRD_PARTY_NOTICES.md"}):
                files[path.relative_to(root)] = path.read_bytes()
    return files


def revision_tag(files):
    digest = hashlib.sha256()
    for relative in sorted(files):
        digest.update(str(relative).encode())
        digest.update(b"\0")
        digest.update(files[relative])
        digest.update(b"\0")
    return "r" + digest.hexdigest()[:12]


def write_stage(files, stage, plugin_id):
    dev_id = plugin_id + ".dev"
    rev = revision_tag(files)
    for relative, data in files.items():
        # Place runtime files under a content-hashed subdirectory so each edit
        # changes entryPoints URLs and bypasses Quickshell's QML component cache.
        destination = (stage / relative) if relative == Path("manifest.json") else (stage / rev / relative)
        destination.parent.mkdir(parents=True, exist_ok=True)
        # Scope quoted plugin-ID literals only; keep prose markers such as
        # display_ctl.py's "-- BEGIN dicey.bar displays" aligned with production.
        if relative.suffix in {".qml", ".js", ".py", ".json"}:
            data = data.decode().replace(f'"{plugin_id}"', f'"{dev_id}"').encode()
        destination.write_bytes(data)
    manifest_path = stage / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["id"] = dev_id
    if isinstance(manifest.get("entryPoints"), dict):
        manifest["entryPoints"] = {k: f"{rev}/{v}" for k, v in manifest["entryPoints"].items()}
    if isinstance(manifest.get("name"), str):
        manifest["name"] += " (development)"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    (stage / MARKER).write_text(plugin_id + "\n")


def require_owned(target, plugin_id):
    if target.is_symlink() or not (target / MARKER).is_file() or (target / MARKER).read_text() != plugin_id + "\n":
        raise ValueError(f"Refusing to replace an unmanaged development folder: {target}")


def _staging_root(plugins, staging_parent):
    """Prefer a staging directory on the same filesystem outside `plugins` so
    deleting old copies never floods Omarchy's recursive inotify watcher."""
    if staging_parent is not None:
        try:
            staging_parent.mkdir(parents=True, exist_ok=True)
            if os.stat(staging_parent).st_dev == os.stat(plugins).st_dev:
                return staging_parent
        except OSError:
            pass
    return plugins


def _swap_in(stage, target, scratch):
    if target.exists():
        # Prefer kernel atomic RENAME_EXCHANGE (GNU mv --exchange) so `target`
        # never briefly vanishes mid-scan; fall back to two renames if absent.
        if shutil.which("mv"):
            probe = subprocess.run(
                ["mv", "--exchange", "--no-copy", "-T", "--", str(stage), str(target)],
                capture_output=True, text=True, timeout=30,
            )
            if probe.returncode == 0:
                return
        old = scratch / "previous"
        target.rename(old)
        try:
            stage.rename(target)
        except OSError:
            if old.exists():
                old.rename(target)
            raise
    else:
        stage.rename(target)


def deploy(files, plugins, plugin_id, staging_parent=None):
    target = plugins / (plugin_id + ".dev")
    plugins.mkdir(parents=True, exist_ok=True)
    if target.exists() or target.is_symlink():
        require_owned(target, plugin_id)
    stage_dir = _staging_root(plugins, staging_parent)
    with tempfile.TemporaryDirectory(prefix=".dicey-stage-", dir=stage_dir) as directory:
        scratch = Path(directory)
        stage = scratch / "plugin"
        stage.mkdir()
        write_stage(files, stage, plugin_id)
        command("omarchy", "plugin", "validate", str(stage))
        _swap_in(stage, target, scratch)
    # Do not call rescanPlugins here: moving `target` into `plugins` already
    # wakes Omarchy's 150ms debounced inotify watcher for a single clean reload.


def listed():
    return json.loads(command("omarchy", "plugin", "list", "--json"))


def rescan_until(plugin_id, present):
    # Let PluginRegistry's inotify watcher finish one reload; poll read-only and
    # only send a single fallback rescanPlugins if discovery hasn't settled.
    start = time.monotonic()
    deadline = start + 25
    fallback_sent = False
    while True:
        try:
            if any(row["id"] == plugin_id for row in listed()) == present:
                return
            if not fallback_sent and time.monotonic() - start >= 1.5:
                command("omarchy-shell", "shell", "rescanPlugins")
                fallback_sent = True
        except (subprocess.SubprocessError, ValueError):
            if time.monotonic() >= deadline:
                raise
        if time.monotonic() >= deadline:
            raise ValueError(f"Shell discovery did not settle for {plugin_id}")
        time.sleep(0.25)


def _remove_quietly(target, plugins, staging_parent):
    stage_dir = _staging_root(plugins, staging_parent)
    try:
        with tempfile.TemporaryDirectory(prefix=".dicey-remove-", dir=stage_dir) as trash:
            target.rename(Path(trash) / "plugin")
            return
    except OSError as exc:
        if exc.errno != errno.EXDEV:
            raise
    shutil.rmtree(target)


def restore(journal, plugins, plugin_id, staging_parent=None):
    if not journal.exists():
        raise ValueError("No development session to restore.")
    previous = json.loads(journal.read_text())["previous"]
    dev_id = plugin_id + ".dev"
    target = plugins / dev_id
    if target.exists() or target.is_symlink():
        require_owned(target, plugin_id)
    if KIND != "bar" and any(row["id"] == dev_id and row["enabled"] for row in listed()):
        command("omarchy", "plugin", "disable", dev_id)
    command("omarchy", "plugin", "enable", previous)
    if target.exists():
        _remove_quietly(target, plugins, staging_parent)
    rescan_until(dev_id, False)
    journal.unlink()
    print(f"Restored {previous}; production files were not modified.", flush=True)


def open_state(path):
    """Open the state directory only if no other account can plant entries in it."""
    refusal = f"Refusing a development state directory that is not private to you: {path}"
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except OSError:
        raise ValueError(refusal) from None
    info = os.fstat(fd)
    if info.st_uid != os.getuid() or info.st_mode & 0o022:
        os.close(fd)
        raise ValueError(refusal)
    return fd


def interrupted(_signum, _frame):
    raise KeyboardInterrupt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["start", "restore"])
    args = parser.parse_args()
    # Recovery must work even while the source manifest is broken or missing.
    plugin_id = PLUGIN_ID
    kind = KIND
    plugins = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "omarchy/plugins"
    state = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "omarchy/development"
    state_fd = open_state(state)  # Kept open for the process lifetime.
    # Use the verified directory itself, so a swapped path cannot redirect writes.
    journal = Path(f"/proc/self/fd/{state_fd}") / (plugin_id + ".json")
    lock_fd = os.open(plugin_id + ".lock", os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600,
                      dir_fd=state_fd)
    with os.fdopen(lock_fd, "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            parser.exit(1, "Development is already running. Stop it with Ctrl+C in its terminal.\n")
        if args.action == "restore":
            restore(journal, plugins, plugin_id, staging_parent=state)
            return
        if journal.exists():
            parser.exit(1, "An interrupted session exists. Run scripts/dev.py restore first.\n")
        target = plugins / (plugin_id + ".dev")
        if target.exists() or target.is_symlink():
            parser.exit(1, f"Development folder already exists; inspect it before continuing: {target}\n")
        previous = [row["id"] for row in listed() if kind in row["kinds"] and row["enabled"]]
        if len(previous) != 1:
            parser.exit(1, f"Expected one enabled {kind}; found {previous}. No activation changed.\n")
        files = snapshot(ROOT)
        journal.write_text(json.dumps({"previous": previous[0]}) + "\n")
        signal.signal(signal.SIGTERM, interrupted)
        signal.signal(signal.SIGINT, interrupted)
        try:
            deploy(files, plugins, plugin_id, staging_parent=state)
            rescan_until(plugin_id + ".dev", True)
            if kind == "menu":
                command("omarchy", "plugin", "disable", previous[0])
            command("omarchy", "plugin", "enable", plugin_id + ".dev")
            if kind == "bar":
                # Omarchy's initial PluginBarWidgetRegistryApi.createObject()
                # strips QQmlComponent pointers inside its initial property map
                # until syncPluginApis() reassigns `widgets` on a rescan.
                command("omarchy-shell", "shell", "rescanPlugins")
            print(f"Development active: {plugin_id}.dev from {ROOT}\nCtrl+C restores {previous[0]}.", flush=True)
            while True:
                time.sleep(0.5)
                try:
                    changed = snapshot(ROOT)
                    if changed != files:
                        deploy(changed, plugins, plugin_id, staging_parent=state)
                        files = changed
                        print("Development copy updated.", flush=True)
                except (OSError, ValueError, subprocess.SubprocessError) as error:
                    print(f"Update rejected; fix the source and save again: {getattr(error, 'stderr', None) or error}", flush=True)
        except KeyboardInterrupt:
            pass
        finally:
            # Keep the journal on failure so restore can be retried, including
            # after a shell crash or an uncatchable termination.
            restore(journal, plugins, plugin_id, staging_parent=state)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        raise SystemExit(getattr(error, "stderr", None) or str(error))
