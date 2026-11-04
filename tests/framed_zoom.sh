#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SOCKET="clarity-frame-test-$$"
unset TMUX
tmux -L "$SOCKET" -f /dev/null new-session -d -s test -x 100 -y 32
SOCKET_PATH="$(tmux -L "$SOCKET" display-message -p '#{socket_path}')"
# kill-server leaves the socket file behind, and the engine its lock.
trap 'tmux -L "$SOCKET" kill-server 2>/dev/null || true; rm -f "$SOCKET_PATH" "$SOCKET_PATH.clarity-lock"' EXIT
export TMUX="$SOCKET_PATH,0,0"
bash "$ROOT/tmux-pane-clarity.tmux"
tmux list-keys -T prefix z | grep -q framed_zoom.sh

# Exercise the same run-shell path used by the prefix-z binding.
toggle() { tmux run-shell "bash \"$ROOT/scripts/framed_zoom.sh\" '$1'"; }
geometry() { tmux list-panes -F '#{pane_id}:#{pane_left},#{pane_top},#{pane_width},#{pane_height}' | LC_ALL=C sort; }
assert_float() {
  [ "$(tmux display-message -p -t "$1" '#{pane_floating_flag}')" = "$2" ]
}
# Single pane: actual frame reserves one character on every side.
pane="$(tmux display-message -p '#{pane_id}')"
pid="$(tmux display-message -p '#{pane_pid}')"
toggle "$pane"
assert_float "$pane" 1
[ "$(tmux display-message -p -t "$pane" '#{pane_left},#{pane_top},#{pane_width},#{pane_height}')" = '1,1,98,30' ]
toggle "$pane"
assert_float "$pane" 0
# Multi-pane exact layout and process identity survive both toggles and reload.
tmux split-window -h
tmux split-window -v
layout="$(geometry)"
tmux set-option -g window-style 'bg=#123456'
tmux set-option -g window-active-style 'bg=#234567'
toggle "$pane"
assert_float "$pane" 1
bash "$ROOT/tmux-pane-clarity.tmux"
toggle "$pane"
assert_float "$pane" 0
[ "$(geometry)" = "$layout" ]
[ "$(tmux display-message -p -t "$pane" '#{pane_pid}')" = "$pid" ]
[ "$(tmux show-option -gqv window-style)" = 'bg=#123456' ]
[ "$(tmux show-option -gqv window-active-style)" = 'bg=#234567' ]
# Existing native zoom is converted to framed zoom.
tmux resize-pane -Z -t "$pane"
toggle "$pane"
assert_float "$pane" 1
[ "$(tmux display-message -p '#{window_zoomed_flag}')" = 0 ]
toggle "$pane"
[ "$(geometry)" = "$layout" ]
# Terminal resizing does not prevent safe return to tiled panes.
toggle "$pane"
tmux resize-window -x 120 -y 40
# The resize hook runs a shell asynchronously relative to this test client.
for ((i=0; i<50; i++)); do
  [ "$(tmux display-message -p -t "$pane" '#{pane_width},#{pane_height}')" = '118,38' ] && break
  sleep 0.05
done
[ "$(tmux display-message -p -t "$pane" '#{pane_left},#{pane_top},#{pane_width},#{pane_height}')" = '1,1,118,38' ]
toggle "$pane"
assert_float "$pane" 0
# Closing a different pane must not resurrect an obsolete saved layout.
toggle "$pane"
other="$(tmux list-panes -F '#{pane_id}' | grep -Fxv "$pane" | head -n 1)"
tmux kill-pane -t "$other"
toggle "$pane"
assert_float "$pane" 0
[ "$(tmux list-panes | wc -l | tr -d ' ')" = 2 ]
# Closing the framed pane clears stale state without moving the survivor.
toggle "$pane"
tmux kill-pane -t "$pane"
survivor="$(tmux display-message -p '#{pane_id}')"
toggle "$survivor"
assert_float "$survivor" 0
[ -z "$(tmux show-option -wqv @pane_clarity_frame_pane)" ]
# A new toggle works normally after stale-state cleanup.
toggle "$survivor"
assert_float "$survivor" 1
toggle "$survivor"
assert_float "$survivor" 0
printf 'PASS: full frame, single/multiple panes, exact layout, process identity, unchanged backgrounds, reload, native zoom, resizing and pane closures\n'
