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
