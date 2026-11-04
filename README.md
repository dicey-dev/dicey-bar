# Dicey Bar

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

## Requirements and updates

Tested on **Omarchy 4.0.6 / Quattro, Qt 6.12 and Quickshell 0.3.2**. Use an
Omarchy installation with its matching shell imports, Hyprland, Bash, Git and
`jq`. Configured widgets use Omarchy's normal helper commands; keyboard-layout
descriptions additionally use `xkbcli` (`libxkbcommon`), recording status uses
`pgrep` (`procps-ng`), and optional dictation uses Omarchy's Voxtype integration.
Only enable helpers you have installed. User-configured custom modules execute
their configured shell commands with your permissions. No installer/build,
privileged helper, external service, download or remote build is required.

```bash
omarchy plugin update dicey.bar
```

Omarchy fetches `origin HEAD` (the remote default branch) and fast-forwards
the installed checkout after showing the diff; it does **not** select releases
by semantic version, and a detached checkout is not protected from updates.
See [Releases and marketplace checklist](docs/publishing.md) for SemVer,
rollback limitations, migration from copied installs, and submission details.

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

Derived from Omarchy's `omarchy.bar`. Dicey code is Apache-2.0; upstream portions
remain MIT. Preserve both [`LICENSE`](LICENSE) and
[`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md). The clone-source metadata is
intentional replacement/restore integration, not a temporary author identity.
Passing validation is not a security audit or marketplace approval.
