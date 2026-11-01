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

## Remaining release work

Full QML lint/type baselining, Qt 6.12 compatibility, upstream license/notice
provenance, and clean-VM lifecycle testing are not completed by this review.
Replacement bars have limited third-party service integration. Install only
trusted plugins: all plugin code runs with the user's permissions.
