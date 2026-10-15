#!/usr/bin/env python3
"""Install reversible key adapters; enable/disable always-framed windows."""
import json
import os
import shlex
import sys

import common
import resurrect

INDEX = "9432"
SAVED = "@pane_clarity_saved_bindings"
# hook: (engine action, target, extra args); expanded by run-shell.
# Notification hooks set #{hook_window}; after-<command> hooks only set the
# command's target context (#{window_id}).
WINDOW, AFTER = ("#{hook_window}",), ("#{window_id}",)
FIT = ("#{session_id}", "#{client_width}", "#{client_height}", "#{status}")
HOOKS = {"window-resized": ("hook", *WINDOW), "pane-exited": ("hook", *WINDOW),
         "after-kill-pane": ("hook", *AFTER), "after-split-window": ("hook", *AFTER),
         "window-pane-changed": ("focus", *WINDOW),
         "after-new-window": ("enable", "#{window_id}"), "after-new-session": ("enable", "#{window_id}"),
         "client-resized": ("fit", *FIT), "client-session-changed": ("fit", *FIT)}
TABLES = ("root", "prefix", "copy-mode-vi", "copy-mode")
MOUSE_KEYS = ("MouseDragEnd1Pane", "MouseDragEnd1Border")
MOUSE_TO_PANE = "#{||:#{pane_in_mode},#{mouse_any_flag}}"


def invoke(action, *args):
    """tmux command tokens that run one engine action (run-shell expands the
    #{...} targets when the binding or hook runs)."""
    return ["run-shell", shlex.join([common.BINARY, action, *args])]


def recognize(command):
    """(action, args) if `command` runs this plugin's engine (any version:
    the Rust binary, or the earlier Python layout.py/main.py), else None."""
    if not command or command[0] not in ("run-shell", "run") or len(command) < 2:
        return None
    try:
        tokens = shlex.split(command[-1])
    except ValueError:
        return None
    for i, token in enumerate(tokens):
        if (os.path.basename(token) in ("layout.py", "main.py", "pane-clarity") and "pane-clarity" in token
                and len(tokens) > i + 2):
            return tokens[i + 1], tokens[i + 2:]
    return None


def split_commands(tokens):
    commands = [[]]
    for token in tokens:
        if token == ";":
            commands.append([])
        else:
            commands[-1].append(token)
    return commands


def render(commands):
    """Inverse of split_commands, in tmux config syntax (`\\;` separates)."""
    return " \\; ".join(shlex.join(command) for command in commands)


def adapt(tokens):
    """Route managed layout commands through the engine. Returns new tokens."""
    return [token for command in split_commands(tokens)
            for token in (";", *adapt_command(command))][1:]


def adapt_command(command):
    if not command:
        return command
    name, *args = command
    name = common.ALIASES.get(name, name)
    if name == "run-shell" and any("framed_zoom.sh" in arg for arg in args):
        return invoke("command", "#{pane_id}", "resize-pane", "-Z")
    if (known := recognize(command)) is not None:
        # Written by this plugin: re-render (upgrades older versions).
        action, rest = known
        return invoke(action, *rest)
    if name in ("if-shell", "if") and len(args) >= 3:
        # Preserve the configured Vim/fzf process test and send-keys branch.
        branches = [render(split_commands(adapt(shlex.split(branch)))) for branch in args[-2:]]
        return [command[0], *args[:-2], *branches]
    if name == "resize-pane" and "-M" in args:
        # Native mouse dragging needs the real event; reconciled on release.
        return command
    if name in common.MANAGED and (name != "select-pane" or any(a in args for a in (*common.DIRECTIONS, "-l"))):
        return invoke("command", "#{pane_id}", name, *args)
    return command


def bind(tmux, prefix, tokens):
    tmux.run("source-file", "-", input=shlex.join(["bind-key", *prefix]) + " " + render(split_commands(tokens)) + "\n")


def current_binding(tmux, table, key):
    return tmux.run("list-keys", "-T", table, key, check=False).stdout.strip()


def adapted_here(item, line):
    """Whether `line` (a key's binding now) is still the one enable gave it.
    Entries saved by earlier versions don't record that: any binding that
    runs the engine is."""
    if "adapted" in item:
        return line == item["adapted"]
    return "pane-clarity" in line


