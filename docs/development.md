# Dicey Bar development environment

## How Omarchy development works

Official sources checked in the browser:

- [Shell Plugins manual](https://omarchy.org/manual/shell-plugins/)
- [Development guide](https://plugins.omarchy.org/develop.html)
- [Shell runtime reference](https://github.com/omacom/omarchy/blob/quattro/docs/omarchy-shell.md)

A plugin is QML/JavaScript inside the existing long-running `omarchy-shell`, not
a standalone application. No frontend server or second Quickshell process is
needed. Omarchy automatically reloads plugin code saved under
`~/.config/omarchy/plugins/`; `shell.json` is also watched. Never edit packaged
files under `/usr/share/omarchy`.

The official tutorial normally edits a user-owned clone directly. We want Git
source under `~/projects`, so a one-way copy deploys it into the real plugin
directory. Validation rejects symlinks. The copy watcher is a development
convenience, not a runtime dependency marketplace users should install.

## Working setup today

- Source: `~/projects/dicey-bar`; manifest ID: **`dicey.bar`**.
- Runtime copy: `~/.config/omarchy/plugins/dicey.bar`.
- The shared `dicey-kustomarchy-sync.path` watches both projects and existing
  runtime subdirectories, including Bar's `widgets/` and `indicators/`.
- Its service stages a filtered copy, validates it, compares contents, backs up
  the previous live folder, replaces changed contents, and requests a rescan.
- Git metadata, docs, tests, mockups, scripts, virtualenvs, secrets in `.env*`,
  caches, and Python bytecode are excluded. A lock serializes deployments.
- Sync is one-way: direct live edits will be overwritten by a later source save.
- This transitional setup still depends on `~/dicey-kustomarchy/scripts`.
  Do not delete that checkout until independent tooling replaces it.
- Run `manage-watcher restart` after adding runtime subdirectories: systemd path
  units do not recursively add watches for future directories.

From this repository:

```bash
omarchy plugin validate .
/usr/bin/python3 -B -m unittest discover -s tests -p 'test_*.py'
~/dicey-kustomarchy/scripts/install-plugin dicey.bar
~/dicey-kustomarchy/scripts/manage-watcher status
```

Sync does not activate a plugin. Use `omarchy bar use dicey.bar` to select this
bar, or `omarchy bar use omarchy.bar` for fallback. Stopping the shared watcher
pauses syncing for both projects without disabling either installed plugin.

## Recommended independent setup (next implementation)

Give each repository a version-controlled `scripts/dev` interface, with separate
targets, locks, backup locations, and optional per-project user services:

| Command | Proposed behavior |
| --- | --- |
| `doctor` | Check Omarchy/Qt versions, tools, import paths, IPC, IDs, target ownership, and conflicting live Git checkouts |
| `check` | Manifest validation, QML lint, regression tests; no live changes |
| `sync` | Stage this plugin, validate, back up, deploy only changed contents, rescan |
| `watch` | Foreground recursive `inotifywait`, debounce saves, handle new directories/deletions/editor renames, exclude non-runtime files |
| `logs` / `status` | Shell logs, enabled state, source/deployed revision, last successful deployment and last error |
| `stop` | Stop only this project's optional background watcher |
| `restart` | Explicit opt-in shell restart, never automatic on every save |
| `rollback` | Stop deployment first, restore the stock bar or a selected last-good snapshot |

Start with foreground `watch` so failures are visible. Add independent user
services only after testing it, then retire the shared watcher. Never let two
watchers deploy the same plugin. Do not rewrite plugin IDs or change activation
and user settings implicitly when starting development tooling.

Use installed Omarchy/Qt tools for runtime fidelity. The existing visibility
test uses system Python and QtTest. `qmllint`/`qmlls` should use the installed
shell import path; no virtualenv or npm runtime dependency is currently needed.
A starting lint command is:

```bash
qmllint -I "${OMARCHY_PATH:-/usr/share/omarchy}/shell" \
  Bar.qml ScaleSafeRemap.qml widgets/*.qml indicators/*.qml
```

Establish a reviewed lint baseline first: dynamically injected shell interfaces
may require additional import/type setup. Manifest validation alone does not
validate QML execution, and this migration does not claim a clean lint baseline.

## Reloading and safe testing

Ordinary QML/JS edits should hot-reload. If discovery is stale, use
`omarchy-shell shell rescanPlugins`. Kept **service** instances can survive
reload, so their code changes may require a deliberate shell restart. Python
changes apply when the helper process is restarted. Do not restart the shell on
every save or during lock/authentication interactions.

Inspect logs with `qs log -p "${OMARCHY_PATH:-/usr/share/omarchy}/shell" --tail 100`.
Use a separate Omarchy VM or graphical test account for risky compositor/bar
changes. A different `$HOME` alone does not isolate the active shell's IPC.
Plugins run unsandboxed with user permissions.

Implementation order:

1. Independent `doctor`, `check`, `sync`, and deployment safety tests.
2. Recursive/debounced `watch` with invalid-revision protection, deterministic
   snapshots, and add/change/delete/rename coverage.
3. Optional per-project services; retire the transitional coordinator.
4. VM smoke tests: themes, monitors/scaling, widgets, restart, fallback,
   update/removal, and interaction with Dicey Pane.

Official docs note reduced service-backed third-party widget integration under
replacement bars; document/test that limitation. Also audit Qt 6.12 palette
references (`import qs.Commons as Commons`, `Commons.Color`), clone capabilities,
and upstream licensing before publication. None are changed by this migration.
