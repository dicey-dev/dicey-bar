# Dicey Bar

A floating-island status bar for Omarchy, retaining its theme integration and
widgets while remapping surfaces safely after monitor layout/scale changes.

This repository was migrated from `dicey-kustomarchy/plugins/dicey.bar`, including
the working-tree source, documentation, and regression test. Its root manifest
still uses **`dicey.bar`**. The source history remains in the original repository;
this repository records the migrated source in a new local history, without a remote.

## Local development

Edit this checkout, not the installed copy. The existing local synchronizer now
maps `~/projects/dicey-bar` to `~/.config/omarchy/plugins/dicey.bar` and is enabled
on this machine. It stages and validates changes, backs up the previous live
copy, and lets Omarchy hot-reload. Git metadata, docs, tests, and mockups are not
deployed. No shell restart is needed for ordinary edits.

```bash
omarchy plugin validate .
/usr/bin/python3 -B -m unittest discover -s tests -p 'test_*.py'
```

Transitional sync controls (from any directory):

```bash
~/dicey-kustomarchy/scripts/install-plugin dicey.bar
~/dicey-kustomarchy/scripts/manage-watcher status
~/dicey-kustomarchy/scripts/manage-watcher stop
~/dicey-kustomarchy/scripts/manage-watcher restart
```

Regenerate the watcher with `restart` after adding a runtime subdirectory.
See [the development-environment plan](docs/development.md) for the proposed
independent per-project workflow and official documentation.

## Activation and rollback

```bash
omarchy bar use dicey.bar
# Return to the built-in bar:
omarchy bar use omarchy.bar
```

Before uninstalling, stop the sync watcher so it cannot reinstall the plugin.
Switch to the built-in bar, then use `omarchy plugin remove dicey.bar`.
Do not delete shared shell configuration or other plugins.

## Documentation and release status

- [Design and behavior](docs/dicey-bar.md)
- [Original upstream bar reference](docs/upstream-bar.md)

Derived from Omarchy's `omarchy.bar`; upstream authorship is retained in the
migrated source. `LICENSE` is copied unchanged from the parent repository
(Apache-2.0). A full upstream-license/notice and compatibility audit remains
required before marketplace publication. This migration does not change
runtime behavior, plugin metadata, or claim marketplace readiness.
