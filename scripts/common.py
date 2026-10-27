"""Shared by the installer (always_mode.py), the tmux-resurrect hooks and the
tests: a small tmux wrapper, the layout engine binary and the names it uses.
The engine itself is Rust (src/), built to target/release/pane-clarity."""
import contextlib
import fcntl
import os
import subprocess

HERE = os.path.dirname(os.path.realpath(__file__))
ROOT = os.path.dirname(HERE)
BINARY = os.path.join(ROOT, "target", "release", "pane-clarity")
STATE = "@pane_clarity_layout"
BUSY = "@pane_clarity_busy"
FRAMED = "@pane_clarity_framed"  # see FRAMED in src/main.rs
DIRECTIONS = ("-L", "-R", "-U", "-D")
# Commands the engine manages. Everything else always goes straight to tmux.
ALIASES = {"selectp": "select-pane", "splitw": "split-window", "resizep": "resize-pane",
           "selectl": "select-layout", "nextl": "next-layout", "lastp": "last-pane",
           "rotatew": "rotate-window", "swapp": "swap-pane", "killp": "kill-pane"}
MANAGED = set(ALIASES.values())
PANE_FORMAT = ("#{pane_id}\t#{pane_index}\t#{pane_floating_flag}\t#{pane_active}"
               "\t#{pane_left}\t#{pane_top}\t#{pane_width}\t#{pane_height}")


def _is_adapter(path):
    """Whether path is a pane clarity bin/tmux adapter: this copy's or another's
    (every copy has tmux-pane-clarity.tmux beside bin/). As the real tmux, one
    would exec itself (or another) forever, or start a second engine that
    waits on this one's lock."""
    plugin = os.path.dirname(os.path.dirname(os.path.realpath(path)))
    return os.path.isfile(os.path.join(plugin, "tmux-pane-clarity.tmux"))


def _real_tmux():
    """$PANE_CLARITY_REAL_TMUX, or the first tmux on PATH that is not an adapter."""
    configured = os.environ.get("PANE_CLARITY_REAL_TMUX")
    if configured and not _is_adapter(configured):
        return configured
    for directory in os.environ.get("PATH", "").split(os.pathsep):
        # Relative entries (e.g. "bin") depend on the cwd and can name the adapter.
        if not os.path.isabs(directory):
            continue
        candidate = os.path.join(directory, "tmux")
        if (os.path.isfile(candidate) and os.access(candidate, os.X_OK)
                and not _is_adapter(candidate)):
            return candidate
    raise RuntimeError("Cannot find the real tmux binary: only pane clarity adapters on PATH.")


REAL_TMUX = _real_tmux()


class TmuxError(RuntimeError):
    pass


def built():
    return os.access(BINARY, os.X_OK)


def run_engine(args, flags=()):
    """Run one engine action now; returns its exit status."""
    if not built():
        raise TmuxError(f"Always-framed mode needs the Rust engine: run `cargo build --release` in {ROOT}")
    return subprocess.run([BINARY, *flags, *args]).returncode


class Tmux:
    def __init__(self, flags=()):
        self.flags = list(flags)

    def run(self, *args, check=True, input=None):
        result = subprocess.run([REAL_TMUX, *self.flags, *map(str, args)],
                                input=input, text=True, capture_output=True)
        if check and result.returncode:
            raise TmuxError(result.stderr.strip() or result.stdout.strip() or str(args))
        return result

    def text(self, *args):
        return self.run(*args).stdout.rstrip("\n")

    def windows(self):
        """Every window id, sorted and unique. `list-windows -a` fails with "no
        current target" while the server has no sessions (e.g. while the config
        loads at server start), which means: no windows."""
        if not self.text("list-sessions", "-F", "#{session_id}"):
            return []
        return sorted(set(self.text("list-windows", "-a", "-F", "#{window_id}").split()))

    @contextlib.contextmanager
    def lock(self):
        """The engine's per-server lock (server_lock in src/main.rs)."""
        socket = os.environ.get("TMUX", "").split(",")[0] if not self.flags else ""
        path = (socket or self.text("display-message", "-p", "#{socket_path}")) + ".clarity-lock"
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)

    def fmt(self, target, fmt):
        return self.text("display-message", "-p", "-t", target, fmt)

    def get(self, window, name):
        return self.text("show-option", "-wqv", "-t", window, name)

    def set(self, window, name, value):
        self.run("set-option", "-w", "-t", window, name, value)

    def panes(self, window):
        panes = {}
        for line in self.text("list-panes", "-t", window, "-F", PANE_FORMAT).splitlines():
            pane, index, floating, active, *geometry = line.split("\t")
            x, y, w, h = map(int, geometry)
            panes[pane] = {"index": int(index), "floating": floating == "1", "active": active == "1",
                           "x": x, "y": y, "w": w, "h": h}
        return panes