def install_bindings(tmux):
    raw = tmux.text("show-option", "-gqv", SAVED)
    saved = {(item["table"], item["key"]): item for item in (json.loads(raw) if raw else [])}

    def store():
        tmux.run("set-option", "-g", SAVED, json.dumps(list(saved.values())))

    def backup(table, key, line):
        item = saved.get((table, key))
        if item and adapted_here(item, line):
            return  # keep the original, not an earlier adapted version
        # New, or the user has bound it again since: that is the original now.
        saved[(table, key)] = {"table": table, "key": key, "line": line}
        store()  # before mutating

    def adapted(table, key):
        saved[(table, key)]["adapted"] = current_binding(tmux, table, key)
        store()

    for table in TABLES:
        for line in tmux.text("list-keys", "-T", table).splitlines():
            try:
                tokens = shlex.split(line)
            except ValueError:
                continue
            index = tokens.index("-T")
            prefix, original = tokens[1:index + 3], tokens[index + 3:]
            # Brace blocks can't be rewritten token-wise; leave them native.
            if "{" in original or "}" in original:
                continue
            try:
                changed = adapt(original)
            except ValueError:
                continue
            if changed == original:
                continue  # unmanaged, or already adapted
            backup(table, prefix[-1], line)
            bind(tmux, prefix, changed)
            # Verify tmux parsed the rewrite as intended; otherwise put the original back.
            if shlex.split(tmux.text("list-keys", "-T", table, prefix[-1])) != ["bind-key", *prefix, *changed]:
                tmux.run("source-file", "-", input=line + "\n")
                if saved[(table, prefix[-1])]["line"] == line:  # newly backed up
                    del saved[(table, prefix[-1])]
                    store()
            else:
                adapted(table, prefix[-1])
    for key in MOUSE_KEYS:
        backup("root", key, current_binding(tmux, "root", key))
        resize = invoke("mouse-resize", "#{mouse_pane}")
        if key == "MouseDragEnd1Pane":
            # A drag inside a pane belongs to the program when it tracks the
            # mouse (or to the pane's mode), exactly as in tmux's default
            # MouseDrag1Pane. Unbound, tmux forwards the release; binding it
            # unconditionally swallowed the button-up, so programs with their
            # own selection (e.g. pi) never finished it and never copied.
            resize = ["if-shell", "-F", MOUSE_TO_PANE, "send-keys -M", render([resize])]
        tmux.run("bind-key", "-T", "root", key, *resize)
        adapted("root", key)


def install(tmux):
    if not common.built():
        raise common.TmuxError(f"Always-framed mode needs the Rust engine: run `cargo build --release` in {common.ROOT}")
    unsupported = common.TmuxError("Always-framed mode requires tmux 3.8 floating panes and version-2 JSON layouts.")
    if "-abdPW" not in tmux.text("list-commands"):
        raise unsupported
    # At server start the config (and so this installer) runs before the first
    # session exists: there is no window to inspect yet, and an untargeted
    # query fails with "no current target". The after-new-session hook frames
    # that first window once it is created.
    windows = tmux.windows()
    if windows and json.loads(tmux.fmt(windows[0], "#{window_layout}")).get("V") != 2:
        raise unsupported
    install_bindings(tmux)
    for hook, (action, *args) in HOOKS.items():
        condition = "#{&&:#{!=:#{@pane_clarity_busy},1},#{!=:#{@pane_clarity_paused},1}}"
        if action in ("hook", "focus"):
            condition = "#{&&:" + condition + ",#{!=:#{@pane_clarity_layout},}}"
        if action == "focus":
            # Skip without starting the engine when it selected this pane itself.
            condition = "#{&&:" + condition + ",#{!=:#{@pane_clarity_active},#{pane_id}}}"
        command = shlex.join(["if-shell", "-F", condition, render([invoke(action, *args)])])
        tmux.run("set-hook", "-g", f"{hook}[{INDEX}]", command)
    resurrect.install(tmux)
    tmux.run("set-option", "-g", "@pane_clarity_mode", "always")


def disable(tmux):
    """Return every window to tiled panes, then remove the adapters. While any
    window is still framed (e.g. the engine binary is gone), change nothing
    else: its bindings and hooks are what lets it recover, and running this
    again once the engine is back finishes the job."""
    failed = False
    framed = [window for window in tmux.windows() if tmux.get(window, common.STATE)]
    if framed and not common.built():
        print(f"pane clarity: always mode left on: {framed[0]} is framed and the engine is missing; "
              f"run `cargo build --release` in {common.ROOT}, then try again", file=sys.stderr)
        return 1
    for window in framed:
        # A failed action falls back to tiled panes on its own.
        failed |= bool(common.run_engine(["disable", window], flags=tmux.flags))
    still = [window for window in framed if tmux.get(window, common.STATE)]
    if still:
        print(f"pane clarity: always mode left on: {', '.join(still)} could not be returned to tiled panes",
              file=sys.stderr)
        return 1
    for hook in HOOKS:
        tmux.run("set-hook", "-gu", f"{hook}[{INDEX}]")
    raw = tmux.text("show-option", "-gqv", SAVED)
    for item in json.loads(raw) if raw else []:
        if not adapted_here(item, current_binding(tmux, item["table"], item["key"])):
            continue  # the user has bound or unbound it since: theirs
        if item["line"]:
            tmux.run("source-file", "-", input=item["line"] + "\n")
        else:
            tmux.run("unbind-key", "-T", item["table"], item["key"])
    tmux.run("set-option", "-gu", SAVED)
    tmux.run("set-option", "-gu", common.FRAMED)
    resurrect.uninstall(tmux)
    tmux.run("set-option", "-gu", "@pane_clarity_paused")
    tmux.run("set-option", "-g", "@pane_clarity_mode", "zoom")
    return int(failed)


def main(argv=None, flags=()):
    args = sys.argv[1:] if argv is None else argv
    tmux = common.Tmux(flags)
    if args == ["disable"]:
        return disable(tmux)
    if args != ["enable"]:
        print("usage: always_mode.py enable|disable", file=sys.stderr)
        return 2
    try:
        install(tmux)
    except (common.TmuxError, ValueError):
        disable(tmux)
        raise
    # Windows that can't be framed (e.g. too small) fall back to tiled view
    # individually; the rest stay framed.
    failed = False
    for window in tmux.windows():
        failed |= bool(common.run_engine(["enable", window], flags=tmux.flags))
    return int(failed)


if __name__ == "__main__":
    sys.exit(main())
