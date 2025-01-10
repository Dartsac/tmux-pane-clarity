# function to dynamically toggle borders
bind-key -n M-l run-shell '
  if [ "$(tmux list-panes | wc -l)" -gt 1 ]; then
    tmux set-option -g pane-border-status top
    tmux set-option -g pane-border-format "#[bold]#[default]"
  else
    tmux set-option -g pane-border-status off
  fi
'

# hook for after-split-window
set-hook -g after-split-window "run-shell '
  if [ \"\$(tmux list-panes | wc -l)\" -gt 1 ]; then
    tmux set-option -g pane-border-status top
    tmux set-option -g pane-border-format \"#[bold]#[default]\"
  fi
'"

# hook for when the active window in a session changes
set-hook -g session-window-changed "run-shell '
  if [ \"\$(tmux list-panes | wc -l)\" -gt 1 ]; then
    tmux set-option -g pane-border-status top
    tmux set-option -g pane-border-format \"#[bold]#[default]\"
  else
    tmux set-option -g pane-border-status off
  fi
'"

# hook for client-session-changed 
set-hook -g client-session-changed "run-shell '
  if [ \"\$(tmux list-panes | wc -l)\" -gt 1 ]; then
    tmux set-option -g pane-border-status top
    tmux set-option -g pane-border-format \"#[bold]#[default]\"
  else
    tmux set-option -g pane-border-status off
  fi
'"

# hook for after-kill-pane
set-hook -g after-kill-pane "run-shell '
  if [ \"\$(tmux list-panes | wc -l)\" -eq 1 ]; then
    tmux set-option -g pane-border-status off
  fi
'"
