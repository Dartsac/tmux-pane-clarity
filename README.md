# tmux-pane-clarity

Border-only focus indication, with full four-sided **framed zoom** on tmux 3.8+.

## Framed zoom: prefix + z

With the configured prefix, **Ctrl-t z** toggles the active tiled pane into a
full-window floating pane. Its highlighted frame surrounds all four sides,
including the terminal's outer edges. Other panes are covered while framed.
Press **Ctrl-t z** again to return to the saved splits and focus the same pane.

- Reuses the existing pane and running program; nothing is restarted.
- Keeps pane backgrounds and text colours unchanged.
- Uses your theme's active border colour and thin lines.
- Reserves one character on each side for the frame.
- Fits the frame when the window is resized.
- Restores saved split sizes, scaled to the current window size if necessary.
- If panes were added, removed or moved while framed, returns the pane without
  applying the obsolete saved layout.

This replaces the native prefix + z binding on versions with floating-pane
support. Native zoom remains available with `resize-pane -Z` from tmux's command
prompt, but native zoom does not provide a complete outer frame. Unrelated
floating panes are not converted by the framed-zoom shortcut.

## Normal split view

Tiled panes still use shared separators, not independent four-sided frames:

- Thin lines with the full active separator highlighted, not half-coloured.
- Your theme's active border colour; muted `#44475a` inactive borders.
- A blank top border with multiple panes; none for a lone tiled pane.
- No arrows, labels, text dimming or background changes.

With two tiled panes, the shared separator cannot clearly indicate which side
owns it. Use framed zoom when you need an unambiguous complete outline.

Updates target the affected window and preserve unrelated indexed hooks.
Alt-l or prefix + Alt-c refreshes the plugin. Recognizable hooks from the original
plugin version are removed on upgrade.

## Installation

Add before TPM initialization, then install with prefix + I:

```tmux
set -g @plugin 'Dartsac/tmux-pane-clarity'
```

Or load your local checkout:

```sh
bash ~/.config/tmux/plugins/tmux-pane-clarity/tmux-pane-clarity.tmux
```

Normal border styling requires tmux 3.2+; framed zoom requires floating-pane
support (tmux 3.8+). Tested on 3.8. Older versions retain their zoom binding.
The plugin owns border line type, indicators, format, inactive border colour and
top-border visibility. It never sets `window-style` or `window-active-style`.
Theme scripts loaded later may replace the inactive border colour; refresh the
plugin to restore it.

## Tests

```sh
bash tests/borders.sh
bash tests/framed_zoom.sh # tmux 3.8+
```

Tests use separate tmux servers without modifying running sessions.
