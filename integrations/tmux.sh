# Route tmux commands typed in bash or zsh through pane-clarity's adapter, so
# layout and navigation commands respect always-framed windows. Source from
# ~/.bashrc or ~/.zshrc. The plugin sets PANE_CLARITY_TMUX in panes started
# after it loads; elsewhere this is plain tmux.
tmux() {
  if [ -n "${PANE_CLARITY_TMUX:-}" ] && [ -x "$PANE_CLARITY_TMUX" ]; then
    "$PANE_CLARITY_TMUX" "$@"
  else
    command tmux "$@"
  fi
}
