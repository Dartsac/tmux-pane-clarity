# tmux-pane-clarity

Thin, complete pane frames without changing pane backgrounds or text colours.

```tmux
set -g @plugin 'Dartsac/tmux-pane-clarity'
set -g @pane_clarity_mode 'zoom'   # default; or 'always'
```

Set the mode before TPM initialization; install with prefix + I. The plugin
owns border lines, indicators, format and top-border visibility. It never sets
`window-style`, `window-active-style` or the active border colour (your theme's).
Each window's `pane-border-status` is the only window option it sets: one you
set on a window yourself after the plugin has is yours, and it stays.

Options (set before TPM initialization; `''` turns a feature off):

```tmux
set -g @pane_clarity_border_style 'fg=#44475a'  # inactive borders; '' keeps yours
set -g @pane_clarity_reload_key 'M-c'           # prefix + Alt-c reloads the plugin
set -g @pane_clarity_root_reload_key 'M-l'      # Alt-l (no prefix) reloads it too
```

The plugin records where it is installed in `@pane_clarity_dir` (TPM uses
`~/.tmux/plugins/` or `~/.config/tmux/plugins/`), and the commands below use it.

### Load order and uninstalling

In `always` mode the plugin adapts the key bindings that exist when it loads, so
load it after anything that binds pane keys: your own `bind-key` lines (e.g.
Ctrl-h/j/k/l for Neovim) and plugins such as vim-tmux-navigator belong above
TPM's `run` line. Bindings made later stay native until you reload the plugin.

The plugin saves each binding and option it replaces (prefix + z, the reload
keys, the border options) and puts it back when it lets go of it. A key you
bind again after it loaded is the one put back, even across reloads. To uninstall,
remove it from the running server first, then remove its line from tmux.conf
and the plugin itself (prefix + Alt-u with TPM):

```sh
bash "$(tmux show-option -gqv @pane_clarity_dir)/scripts/uninstall.sh"
```

This switches always mode off, returns framed-zoom panes to their splits,
removes the plugin's hooks and settings, and restores your bindings, global
options, windows' own `pane-border-status` and tmux-resurrect hooks. Anything
you changed yourself since the plugin set it stays as you left it. A framed
pane is returned only from its own window (one you moved elsewhere is tiled
already), and nothing is ever framed. If a pane can't be returned to tiled
view (for example the engine binary is gone after `cargo clean`), it stops
with an error and changes nothing else, so the plugin's keys still work: fix
the problem and run it again. Switching only always mode off
(`always_mode.py disable`, below) keeps the plugin loaded in zoom mode.

## `zoom` mode: framed zoom

Normal splits use native shared separators. On tmux 3.8+, prefix + z floats the
active pane to a full-window frame and restores the saved splits on the second
press (scaled if the terminal was resized; kept as-is if panes changed while
framed). The running program is never restarted. Older tmux keeps native zoom.

## `always` mode: every pane framed

Requires **tmux 3.8 (floating panes, version-2 JSON layouts), Rust 1.82+**
(cargo, to build the layout engine; [rustup.rs](https://rustup.rs)) **and Python
3.8+** (installer and tmux-resurrect hooks). If any is missing, loading the plugin
says which on the status line (and in `@pane_clarity_error`) and loads in zoom
mode instead. If always mode is already on, a reload that finds a problem (or a
failed engine build) changes nothing and leaves it running as it was. Check by
hand with:

```sh
bash "$(tmux show-option -gqv @pane_clarity_dir)/scripts/preflight.sh"
```

The engine is built on first load (a few seconds) and whenever its sources change.
A failed build's output is kept in `target/build.log` in the plugin directory.

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

Switch always mode on or off on a running server, keeping the plugin loaded (or
change `@pane_clarity_mode` and reload):

```sh
python3 "$(tmux show-option -gqv @pane_clarity_dir)/scripts/always_mode.py" enable
python3 "$(tmux show-option -gqv @pane_clarity_dir)/scripts/always_mode.py" disable
```

### Key bindings

Enabling rewrites your existing bindings (root, prefix and copy-mode tables) that
call managed commands — e.g. `|`/`_` splits, Vim-aware Ctrl-h/j/k/l (the process
test and send-keys branch are kept), prefix resize keys, prefix + z — to route
through the manager. Command lists (`a \; b`) are rewritten per command. Bindings
using `{ }` blocks are left native. Every rewrite is read back and reverted if
tmux parsed it differently. Originals are backed up and restored exactly on
disable, unless you have bound or unbound that key since; a binding you change
while always mode is on becomes the original at the next reload.

### Commands from outside tmux key bindings

Raw tmux commands against floats have floating-pane semantics. Route layout and
navigation commands through the adapter, `bin/tmux`. The plugin exports its path
as `PANE_CLARITY_TMUX` to panes started after it loads:

```sh
"$PANE_CLARITY_TMUX" split-window -h
```

`bin/tmux` is a small `sh` filter: anything other than the managed commands
(`split-window`, `resize-pane`, directional `select-pane`, `last-pane`,
`select-layout`, `next-layout`, `rotate-window`, `swap-pane`, `kill-pane`) is
exec'd directly to the real tmux, without starting the engine. Managed commands in
windows that aren't always-framed also pass through unchanged. It preserves
literal argv and server selection (`-S`, `-L`). Do not replace the real tmux
binary with it.

### Shell and editor integration

`integrations/` has drop-in recipes:

- **fish:** copy `integrations/tmux.fish` to `~/.config/fish/functions/`.
- **bash/zsh:** source `integrations/tmux.sh` from `~/.bashrc` or `~/.zshrc`.

Both make the `tmux` you type go through the adapter when the plugin is loaded,
and are plain tmux elsewhere.

- **Neovim:** `integrations/nvim` is a small plugin for Ctrl-h/j/k/l (and Ctrl-\
  for the previous pane) between Neovim windows and tmux panes, in place of
  vim-tmux-navigator. At a window edge it starts the adapter's `select-pane`
  directly and doesn't wait for it (vim-tmux-navigator goes through `system()`,
  which starts Neovim's `shell` on every move). With lazy.nvim:

  ```lua
  {
    dir = "~/.tmux/plugins/tmux-pane-clarity/integrations/nvim", -- your TPM directory
    config = function() require("pane_clarity").setup() end,     -- { modes = { "n", "i" } } for insert too
  }
  ```

  tmux must pass these keys to Neovim, as for vim-tmux-navigator (above TPM's
  `run` line, so always mode adapts them):

  ```tmux
  is_vim="ps -o state= -o comm= -t '#{pane_tty}' | grep -iqE '^[^TXZ ]+ +(\\S+\\/)?g?(view|l?n?vim?x?|fzf)(diff)?$'"
  bind-key -n C-h if-shell "$is_vim" 'send-keys C-h' 'select-pane -L'
  bind-key -n C-j if-shell "$is_vim" 'send-keys C-j' 'select-pane -D'
  bind-key -n C-k if-shell "$is_vim" 'send-keys C-k' 'select-pane -U'
  bind-key -n C-l if-shell "$is_vim" 'send-keys C-l' 'select-pane -R'
  bind-key -n 'C-\' if-shell "$is_vim" 'send-keys C-\\' 'select-pane -l'
  ```

- **Everything else** (vim-tmux-navigator, scripts, other tools): put the
  adapter's directory first on `PATH`, e.g. `fish_add_path --prepend
  ~/.tmux/plugins/tmux-pane-clarity/bin`. The adapter finds the real tmux further
  along `PATH`; each tmux call then costs one extra `sh` start. It skips
  relative `PATH` entries and every pane clarity adapter (any copy's: a
  `bin/tmux` with `tmux-pane-clarity.tmux` beside its folder, directly or
  through symlinks), and fails with an error if nothing else is left.

### tmux-resurrect / continuum

Saves contain the logical tiled layout. Restores pause framing until the native
restore finishes. Existing resurrect hooks are chained and restored on disable.
A hook you set while always mode is on is chained from the next reload, and
kept as it is by disable. The plugin records every hook command it sets, so
its own hooks, from any copy of it (e.g. after moving the plugin), are never
chained or restored; any other hook is yours, whatever its script is called.
Hooks set by earlier versions, which kept no record, are recognized by their
script: Python running that copy's `resurrect.py` (or, if the copy was deleted
first, a `scripts/resurrect.py` that no longer exists), so upgrading into a new
location is safe too. A hook that runs anything other than Python is yours.
These hooks don't change how resurrect itself saves: overlapping saves (a manual
save during an autosave, say) are still resurrect's own behaviour.

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
`@pane_clarity_busy` set, rerun `always_mode.py enable` or `disable`. Disable
changes nothing while a window can't be returned to tiled view (the engine
binary is missing): its bindings and hooks are what let it recover.

## Tests

```sh
bash tests/borders.sh
bash tests/framed_zoom.sh
python3 tests/layout_test.py    # needs the built engine
python3 tests/setup_test.py     # options, preflight, reload/uninstall, integrations
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
