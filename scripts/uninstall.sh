#!/usr/bin/env bash
# Remove pane clarity from the current tmux server: switch always mode off,
# return framed-zoom panes to their splits, remove its hooks and settings, and
# put back the bindings and options it replaced. Then remove the plugin's line
# from tmux.conf (and the plugin: prefix + Alt-u with TPM).
#   bash "$(tmux show-option -gqv @pane_clarity_dir)/scripts/uninstall.sh"
set -u
ROOT="$( cd "$( dirname "${BASH_SOURCE[0]}" )/.." && pwd )"
source "$ROOT/scripts/owned.sh"

windows() {
  # No sessions: no windows (`list-windows -a` would fail).
  [ -n "$(tmux list-sessions -F '#{session_id}' 2>/dev/null)" ] &&
    tmux list-windows -a -F '#{window_id}' | sort -u
}

# Panes first: every window back to tiled panes, or nothing else is removed,
# so the plugin's own controls are still there and running this again
# finishes the job. Always mode restores the bindings and hooks it adapted.
if [ -n "$(tmux show-option -gqv @pane_clarity_saved_bindings)" ]; then
  if ! command -v python3 >/dev/null 2>&1; then
    echo "pane clarity: always mode is on and switching it off needs python3; nothing was changed" >&2
    exit 1
  fi
  if ! python3 "$ROOT/scripts/always_mode.py" disable; then
    echo "pane clarity: not removed; fix the above and run this again" >&2
    exit 1
  fi
fi
for window in $(windows); do
  # Return a framed-zoom pane from its own window only: one that has since
  # moved elsewhere is no longer framed, and nothing new is opened.
  if ! bash "$ROOT/scripts/framed_zoom.sh" "$window" --return; then
    echo "pane clarity: not removed: the framed pane in window $window did not return to its split" >&2
    exit 1
  fi
done

tmux set-hook -gu 'window-layout-changed[9431]'
tmux set-hook -gu 'window-resized[9431]'
for window in $(windows); do
  restore_border_status "$window"
done
for bound in $(tmux show-option -gqv @pane_clarity_bound_keys); do
  restore_key "${bound%%:*}" "${bound#*:}"
done
restore_key prefix z
for name in pane-border-style pane-border-lines pane-border-indicators pane-border-format pane-border-status; do
  restore_option "$name"
done
for name in @pane_clarity_dir @pane_clarity_bound_keys @pane_clarity_error "$SAVED_KEYS" "$SAVED_OPTIONS" "$SET_OPTIONS"; do
  tmux set-option -gu "$name"
done
tmux set-environment -gu PANE_CLARITY_TMUX
echo "pane clarity removed from this tmux server; remove its line from tmux.conf"
