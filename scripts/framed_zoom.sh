#!/usr/bin/env bash
# tmux 3.8+: float the existing pane, never replace its running process.
set -eu
pane="${1:?Expected pane ID}"
window="$(tmux display-message -p -t "$pane" '#{window_id}')"
frame="$(tmux show-option -wqv -t "$window" @pane_clarity_frame_pane)"

# Called by the resize hook: keep the frame fitted to the current window.
if [ "${2:-}" = --resize ]; then
  if [ -n "$frame" ] &&
      tmux list-panes -t "$window" -F '#{pane_id}' | grep -Fxq -- "$frame" &&
      [ "$(tmux display-message -p -t "$frame" '#{pane_floating_flag}')" = 1 ]; then
    tmux resize-pane -t "$frame" -x 100% -y 100%
    tmux move-pane -t "$frame" -X 0 -Y 0
  fi
  exit 0
fi

clear_state() {
  tmux set-option -wu -t "$window" @pane_clarity_frame_pane
  tmux set-option -wu -t "$window" @pane_clarity_frame_layout
  tmux set-option -wu -t "$window" @pane_clarity_frame_members
  tmux if-shell -F -t "$window" '#{>:#{window_panes},1}' \
    "set-option -w -t $window pane-border-status top" \
    "set-option -w -t $window pane-border-status off"
}

members() {
  tmux list-panes -t "$window" -F '#{pane_id}' | LC_ALL=C sort
}

if [ -n "$frame" ]; then
  # A pane may have exited, moved, or been manually returned to the layout.
  if ! tmux list-panes -t "$window" -F '#{pane_id}' | grep -Fxq -- "$frame"; then
    clear_state
    tmux display-message 'Framed pane closed or moved; cleared framed-zoom state.'
    exit 0
  fi
  layout="$(tmux show-option -wqv -t "$window" @pane_clarity_frame_layout)"
  saved_members="$(tmux show-option -wqv -t "$window" @pane_clarity_frame_members)"
  # Native zoom temporarily hides a pane's floating state.
  if [ "$(tmux display-message -p -t "$window" '#{window_zoomed_flag}')" = 1 ]; then
    tmux resize-pane -Z -t "$frame"
  fi
  if [ "$(tmux display-message -p -t "$frame" '#{pane_floating_flag}')" != 1 ]; then
    clear_state
    tmux display-message 'Pane already tiled; cleared framed-zoom state.'
    exit 0
  fi
  tmux join-pane -s "$frame"
  clear_state
  if [ "$(members)" = "$saved_members" ]; then
    # tmux scales the saved layout if the terminal was resized.
    if ! tmux select-layout -t "$window" "$layout" >/dev/null; then
      tmux display-message 'Pane returned; original split sizes could not be restored.'
    fi
  else
    tmux display-message 'Pane returned; splits changed while framed, so kept the current layout.'
  fi
  tmux select-pane -t "$frame"
  exit 0
fi

# Do not repurpose an unrelated floating pane or its return location.
if [ "$(tmux display-message -p -t "$pane" '#{pane_floating_flag}')" = 1 ]; then
  tmux display-message 'Framed zoom is available for tiled panes only.'
  exit 0
fi
if [ "$(tmux display-message -p -t "$window" '#{window_zoomed_flag}')" = 1 ]; then
  tmux resize-pane -Z -t "$pane"
fi
layout="$(tmux display-message -p -t "$window" '#{window_layout}')"
saved_members="$(members)"
tmux set-option -w -t "$window" @pane_clarity_frame_layout "$layout"
tmux set-option -w -t "$window" @pane_clarity_frame_members "$saved_members"
tmux set-option -w -t "$window" @pane_clarity_frame_pane "$pane"
if ! tmux break-pane -W -s "$pane" -x 100% -y 100% -X 0 -Y 0; then
  clear_state
  tmux display-message 'Could not open framed zoom; requires tmux with floating-pane support (3.8+).'
  exit 1
fi
