#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SOCKET="clarity-borders-$$"
unset TMUX
tmux -L "$SOCKET" -f /dev/null new-session -d -s test -x 120 -y 40
trap 'tmux -L "$SOCKET" kill-server 2>/dev/null || true' EXIT
export TMUX="$(tmux -L "$SOCKET" display-message -p '#{socket_path}'),0,0"
assert_border() {
  [ "$(tmux show-option -wAqv -t "$1" pane-border-status)" = "$2" ]
}
tmux set-option -g window-style 'fg=#abcdef,bg=#222120'
tmux set-option -g window-active-style 'bg=#123456'
tmux set-option -g pane-active-border-style 'fg=#8aff80'
tmux set-hook -g 'window-layout-changed[42]' 'set-option -g @other_hook preserved'
first="$(tmux display-message -p '#{window_id}')"
bash "$ROOT/tmux-pane-clarity.tmux"
assert_border "$first" off
tmux split-window -h -t "$first"
assert_border "$first" top
second="$(tmux new-window -d -P -F '#{window_id}')"
assert_border "$second" off
tmux split-window -v -t "$second"
assert_border "$second" top
tmux resize-pane -Z -t "$second"
assert_border "$second" top
# Reloading while zoomed must also keep the border.
bash "$ROOT/tmux-pane-clarity.tmux"
assert_border "$second" top
assert_border "$first" top
tmux resize-pane -Z -t "$second"
assert_border "$second" top
tmux kill-pane -t "$(tmux list-panes -t "$second" -F '#{pane_id}' | tail -n 1)"
assert_border "$second" off
assert_border "$first" top
bash "$ROOT/tmux-pane-clarity.tmux"
[ "$(tmux show-option -gqv window-style)" = 'fg=#abcdef,bg=#222120' ]
[ "$(tmux show-option -gqv window-active-style)" = 'bg=#123456' ]
[ "$(tmux show-option -gqv pane-active-border-style)" = 'fg=#8aff80' ]
[ "$(tmux show-option -gqv pane-border-indicators)" = off ]
[ "$(tmux show-option -gqv @other_hook)" = preserved ]
[ "$(tmux show-hooks -g window-layout-changed | grep -c '\[9431\]')" = 1 ]
printf 'PASS: borders, zoom, background windows, close, reload, other hooks; pane styles and active colour unchanged\n'
