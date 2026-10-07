#!/usr/bin/env bash
# Check everything always mode needs, against the current tmux server. Prints
# one line per missing requirement and exits 1 if there is any.
#   bash "$(tmux show-option -gqv @pane_clarity_dir)/scripts/preflight.sh"
ROOT="$( cd "$( dirname "${BASH_SOURCE[0]}" )/.." && pwd )"
problems=0
problem() {
  echo "$*"
  problems=1
}
# Whether version $1 is at least $2 (dotted numbers).
at_least() {
  [ "$(printf '%s\n%s\n' "$2" "$1" | sort -t. -k1,1n -k2,2n -k3,3n | head -n 1)" = "$2" ]
}

# Floating panes and version-2 JSON layouts arrived together in tmux 3.8.
if ! tmux list-commands 2>/dev/null | grep -Eq '^break-pane .*W'; then
  problem "tmux 3.8+ is required (found: $(tmux -V 2>/dev/null || echo 'no tmux server'))"
fi

if ! command -v python3 >/dev/null 2>&1; then
  problem "Python 3.8+ is required (python3 not found)"
elif ! python3 -c 'import sys; sys.exit(sys.version_info < (3, 8))' 2>/dev/null; then
  problem "Python 3.8+ is required (found: $(python3 --version 2>&1))"
fi

# The engine is built from source on first use, and again when it changes.
binary="$ROOT/target/release/pane-clarity"
if [ ! -x "$binary" ] || [ -n "$(find "$ROOT/src" "$ROOT/Cargo.toml" -newer "$binary" 2>/dev/null)" ]; then
  minimum="$(sed -n 's/^rust-version = "\([^"]*\)".*/\1/p' "$ROOT/Cargo.toml")"
  if ! command -v cargo >/dev/null 2>&1; then
    problem "Rust $minimum+ (cargo) is required to build the engine: https://rustup.rs"
  else
    found="$(rustc --version 2>/dev/null | awk '{print $2}')"
    if [ -z "$found" ] || ! at_least "${found%%-*}" "$minimum"; then
      problem "Rust $minimum+ is required to build the engine (found: ${found:-no rustc})"
    fi
  fi
fi
exit "$problems"
