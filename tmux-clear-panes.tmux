#!/usr/bin/env bash

CURRENT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
SCRIPTS_DIR="$CURRENT_DIR/scripts"
BINDINGS_DIR="$CURRENT_DIR/bindings"

# Source helper variables
source "$SCRIPTS_DIR/variables.sh"

# Load the selected mode
selected_mode="$(tmux show-option -gqv "$TMUX_CLEAR_PANES_MODE_OPTION" || echo "$DEFAULT_MODE")"

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

# Bind key for manually switching modes (placeholder for future modes)
tmux bind-key M-c run-shell "$BINDINGS_DIR/apply_mode.sh"
