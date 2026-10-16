#!/usr/bin/env python3
"""Keep tmux-resurrect/continuum saves in native tiled-layout form."""
import contextlib
import json
import os
import re
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile

import common

PREFIX = "@pane_clarity_saved_resurrect_"
INSTALLED = "@pane_clarity_resurrect_installed"
KINDS = ("post-save-layout", "pre-restore-all", "post-restore-all")


PLUGIN_HOOKS = "@pane_clarity_resurrect_hooks"  # every hook command it has set
SCRIPT = str(Path(__file__).resolve())
PYTHON = re.compile(r"python[0-9.]*", re.IGNORECASE)


def ours(tmux, hook):
    """Whether `hook` is one this plugin set: exactly a command recorded when
    it was set, by this copy or any other (a plugin moved or copied elsewhere
    still has the old copy's hook set). Earlier versions recorded none: theirs
    runs this very script, or another copy of this plugin's resurrect.py (an
    upgrade installed elsewhere), which is known by the option it chains with,
    or a scripts/resurrect.py deleted since, while the plugin is installed here
    (that copy was removed before the upgrade loaded; its hook can't run)."""
    if not hook:
        return False
    if hook in tmux.text("show-option", "-gqv", PLUGIN_HOOKS).split("\n"):
        return True
    try:
        tokens = shlex.split(hook)
    except ValueError:
        return False
    # The command this plugin writes: Python (sys.executable) running a script
    # with the hook's kind. Anything else is the user's, whatever it names.
    if len(tokens) != 3 or tokens[2] not in KINDS or not PYTHON.fullmatch(os.path.basename(tokens[0])):
        return False
    if os.path.realpath(tokens[1]) == SCRIPT:
        return True
    try:
        with open(tokens[1], encoding="utf-8", errors="replace") as script:
            return f'PREFIX = "{PREFIX}"' in script.read(1 << 16)
    except FileNotFoundError:
        return (tokens[1].endswith("/scripts/resurrect.py")
                and tmux.text("show-option", "-gqv", INSTALLED) == "1")
    except OSError:
        return False


def predecessor(tmux, kind):
    """The hook to chain to: never a plugin hook, which would chain back."""
    hook = tmux.text("show-option", "-gqv", PREFIX + kind)
    return "" if ours(tmux, hook) else hook


def install(tmux):
    """Chain onto existing resurrect hooks (restored by uninstall). A hook set
    since the last install replaces the saved one: it is the one to chain."""
    for kind in KINDS:
        current = tmux.text("show-option", "-gqv", "@resurrect-hook-" + kind)
        saved = current if not ours(tmux, current) else predecessor(tmux, kind)
        tmux.run("set-option", "-g" if saved else "-gu", PREFIX + kind, *([saved] if saved else []))
    tmux.run("set-option", "-g", INSTALLED, "1")
    recorded = [hook for hook in tmux.text("show-option", "-gqv", PLUGIN_HOOKS).split("\n") if hook]
    for kind in KINDS:
        hook = shlex.join([sys.executable, SCRIPT, kind])
        if hook not in recorded:
            recorded.append(hook)
            tmux.run("set-option", "-g", PLUGIN_HOOKS, "\n".join(recorded))  # before setting it
        tmux.run("set-option", "-g", "@resurrect-hook-" + kind, hook)


def uninstall(tmux):
    if tmux.text("show-option", "-gqv", INSTALLED) != "1":
        return
    for kind in KINDS:
        old = predecessor(tmux, kind)
        # Unless the user has set the hook since.
        if ours(tmux, tmux.text("show-option", "-gqv", "@resurrect-hook-" + kind)):
            tmux.run("set-option", "-g" if old else "-gu", "@resurrect-hook-" + kind, *([old] if old else []))
        tmux.run("set-option", "-gu", PREFIX + kind)
    tmux.run("set-option", "-gu", INSTALLED)
    tmux.run("set-option", "-gu", PLUGIN_HOOKS)


def run_previous(tmux, kind, args):
    """Run the hook this one replaced; returns whether it succeeded. Its
    failure must not skip this plugin's own step (e.g. leave framing paused)."""
    old = predecessor(tmux, kind)
    return not old or subprocess.run(["/bin/bash", "-c", old + ' "$@"', "pane-clarity", *args]).returncode == 0


def managed_windows(tmux):
    return [w for w in tmux.windows() if tmux.get(w, common.STATE)]


