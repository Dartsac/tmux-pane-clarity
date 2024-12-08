#!/usr/bin/env bash

CURRENT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
SCRIPTS_DIR="$CURRENT_DIR/../scripts"
source "$SCRIPTS_DIR/variables.sh"

current_mode="$(tmux show-option -gqv "$TMUX_CLEAR_PANES_MODE_OPTION" || echo "$DEFAULT_MODE")"

# Toggle modes (placeholder for future expansion)
if [ "$current_mode" == "dynamic" ]; then
  new_mode="dynamic"  # Only dynamic mode for now
else
  new_mode="dynamic"
fi

tmux set-option -g "$TMUX_CLEAR_PANES_MODE_OPTION" "$new_mode"
tmux source-file "$SCRIPTS_DIR/${new_mode}_mode.tmux"

tmux display-message "tmux-clear-panes mode: $new_mode"
