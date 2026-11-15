# Dicey Bar

`dicey.bar` is a clone of Omarchy's first-party `omarchy.bar` plugin.
It is deliberately user-owned so Omarchy package updates do not overwrite its
floating-island presentation.

Omarchy rejects symlinked plugin directories. The project copy is the source of
truth; follow [the native development workflow](development.md) to validate and reload
the live plugin safely.

## What changed

- The top/bottom bar surface is transparent; left, center, and right sections
  paint their own themed rounded islands.
- Islands use `Color.bar.background` and `Style` tokens, so theme and font
  changes continue to apply.
- The bar has a 5px top offset and 6px island corners.
- `ScaleSafeRemap.qml` remaps layer surfaces after monitor geometry changes.
- Widget loaders preserve their instances while monitor changes remap the
  bar surface; monitor changes do not restart the shell.
- Island visibility follows configured entries, not child effective visibility,
  so remapping cannot latch all three islands permanently hidden.

## Notification island

When a new desktop notification arrives, the center island grows into a
themed pill showing the app icon and name, the summary and a one-line body,
then returns to the clock. It opens on the focused monitor's bar, stays about
5.5 seconds (4 for low urgency, 8 for critical, with an accent border), and
stays open while hovered.

- **Left click** runs the notification's default action through the host's
  public `notifications invokeLast` IPC, exactly as clicking its toast would.
  That IPC targets whichever toast is newest on screen when it runs, and the
  snapshot is not the screen: it omits transient toasts and still lists toasts
  that are held back or leaving. So the bar asks the host's public
  `notifications count` and re-reads the snapshot, and invokes only when the
  counts agree and the island's notification is the snapshot's newest.
  Otherwise the click only closes the island. A notification that reaches the
  host within about a quarter second of the click, before the host writes its
  snapshot, can still be the one activated, and so can an older toast if the
  island's own toast is in its brief leave animation. Clicks in the first 0.4 seconds
  after the pill opens are ignored, so a click aimed at what was under it does
  not run an action.
- **Right or middle click** closes the island. The host toast is untouched.
- Turn it off with `"notificationIsland": false` in the `bar` section of
  `~/.config/omarchy/shell.json` (hot-reloads). Horizontal bars only; it does
  not show while the bar is hidden. Needs Omarchy 4.5's notification snapshot.

### Host boundary

Omarchy's notification service stays the only notification server and toast
owner. The island reads the host's popup snapshot
`~/.local/state/omarchy/notifications/live.json` read-only (Omarchy 4.5 format;
not a promised upstream API), skips rows the host already had when the bar
started and rows older than 15 seconds, and draws all sender text as plain
text. Sender images and links are never loaded; icons come only from the icon
theme by name. It starts no D-Bus owner, daemon or process besides the one
IPC call per left click, and never uses Do Not Disturb.

Omarchy has no supported setting or plugin API to hide its toasts, so they
still appear next to the island. Dicey Bar does not hide them with layer
rules or by editing host files. If Omarchy adds a supported switch, the island
can replace the toast. Dicey Bar does not depend on Dicey Home, and Dicey Home
does not depend on it.

## Activation

```bash
omarchy bar use dicey.bar
omarchy restart shell
```

To return to the packaged bar:

```bash
omarchy bar use omarchy.bar
omarchy restart shell
```
