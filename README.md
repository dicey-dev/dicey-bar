# Dicey Bar

**Alpha software (0.x).** Expect rough edges and compatibility changes.

A floating-island status bar for Omarchy, retaining its theme integration and
widgets while remapping surfaces safely after monitor layout/scale changes.

An independent Omarchy plugin repository. The plugin ID is **`dicey.bar`**;
no other Dicey repository, installer, or synchronization service is required.

## Install

Plugins run unsandboxed inside the existing Omarchy shell. Review the source
before enabling it. Requires Omarchy's Quattro shell and its Qt/Quickshell imports.

```bash
omarchy plugin add https://github.com/dicey-dev/dicey-bar.git
omarchy bar use dicey.bar
```

## Development

From any checkout (not the installed production folder):

```bash
tests/run.sh
omarchy plugin validate .
scripts/dev.py start
# Ctrl+C stops watching and restores the previously active bar.
```

Python is a **development tool**, not a runtime dependency. Its standard library
orchestrates a real offscreen Qt test. `python3` is resolved through `PATH`; no
pip packages are needed. Development runs as `dicey.bar.dev`, without modifying
the installed `dicey.bar` files. No background watcher is installed.
See [Development](docs/development.md) for setup, recovery, and production updates.

## Activation and rollback

```bash
omarchy bar use dicey.bar
# Return to the built-in bar:
omarchy bar use omarchy.bar
```

Switch to the built-in bar, then use `omarchy plugin remove dicey.bar`.
Do not delete shared shell configuration or other plugins.

## Documentation and release status

- [Design and behavior](docs/dicey-bar.md)
- [Original upstream bar reference](docs/upstream-bar.md)

Derived from Omarchy's `omarchy.bar`; upstream authorship is retained in the
source. `LICENSE` is Apache-2.0. A full upstream-license/notice and compatibility
audit remains required before marketplace publication. Passing development
checks is not a security audit or marketplace approval.
