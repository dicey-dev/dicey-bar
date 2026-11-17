# Dicey Bar

**A floating-island status bar for [Omarchy](https://omarchy.org/).** It uses
your theme and your widgets, splits them into three rounded islands, and keeps
working when you change monitor layout or scale.

[![Release](https://img.shields.io/github/v/release/dicey-dev/dicey-bar?include_prereleases&label=release)](https://github.com/dicey-dev/dicey-bar/releases)
[![License](https://img.shields.io/badge/license-Apache--2.0%20AND%20MIT-blue)](THIRD_PARTY_NOTICES.md)
![Omarchy plugin](https://img.shields.io/badge/omarchy-plugin-orange)

![Dicey Bar: workspaces, clock and status islands floating over the desktop](preview.png)

**Alpha software (0.x).** The current release is **v0.1.1**. Expect rough edges
and possible compatibility changes; validation is not a stability guarantee.

An independent Omarchy plugin repository. The plugin ID is **`dicey.bar`**;
no other Dicey repository, installer, or synchronization service is required.

## Screenshots

The full bar on a 2560px monitor:

![Full-width Dicey Bar with left, center and right islands](docs/screenshots/bar.png)

| Left: launcher and workspaces | Center: clock | Right: tray and system status |
| :---: | :---: | :---: |
| ![Workspaces island](docs/screenshots/island-left.png) | ![Clock island](docs/screenshots/island-center.png) | ![Status island](docs/screenshots/island-right.png) |

These captures use the Starship theme and the author's own widget layout. The
islands show whatever widgets your bar layout lists, so some icons above come
from separate widget plugins rather than from Dicey Bar itself.

## Features

- **Floating islands.** The bar surface is transparent. The left, center and
  right sections each draw their own rounded island, with a small gap from the
  screen edge.
- **Theme-aware.** Islands use Omarchy's theme colors and style tokens, so
  theme and font changes apply straight away.
- **Safe across monitor and scale changes.** `ScaleSafeRemap.qml` remaps the
  bar's layer surfaces after a monitor geometry or scale change. Widgets keep
  their state and the shell isn't restarted.
- **Islands never get stuck hidden.** Island visibility follows your
  configured entries, so a remap can't leave all three islands hidden.
- **Clock stays centered.** The center anchor (normally the clock) stays at
  the physical center of the display, even when the center island holds other
  widgets.
- **Your existing layout.** Dicey Bar is a clone of Omarchy's own
  `omarchy.bar`. It reads the same bar layout and widget IDs, and the
  `omarchy bar` commands edit it.
- **Drop-in and reversible.** You can switch back to the stock bar with one
  command. There is no build step, daemon, privileged helper or second
  Quickshell process.
- **Plain-text rendering.** Window titles, tray tooltips and command output are
  always drawn as plain text, never as rich text or markup.

## Install

Plugins run unsandboxed inside the existing Omarchy shell. Review the source
before enabling it. Requires Omarchy's Quattro shell and its Qt/Quickshell imports.

```bash
omarchy plugin add https://github.com/dicey-dev/dicey-bar.git
omarchy bar use dicey.bar
```

## Configuration

Dicey Bar reads the `bar` section of `~/.config/omarchy/shell.json`, the same
section the stock bar uses. Edits hot-reload. The `omarchy bar` commands edit
that file for you:

```bash
omarchy bar position top                                   # or bottom, left, right
omarchy bar put omarchy.keyboard-layout --after omarchy.clock
omarchy bar move omarchy.weather --section center --index 0
omarchy bar set omarchy.clock format HH:mm
```

The `left`, `center` and `right` sections become the three islands. The stock
bar's newer `centerBegin`, `centerEnd` and `tray` sections are not drawn, so
keep widgets in the three main sections. `centerAnchor` names the widget that
stays at the physical center of the screen:

```json
"bar": {
  "id": "dicey.bar",
  "position": "top",
  "centerAnchor": "omarchy.clock",
  "layout": {
    "left": [{ "id": "omarchy.workspaces" }],
    "center": [{ "id": "omarchy.clock" }],
    "right": [{ "id": "omarchy.tray" }, { "id": "omarchy.audio" }, { "id": "omarchy.power" }]
  }
}
```

See [Design and behavior](docs/dicey-bar.md) for what Dicey Bar changes
compared with the stock bar.

## Requirements and updates

Tested on **Omarchy 4.0.6 and 4.5.0 / Quattro, Qt 6.12 and Quickshell 0.3.2**. Use an
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

## Removal

Switch to the built-in bar, then remove the plugin:

```bash
omarchy bar use omarchy.bar
omarchy plugin remove dicey.bar
```

Do not delete shared shell configuration or other plugins.

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

## Documentation and release status

- [Design and behavior](docs/dicey-bar.md)
- [Development](docs/development.md)
- [Releases and marketplace checklist](docs/publishing.md)
- [Original upstream bar reference](docs/upstream-bar.md)
- [Changelog](CHANGELOG.md)

Derived from Omarchy's `omarchy.bar`. Dicey code is Apache-2.0; upstream portions
remain MIT. Preserve both [`LICENSE`](LICENSE) and
[`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md). The clone-source metadata is
intentional replacement/restore integration, not a temporary author identity.
Passing validation is not a security audit or marketplace approval.
