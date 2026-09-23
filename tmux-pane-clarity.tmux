#!/usr/bin/env bash

CURRENT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
SCRIPTS_DIR="$CURRENT_DIR/scripts"

# Source helper variables
source "$SCRIPTS_DIR/variables.sh"

# Load the selected mode
selected_mode="$(tmux show-option -gqv "$TMUX_CLEAR_PANES_MODE_OPTION")"
selected_mode="${selected_mode:-$DEFAULT_MODE}"

# Retire only the original plugin's global border hooks, not other plugins' hooks.
for hook in after-split-window session-window-changed client-session-changed after-kill-pane; do
  legacy="$(tmux show-hooks -g "$hook" 2>/dev/null)"
  case "$legacy" in
    *"$hook[0] run-shell"*'tmux list-panes | wc -l'*'tmux set-option -g pane-border-status'*)
      tmux set-hook -gu "$hook[0]"
      ;;
  esac
done

case "$selected_mode" in
  dynamic)
    tmux source-file "$SCRIPTS_DIR/dynamic_mode.tmux"
    ;;
  *)
    tmux display-message "Invalid or unsupported mode selected: $selected_mode. Defaulting to dynamic mode."
    tmux set-option -g "$TMUX_CLEAR_PANES_MODE_OPTION" "dynamic"
    tmux source-file "$SCRIPTS_DIR/dynamic_mode.tmux"
    ;;
esac

# Initialize each existing window without changing unrelated windows globally.
while IFS= read -r window; do
  tmux if-shell -F -t "$window" '#{?@pane_clarity_frame_pane,0,#{>:#{window_panes},1}}' \
    "set-option -w -t $window pane-border-status top" \
    "set-option -w -t $window pane-border-status off"
done < <(tmux list-windows -a -F '#{window_id}' | sort -u)

# Full four-sided frame: toggle the existing pane into/out of a full-size float.
# Older tmux versions retain their existing zoom binding.
if tmux list-commands | grep -Eq '^break-pane .*W'; then
  tmux bind-key z run-shell "bash \"$SCRIPTS_DIR/framed_zoom.sh\" '#{pane_id}'"
  tmux set-hook -g 'window-resized[9431]' \
    "run-shell 'bash \"$SCRIPTS_DIR/framed_zoom.sh\" \"#{hook_window}\" --resize'"
fi

# Retain the existing refresh shortcuts.
tmux bind-key M-c run-shell "bash \"$CURRENT_DIR/tmux-pane-clarity.tmux\""
tmux bind-key -n M-l run-shell "bash \"$CURRENT_DIR/tmux-pane-clarity.tmux\""
