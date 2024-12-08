# tmux-pane-clarity

`tmux-pane-clarity` is a Tmux plugin that customizes the appearance of pane borders dynamically. It currently supports:

- **Dynamic Mode**: Pane borders toggle visibility based on the number of panes.

Future updates will include additional modes, such as **Stealth Mode**.

## Features
- Toggle pane borders dynamically.
- Seamlessly integrates into your Tmux workflow.
- Designed for future expansion.

## Installation

### Using TPM (Recommended)
1. Add the plugin to your `.tmux.conf`:
   ```tmux
   set -g @plugin 'Dartsac/tmux-pane-clarity'
