#!/usr/bin/env python3
"""Isolated-server integration tests; never touch the user's tmux server."""
import json
import os
import shlex
import shutil
from pathlib import Path
import subprocess
import sys
import time
import signal
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import common  # noqa: E402
import geometry  # noqa: E402  (independent reference for frame positions)

SOCKET_PREFIX = f"clarity-layout-test-{os.getpid()}-"


def remove_socket(path):
    """kill-server leaves the socket file behind, and the engine its lock."""
    path.unlink(missing_ok=True)
    path.with_name(path.name + ".clarity-lock").unlink(missing_ok=True)


def tearDownModule():
    # Every test removes its server's socket and lock files (remove_socket).
    left = [p.name for p in FramesTest.socket_dir.glob(SOCKET_PREFIX + "*")] if FramesTest.socket_dir else []
    assert not left, f"test sockets left behind: {left}"


def resurrect_prefix(kind):
    """Where the resurrect hook that pane clarity chains onto is kept."""
    import resurrect
    return resurrect.PREFIX + kind


class FramesTest(unittest.TestCase):
    socket_dir = None  # where tmux puts the -L sockets, once a test has started one

    def setUp(self):
        self.socket = SOCKET_PREFIX + self.id().split('.')[-1]
        self.tmux = common.Tmux(["-L", self.socket])
        self.tmux.run("-f", "/dev/null", "new-session", "-d", "-s", "test", "-x", 120, "-y", 40)
        self.socket_path = Path(self.tmux.text("display-message", "-p", "#{socket_path}"))
        FramesTest.socket_dir = self.socket_path.parent
        self.window = self.tmux.fmt("test:0", "#{window_id}")
        self.pane = self.tmux.fmt(self.window, "#{pane_id}")
        self.tmux.set(self.window, "pane-border-status", "off")
        self.pid = self.tmux.fmt(self.pane, "#{pane_pid}")

    def tearDown(self):
        self.tmux.run("kill-server", check=False)
        remove_socket(self.socket_path)

    def run_manager(self, action, target=None, *args, success=True):
        code = common.run_engine([action, target or self.pane, *args], flags=["-L", self.socket])
        self.assertEqual(code, 0 if success else 1)

    def state(self, window=None):
        raw = self.tmux.get(window or self.window, common.STATE)
        return json.loads(raw) if raw else None

    def size(self):
        return tuple(map(int, self.tmux.fmt(self.window, "#{window_width} #{window_height}").split()))

    def native_boxes(self, window):
        root = json.loads(self.tmux.fmt(window, "#{window_layout}"))["L"]
        return [(n["x"], n["y"], n["w"], n["h"]) for n in geometry.leaves(root)]

    def state_boxes(self):
        return [(n["x"], n["y"], n["w"], n["h"]) for n in geometry.leaves(self.state()["root"])]

    def split(self, direction="-h", pane=None):
        self.run_manager("command", pane, "split-window", direction, "-t", pane or self.pane)
        return self.tmux.fmt(self.window, "#{pane_id}")

    def assert_frames(self):
        state = self.state()
        self.assertIsNotNone(state)
        panes = self.tmux.panes(self.window)
        self.assertEqual(set(panes), {p["I"] for p in geometry.leaves(state["root"])})
        w, h = self.size()
        if len(panes) == 1:
            # A lone pane is not framed: natively tiled, full window.
            (only,) = panes.values()
            self.assertFalse(only["floating"])
            self.assertEqual((only["x"], only["y"], only["w"], only["h"]), (0, 0, w, h))
            self.assertIsNone(state.get("zoom"))
        else:
            expected = geometry.frame_boxes(state["root"], (w, h))
            for p, box in expected.items():
                actual = panes[p]
                self.assertTrue(actual["floating"], p)
                if state.get("zoom") == p:
                    self.assertEqual((actual["x"], actual["y"], actual["w"], actual["h"]), (1, 1, w - 2, h - 2))
                else:
                    self.assertEqual((actual["x"], actual["y"], actual["w"], actual["h"]),
                                     (box["x"] + 1, box["y"] + 1, box["w"] - 2, box["h"] - 2))
            boxes = list(expected.values())
            for i, a in enumerate(boxes):
                for b in boxes[i + 1:]:
                    self.assertTrue(a["x"] + a["w"] <= b["x"] or b["x"] + b["w"] <= a["x"] or
                                    a["y"] + a["h"] <= b["y"] or b["y"] + b["h"] <= a["y"])
        self.assertEqual(self.tmux.fmt(self.pane, "#{pane_pid}"), self.pid)

    def test_lone_pane_has_no_frame_until_split(self):
        self.run_manager("enable")
        self.assert_frames()
        self.assertFalse(self.tmux.panes(self.window)[self.pane]["floating"])
        right = self.split()
        self.assert_frames()
        self.run_manager("command", right, "kill-pane", "-t", right)
        self.assert_frames()
        self.assertFalse(self.tmux.panes(self.window)[self.pane]["floating"])
        # A split the adapter never saw also starts framing (via the hook).
        self.tmux.run("split-window", "-v", "-t", self.pane)
        self.run_manager("hook")
        self.assert_frames()
        top, bottom = sorted(self.tmux.panes(self.window).values(), key=lambda p: p["y"])
        # Stacked: top frame's bottom edge (row y+h) sits directly above the
        # bottom frame's top edge (row y-1) - no blank row between them.
        self.assertEqual(bottom["y"] - 1, top["y"] + top["h"] + 1)
        bottom_id = next(p for p, info in self.tmux.panes(self.window).items() if info["y"] == bottom["y"])
        right_id = self.split("-h", bottom_id)
        left, right = self.tmux.panes(self.window)[bottom_id], self.tmux.panes(self.window)[right_id]
        # Side by side: one blank separator column between the two edges.
        self.assertEqual(right["x"] - 1, left["x"] + left["w"] + 2)

    def test_single_zoom_and_rollback(self):
        self.tmux.set(self.window, "window-style", "bg=#112233")
        self.run_manager("enable")
        self.assert_frames()
        self.run_manager("zoom")
        self.assert_frames()
        self.run_manager("zoom")
        self.assert_frames()
        self.run_manager("disable")
        self.assertFalse(self.tmux.panes(self.window)[self.pane]["floating"])
        self.assertEqual(self.tmux.get(self.window, "window-style"), "bg=#112233")

    def test_splits_resize_navigation_and_layout(self):
        self.run_manager("enable")
        right = self.split()
        bottom = self.split("-v", right)
        self.assert_frames()
        self.run_manager("command", self.pane, "select-pane", "-R")
        self.assertIn(self.tmux.fmt(self.window, "#{pane_id}"), (right, bottom))
        self.run_manager("command", right, "select-pane", "-D")
        self.assertEqual(self.tmux.fmt(self.window, "#{pane_id}"), bottom)
        self.run_manager("command", bottom, "select-pane", "-L")
        self.assertEqual(self.tmux.fmt(self.window, "#{pane_id}"), self.pane)
        before = list(geometry.leaves(self.state()["root"]))[0]["w"]
        self.run_manager("command", self.pane, "resize-pane", "-R", "5", "-t", self.pane)
        after = list(geometry.leaves(self.state()["root"]))[0]["w"]
        self.assertEqual(after - before, 5)
        self.run_manager("command", self.pane, "select-layout", "-t", self.window, "tiled")
        self.assert_frames()
        self.run_manager("zoom", right)
        self.assert_frames()
        self.run_manager("command", right, "select-pane", "-L")
        self.assertIsNone(self.state()["zoom"])
        self.assert_frames()

    def test_navigation_matches_native_tiled_neighbors(self):
        for _ in range(3):
            self.tmux.run("split-window", "-h", "-t", self.pane)
        reference = self.tmux.text("new-window", "-d", "-P", "-F", "#{window_id}")
        ref_first = self.tmux.fmt(reference, "#{pane_id}")
        for _ in range(3):
            self.tmux.run("split-window", "-h", "-t", ref_first)
        for preset in ("even-horizontal", "even-vertical", "tiled", "main-horizontal", "main-vertical"):
            for window in (self.window, reference):
                self.tmux.run("select-layout", "-t", window, preset)
                # Establish identical MRU histories before comparing behavior.
                for p in self.tmux.panes(window):
                    self.tmux.run("select-pane", "-t", p)
            panes = list(self.tmux.panes(self.window))
            refs = list(self.tmux.panes(reference))
            self.run_manager("enable")
            for index, pane in enumerate(panes):
                for direction in ("-L", "-R", "-U", "-D"):
                    self.tmux.run("select-pane", "-t", refs[index])
                    self.tmux.run("select-pane", "-t", refs[index], direction)
                    expected = refs.index(self.tmux.fmt(reference, "#{pane_id}"))
                    self.tmux.run("select-pane", "-t", pane)
                    self.run_manager("command", pane, "select-pane", direction)
                    actual = panes.index(self.tmux.fmt(self.window, "#{pane_id}"))
                    self.assertEqual(actual, expected, (preset, pane, direction))
            self.run_manager("disable")

    def test_resize_close_reload_and_concurrency_state(self):
        self.run_manager("enable")
        right = self.split()
        self.tmux.run("resize-window", "-t", self.window, "-x", 150, "-y", 50)
        self.run_manager("refresh")
        self.assert_frames()
        self.run_manager("enable")
        self.assert_frames()
        self.tmux.run("kill-pane", "-t", right)
        self.run_manager("refresh")
        self.assert_frames()
        self.assertEqual(len(self.tmux.panes(self.window)), 1)

    def test_native_command_output_and_quoted_shell_arguments(self):
        self.run_manager("enable")
        self.run_manager("command", self.pane, "split-window", "-h", "-t", self.pane,
                         "-c", "/tmp", "/bin/sh", "-c", "printf '%s' \"$1\"; sleep 30", "test", "a'b; $HOME")
        new = self.tmux.fmt(self.window, "#{pane_id}")
        self.assertEqual(self.tmux.fmt(new, "#{pane_current_path}"), "/private/tmp" if sys.platform == "darwin" else "/tmp")
        self.assert_frames()

    def test_cross_window_kill_with_hooks_does_not_deadlock(self):
        # The engine holds the server lock while the native kill-pane runs;
        # its after-kill-pane hook evaluates in the session's *current* window
        # (not busy), so the hook used to block on the lock while kill-pane
        # waited for the hook, forever.
        sys.path.insert(0, str(ROOT / "scripts"))
        import always_mode
        self.assertEqual(always_mode.main(["enable"], flags=self.tmux.flags), 0)
        right = self.split()
        self.split("-v", right)
        self.tmux.run("new-window", "-t", "test")  # becomes the current window
        self.assertNotEqual(self.tmux.fmt("test:", "#{window_id}"), self.window)
        socket = self.tmux.text("display-message", "-p", "#{socket_path}")
        try:
            subprocess.run([str(ROOT / "bin/tmux"), "-S", socket, "kill-pane", "-t", right],
                           env={**os.environ, "TMUX": ""}, check=True, timeout=10)
        except subprocess.TimeoutExpired:
            self.fail("cross-window kill-pane deadlocked on the server lock")
        self.wait_for(lambda: not self.tmux.get(self.window, common.BUSY))
        self.assertEqual(len(self.tmux.panes(self.window)), 2)
        self.assert_frames()
        # Hooks and engine actions still work afterwards (the lock is free).
        self.split("-v", self.pane)
        self.assert_frames()

    def test_install_before_first_session(self):
        # At server start the config (TPM -> this plugin) runs before any
        # session exists; `list-windows -a` and untargeted formats fail there
        # with "no current target", which used to roll the whole install back.
        sys.path.insert(0, str(ROOT / "scripts"))
        import always_mode
        empty = common.Tmux(["-L", self.socket + "-empty"])
        empty.run("-f", "/dev/null", "start-server", ";", "set-option", "-s", "exit-empty", "off")
        try:
            self.assertEqual(empty.text("list-sessions"), "")
            self.assertEqual(always_mode.main(["enable"], flags=empty.flags), 0)
            self.assertIn("pane-clarity", empty.text("show-hooks", "-g", f"after-new-session[{always_mode.INDEX}]"))
            # The first session's window is managed by the hook, and splits frame.
            window = empty.text("new-session", "-d", "-x", 120, "-y", 40, "-P", "-F", "#{window_id}")
            self.wait_for(lambda: bool(empty.get(window, common.STATE)))
            empty.run("split-window", "-t", window)
            self.wait_for(lambda: all(p["floating"] for p in empty.panes(window).values()))
        finally:
            empty.run("kill-server", check=False)
            remove_socket(self.socket_path.with_name(self.socket + "-empty"))

    def test_drag_release_reaches_mouse_tracking_program(self):
        # Programs that track the mouse (pi, other TUIs) select and copy
        # themselves and finish on the button-up. The installed
        # MouseDragEnd1Pane binding used to swallow that release.
        import pty
        import select
        sys.path.insert(0, str(ROOT / "scripts"))
        import always_mode
        received = Path(tempfile.mkdtemp()) / "mouse.log"
        app = ("import os, sys, tty; log = open(sys.argv[1], 'ab', buffering=0); tty.setraw(0); "
               "os.write(1, b'\\x1b[?1002h\\x1b[?1006h')\nwhile True: log.write(os.read(0, 1024))")
        self.tmux.run("respawn-pane", "-k", "-t", self.pane, sys.executable, "-I", "-c", app, str(received))
        self.assertEqual(always_mode.main(["enable"], flags=self.tmux.flags), 0)
        self.wait_for(lambda: self.tmux.fmt(self.pane, "#{mouse_any_flag}") == "1")
        pid, fd = pty.fork()
        if pid == 0:
            os.execv(common.REAL_TMUX, [common.REAL_TMUX, *self.tmux.flags, "attach", "-t", "test"])

        def pump(seconds):
            end = time.time() + seconds
            while time.time() < end:
                if select.select([fd], [], [], 0.05)[0]:
                    try:
                        os.read(fd, 65536)
                    except OSError:
                        return
        try:
            pump(1)
            for event in (b"\x1b[<0;10;5M", b"\x1b[<32;14;5M", b"\x1b[<32;20;5M", b"\x1b[<0;20;5m"):
                os.write(fd, event)
                pump(0.2)
            self.wait_for(lambda: received.exists() and b"\x1b[<0;20;5m" in received.read_bytes())
        finally:
            os.kill(pid, signal.SIGKILL)
            os.close(fd)  # else the client's exit waits to drain unread output
            os.waitpid(pid, 0)
        # Without mouse tracking, the release still reconciles border drags.
        tokens = shlex.split(self.tmux.text("list-keys", "-T", "root", "MouseDragEnd1Pane"))
        self.assertEqual(tokens[4:8], ["if-shell", "-F", always_mode.MOUSE_TO_PANE, "send-keys -M"])
        self.assertIn("mouse-resize", tokens[8])

    def adapter(self, *args):
        socket = self.tmux.text("display-message", "-p", "#{socket_path}")
        env = {**os.environ, "TMUX": "", "TMUX_PANE": os.environ.get("CLARITY_TEST_PANE", "")}
        return subprocess.run([str(ROOT / "bin/tmux"), "-S", socket, *args], env=env, timeout=10,
                              capture_output=True, text=True)

    def test_targeted_directional_select_starts_from_target(self):
        self.run_manager("enable")
        middle = self.split()
        right = self.split("-h", middle)
        self.run_manager("command", right, "select-pane", "-R")  # stays at the right edge
        # -R is a flag here, not an option value: the target is -t's pane.
        self.run_manager("command", right, "select-pane", "-R", "-t", self.pane)
        self.assertEqual(self.tmux.fmt(self.window, "#{pane_id}"), middle)
        os.environ["CLARITY_TEST_PANE"] = right
        try:
            self.adapter("select-pane", "-t", right)  # native: unmanaged
            self.assertEqual(self.adapter("select-pane", "-R", "-t", self.pane).returncode, 0)
        finally:
            del os.environ["CLARITY_TEST_PANE"]
        self.assertEqual(self.tmux.fmt(self.window, "#{pane_id}"), middle)
        self.assertEqual(self.state()["active"], middle)
        self.assert_frames()

    def test_cross_window_swap_keeps_source_proportions(self):
        self.run_manager("enable")
        bottom = self.split("-v")
        self.run_manager("command", self.pane, "resize-pane", "-D", "6")
        before = {n["I"]: (n["x"], n["y"], n["w"], n["h"]) for n in geometry.leaves(self.state()["root"])}
        other = self.tmux.text("new-window", "-d", "-t", "test", "-P", "-F", "#{window_id}")
        incoming = self.tmux.fmt(other, "#{pane_id}")
        self.tmux.run("split-window", "-h", "-t", incoming)
        self.run_manager("enable", other)
        # A swap that fails moves nothing, so nothing carries over.
        self.assertNotEqual(self.adapter("swap-pane", "-Q", "-s", bottom, "-t", incoming).returncode, 0)
        self.run_manager("refresh", self.window)
        self.assertEqual({n["I"]: (n["x"], n["y"], n["w"], n["h"]) for n in geometry.leaves(self.state()["root"])}, before)
        self.assertEqual(self.adapter("swap-pane", "-d", "-s", bottom, "-t", incoming).returncode, 0)
        self.run_manager("refresh", self.window)
        after = {n["I"]: (n["x"], n["y"], n["w"], n["h"]) for n in geometry.leaves(self.state()["root"])}
        self.assertEqual(after, {incoming if p == bottom else p: box for p, box in before.items()})
        self.assert_frames()
        self.assertIn(bottom, self.tmux.panes(other))

    def test_unreadable_state_fails_open(self):
        self.run_manager("enable")
        self.split()
        for action in ("refresh", "hook"):
            self.run_manager("enable")
            self.assertTrue(any(p["floating"] for p in self.tmux.panes(self.window).values()))
            self.tmux.set(self.window, common.STATE, '{"root": ')
            self.run_manager(action, self.window, success=action == "hook")
            self.assertEqual(self.tmux.get(self.window, common.BUSY), "", action)
            self.assertFalse(any(p["floating"] for p in self.tmux.panes(self.window).values()), action)
            self.assertIsNone(self.state(), action)
            self.assertEqual(self.tmux.fmt(self.pane, "#{pane_pid}"), self.pid)

    def test_failed_chained_restore_hook_still_resumes_framing(self):
        sys.path.insert(0, str(ROOT / "scripts"))
        import always_mode
        for kind in ("pre-restore-all", "post-restore-all"):
            self.tmux.run("set-option", "-g", "@resurrect-hook-" + kind, "/usr/bin/false")
        self.assertEqual(always_mode.main(["enable"], flags=self.tmux.flags), 0)
        self.split()
        script = shlex.split(self.tmux.text("show-option", "-gqv", "@resurrect-hook-post-restore-all"))
        env = {**os.environ, "TMUX": self.tmux.text("display-message", "-p", "#{socket_path},0,0")}
        run = lambda kind: subprocess.run([*script[:-1], kind], env=env, timeout=20).returncode
        self.assertEqual(run("pre-restore-all"), 1)  # the chained hook's failure is reported...
        self.assertEqual(self.tmux.text("show-option", "-gqv", "@pane_clarity_paused"), "1")
        self.assertEqual(run("post-restore-all"), 1)
        # ...but framing resumes.
        self.assertEqual(self.tmux.text("show-option", "-gqv", "@pane_clarity_paused"), "")
        self.assert_frames()
        self.assertEqual(always_mode.disable(self.tmux), 0)

    def test_save_keeps_layouts_matching_the_snapshot(self):
        sys.path.insert(0, str(ROOT / "scripts"))
        import resurrect
        self.run_manager("enable")
        self.split()
        native = self.tmux.fmt(self.window, "#{window_layout}")
        snapshot = "\t".join(["window", "test", "0", ":zsh", "1", ":*", native, "on"]) + "\n"
        with tempfile.TemporaryDirectory(prefix="clarity-resurrect-") as directory:
            path = Path(directory) / "save.txt"
            path.write_text(snapshot)
            resurrect.rewrite_save(self.tmux, path)  # same panes: the logical layout
            data = json.loads(path.read_text().split("\t")[6])
            self.assertNotIn("F", data)
            self.assertEqual({n["I"] for n in geometry.leaves(data["L"])}, set(self.tmux.panes(self.window)))
            # A split between the snapshot and the hook: the logical layout
            # no longer matches the saved panes, and the snapshot's own is
            # floating. Save a tiled layout valid for any pane count.
            path.write_text(snapshot)
            self.split("-v")
            resurrect.rewrite_save(self.tmux, path)
            self.assertEqual(path.read_text(), snapshot.replace(native, "tiled"))

    def test_save_race_then_full_restore_frames_window(self):
        sys.path.insert(0, str(ROOT / "scripts"))
        import always_mode
        self.assertEqual(always_mode.main(["enable"], flags=self.tmux.flags), 0)
        self.split()
        socket = self.tmux.text("display-message", "-p", "#{socket_path}")
        # The hook chained before ours splits the window after resurrect has
        # snapshotted it: the race, made deterministic.
        split = shlex.join([common.REAL_TMUX, "-S", socket, "split-window", "-d", "-v", "-t", self.pane])
        self.tmux.run("set-option", "-g", resurrect_prefix("post-save-layout"), split + " ; true")
        scripts = ROOT.parent / "tmux-resurrect/scripts"
        with tempfile.TemporaryDirectory(prefix="clarity-resurrect-") as directory:
            self.tmux.run("set-option", "-g", "@resurrect-dir", directory)
            self.tmux.run("run-shell", shlex.join(["bash", str(scripts / "save.sh"), "quiet"]))
            self.assertEqual(len(self.tmux.panes(self.window)), 3)
            saved = [l.split("\t") for l in (Path(directory) / "last").read_text().splitlines()]
            self.assertEqual(len([f for f in saved if f[0] == "pane" and f[1] == "test"]), 2)
            self.assertEqual(next(f[6] for f in saved if f[0] == "window" and f[1] == "test"), "tiled")
            self.tmux.run("new-session", "-d", "-s", "keeper")
            self.tmux.run("kill-session", "-t", "test")
            self.tmux.run("run-shell", shlex.join(["bash", str(scripts / "restore.sh")]))
            self.wait_for(lambda: self.tmux.run("has-session", "-t", "test", check=False).returncode == 0)
        window = self.tmux.fmt("test:0", "#{window_id}")
        self.assertEqual(self.tmux.text("show-option", "-gqv", "@pane_clarity_paused"), "")
        self.assertTrue(self.state(window), "restored window is managed")
        panes = self.tmux.panes(window)
        self.assertEqual(len(panes), 2)
        self.assertTrue(all(p["floating"] for p in panes.values()))
        self.assertEqual(set(panes), {n["I"] for n in geometry.leaves(self.state(window)["root"])})

    def test_window_closed_during_save_restores_managed(self):
        sys.path.insert(0, str(ROOT / "scripts"))
        import always_mode
        self.assertEqual(always_mode.main(["enable"], flags=self.tmux.flags), 0)
        right = self.split()
        framed = lambda: set(self.tmux.text("show-option", "-gqv", common.FRAMED).split())
        self.assertEqual(framed(), {self.pane, right})
        # The user's own float, in a window the plugin doesn't manage.
        self.tmux.run("new-session", "-d", "-s", "user", "-x", 120, "-y", 40)
        user = self.tmux.fmt("user:0", "#{window_id}")
        self.run_manager("disable", user)
        own = self.tmux.text("split-window", "-d", "-h", "-t", user, "-P", "-F", "#{pane_id}")
        self.tmux.run("break-pane", "-dW", "-s", own, "-x", 30, "-y", 10, "-X", 5, "-Y", 5)
        self.assertEqual(framed(), {self.pane, right})
        user_layout = self.tmux.fmt(user, "#{window_layout}")
        self.assertIn('"z"', user_layout)
        self.tmux.run("new-session", "-d", "-s", "keeper")
        socket = self.tmux.text("display-message", "-p", "#{socket_path}")
        # Both sessions close after resurrect snapshots them.
        kill = lambda s: shlex.join([common.REAL_TMUX, "-S", socket, "kill-session", "-t", s])
        self.tmux.run("set-option", "-g", resurrect_prefix("post-save-layout"), f"{kill('test')} ; {kill('user')} ; true")
        scripts = ROOT.parent / "tmux-resurrect/scripts"
        with tempfile.TemporaryDirectory(prefix="clarity-resurrect-") as directory:
            self.tmux.run("set-option", "-g", "@resurrect-dir", directory)
            self.tmux.run("run-shell", shlex.join(["bash", str(scripts / "save.sh"), "quiet"]))
            self.assertNotEqual(self.tmux.run("has-session", "-t", "test", check=False).returncode, 0)
            rows = {f[1]: f[6] for f in (l.split("\t") for l in (Path(directory) / "last").read_text().splitlines())
                    if f[0] == "window"}
            self.assertEqual(rows["test"], "tiled")
            self.assertEqual(rows["user"], user_layout)  # not the plugin's: kept as saved
            self.tmux.run("set-option", "-gu", resurrect_prefix("post-save-layout"))
            self.tmux.run("run-shell", shlex.join(["bash", str(scripts / "restore.sh")]))
        window = self.tmux.fmt("test:0", "#{window_id}")
        self.assertTrue(self.state(window), "restored window is managed")
        panes = self.tmux.panes(window)
        self.assertEqual(len(panes), 2)
        self.assertTrue(all(p["floating"] for p in panes.values()))
        self.assertTrue(set(panes) <= framed())
        self.assertEqual(always_mode.disable(self.tmux), 0)
        self.assertEqual(framed(), set())

    def test_pane_moved_into_user_float_keeps_its_layout(self):
        sys.path.insert(0, str(ROOT / "scripts"))
        import always_mode
        self.assertEqual(always_mode.main(["enable"], flags=self.tmux.flags), 0)
        right = self.split()
        framed = lambda: set(self.tmux.text("show-option", "-gqv", common.FRAMED).split())
        user = self.tmux.text("new-window", "-d", "-t", "test", "-P", "-F", "#{window_id}")
        self.wait_for(lambda: self.state(user) and not self.tmux.get(user, common.BUSY))
        self.run_manager("disable", user)
        own = self.tmux.fmt(user, "#{pane_id}")
        self.tmux.run("break-pane", "-dW", "-s", own, "-x", 30, "-y", 10, "-X", 5, "-Y", 5)
        index = self.tmux.fmt(user, "#{window_index}")
        scripts = ROOT.parent / "tmux-resurrect/scripts"
        def saved_user_layout():
            # A directory per save: resurrect names saves by the second, and a
            # second save within one deletes the first's file.
            directory = tempfile.mkdtemp(prefix="clarity-resurrect-")
            self.addCleanup(shutil.rmtree, directory)
            self.tmux.run("set-option", "-g", "@resurrect-dir", directory)
            self.tmux.run("run-shell", shlex.join(["bash", str(scripts / "save.sh"), "quiet"]))
            rows = [l.split("\t") for l in (Path(directory) / "last").read_text().splitlines()]
            return next(f[6] for f in rows if f[:3] == ["window", "test", index])

        # Through the engine: the pane leaving for the user's window is retired.
        self.assertEqual(self.adapter("swap-pane", "-d", "-s", right, "-t", own).returncode, 0)
        self.assertEqual(self.tmux.fmt(right, "#{window_id}"), user)
        self.assertNotIn(right, framed())
        self.assertIn(own, framed())
        expected = self.tmux.fmt(user, "#{window_layout}")
        self.assertIn('"z"', expected)
        self.assertEqual(saved_user_layout(), expected)
        # Natively, bypassing the engine: the pane is still recorded as framed,
        # but it is alive in an unmanaged window, so it isn't the plugin's.
        self.tmux.run("swap-pane", "-d", "-s", self.pane, "-t", right)
        self.assertIn(self.pane, framed())
        expected = self.tmux.fmt(user, "#{window_layout}")
        self.assertEqual(saved_user_layout(), expected)
        self.assertNotIn(self.pane, framed())  # pruned by the save
        # The user's window closes mid-save: its panes are gone, none framed.
        socket = self.tmux.text("display-message", "-p", "#{socket_path}")
        kill = shlex.join([common.REAL_TMUX, "-S", socket, "kill-window", "-t", user])
        self.tmux.run("set-option", "-g", resurrect_prefix("post-save-layout"), kill + " ; true")
        expected = self.tmux.fmt(user, "#{window_layout}")
        self.assertEqual(saved_user_layout(), expected)

    def wait_for(self, predicate):
        for _ in range(100):
            if predicate():
                return
            time.sleep(0.05)
        self.fail("Timed out waiting for tmux hook")

    def test_installed_bindings_hooks_adapter_and_rollback(self):
        sys.path.insert(0, str(ROOT / "scripts"))
        import always_mode
        self.tmux.run("bind-key", "|", "split-window", "-h")
        self.tmux.run("bind-key", "z", "run-shell", "bash '/tmp/framed_zoom.sh' '#{pane_id}'")
        self.tmux.run("bind-key", "-n", "C-h", "if-shell", "false", "send-keys C-h", "select-pane -L")
        before = {table: self.tmux.text("list-keys", "-T", table) for table in ("root", "prefix", "copy-mode-vi", "copy-mode")}
        self.assertEqual(always_mode.main(["enable"], flags=self.tmux.flags), 0)
        self.assert_frames()
        # Exercise the installed split binding's exact tmux command.
        tokens = shlex.split(self.tmux.text("list-keys", "-T", "prefix", "|"))
        self.tmux.run(*tokens[tokens.index("|") + 1:])
        self.assert_frames()
        other = self.tmux.fmt(self.window, "#{pane_id}")
        self.assertNotEqual(other, self.pane)
        socket = self.tmux.text("display-message", "-p", "#{socket_path}")
        env = {**os.environ, "TMUX": f"{socket},0,0", "TMUX_PANE": other}
        # Exactly the form used by vim-tmux-navigator, including explicit socket.
        subprocess.run([str(ROOT / "bin/tmux"), "-S", socket, "select-pane", "-t", other, "-L"], env=env, check=True)
        self.assertEqual(self.tmux.fmt(self.window, "#{pane_id}"), self.pane)
        # Neovim's Ctrl-l at its right edge (config/tmux.lua): tmux is started
        # directly, never through Neovim's 'shell'.
        nvim_config = Path.home() / ".config/nvim"
        if shutil.which("nvim") and (nvim_config / "lua/config/tmux.lua").exists():
            lua = "local p = require('config.tmux').navigate('l'); assert(p, 'no tmux call'); assert(p:wait().code == 0)"
            subprocess.run(["nvim", "--headless", "-u", "NONE", "-i", "NONE", "--cmd", "set rtp^=" + str(nvim_config),
                            "-c", "lua " + lua, "-c", "qa!"],
                           env={**env, "TMUX_PANE": self.pane, "SHELL": "/usr/bin/false"}, check=True, timeout=15)
            self.assertEqual(self.tmux.fmt(self.window, "#{pane_id}"), other)
        fish_function = Path.home() / ".config/fish/functions/tmux.fish"
        if shutil.which("fish") and fish_function.exists():
            subprocess.run(["fish", "-c", "source $argv[1]; tmux -S $argv[2] select-pane -t $argv[3] -L",
                            str(fish_function), socket, other], env=env, check=True, timeout=15)
            self.assertEqual(self.tmux.fmt(self.window, "#{pane_id}"), self.pane)
        subprocess.run([str(ROOT / "bin/tmux"), "-S", socket, "split-window", "-v", "-t", self.pane], env=env, check=True)
        self.assert_frames()
        # Structural native commands that the adapter does not change still
        # produce new managed windows through hooks.
        new_window = self.tmux.text("new-window", "-d", "-P", "-F", "#{window_id}")
        self.wait_for(lambda: bool(self.tmux.get(new_window, common.STATE)))
        self.assertFalse(any(p["floating"] for p in self.tmux.panes(new_window).values()))  # lone pane: no frame
        # Splitting it (natively, e.g. from the command prompt) starts framing.
        self.tmux.run("split-window", "-t", new_window)
        self.wait_for(lambda: all(p["floating"] for p in self.tmux.panes(new_window).values()))
        self.tmux.run("resize-window", "-t", self.window, "-x", 140, "-y", 48)
        self.wait_for(lambda: self.state()["root"]["w"] == 140)
        self.wait_for(lambda: not self.tmux.get(self.window, common.BUSY))
        self.assert_frames()
        new_session_window = self.tmux.text("new-session", "-d", "-s", "second", "-P", "-F", "#{window_id}")
        self.wait_for(lambda: bool(self.tmux.get(new_session_window, common.STATE)))
        self.wait_for(lambda: not self.tmux.get(new_session_window, common.BUSY))
        always_mode.install(self.tmux)  # idempotence
        self.assertEqual(always_mode.disable(self.tmux), 0)
        for table, text in before.items():
            self.assertEqual(self.tmux.text("list-keys", "-T", table), text)
        self.assertFalse(any(p["floating"] for p in self.tmux.panes(self.window).values()))
        self.assertEqual(self.tmux.fmt(self.pane, "#{pane_pid}"), self.pid)

    def test_mouse_resize_failed_command_and_serialization(self):
        self.run_manager("enable")
        self.split()
        old = next(n for n in geometry.leaves(self.state()["root"]) if n["I"] == self.pane)["w"]
        # Emulate native floating-border dragging, then the drag-release adapter.
        self.tmux.run("resize-pane", "-t", self.pane, "-R", 5)
        self.run_manager("mouse-resize")
        new = next(n for n in geometry.leaves(self.state()["root"]) if n["I"] == self.pane)["w"]
        self.assertEqual(new - old, 5)
        self.assert_frames()
        self.run_manager("command", self.pane, "split-window", "-t", "%999999", success=False)
        self.assert_frames()
        workers = [subprocess.Popen([common.BINARY, "command", self.pane, "resize-pane", "-R", "1"], env={**os.environ, "TMUX": self.tmux.text("display-message", "-p", "#{socket_path}") + ",0,0"})
                   for _ in range(3)]
        for worker in workers:
            self.assertEqual(worker.wait(timeout=15), 0)
        self.assert_frames()

    def test_resurrect_continuum_save_and_restore_hooks(self):
        sys.path.insert(0, str(ROOT / "scripts"))
        import always_mode
        self.assertEqual(always_mode.main(["enable"], flags=self.tmux.flags), 0)
        self.split()
        with tempfile.TemporaryDirectory(prefix="clarity-resurrect-") as directory:
            self.tmux.run("set-option", "-g", "@resurrect-dir", directory)
            save = ROOT.parent / "tmux-resurrect/scripts/save.sh"
            self.tmux.run("run-shell", shlex.join(["bash", str(save), "quiet"]))
            path = Path(directory) / "last"
            self.assertTrue(path.exists())
            row = next(line for line in path.read_text().splitlines() if line.startswith("window\ttest\t0\t"))
            data = json.loads(row.split("\t")[6])
            self.assertIsNotNone(data["L"])
            self.assertNotIn("F", data)
            self.assertEqual(len(list(geometry.leaves(data["L"]))), 2)
            self.assert_frames()
        pre = self.tmux.text("show-option", "-gqv", "@resurrect-hook-pre-restore-all")
        post = self.tmux.text("show-option", "-gqv", "@resurrect-hook-post-restore-all")
        self.tmux.run("run-shell", pre)
        self.assertFalse(any(p["floating"] for p in self.tmux.panes(self.window).values()))
        new = self.tmux.text("new-window", "-d", "-P", "-F", "#{window_id}")
        self.tmux.run("run-shell", "true")
        self.assertFalse(any(p["floating"] for p in self.tmux.panes(new).values()))
        self.tmux.run("run-shell", post)
        self.assert_frames()
        self.assertTrue(self.state(new))
        self.assertEqual(always_mode.disable(self.tmux), 0)

    def test_failure_returns_to_native_without_losing_process(self):
        self.tmux.run("split-window", "-h", "-t", self.pane)
        self.tmux.run("resize-window", "-t", self.window, "-x", 5, "-y", 3)
        self.run_manager("enable", success=False)
        self.assertFalse(any(p["floating"] for p in self.tmux.panes(self.window).values()))
        self.assertIsNone(self.state())
        self.assertEqual(self.tmux.fmt(self.pane, "#{pane_pid}"), self.pid)

    def test_terminal_shrink_refits_frames(self):
        # tmux never shrinks a window whose panes all float; needs a real client.
        import fcntl, pty, select, signal, struct, termios
        sys.path.insert(0, str(ROOT / "scripts"))
        import always_mode
        self.split()
        self.assertEqual(always_mode.main(["enable"], flags=self.tmux.flags), 0)
        second = self.tmux.text("new-window", "-d", "-P", "-F", "#{window_id}")
        self.wait_for(lambda: bool(self.state(second)))
        pid, fd = pty.fork()
        if pid == 0:
            os.environ.pop("TMUX", None)
            os.environ["TERM"] = "xterm-256color"
            os.execv(common.REAL_TMUX, [common.REAL_TMUX, *self.tmux.flags, "attach", "-t", "test"])

        def resize(cols, rows):
            fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
            os.kill(pid, signal.SIGWINCH)

        def settled(width, height):
            while select.select([fd], [], [], 0)[0]:
                os.read(fd, 65536)
            return (self.size() == (width, height) and not self.tmux.get(self.window, common.BUSY)
                    and self.state()["root"]["w"] == width)

        try:
            resize(120, 41)
            self.wait_for(lambda: settled(120, 40))
            resize(90, 26)
            self.wait_for(lambda: settled(90, 25))
            self.assert_frames()
            resize(130, 46)
            self.wait_for(lambda: settled(130, 45))
            self.assert_frames()
            self.tmux.run("select-window", "-t", second)
            self.assertEqual(self.tmux.fmt(second, "#{window_width}x#{window_height}"), "130x45")
        finally:
            # SIGKILL: a client blocked writing to an undrained pty ignores SIGTERM.
            os.kill(pid, signal.SIGKILL)
            os.waitpid(pid, 0)
            os.close(fd)
        always_mode.disable(self.tmux)

    def test_foreign_split_is_adopted(self):
        self.run_manager("enable")
        right = self.split()
        # A split the adapter never saw (command prompt, plugin, script).
        self.tmux.run("split-window", "-v", "-t", right)
        new = self.tmux.fmt(self.window, "#{pane_id}")
        self.run_manager("hook")
        self.assert_frames()
        self.assertEqual(self.tmux.fmt(self.window, "#{pane_id}"), new)
        tree = geometry.boxes(self.state()["root"])
        self.assertEqual(tree[new]["x"], tree[right]["x"])
        self.assertGreater(tree[new]["y"], tree[right]["y"])
        # And through the installed after-split-window hook.
        sys.path.insert(0, str(ROOT / "scripts"))
        import always_mode
        always_mode.install(self.tmux)
        self.tmux.run("split-window", "-h", "-t", self.pane)
        self.wait_for(lambda: len(geometry.boxes(self.state()["root"])) == 4)
        self.wait_for(lambda: not self.tmux.get(self.window, common.BUSY))
        self.assert_frames()
        always_mode.disable(self.tmux)

    def test_geometry_operations_do_not_retile(self):
        self.run_manager("enable")
        right = self.split()
        self.split("-v", right)
        # Trace the engine's own tmux invocations (the Rust subprocess).
        directory = tempfile.mkdtemp(prefix="clarity-trace-")
        self.addCleanup(shutil.rmtree, directory)
        log, wrapper = Path(directory) / "calls", Path(directory) / "tmux"
        wrapper.write_text(f"#!/bin/sh\nprintf '%s\\037' \"$@\" >> {shlex.quote(str(log))}\n"
                           f"printf '\\036' >> {shlex.quote(str(log))}\nexec {shlex.quote(common.REAL_TMUX)} \"$@\"\n")
        wrapper.chmod(0o755)

        def traced(*args):
            log.write_text("")
            os.environ["PANE_CLARITY_REAL_TMUX"] = str(wrapper)
            try:
                self.run_manager(*args)
            finally:
                del os.environ["PANE_CLARITY_REAL_TMUX"]
            return [call.split("\037")[:-1] for call in log.read_text().split("\036")[:-1]]

        for args in (["select-pane", "-L"], ["resize-pane", "-R", "4"], ["resize-pane", "-Z"],
                     ["select-pane", "-R"], ["resize-pane", "-Z"], ["last-pane"]):
            active = self.tmux.fmt(self.window, "#{pane_id}")
            calls = traced("command", active, *args)
            self.assertTrue(calls, args)  # the trace sees the engine
            self.assertNotIn("join-pane", [a for call in calls for a in call], args)
            self.assertLessEqual(len(calls), 3, args)
            self.assert_frames()
        self.tmux.run("resize-window", "-t", self.window, "-x", 150, "-y", 50)
        calls = traced("refresh")
        self.assertNotIn("join-pane", [a for call in calls for a in call])
        self.assert_frames()
        calls = traced("enable")  # reload: nothing to change
        self.assertTrue(calls)
        self.assertFalse([c for c in calls if "resize-pane" in c or "break-pane" in c or "move-pane" in c])

    def test_resize_and_close_match_native_tiled(self):
        reference = self.tmux.text("new-window", "-d", "-P", "-F", "#{window_id}")
        for window in (self.window, reference):
            first = self.tmux.fmt(window, "#{pane_id}")
            self.tmux.run("split-window", "-h", "-t", first)
            self.tmux.run("split-window", "-v", "-t", first)
            self.tmux.run("split-window", "-h", "-t", window)
        panes = list(self.tmux.panes(self.window))
        refs = list(self.tmux.panes(reference))
        self.run_manager("enable")
        self.assertEqual(self.state_boxes(), self.native_boxes(reference))
        for index in range(len(panes)):
            for direction in common.DIRECTIONS:
                self.tmux.run("resize-pane", "-t", refs[index], direction, 3)
                self.run_manager("command", panes[index], "resize-pane", direction, "3")
                self.assertEqual(self.state_boxes(), self.native_boxes(reference), (index, direction))
        self.assert_frames()
        for index in (2, 1):
            self.tmux.run("kill-pane", "-t", refs[index])
            self.run_manager("command", panes[index], "kill-pane", "-t", panes[index])
            self.assertEqual(self.state_boxes(), self.native_boxes(reference), index)
        self.assert_frames()

    def test_mouse_corner_drag_and_move(self):
        self.run_manager("enable")
        right = self.split()
        bottom = self.split("-v", right)
        before = geometry.boxes(self.state()["root"])[right]
        self.tmux.run("resize-pane", "-t", right, "-L", 4)
        self.tmux.run("resize-pane", "-t", right, "-D", 2)
        self.run_manager("mouse-resize", right)
        after = geometry.boxes(self.state()["root"])[right]
        self.assertEqual((after["x"], after["w"], after["h"]), (before["x"] - 4, before["w"] + 4, before["h"] + 2))
        self.assert_frames()
        # Dragging a float without resizing it snaps back.
        before = self.state()["root"]
        self.tmux.run("move-pane", "-t", bottom, "-X", 3, "-Y", 3)
        self.run_manager("mouse-resize", bottom)
        self.assertEqual(self.state()["root"], before)
        self.assert_frames()

    def test_binding_rewrite_handles_command_lists_and_blocks(self):
        sys.path.insert(0, str(ROOT / "scripts"))
        import always_mode
        self.tmux.run("source-file", "-", input="\n".join([
            "bind-key x split-window -h \\; display-message 'split; done'",
            "bind-key y { split-window -v }",
            "bind-key w if-shell 'true' 'select-pane -L \\; display hi' 'send-keys x'",
        ]) + "\n")
        before = {t: self.tmux.text("list-keys", "-T", t) for t in always_mode.TABLES}
        self.assertEqual(always_mode.main(["enable"], flags=self.tmux.flags), 0)
        x = shlex.split(self.tmux.text("list-keys", "-T", "prefix", "x"))
        self.assertEqual(x[x.index("x") + 1], "run-shell")
        self.assertEqual(x[-3:], [";", "display-message", "split; done"])
        self.assertIn("{", self.tmux.text("list-keys", "-T", "prefix", "y"))  # left native
        self.assertIn(common.BINARY, self.tmux.text("list-keys", "-T", "prefix", "w"))
        # Run the rewritten command list exactly as tmux would.
        self.tmux.run(*x[x.index("x") + 1:])
        self.assertEqual(len(self.tmux.panes(self.window)), 2)
        self.assert_frames()
        self.assertEqual(always_mode.disable(self.tmux), 0)
        for table, text in before.items():
            self.assertEqual(self.tmux.text("list-keys", "-T", table), text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
