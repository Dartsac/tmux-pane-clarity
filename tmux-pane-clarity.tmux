#!/usr/bin/env bash
# Border-only focus indication, framed zoom, and optional always-framed panes.
# Mode: set -g @pane_clarity_mode 'zoom' (default) | 'always'
# Uninstall: scripts/uninstall.sh (README).
CURRENT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
SCRIPTS_DIR="$CURRENT_DIR/scripts"
source "$SCRIPTS_DIR/owned.sh"

# An option's value, or $2 when it is unset ('' set explicitly turns a feature off).
option() {
  if [ -n "$(tmux show-option -gq "$1")" ]; then
    tmux show-option -gqv "$1"
  else
    printf '%s' "$2"
  fi
}

# Plugin load output goes nowhere (TPM discards it): report on the status line,
# and keep the last problem in @pane_clarity_error.
tmux set-option -gu @pane_clarity_error
report() {
  tmux set-option -g @pane_clarity_error "$*"
  tmux display-message -d 0 "Pane clarity: $*" 2>/dev/null || true
}

# Always mode: check every requirement, and build the engine, before changing
# anything. If it is already on, a failed reload leaves it exactly as it was;
# otherwise the plugin loads in zoom mode.
always=
if [ "$(tmux show-option -gqv @pane_clarity_mode)" = always ]; then
  active="$(tmux show-option -gqv @pane_clarity_saved_bindings)"
  binary="$CURRENT_DIR/target/release/pane-clarity"
  log="$CURRENT_DIR/target/build.log"
  if ! problems="$(bash "$SCRIPTS_DIR/preflight.sh")"; then
    problem="$(printf '%s' "$problems" | paste -sd ';' - | sed 's/;/; /g')"
  elif [ ! -x "$binary" ] || [ -n "$(find "$CURRENT_DIR/src" "$CURRENT_DIR/Cargo.toml" -newer "$binary" 2>/dev/null)" ]; then
    mkdir -p "$CURRENT_DIR/target"
    tmux display-message "Pane clarity: building the layout engine..."
    if (cd "$CURRENT_DIR" && cargo build --release) >"$log" 2>&1; then
      # cargo leaves the binary as it was when nothing in it changed (e.g. only
      # Cargo.toml's metadata): mark it current, or every load would rebuild.
      touch "$binary"
    else
      problem="engine build failed: see $log"
    fi
  fi
  if [ -z "${problem:-}" ]; then
    always=1
  elif [ -n "$active" ]; then
    report "reload skipped, always mode left as it was: $problem"
    exit 0
  else
    report "always mode not enabled: $problem"
  fi
elif [ -n "$(tmux show-option -gqv @pane_clarity_saved_bindings)" ]; then
  # The mode changed away from 'always' on a running server: switch it off
  # first, and change nothing else if that fails (see always_mode.py disable).
  if ! command -v python3 >/dev/null 2>&1; then
    report "reload skipped, always mode left on: switching it off needs python3"
    exit 0
  fi
  if ! error="$(python3 "$SCRIPTS_DIR/always_mode.py" disable 2>&1 >/dev/null)"; then
    error="$(printf '%s' "$error" | tail -n 1)"
    report "reload skipped, ${error#pane clarity: }"
    exit 0
  fi
fi

# Where the plugin is installed (TPM's directory varies), for manual commands
# and the shell/editor integrations: see README.
tmux set-option -g @pane_clarity_dir "$CURRENT_DIR"
tmux set-environment -g PANE_CLARITY_TMUX "$CURRENT_DIR/bin/tmux"

# Never set window-style/window-active-style or the active border colour: the
# theme owns those. Hide the pane-status line while a pane is in framed zoom
# (the float has its own edges) and in always-framed windows.
border_style="$(option @pane_clarity_border_style 'fg=#44475a')"
if [ -n "$border_style" ]; then
  own_option pane-border-style "$border_style"
else
  restore_option pane-border-style
fi
own_option pane-border-lines single
own_option pane-border-indicators off
own_option pane-border-format ' '
own_option pane-border-status off

# Each window shows a title line between split panes, unless the user has set
# its pane-border-status since the plugin did (owned.sh). Existing windows
# first (always-framed windows manage their own), then the hook for changes.
while IFS= read -r window; do
  [ -n "$(tmux show-option -wqv -t "$window" @pane_clarity_layout)" ] && continue
  set_border_status "$window" "$(tiled_border_status "$window")"
done < <(
  # Before the first session exists (config load at server start) there are
  # no windows, and `list-windows -a` would fail with "no current target".
  [ -n "$(tmux list-sessions -F '#{session_id}' 2>/dev/null)" ] &&
    tmux list-windows -a -F '#{window_id}' | sort -u
)
# The same rule, in the changed window. A hook runs in the window of its pane,
# but when that pane was just killed, in the session's current window: so
# run-shell -C names #{hook_window} (and ## survives its expansion as #).
tmux source-file - <<'EOF'
set-hook -g window-layout-changed[9431] {
  run-shell -C "if-shell -F -t #{hook_window} '##{&&:##{==:##{@pane_clarity_layout},},##{||:##{==:##{@pane_clarity_border_status},},##{==:##{@pane_clarity_border_status},##{pane-border-status}}}}' 'set-option -wF -t #{hook_window} pane-border-status \"##{?@pane_clarity_frame_pane,off,##{?##{>:##{window_panes},1},top,off}}\" ; set-option -wF -t #{hook_window} @pane_clarity_border_status \"##{pane-border-status}\"'"
}
EOF

# Reload keys. A key bound by an earlier load but no longer wanted gets its
# original binding back, so changing or clearing an option takes effect.
reload="run-shell 'bash \"$CURRENT_DIR/tmux-pane-clarity.tmux\"'"
wanted=" prefix:$(option @pane_clarity_reload_key M-c) root:$(option @pane_clarity_root_reload_key M-l) "
for bound in $(tmux show-option -gqv @pane_clarity_bound_keys); do
  case "$wanted" in *" $bound "*) ;; *) restore_key "${bound%%:*}" "${bound#*:}" ;; esac
done
bound_keys=
for spec in $wanted; do
  table=${spec%%:*} key=${spec#*:}
  [ -n "$key" ] || continue
  save_key "$table" "$key"
  tmux bind-key -T "$table" "$key" "$reload" && bound_keys="$bound_keys $table:$key"
done
tmux set-option -g @pane_clarity_bound_keys "${bound_keys# }"

# Framed zoom (tmux 3.8+ floating panes); older versions keep native zoom.
if tmux list-commands | grep -Eq '^break-pane .*W'; then
  save_key prefix z
  tmux source-file - <<EOF
bind-key z run-shell 'bash "$SCRIPTS_DIR/framed_zoom.sh" "#{pane_id}"'
set-hook -g window-resized[9431] {
  if-shell -F '#{@pane_clarity_frame_pane}' {
    run-shell 'bash "$SCRIPTS_DIR/framed_zoom.sh" "#{hook_window}" --resize'
  }
}
EOF
fi

if [ -n "$always" ]; then
  # A window too small to frame falls back to tiled on its own (exit 1, no
  # error output); report only real failures.
  error="$(python3 "$SCRIPTS_DIR/always_mode.py" enable 2>&1 >/dev/null)" ||
    [ -z "$error" ] || report "enabling always mode failed: $(printf '%s' "$error" | tail -n 1)"
fi
