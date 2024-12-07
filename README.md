# tmux-clear-panes

`tmux-clear-panes` is a Tmux plugin that provides two modes for managing pane borders:

- **Stealth Mode:** Pane borders are dim or invisible until more than one pane is present.
- **Dynamic Mode:** Pane borders dynamically toggle between a visible and hidden state based on the number of panes.

## Installation

### Using Tmux Plugin Manager (TPM)

1. Add the plugin to your `.tmux.conf`:

   ```tmux
   set -g @plugin 'Dartsac/tmux-clear-panes'
