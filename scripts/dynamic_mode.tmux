# Border-only focus indication. Never change pane contents or backgrounds.
set-option -g pane-border-lines single
# Disable half-colouring and arrows so the entire active separator is highlighted.
set-option -g pane-border-indicators off
set-option -g pane-border-format ' '
set-option -g pane-border-style 'fg=#44475a'
# Keep the active border colour supplied by the user's theme.
set-option -g pane-border-status off

# Floating framed zoom already has its own top/bottom edges: don't add a
# pane-status line there. Native zoom retains the top line.
# Target the window that changed, including when a background pane exits.
# Double hashes defer the condition until if-shell has selected that window.
set-hook -g window-layout-changed[9431] {
    run-shell -C 'if-shell -F -t #{hook_window} "##{?@pane_clarity_frame_pane,0,##{>:##{window_panes},1}}" "set-option -w -t #{hook_window} pane-border-status top" "set-option -w -t #{hook_window} pane-border-status off"'
}
