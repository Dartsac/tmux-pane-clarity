#!/usr/bin/env bash
# Border-only focus indication and framed zoom.
CURRENT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
SCRIPTS_DIR="$CURRENT_DIR/scripts"

# Never set window-style/window-active-style or the active border colour: the
# theme owns those. Hide the pane-status line while a pane is in framed zoom
# (the float has its own edges) and in always-framed windows.
tmux source-file - <<EOF
set-option -g pane-border-lines single
set-option -g pane-border-indicators off
set-option -g pane-border-format ' '
set-option -g pane-border-style 'fg=#44475a'
set-option -g pane-border-status off
set-hook -g window-layout-changed[9431] {
  if-shell -F '#{==:#{@pane_clarity_layout},}' {
    run-shell -C 'if-shell -F -t #{hook_window} "##{?@pane_clarity_frame_pane,0,##{>:##{window_panes},1}}" "set-option -w -t #{hook_window} pane-border-status top" "set-option -w -t #{hook_window} pane-border-status off"'
  }
}
bind-key M-c run-shell 'bash "$CURRENT_DIR/tmux-pane-clarity.tmux"'
bind-key -n M-l run-shell 'bash "$CURRENT_DIR/tmux-pane-clarity.tmux"'
EOF

# Initialize existing windows (always-framed windows manage their own borders).
while IFS= read -r window; do
  [ -n "$(tmux show-option -wqv -t "$window" @pane_clarity_layout)" ] && continue
  tmux if-shell -F -t "$window" '#{?@pane_clarity_frame_pane,0,#{>:#{window_panes},1}}' \
    "set-option -w -t $window pane-border-status top" \
    "set-option -w -t $window pane-border-status off"
done < <(
  # Before the first session exists (config load at server start) there are
  # no windows, and `list-windows -a` would fail with "no current target".
  [ -n "$(tmux list-sessions -F '#{session_id}' 2>/dev/null)" ] &&
    tmux list-windows -a -F '#{window_id}' | sort -u
)

# Framed zoom (tmux 3.8+ floating panes); older versions keep native zoom.
if tmux list-commands | grep -Eq '^break-pane .*W'; then
  tmux source-file - <<EOF
bind-key z run-shell 'bash "$SCRIPTS_DIR/framed_zoom.sh" "#{pane_id}"'
set-hook -g window-resized[9431] {
  if-shell -F '#{@pane_clarity_frame_pane}' {
    run-shell 'bash "$SCRIPTS_DIR/framed_zoom.sh" "#{hook_window}" --resize'
  }
}
EOF
fi