def pane_ids(node):
    """Every pane id ("I") in a version-2 layout, floating ("F") ones included."""
    if isinstance(node, dict):
        return ({node["I"]} if "I" in node else set()).union(*map(pane_ids, node.values()))
    if isinstance(node, list):
        return set().union(*map(pane_ids, node))
    return set()


def floating(node):
    """Whether a version-2 layout has floating panes (leaves with "z")."""
    if isinstance(node, dict):
        return "z" in node or any(map(floating, node.values()))
    return isinstance(node, list) and any(map(floating, node))


def rewrite_save(tmux, path):
    with tmux.lock():  # FRAMED is read-modify-written by the engine too
        _rewrite_save(tmux, path)


def _rewrite_save(tmux, path):
    """Replace saved window layouts with the logical tiled layout.

    The save was snapshotted before this hook ran and the logical layout is
    read now, so a pane split or closed in between would pair the saved pane
    records with a layout for different panes. Only a logical layout with
    exactly the saved layout's panes replaces it. Otherwise the save gets
    tmux's `tiled` preset, valid for any pane count: the snapshot's own layout
    is floating frames, and enabling refuses a window restored with floats.

    A window that closed in between has no live state at all. Its saved
    floats were frames if the plugin framed every one of its panes; any other
    floating layout is the user's and is kept as saved. A pane still alive is
    the plugin's only while its window is managed (it may have been moved
    into one of the user's windows since it was framed); only a pane that is
    gone needs the record of framed panes (FRAMED)."""
    framed = set(tmux.text("show-option", "-gqv", common.FRAMED).split())
    live = dict(row.split("\t") for row in
                tmux.text("list-panes", "-a", "-F", "#{pane_id}\t#{?#{@pane_clarity_layout},1,0}").splitlines())
    owned = lambda pane: live[pane] == "1" if pane in live else pane in framed
    in_save = set()
    layouts = {}
    for row in tmux.text("list-windows", "-a", "-F", "#{window_id}\t#{session_name}\t#{window_index}").splitlines():
        window, session, index = row.split("\t")
        raw = tmux.get(window, common.STATE)
        if raw:
            layouts[(session, index)] = {"V": 2, "L": json.loads(raw)["root"]}
    lines = []
    for line in path.read_text().splitlines(keepends=True):
        fields = line.rstrip("\n").split("\t")
        if fields[0] == "window" and len(fields) > 6:
            layout = layouts.get((fields[1], fields[2]))
            try:
                snapshot = json.loads(fields[6])
            except ValueError:
                snapshot = None
            saved = pane_ids(snapshot)
            in_save |= saved
            if layout is not None:
                fields[6] = json.dumps(layout, separators=(",", ":")) if saved == pane_ids(layout) else "tiled"
            elif saved and all(map(owned, saved)) and floating(snapshot):
                fields[6] = "tiled"
            line = "\t".join(fields) + "\n"
        lines.append(line)
    # Forget panes now in unmanaged windows, and gone panes in no save; keep
    # the rest for the next save.
    keep = {pane for pane in framed if live.get(pane) == "1" or (pane not in live and pane in in_save)}
    if keep and keep != framed:
        tmux.run("set-option", "-g", common.FRAMED, " ".join(sorted(keep)))
    elif not keep and framed:
        tmux.run("set-option", "-gu", common.FRAMED)
    # Replace atomically so an interrupted hook cannot truncate a save.
    fd, temporary = tempfile.mkstemp(prefix=".clarity-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as out:
            out.writelines(lines)
        os.chmod(temporary, path.stat().st_mode & 0o777)
        os.replace(temporary, path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temporary)


def main(argv=None):
    kind, *args = sys.argv[1:] if argv is None else argv
    tmux = common.Tmux()
    if kind == "post-save-layout":
        ok = run_previous(tmux, kind, args)
        rewrite_save(tmux, Path(args[0]))
    elif kind == "pre-restore-all":
        # Pause hooks so restored panes aren't framed mid-restore.
        tmux.run("set-option", "-g", "@pane_clarity_paused", "1")
        ok = not any([common.run_engine(["disable", window]) for window in managed_windows(tmux)])
        ok &= run_previous(tmux, kind, args)
    elif kind == "post-restore-all":
        ok = run_previous(tmux, kind, args)
        tmux.run("set-option", "-gu", "@pane_clarity_paused")
        ok &= not any([common.run_engine(["enable", window]) for window in tmux.windows()])
    else:
        return 2
    return int(not ok)


if __name__ == "__main__":
    sys.exit(main())
