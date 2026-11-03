# Development and production

## Structure and the Omarchy contract

This repository follows the small native-plugin organization used by
[Omaplug](https://github.com/fross100/omaplug): root manifest and public QML
entry points, feature components, runtime helpers where needed, `tests/run.sh`,
README and license. Omaplug itself documents normal installation/updates, not a
dedicated dev mode; our opt-in development tool is an additional workflow.

`manifest.json` declares `dicey.bar`, `kinds: ["bar"]` and
`entryPoints.bar: "Bar.qml"`. It replaces the whole bar, unlike the clock
`bar-widget` in the [official guide](https://plugins.omarchy.org/develop.html).
`widgets/` and `indicators/` contain reusable runtime components. `scripts/`,
`tests/` and `docs/` are author tooling, not shell entry points.

QML is Qt's declarative UI language. Quickshell exposes desktop APIs to QML;
Omarchy supplies lifecycle, components and theming. JavaScript runs inside Qt,
not Node. Plugins run unsandboxed in the existing long-running shell: no web
bundler, development web server or second Quickshell process is needed.

## Tests and validation

Install Python 3 and Arch's `qt6-declarative` package. Tests use only Python's
standard library and the system QtQuick/QtTest modules; no pip installation or
virtualenv is necessary. The runner deliberately resolves `python3` via `PATH`,
so check `command -v python3` if you have Conda or another activated environment.
Versions are not pinned or hermetic in this setup.

```bash
tests/run.sh
omarchy plugin validate .
qmllint -I "${OMARCHY_PATH:-/usr/share/omarchy}/shell" ./*.qml widgets/*.qml indicators/*.qml
```

The test runner works from any working directory. Python orchestrates real
offscreen Qt hide/show tests using the bar's visibility expressions, plus
temporary-folder tests of development deployment/recovery. Tests never activate
a desktop plugin. The Qt regression uses Arch's system Qt test runner; ensure
`qmllint` also comes from the matching Qt installation, not a different Conda Qt.
Existing QML lint warnings are not suppressed. Manifest validation alone does
not prove that QML loads or that the desktop interaction works.

## Opt-in development mode

Keep the Git checkout anywhere outside the installed production folder:

```bash
scripts/dev.py start
```

This foreground Python command:

1. Saves the previously active bar ID in a per-plugin recovery journal.
2. Copies runtime QML/JS/Python/JSON and license files into a validated, real
   `~/.config/omarchy/plugins/dicey.bar.dev` folder. Tooling, hidden directories,
   docs, tests, mockups and symlinks are excluded/rejected.
3. Activates that development ID. Installed `dicey.bar` files are never written.
4. Checks for source content changes every half second, including new runtime
   subdirectories and deletions. Each update is staged and manifest-validated
   before replacing the last valid development copy. Omarchy does the hot reload.
5. On Ctrl+C/SIGTERM, restores the previous bar and removes its development copy.

No daemon or systemd unit is installed. A per-plugin lock prevents two development
sessions from competing. Do not manually switch bars or update/remove plugins
while this command is active. After a terminal/shell failure or SIGKILL, run:

```bash
scripts/dev.py restore
```

The journal is retained if restoration fails, so it can be retried after the
shell is healthy. Its path is `$XDG_STATE_HOME/omarchy/development/dicey.bar.json`
(default `~/.local/state/omarchy/development/dicey.bar.json`). Unmanaged folders
using the development ID are not overwritten. New runtime asset formats should
be added deliberately to the tool's file selection.

Stop/disable any pre-existing external auto-deployer before adopting this
workflow. This repository depends on no other source checkout or shared watcher.
Never symlink the project into the plugin folder: Omarchy validation rejects it.

Development shares your current shell process and bar layout configuration.
It preserves production **files**, not process isolation. Use a separate
graphical session/VM for risky changes or fully isolated configuration testing.
Do not edit packaged `/usr/share/omarchy` files.

## Production install, update and removal

End users install this repository normally; no development scripts are needed:

```bash
omarchy plugin add https://github.com/dicey-dev/dicey-bar.git
omarchy bar use dicey.bar
# Later, deliberately review and install a published update:
omarchy plugin update dicey.bar
# Roll back to stock before removing:
omarchy bar use omarchy.bar
omarchy plugin remove dicey.bar
```

`plugin add` is for a fresh installation; `plugin update` requires a Git-managed
installed copy. An existing manually copied install needs an explicit migration,
not an automatic overwrite. Source edits do not update production.

Before [marketplace submission](https://plugins.omarchy.org/publish.html), finish
upstream attribution/license review and native monitor-scale/remapping,
restart, disable/re-enable and removal tests. Keep `omarchy.clonedFrom` while
replacement/restore behavior depends on it. Passing tests is not a security audit.
