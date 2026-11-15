# Changelog

All notable changes to Dicey Bar are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/), with **0.x alpha** versions while
the project is under development. `manifest.json` records the last released
version; changes under Unreleased are not a release. Every released version
also has release notes in `docs/releases/vX.Y.Z.md`, and GitHub releases use
matching `vX.Y.Z` tags.

## [Unreleased]

### Added

- **Notification island.** When a new desktop notification arrives, the center
  (clock) island grows into a themed pill with the app icon and name, the
  summary and a one-line body, then shrinks back to the clock. Left click runs
  the notification's default action, as clicking its toast would. Right or
  middle click closes the island. Hovering keeps it open. Set
  `"notificationIsland": false` in the `bar` section of
  `~/.config/omarchy/shell.json` to turn it off.
- Release notes for every version under `docs/releases/`, written and checked
  by `scripts/version.py`.

### Known limitations

- Omarchy's own notification toasts still appear alongside the island.
  Omarchy has no supported way for a plugin to hide them yet, and Dicey Bar
  does not hack around the host.
- The island reads the host's popup snapshot, so it opens about a quarter
  second after the toast, and skips notifications the host never stores
  (transient ones, and those silenced by Do Not Disturb).

## [0.1.0] - 2026-11-04

### Added

- Floating-island replacement for Omarchy's bar: themed left, center and right
  islands with the local widgets, derived from `omarchy.bar`.
- Monitor-scale-safe remapping of the bar surface after monitor layout or
  scale changes.
- Opt-in development mode (`scripts/dev.py`) that runs `dicey.bar.dev` without
  touching the installed `dicey.bar` files.
- Upstream MIT notices, release tooling and marketplace preparation docs.

### Fixed

- Plugin ownership and description metadata, without changing the plugin ID.

## Pre-release development

- Floating-island bar with monitor-scale-safe remapping and local widgets.

[Unreleased]: https://github.com/dicey-dev/dicey-bar/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/dicey-dev/dicey-bar/releases/tag/v0.1.0
