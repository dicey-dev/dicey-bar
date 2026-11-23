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

## Kept in step with the stock bar

- The center island maps Omarchy's `centerBegin`, `center` and `centerEnd`
  sections onto its hover-revealed flanks and anchor; widgets in the `tray`
  section are drawn in the tray's drawer. Each widget keeps its own section, so
  drags and settings write back where it lives.
- Under a fullscreen window the bar takes no input, so a bar Hyprland lifts
  back above fullscreen cannot steal clicks from it.
- `omarchy-toggle-bar` can nudge the bar to re-read its hidden flag through
  the `omarchy.bar` `syncHidden` IPC target.
- Panels switching within one section morph their card from the last one
  (`notePopoutCard` / `popoutCardFor`).

The tray-section drawer and the card morph rely on the Omarchy 4.5 tray and
panels; on Omarchy 4.0.6 the tray and panels keep their older behavior.

## Displays panel

`displays/` holds an optional monitor manager, used through the
`dicey.displays` layout ID. Bar.qml resolves that ID itself because a
third-party bar only sees a read-only snapshot of the widget registry.

- `DisplayModel.js` is pure layout logic: monitor identity (EDID
  make/model/serial, connector name for internal panels), remembered
  per-monitor profiles and per-combination layouts, snapping and gap closing.
- `display_ctl.py` is the only code that runs commands or writes files. It
  validates every plan, writes Lua strings with byte escapes, applies rules
  with `hyprctl eval`, and owns the `-- BEGIN dicey.bar displays` block in
  `monitors.lua`.
- `DisplayService.qml` runs once per bar. It reapplies remembered settings
  when monitors are added or removed, polls DDC/CI input for watched
  monitors, and shows the identify and keep/revert overlays.
- `DisplaysWidget.qml` is the panel.

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
