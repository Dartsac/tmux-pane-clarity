# tmux-pane-clarity

Thin, complete pane frames without changing pane backgrounds or text colours.

```tmux
set -g @plugin 'Dartsac/tmux-pane-clarity'
set -g @pane_clarity_mode 'zoom'   # default; or 'always'
```

Set the mode before TPM initialization; install with prefix + I. Alt-l or
prefix + Alt-c reloads the plugin. The plugin owns border lines, indicators,
format, inactive border colour and top-border visibility. It never sets
`window-style`, `window-active-style` or the active border colour (your theme's).

## `zoom` mode: framed zoom

Normal splits use native shared separators. On tmux 3.8+, prefix + z floats the
active pane to a full-window frame and restores the saved splits on the second
press (scaled if the terminal was resized; kept as-is if panes changed while
framed). The running program is never restarted. Older tmux keeps native zoom.

## `always` mode: every pane framed

Requires **tmux 3.8 (floating panes, version-2 JSON layouts), Rust/cargo** (to
build the layout engine) **and Python 3.8+** (installer and tmux-resurrect hooks).
This is a custom layout manager. The logical tiled layout is stored on the
window (`@pane_clarity_layout`) and each pane is rendered as a non-overlapping
floating pane inside its box. Programs keep their pane IDs and processes.
A window with a single pane is left natively tiled, without a frame; framing
starts as soon as it is split and stops again when it is back to one pane.

Side-by-side frames keep tmux's one-column separator between them. Stacked frames
touch, with no blank row, because a terminal row is about twice as tall as a
column is wide. Both gaps then look the same.

How operations are handled:

- **Geometry only, no retiling** (one batched tmux call): directional and last-pane
  navigation (tmux's MRU rule at T-junctions, wraparound), zoom, directional
  `resize-pane` (same border/neighbour semantics as tiled tmux), closing panes
  (space goes to the neighbour, as in tmux), mouse border drags (corners
  included; dragging a frame without resizing snaps it back), window growth.
- **Native round trip** (panes briefly tiled, so apps redraw): `split-window`,
  `swap-pane`, `rotate-window`, `select-layout`/`next-layout` and non-directional
  `resize-pane` forms. tmux keeps its own semantics for cwd, `-b/-f/-l`, etc.
- **Panes created outside the adapter** (the `:` prompt, other plugins, scripts)
  are adopted next to the pane they were split from.
- **Terminal shrink**: tmux 3.8 never shrinks a window whose panes all float, so
  on `client-resized` the manager tiles one pane to let tmux re-fit the window,
  then frames again.

Each frame costs one cell on each side (plus the separator column between side-by-side frames).

Toggle on a running server (or change `@pane_clarity_mode` and reload):

```sh
python3 ~/.config/tmux/plugins/tmux-pane-clarity/scripts/always_mode.py enable
python3 ~/.config/tmux/plugins/tmux-pane-clarity/scripts/always_mode.py disable
```

### Key bindings

Enabling rewrites your existing bindings (root, prefix and copy-mode tables) that
call managed commands — e.g. `|`/`_` splits, Vim-aware Ctrl-h/j/k/l (the process
test and send-keys branch are kept), prefix resize keys, prefix + z — to route
through the manager. Command lists (`a \; b`) are rewritten per command. Bindings
using `{ }` blocks are left native. Every rewrite is read back and reverted if
tmux parsed it differently. Originals are backed up and restored exactly on
disable.

### Commands from outside tmux key bindings

Raw tmux commands against floats have floating-pane semantics. Route layout and
navigation commands through the adapter:

```sh
~/.config/tmux/plugins/tmux-pane-clarity/bin/tmux split-window -h
```

`bin/tmux` is a small `sh` filter: anything other than the managed commands
(`split-window`, `resize-pane`, directional `select-pane`, `last-pane`,
`select-layout`, `next-layout`, `rotate-window`, `swap-pane`, `kill-pane`) is
exec'd directly to the real tmux, without starting the engine. Managed commands in
windows that aren't always-framed also pass through unchanged. It preserves
literal argv and server selection (`-S`, `-L`). Do not replace the real tmux
binary with it.

This dotfiles setup routes through it from a Fish `tmux` function
(`~/.config/fish/functions/tmux.fish`) and Neovim's `config/tmux.lua`. That file
also handles Ctrl-h/j/k/l in Neovim in place of vim-tmux-navigator. At a window
edge it starts `bin/tmux select-pane` directly and doesn't wait for it.
vim-tmux-navigator went through `system()`, which starts Neovim's `shell` (a
full fish startup) on every move.

### tmux-resurrect / continuum

Saves contain the logical tiled layout. Restores pause framing until the native
restore finishes. Existing resurrect hooks are chained and restored on disable.

### Layout engine and performance

The engine is `target/release/pane-clarity`, built from `src/` with no
dependencies. Plugin load runs `cargo build --release` when the binary is
missing or older than its sources. Each managed key press, hook or `bin/tmux`
call runs it once. It reads the window in one tmux call and applies the result
in one batch, which also clears the busy flag.

Time from a key press, typed into an attached terminal, to the visible change
(median, Apple M1 Max). Ctrl-h/l includes the Vim `ps | grep` check that native
tmux runs as well:

| | move (Ctrl-h/l) | zoom (prefix z) | split (prefix \|) |
|---|---|---|---|
| native tmux | 8.7 ms | 1.7 ms | 2.8 ms |
| pane clarity | 26 ms | 20 ms | 41 ms |

Older bindings written by this plugin, including those of its earlier Python
engine, are rewritten for the current engine on reload.

### Failure behaviour

Operations are serialized per server (a lock next to the tmux socket). Recovery
state is saved before every layout mutation. If a window becomes too small for
complete frames, or anything unexpected fails, that window falls back to native
tiled view. Panes are never destroyed. If a hard-killed manager leaves
`@pane_clarity_busy` set, rerun `always_mode.py enable` or `disable`.

## Tests

```sh
bash tests/borders.sh
bash tests/framed_zoom.sh
python3 tests/layout_test.py    # needs the built engine
cargo test --release            # Rust unit tests
```

Tests use isolated tmux servers (one attaches a real client in a pty to test
terminal resizing). They compare navigation, resizing and pane closing against
native tiled windows. They also check frame geometry and non-overlap, process
identity, unchanged styles, that geometry operations never retile, adoption,
binding rewrite/rollback, hooks, the adapter, resurrect and failure fallback.
Expected frame positions come from `tests/geometry.py`, a reference written
independently of the engine. Real Neovim/Fish integration is exercised when
installed.
