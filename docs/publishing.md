# Releases and marketplace checklist

## Versioning

`manifest.json` is the single version source of truth. Use stable
`MAJOR.MINOR.PATCH` versions: major for breaking public behavior/configuration,
minor for compatible features, patch for compatible fixes. The plugin ID stays
`dicey.bar`; changing it would create a different installation/listing.

```bash
python3 scripts/version.py check
python3 scripts/version.py patch "Describe the compatible fix."
# Use minor or major instead when appropriate.
tests/run.sh
```

The bump command updates the manifest and prepends a dated changelog entry. It
does not commit, publish, modify an installed copy, or create a tag. Review the
diff and checks, merge the release PR, then tag that exact tested main commit
`vX.Y.Z` and publish matching GitHub release notes. Never reuse or move a
published version tag; release another patch instead.

## Install/update semantics

Use the README's `omarchy plugin add`, `update` and `remove` commands. The
official installer clones a root plugin repository and runs manifest validation;
it does not run our development tools or install hooks. There is no build step.
Updates fetch `origin HEAD` (the remote default branch) and fast-forward to
`FETCH_HEAD`, with a reviewable diff, **not a SemVer package resolver**. They do
not follow the local branch's tracking configuration. Local changes are not a
supported production update strategy.

For a rollback snapshot, fetch tags in the installed Git checkout, review the
desired tag, and switch to it deliberately:

```bash
PLUGIN_DIR="$HOME/.config/omarchy/plugins/dicey.bar"
git -C "$PLUGIN_DIR" fetch --tags origin
git -C "$PLUGIN_DIR" switch --detach v1.0.1
omarchy-shell shell rescanPlugins
```

A detached checkout is **not an update-resistant pin**. Both
`omarchy plugin update dicey.bar` and the all-plugin `omarchy plugin update`
can advance it to the remote default HEAD. This Omarchy version has no pin or
exclusion option; avoid both updater forms if you need to hold that snapshot.
Returning to `main` is optional for ordinary updates, not a protection mechanism.
Do not overwrite local changes. For an existing non-Git copy: stop development,
switch to `omarchy.bar`, back up the installed directory outside the plugin scan
root, remove the old copy using Omarchy, and perform a fresh standard install.
Removal backs up non-Git copies; preferences/shared shell config are not reset.
These steps change production and must be deliberate user actions.

## Guide compliance and release evidence

References: [development guide](https://plugins.omarchy.org/develop.html),
[runtime contract](https://github.com/omacom/omarchy/blob/quattro/shell/README.md),
[publishing guide](https://plugins.omarchy.org/publish.html).

- Public, single-plugin repository with root manifest, README and licenses.
- Permanent non-reserved namespaced ID; kind `bar` matches `entryPoints.bar`.
- Safe relative entry point, real files and no symlinks. Runtime components are
  included; tests/docs are not separate plugins.
- Author/repository/license metadata describes Dicey; original Omarchy authorship
  and the complete MIT notice are preserved separately. `clonedFrom` is retained
  deliberately for supported replacement/restore semantics; the tutorial's
  clock-only development cleanup is not applied blindly to a full replacement.
- Dependencies, command execution, permissions, install/update/removal and
  development isolation limits are documented. No privileged setup/remote build.
- `tests/run.sh`, manifest validation and system `qmllint` run before release.
- `python3 -B tests/verify_install.py` exercises official add/update/remove in a
  temporary HOME with local Git and **stubbed desktop IPC**. It is not a VM test.
- Native `.dev` testing covers activation, edits, invalid-update rejection,
  restoration/removal and production-file preservation. Offscreen Qt tests
  cover monitor-style hide/show remapping. The supported host is Omarchy 4.0.6,
  Qt 6.12, Quickshell 0.3.2; untested versions are not claimed compatible.

An optional preview is not required; no user desktop screenshot is distributed.
The marketplace will use its fallback image. Validation/security scanning never
means certification, and the marketplace maintainer still decides approval.

## Form values (owner submits)

- Repository: `https://github.com/dicey-dev/dicey-bar`
- Category: `Appearance`
- Tags: `bar`, `quickshell`, `hyprland`
- Notes: Floating-island full bar replacement; see README for compatibility,
  trusted custom-command behavior and stock-bar rollback. No build or installer.

Open the [submission form](https://github.com/omacom/omarchy-plugin-marketplace/issues/new?template=submit-plugin.yml).
The owner confirms permission/ownership and all submission checklist statements.
This repository does not submit the form automatically. Review capability
disclosures from the static security baseline with the marketplace maintainer;
do not strip legitimate features simply to hide what the plugin does.
