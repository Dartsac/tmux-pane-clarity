# Key bindings and options the plugin takes over. The original of each is saved
# the first time, and put back when the plugin lets go of it (a reload key that
# changed, a border style turned off, scripts/uninstall.sh). Sourced by
# tmux-pane-clarity.tmux and scripts/uninstall.sh; needs only bash and tmux.
SAVED_KEYS=@pane_clarity_saved_keys        # lines: table<TAB>key<TAB>original ('' if unbound)
SAVED_OPTIONS=@pane_clarity_saved_options  # lines: name<TAB>original value
SET_OPTIONS=@pane_clarity_set_options      # lines: name<TAB>value this plugin set

# Whether a binding (as list-keys prints it) runs this plugin.
ours() {
  case "$1" in *pane-clarity*|*framed_zoom.sh*) return 0 ;; esac
  return 1
}

# The saved entry for $2 (table<TAB>name) in list $1: prints its value.
saved() {
  local entry
  while IFS= read -r entry; do
    case "$entry" in "$2"$'\t'*) printf '%s' "${entry#"$2"$'\t'}"; return 0 ;; esac
  done <<< "$(tmux show-option -gqv "$1")"
  return 1
}

save() {
  local old
  old="$(tmux show-option -gqv "$1")"
  tmux set-option -g "$1" "${old:+$old$'\n'}$2"$'\t'"$3"
}

forget() {
  local entry kept=
  while IFS= read -r entry; do
    case "$entry" in "$2"$'\t'*|'') ;; *) kept="${kept:+$kept$'\n'}$entry" ;; esac
  done <<< "$(tmux show-option -gqv "$1")"
  if [ -n "$kept" ]; then tmux set-option -g "$1" "$kept"; else tmux set-option -gu "$1"; fi
}

# Before binding table $1 key $2.
save_key() {
  local current
  current="$(tmux list-keys -T "$1" "$2" 2>/dev/null)"
  if saved "$SAVED_KEYS" "$1"$'\t'"$2" >/dev/null; then
    ours "$current" && return 0  # still the plugin's: keep the original
    # Bound or unbound since the plugin bound it: that is the original now.
    forget "$SAVED_KEYS" "$1"$'\t'"$2"
  elif ours "$current"; then
    # Bound by a version that saved nothing: tmux's own default.
    current=
    [ "$1:$2" = prefix:z ] && current="bind-key -T prefix z resize-pane -Z"
  fi
  save "$SAVED_KEYS" "$1"$'\t'"$2" "$current"
}

# Put back table $1 key $2, if it is still this plugin's binding.
restore_key() {
  local original
  if ours "$(tmux list-keys -T "$1" "$2" 2>/dev/null)"; then
    original="$(saved "$SAVED_KEYS" "$1"$'\t'"$2")" || original=
    if [ -n "$original" ]; then
      printf '%s\n' "$original" | tmux source-file -
    else
      tmux unbind-key -T "$1" "$2"
    fi
  fi
  forget "$SAVED_KEYS" "$1"$'\t'"$2"
}

# Set global option $1 to $2, saving its original the first time.
own_option() {
  saved "$SAVED_OPTIONS" "$1" >/dev/null || save "$SAVED_OPTIONS" "$1" "$(tmux show-option -gv "$1")"
  tmux set-option -g "$1" "$2"
  forget "$SET_OPTIONS" "$1"
  save "$SET_OPTIONS" "$1" "$(tmux show-option -gv "$1")"  # as tmux reads it back
}

# Put back option $1's original, unless it has been changed since the plugin set it.
restore_option() {
  local original set
  original="$(saved "$SAVED_OPTIONS" "$1")" || return 0
  set="$(saved "$SET_OPTIONS" "$1")" || set=
  [ "$(tmux show-option -gv "$1")" = "$set" ] && tmux set-option -g "$1" "$original"
  forget "$SAVED_OPTIONS" "$1"
  forget "$SET_OPTIONS" "$1"
}

# Each window's pane-border-status (the only window option the plugin sets).
# @pane_clarity_border_status holds the value it last set: a different value
# is the user's, and is left alone. The window's own value from before the
# plugin first set it is kept in @pane_clarity_original_border_status.
# The engine (src/manager.rs) and the window-layout-changed hook in
# tmux-pane-clarity.tmux follow the same rule.
BORDER_STATUS=@pane_clarity_border_status
ORIGINAL_BORDER_STATUS=@pane_clarity_original_border_status

# Set window $1's pane-border-status to $2, unless the user has set it since.
set_border_status() {
  local set
  set="$(tmux show-option -wqv -t "$1" "$BORDER_STATUS")"
  if [ -z "$set" ]; then
    local own
    own="$(tmux show-option -wqv -t "$1" pane-border-status)"
    [ -n "$own" ] && tmux set-option -w -t "$1" "$ORIGINAL_BORDER_STATUS" "$own"
  elif [ "$(tmux show-option -wAqv -t "$1" pane-border-status)" != "$set" ]; then
    return 0
  fi
  tmux set-option -w -t "$1" pane-border-status "$2" \; set-option -w -t "$1" "$BORDER_STATUS" "$2"
}

# The status window $1 shows in zoom mode: a title line between split panes.
tiled_border_status() {
  tmux display-message -p -t "$1" '#{?@pane_clarity_frame_pane,off,#{?#{>:#{window_panes},1},top,off}}'
}

# Put back window $1's own pane-border-status, if the plugin's is still there.
restore_border_status() {
  local set original
  set="$(tmux show-option -wqv -t "$1" "$BORDER_STATUS")"
  if [ -n "$set" ] && [ "$(tmux show-option -wqv -t "$1" pane-border-status)" = "$set" ]; then
    original="$(tmux show-option -wqv -t "$1" "$ORIGINAL_BORDER_STATUS")"
    if [ -n "$original" ]; then
      tmux set-option -w -t "$1" pane-border-status "$original"
    else
      tmux set-option -wu -t "$1" pane-border-status
    fi
  fi
  tmux set-option -wu -t "$1" "$BORDER_STATUS" \; set-option -wu -t "$1" "$ORIGINAL_BORDER_STATUS"
}
