# Route tmux commands typed in fish through pane-clarity's adapter, so layout
# and navigation commands respect always-framed windows. Copy to
# ~/.config/fish/functions/tmux.fish. The plugin sets PANE_CLARITY_TMUX in
# panes started after it loads; elsewhere this is plain tmux.
function tmux --wraps tmux --description 'tmux, through pane-clarity when it is loaded'
    if set -q PANE_CLARITY_TMUX; and test -x "$PANE_CLARITY_TMUX"
        "$PANE_CLARITY_TMUX" $argv
    else
        command tmux $argv
    end
end
