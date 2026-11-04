# Initial-import security review

Reviewed before committing the standalone import. This is a focused source
review, not an independent security audit or a sandbox guarantee.

## Reviewed boundaries

- Manifest/entry points, runtime command execution, configured custom modules,
  widget loading, theme/helper output, test subprocesses, and staged file contents.
- Built-in helper invocations use fixed arguments or validated values. The bar
  intentionally supports shell commands explicitly configured by the user;
  `shell.json`, custom modules, and installed plugins must therefore be trusted.
- Monitor remapping keeps the existing shell and widget instances; it does not
  restart authentication services or change lock configuration.
- No installer, privileged command, download-to-shell pipeline, or embedded
  credential was identified in the imported runtime. Tooltips and widget content
  remain part of the shared, unsandboxed QML environment.
- Tests write only temporary fixtures and invoke QtTest with the offscreen
  software backend. They do not manipulate the active bar.
- Git metadata, environment secrets, local agent state, and generated Python
  files are excluded from the new source history where applicable.

## Checks

`omarchy plugin validate .` and the offscreen visibility regression passed.
Each staged commit is checked for high-confidence credential patterns, regular
file modes, generated/private filenames, applicable syntax, and whitespace.
The source command boundaries were manually reviewed; a regex scan alone is
not evidence that code is safe.

## Release follow-up

The publication-readiness pass adds the complete upstream MIT notice, explicit
Apache/MIT distribution metadata, SemVer/changelog checks and repeatable official
install/update/remove filesystem tests. System QML lint and native development
activation/reload/restore checks were run on Omarchy 4.0.6 / Qt 6.12 / Quickshell
0.3.2. Existing lint warnings are not suppressed; a clean-VM or broader-version
certification is not claimed. See [publishing evidence](publishing.md).

The dev tool writes only its marked `.dev` plugin, retains recovery state on
failed restoration and does not modify production source files. Version tooling
only edits the checkout's manifest/changelog; it does not publish or install.
Replacement bars retain the host's limited third-party service integration.
Install only trusted plugins: all plugin code runs with the user's permissions.
