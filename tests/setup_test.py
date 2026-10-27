#!/usr/bin/env python3
"""Plugin load options, the always-mode preflight and the shell/editor
integrations, on isolated tmux servers (never the user's)."""
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
ENTRY = ROOT / "tmux-pane-clarity.tmux"
# What the plugin's scripts need on PATH, besides the tools a test fakes.
TOOLS = ("bash", "sh", "tmux", "sed", "find", "sort", "head", "tail", "awk", "grep", "dirname", "paste", "mkdir",
         "cat", "env", "touch", "python3")
# Only absolute PATH entries: a relative one (e.g. `bin`) would find this
# plugin's own bin/tmux when run from its directory.
SYSTEM_PATH = os.pathsep.join(p for p in os.environ["PATH"].split(os.pathsep) if os.path.isabs(p))


# Every hook the plugin sets (tmux-pane-clarity.tmux, always_mode.HOOKS).
PLUGIN_HOOKS = ("window-layout-changed", "window-resized", "pane-exited", "after-kill-pane", "after-split-window",
                "window-pane-changed", "after-new-window", "after-new-session", "client-resized",
                "client-session-changed")


def which(tool):
    return str(Path(shutil.which(tool, path=SYSTEM_PATH)).resolve())


class SetupTest(unittest.TestCase):
    def setUp(self):
        # Short: a socket path is limited to 104 bytes on macOS.
        self.socket = f"clarity-setup-test-{os.getpid()}-{hashlib.sha1(self.id().encode()).hexdigest()[:8]}"
        self.run_tmux("-f", "/dev/null", "new-session", "-d", "-x", "100", "-y", "30")
        self.socket_path = Path(self.run_tmux("display-message", "-p", "#{socket_path}"))
        self.assertTrue(self.socket_path.is_socket(), "test server did not start")
        self.addCleanup(self.remove_server)
        self.temp = Path(tempfile.mkdtemp(prefix="clarity-setup-test-"))
        self.addCleanup(shutil.rmtree, self.temp)

    def remove_server(self):
        self.run_tmux("kill-server")
        self.socket_path.unlink(missing_ok=True)  # kill-server leaves it
        self.socket_path.with_name(self.socket_path.name + ".clarity-lock").unlink(missing_ok=True)

    def run_tmux(self, *args):
        # The server's PATH is what its hooks run with: no relative entries, or
        # one could find this checkout's bin/tmux while a test runs a copy.
        return subprocess.run(["tmux", "-L", self.socket, *args], env={**os.environ, "PATH": SYSTEM_PATH},
                              capture_output=True, text=True, timeout=30).stdout.strip()

    def option(self, name):
        return self.run_tmux("show-option", "-gqv", name)

    def env(self, path=SYSTEM_PATH):
        return {**os.environ, "TMUX": f"{self.socket_path},0,0", "PATH": path}

    def load(self, entry=ENTRY, path=SYSTEM_PATH):
        result = subprocess.run(["bash", str(entry)], env=self.env(path), capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)

    def fake_bin(self, omit=(), **fakes):
        """A PATH holding only the needed tools, minus `omit`, plus `fakes`
        (name: shell script body)."""
        directory = Path(tempfile.mkdtemp(dir=self.temp, prefix="bin-"))
        for tool in TOOLS:
            if tool not in omit and tool not in fakes:
                (directory / tool).symlink_to(which(tool))
        for name, body in fakes.items():
            (directory / name).write_text("#!/bin/sh\n" + body + "\n")
            (directory / name).chmod(0o755)
        return str(directory)

    def unbuilt_copy(self):
        """The plugin without its built engine, so loading it in always mode
        checks for and runs a build."""
        copy = Path(tempfile.mkdtemp(dir=self.temp, prefix="plugin-")) / "tmux-pane-clarity"
        shutil.copytree(ROOT, copy, ignore=shutil.ignore_patterns("target", ".git", "__pycache__"))
        return copy

    def engine_copy(self):
        """The plugin with a current copy of the built engine: self-contained,
        whatever state this checkout's own build is in."""
        built = ROOT / "target/release/pane-clarity"
        self.assertTrue(os.access(built, os.X_OK), "build the engine first: cargo build --release")
        copy = self.unbuilt_copy()
        (copy / "target/release").mkdir(parents=True)
        shutil.copy2(built, copy / "target/release/pane-clarity")
        os.utime(copy / "target/release/pane-clarity")  # newer than every source
        return copy

    def snapshot(self):
        """Every binding, hook, option (server, global session and window, each
        window's) and global environment variable, and the panes."""
        windows = self.run_tmux("list-windows", "-a", "-F", "#{window_id}").split()
        options = [line for line in self.run_tmux("show-options", "-g").splitlines()
                   if not line.startswith(("@pane_clarity_mode ", "@pane_clarity_error "))]
        return {"keys": {table: self.run_tmux("list-keys", "-T", table)
                         for table in ("root", "prefix", "copy-mode", "copy-mode-vi")},
                "hooks": {scope: self.run_tmux("show-hooks", scope) for scope in ("-g", "-gw", "-gp")},
                "plugin hooks": {hook: self.run_tmux("show-hooks", "-g", hook) for hook in PLUGIN_HOOKS},
                "options": options, "window options": self.run_tmux("show-options", "-gw"),
                "server options": self.run_tmux("show-options", "-s"),
                "each window": {w: self.run_tmux("show-options", "-w", "-t", w) for w in windows},
                "environment": self.run_tmux("show-environment", "-g"),
                "panes": self.run_tmux("list-panes", "-a", "-F", "#{pane_id} #{pane_pid} #{window_id} #{pane_floating_flag}")}

    def binding(self, table, key):
        return self.run_tmux("list-keys", "-T", table, key)

    # Where it is installed --------------------------------------------------
    def test_records_its_location_for_commands_and_integrations(self):
        self.load()
        self.assertEqual(self.option("@pane_clarity_dir"), str(ROOT))
        self.assertEqual(self.run_tmux("show-environment", "-g", "PANE_CLARITY_TMUX"),
                         f"PANE_CLARITY_TMUX={ROOT}/bin/tmux")

    # Options ------------------------------------------------------------------
    def test_border_style_default_custom_and_off(self):
        self.load()
        self.assertEqual(self.option("pane-border-style"), "fg=#44475a")
        self.run_tmux("set-option", "-g", "@pane_clarity_border_style", "fg=blue")
        self.load()
        self.assertEqual(self.option("pane-border-style"), "fg=blue")
        self.run_tmux("set-option", "-g", "pane-border-style", "fg=red")  # the user's own
        self.run_tmux("set-option", "-g", "@pane_clarity_border_style", "")
        self.load()
        self.assertEqual(self.option("pane-border-style"), "fg=red")

    def test_reload_keys_default_changed_and_off(self):
        self.load()
        self.assertIn("tmux-pane-clarity.tmux", self.binding("prefix", "M-c"))
        self.assertIn("tmux-pane-clarity.tmux", self.binding("root", "M-l"))
        self.run_tmux("set-option", "-g", "@pane_clarity_reload_key", "M-r")
        self.run_tmux("set-option", "-g", "@pane_clarity_root_reload_key", "")
        self.load()
        self.assertIn("tmux-pane-clarity.tmux", self.binding("prefix", "M-r"))
        self.assertEqual(self.binding("prefix", "M-c"), "")  # the old key goes
        self.assertEqual(self.binding("root", "M-l"), "")
        # A key the user has since bound to something else is theirs: kept.
        self.run_tmux("bind-key", "-T", "prefix", "M-r", "display-message", "mine")
        self.run_tmux("set-option", "-g", "@pane_clarity_reload_key", "")
        self.load()
        self.assertIn("mine", self.binding("prefix", "M-r"))

    # Preflight ----------------------------------------------------------------
    def preflight(self, root=ROOT, **fake_bin):
        result = subprocess.run(["bash", str(root / "scripts/preflight.sh")], env=self.env(self.fake_bin(**fake_bin)),
                                capture_output=True, text=True, timeout=30)
        return result.returncode, result.stdout.splitlines()

    def test_preflight_passes_with_everything_installed(self):
        self.assertEqual(self.preflight(self.engine_copy()), (0, []))  # no cargo needed: the engine is current

    def test_preflight_names_each_missing_requirement(self):
        old_tmux = 'case "$1" in -V) echo "tmux 3.4" ;; list-commands) echo "break-pane (breakp) [-abdP]" ;; esac'
        code, lines = self.preflight(self.unbuilt_copy(), omit=("python3",), tmux=old_tmux)
        self.assertEqual(code, 1)
        self.assertEqual(lines, ["tmux 3.8+ is required (found: tmux 3.4)",
                                 "Python 3.8+ is required (python3 not found)",
                                 "Rust 1.82+ (cargo) is required to build the engine: https://rustup.rs"])

    def test_preflight_names_an_old_rust(self):
        code, lines = self.preflight(self.unbuilt_copy(), cargo="exit 0", rustc='echo "rustc 1.81.0 (eeb90cda1 2024-09-04)"')
        self.assertEqual((code, lines), (1, ["Rust 1.82+ is required to build the engine (found: 1.81.0)"]))
        code, lines = self.preflight(self.unbuilt_copy(), cargo="exit 0", rustc='echo "rustc 1.82.0 (f6e511eec 2024-10-15)"')
        self.assertEqual((code, lines), (0, []))

    def test_always_mode_reports_what_is_missing_and_stays_off(self):
        copy = self.unbuilt_copy()
        self.run_tmux("set-option", "-g", "@pane_clarity_mode", "always")
        self.load(copy / ENTRY.name, path=self.fake_bin())  # no cargo
        self.assertIn("Rust 1.82+ (cargo) is required", self.option("@pane_clarity_error"))
        self.assertEqual(self.option("@pane_clarity_saved_bindings"), "")  # not enabled

    def test_failed_build_keeps_its_output(self):
        copy = self.unbuilt_copy()
        self.run_tmux("set-option", "-g", "@pane_clarity_mode", "always")
        self.load(copy / ENTRY.name, path=self.fake_bin(cargo='echo "error[E0001]: boom" >&2; exit 101',
                                                        rustc='echo "rustc 1.99.0 (x 2026-01-01)"'))
        log = copy / "target/build.log"
        self.assertIn(f"engine build failed: see {log}", self.option("@pane_clarity_error"))
        self.assertIn("error[E0001]: boom", log.read_text())
        self.assertEqual(self.option("@pane_clarity_saved_bindings"), "")

    def test_a_build_that_changes_nothing_still_marks_the_engine_current(self):
        # cargo leaves the binary untouched when only Cargo.toml's metadata
        # changed; it must not then look out of date on every later load.
        copy = self.unbuilt_copy()
        binary = copy / "target/release/pane-clarity"
        binary.parent.mkdir(parents=True)
        binary.write_text("#!/bin/sh\nexit 0\n")
        binary.chmod(0o755)
        os.utime(binary, (1, 1))  # older than every source
        self.run_tmux("set-option", "-g", "@pane_clarity_mode", "always")
        self.load(copy / ENTRY.name, path=self.fake_bin(cargo="exit 0", rustc='echo "rustc 1.99.0 (x 2026-01-01)"'))
        stale = subprocess.run(["find", str(copy / "src"), str(copy / "Cargo.toml"), "-newer", str(binary)],
                               capture_output=True, text=True).stdout
        self.assertEqual(stale, "")

    def test_failed_reload_leaves_always_mode_as_it_was(self):
        copy = self.engine_copy()
        self.run_tmux("set-option", "-g", "@pane_clarity_mode", "always")
        self.run_tmux("split-window", "-h")
        self.load(copy / ENTRY.name, path=SYSTEM_PATH)
        self.assertNotEqual(self.option("@pane_clarity_saved_bindings"), "")  # on
        before = self.snapshot()
        # A requirement gone missing ...
        self.load(copy / ENTRY.name, path=self.fake_bin(omit=("python3",)))
        self.assertIn("reload skipped, always mode left as it was: Python 3.8+", self.option("@pane_clarity_error"))
        self.assertEqual(self.snapshot(), before)
        # ... or a failed build of changed sources.
        (copy / "src/main.rs").touch()
        self.load(copy / ENTRY.name, path=self.fake_bin(cargo="exit 101", rustc='echo "rustc 1.99.0 (x 2026-01-01)"'))
        self.assertIn("reload skipped, always mode left as it was: engine build failed",
                      self.option("@pane_clarity_error"))
        self.assertEqual(self.snapshot(), before)
        # ... or switching it off without the engine, which unframes the panes.
        (copy / "target/release/pane-clarity").unlink()
        self.run_tmux("set-option", "-g", "@pane_clarity_mode", "zoom")
        self.load(copy / ENTRY.name, path=SYSTEM_PATH)
        self.assertIn("reload skipped, always mode left on: @0 is framed and the engine is missing",
                      self.option("@pane_clarity_error"))
        self.run_tmux("set-option", "-g", "@pane_clarity_mode", "always")
        self.assertEqual(self.snapshot(), before)

    # Uninstall ------------------------------------------------------------------
    def users_own_setup(self):
        """Settings of the user's that the plugin replaces while loaded."""
        for args in (("set-option", "-g", "pane-border-style", "fg=red"), ("set-option", "-g", "pane-border-lines", "double"),
                     ("bind-key", "z", "display-message", "my zoom"), ("bind-key", "M-c", "display-message", "mine"),
                     ("bind-key", "-n", "C-h", "select-pane", "-L"), ("bind-key", "|", "split-window", "-h")):
            self.run_tmux(*args)
        self.run_tmux("split-window", "-h")

    def uninstall(self, copy, status=0):
        result = subprocess.run(["bash", str(copy / "scripts/uninstall.sh")], env=self.env(SYSTEM_PATH),
                                capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, status, result.stderr)
        return result.stderr

    def load_always(self, copy):
        self.run_tmux("set-option", "-g", "@pane_clarity_mode", "always")
        self.load(copy / ENTRY.name, path=SYSTEM_PATH)
        self.assertNotEqual(self.option("@pane_clarity_saved_bindings"), "")
        self.assertEqual(self.option("@pane_clarity_error"), "")

    def test_uninstall_from_always_mode_restores_the_server(self):
        self.users_own_setup()
        before = self.snapshot()
        copy = self.engine_copy()
        self.run_tmux("set-option", "-g", "@pane_clarity_mode", "always")
        self.load(copy / ENTRY.name, path=SYSTEM_PATH)
        self.assertNotEqual(self.option("@pane_clarity_saved_bindings"), "")
        self.assertIn("1", self.run_tmux("list-panes", "-F", "#{pane_floating_flag}"))  # framed
        self.uninstall(copy)
        self.assertEqual(self.snapshot(), before)

    def test_uninstall_from_zoom_mode_returns_a_framed_pane(self):
        self.users_own_setup()
        before = self.snapshot()
        copy = self.unbuilt_copy()
        self.load(copy / ENTRY.name)
        pane = self.run_tmux("display-message", "-p", "#{pane_id}")
        subprocess.run(["bash", str(copy / "scripts/framed_zoom.sh"), pane], env=self.env(), check=True)
        self.assertEqual(self.run_tmux("display-message", "-p", "-t", pane, "#{pane_floating_flag}"), "1")
        self.uninstall(copy)
        self.assertEqual(self.snapshot(), before)
        shutil.rmtree(copy)  # gone: nothing may still run its scripts
        after = self.snapshot()
        self.assertNotIn(str(copy), "\n".join([*after["keys"].values(), *after["hooks"].values()]))

    def test_uninstall_keeps_what_the_user_changed_since(self):
        copy = self.unbuilt_copy()
        self.load(copy / ENTRY.name)
        self.run_tmux("set-option", "-g", "pane-border-style", "fg=green")  # the user's, after loading
        self.run_tmux("bind-key", "z", "display-message", "rebound")
        self.uninstall(copy)
        self.assertEqual(self.option("pane-border-style"), "fg=green")
        self.assertIn("rebound", self.binding("prefix", "z"))

    def test_always_mode_uninstall_keeps_bindings_changed_since(self):
        self.users_own_setup()
        copy = self.engine_copy()
        self.load_always(copy)
        self.run_tmux("bind-key", "z", "display-message", "my new zoom")
        self.run_tmux("bind-key", "-n", "C-h", "select-pane", "-R")
        self.run_tmux("unbind-key", "|")
        self.uninstall(copy)
        self.assertIn("my new zoom", self.binding("prefix", "z"))
        self.assertIn("select-pane -R", self.binding("root", "C-h"))
        self.assertEqual(self.binding("prefix", "|"), "")

    def test_a_binding_changed_in_always_mode_is_the_original_after_a_reload(self):
        self.users_own_setup()
        copy = self.engine_copy()
        self.load_always(copy)
        self.run_tmux("bind-key", "-n", "C-h", "select-pane", "-R")  # adapted again by the reload
        self.load_always(copy)
        self.assertIn("pane-clarity", self.binding("root", "C-h"))
        self.uninstall(copy)
        self.assertEqual(self.binding("root", "C-h").split(), ["bind-key", "-T", "root", "C-h", "select-pane", "-R"])

    def test_bindings_changed_since_loading_are_the_originals_after_a_reload(self):
        self.users_own_setup()
        copy = self.engine_copy()
        self.load_always(copy)
        self.run_tmux("bind-key", "z", "display-message", "my new zoom")
        self.run_tmux("bind-key", "M-c", "display-message", "my new key")
        self.run_tmux("bind-key", "-n", "C-h", "select-pane", "-R")
        self.load_always(copy)
        self.assertIn("framed_zoom.sh", self.run_tmux("show-option", "-gqv", "@pane_clarity_saved_bindings"))  # z, adapted
        self.uninstall(copy)
        self.assertIn("my new zoom", self.binding("prefix", "z"))
        self.assertIn("my new key", self.binding("prefix", "M-c"))
        self.assertIn("select-pane -R", self.binding("root", "C-h"))

    def test_a_resurrect_hook_set_since_loading_is_chained_after_a_reload(self):
        marker = self.temp / "fired"
        self.run_tmux("set-option", "-g", "@resurrect-hook-post-save-layout", f"printf old > {marker}")
        copy = self.engine_copy()
        self.load_always(copy)
        newer = f"printf newer > {marker}"
        self.run_tmux("set-option", "-g", "@resurrect-hook-post-save-layout", newer)
        self.load_always(copy)
        save = self.temp / "save.txt"
        save.write_text("state\tfixture\n")
        hook = self.option("@resurrect-hook-post-save-layout")
        subprocess.run(["sh", "-c", hook + ' "$1"', "sh", str(save)], env=self.env(), check=True, timeout=30)
        self.assertEqual(marker.read_text(), "newer")  # the chained hook is the newer one
        self.uninstall(copy)
        self.assertEqual(self.option("@resurrect-hook-post-save-layout"), newer)

    def save_hook(self):
        """Run the resurrect post-save-layout hook as tmux-resurrect does."""
        save = self.temp / "save.txt"
        save.write_text("state\tfixture\n")
        return subprocess.run(["sh", "-c", self.option("@resurrect-hook-post-save-layout") + ' "$1"', "sh", str(save)],
                              env=self.env(), capture_output=True, text=True, timeout=30)

    def test_a_plugin_copied_elsewhere_keeps_the_original_resurrect_hook(self):
        marker, ran = self.temp / "fired", self.temp / "old-copy-ran"
        original = f"printf x >> {marker}"
        self.run_tmux("set-option", "-g", "@resurrect-hook-post-save-layout", original)
        old = self.engine_copy()
        self.load_always(old)
        new = self.engine_copy()  # the old copy's files remain, its hooks still set
        self.load_always(new)
        # The old copy now only records being run, so a regression can't loop.
        (old / "scripts/resurrect.py").write_text(f"open({str(ran)!r}, 'w').close()\n")
        self.assertIn(str(new), self.option("@resurrect-hook-post-save-layout"))
        self.assertEqual(self.save_hook().returncode, 0)
        self.assertEqual((marker.read_text(), ran.exists()), ("x", False))  # the user's hook, once
        self.uninstall(new)
        self.assertEqual(self.option("@resurrect-hook-post-save-layout"), original)

    def test_an_upgrade_installed_elsewhere_keeps_the_original_resurrect_hook(self):
        # Versions before the record of plugin hooks: the old copy's hook is
        # known by its script, not by the record.
        marker, ran = self.temp / "fired", self.temp / "old-copy-ran"
        original = f"printf x >> {marker}"
        self.run_tmux("set-option", "-g", "@resurrect-hook-post-save-layout", original)
        old = self.engine_copy()
        self.load_always(old)
        self.run_tmux("set-option", "-gu", "@pane_clarity_resurrect_hooks")  # as an earlier version left it
        new = self.engine_copy()
        self.load_always(new)
        self.assertEqual(self.option("@pane_clarity_saved_resurrect_post-save-layout"), original)
        # The old copy now only records being run (still recognizably the
        # plugin's), so a regression can't loop.
        (old / "scripts/resurrect.py").write_text(
            f'PREFIX = "@pane_clarity_saved_resurrect_"\nopen({str(ran)!r}, "w").close()\n')
        self.assertEqual(self.save_hook().returncode, 0)
        self.assertEqual((marker.read_text(), ran.exists()), ("x", False))
        self.uninstall(new)
        self.assertEqual(self.option("@resurrect-hook-post-save-layout"), original)

    def test_an_upgrade_after_deleting_the_earlier_copy_keeps_the_original_resurrect_hook(self):
        marker = self.temp / "fired"
        original = f"printf x >> {marker}"
        self.run_tmux("set-option", "-g", "@resurrect-hook-post-save-layout", original)
        old = self.engine_copy()
        self.load_always(old)
        self.run_tmux("set-option", "-gu", "@pane_clarity_resurrect_hooks")  # as an earlier version left it
        shutil.rmtree(old)  # gone before the new version first loads
        new = self.engine_copy()
        self.load_always(new)
        self.assertEqual(self.option("@pane_clarity_saved_resurrect_post-save-layout"), original)
        result = self.save_hook()
        self.assertEqual((result.returncode, result.stderr, marker.read_text()), (0, "", "x"))
        self.uninstall(new)
        self.assertEqual(self.option("@resurrect-hook-post-save-layout"), original)

    def test_a_hook_that_only_names_a_deleted_plugin_script_is_the_users(self):
        # Not Python: the path is only an argument (here, a message).
        mine = f"/bin/echo {self.temp}/deleted/scripts/resurrect.py post-save-layout"
        copy = self.engine_copy()
        self.load_always(copy)
        self.run_tmux("set-option", "-g", "@resurrect-hook-post-save-layout", mine)  # a replacement
        self.load_always(copy)
        self.assertEqual(self.option("@pane_clarity_saved_resurrect_post-save-layout"), mine)
        self.assertIn("deleted/scripts/resurrect.py post-save-layout", self.save_hook().stdout)  # chained
        self.uninstall(copy)
        self.assertEqual(self.option("@resurrect-hook-post-save-layout"), mine)

    def test_a_plugin_hook_saved_as_the_predecessor_is_never_chained_or_restored(self):
        old = self.engine_copy()
        self.load_always(old)
        old_hook = self.option("@resurrect-hook-post-save-layout")
        new = self.engine_copy()
        self.load_always(new)
        shutil.rmtree(old)
        predecessor = "@pane_clarity_saved_resurrect_post-save-layout"
        self.run_tmux("set-option", "-g", predecessor, old_hook)  # as an earlier version saved it
        result = self.save_hook()
        self.assertEqual((result.returncode, result.stderr), (0, ""))  # skipped, not run
        self.load_always(new)
        self.assertEqual(self.option(predecessor), "")  # a reload drops it
        self.run_tmux("set-option", "-g", predecessor, old_hook)
        self.uninstall(new)
        self.assertEqual(self.option("@resurrect-hook-post-save-layout"), "")  # not restored

    def test_a_users_own_script_named_resurrect_py_is_chained_and_restored(self):
        marker = self.temp / "fired"
        script = self.temp / "my-hooks/resurrect.py"
        script.parent.mkdir()
        script.write_text(f"open({str(marker)!r}, 'w').close()\n")
        mine = f"python3 {script} post-save-layout"
        self.run_tmux("set-option", "-g", "@resurrect-hook-post-save-layout", mine)
        copy = self.engine_copy()
        self.load_always(copy)
        self.assertEqual(self.save_hook().returncode, 0)
        self.assertTrue(marker.exists())
        self.uninstall(copy)
        self.assertEqual(self.option("@resurrect-hook-post-save-layout"), mine)

    def test_uninstall_keeps_resurrect_hooks_set_since(self):
        self.run_tmux("set-option", "-g", "@resurrect-hook-pre-restore-all", "echo mine")
        copy = self.engine_copy()
        self.load_always(copy)
        self.assertIn("resurrect.py", self.option("@resurrect-hook-pre-restore-all"))  # chained
        self.run_tmux("set-option", "-g", "@resurrect-hook-post-save-layout", "echo newer")
        self.uninstall(copy)
        self.assertEqual(self.option("@resurrect-hook-pre-restore-all"), "echo mine")
        self.assertEqual(self.option("@resurrect-hook-post-save-layout"), "echo newer")
        self.assertEqual(self.option("@resurrect-hook-post-restore-all"), "")

    def test_uninstall_keeps_window_border_settings(self):
        own = {"pane-border-lines": "double", "pane-border-indicators": "both", "pane-border-status": "bottom"}
        window = self.run_tmux("display-message", "-p", "#{window_id}")

        def set_own():
            for name, value in own.items():
                self.run_tmux("set-option", "-w", "-t", window, name, value)

        def local():
            return {name: self.run_tmux("show-option", "-wqv", "-t", window, name) for name in own}

        for mode in ("always", "zoom"):
            for when in ("before", "after"):
                with self.subTest(mode=mode, set=when):
                    self.run_tmux("kill-pane", "-a")
                    for name in own:
                        self.run_tmux("set-option", "-wu", "-t", window, name)
                    self.run_tmux("split-window", "-h")
                    if when == "before":
                        set_own()
                    copy = self.engine_copy()
                    if mode == "always":
                        self.load_always(copy)
                    else:
                        self.load(copy / ENTRY.name)
                    self.assertEqual(self.run_tmux("show-option", "-wqv", "-t", window, "pane-border-status"),
                                     "off" if mode == "always" else "top")  # the plugin's while loaded
                    if when == "after":
                        set_own()
                        self.run_tmux("split-window", "-v")  # the plugin leaves the user's alone
                        self.assertEqual(local(), own)
                    self.uninstall(copy)
                    self.assertEqual(local(), own)
                    self.assertEqual(self.run_tmux("show-options", "-w", "-t", window).count("@pane_clarity"), 0)

    def test_uninstall_returns_a_framed_pane_only_from_its_own_window(self):
        first = self.run_tmux("display-message", "-p", "#{pane_id}")
        other = self.run_tmux("new-window", "-P", "-F", "#{window_id}")
        pane = self.run_tmux("display-message", "-p", "-t", other, "#{pane_id}")
        self.run_tmux("split-window", "-h", "-t", other)
        copy = self.unbuilt_copy()
        self.load(copy / ENTRY.name)
        subprocess.run(["bash", str(copy / "scripts/framed_zoom.sh"), pane], env=self.env(), check=True)
        self.run_tmux("join-pane", "-d", "-s", pane, "-t", first)  # moved while framed: tiled now
        before = self.snapshot()["panes"]
        self.assertNotIn(" 1\n", before + "\n")
        self.uninstall(copy)
        self.assertEqual(self.snapshot()["panes"], before)  # no new frame
        self.assertEqual(self.option("@pane_clarity_dir"), "")

    def test_uninstall_without_the_engine_changes_nothing_and_can_be_run_again(self):
        self.users_own_setup()
        start = self.snapshot()
        copy = self.engine_copy()
        self.load_always(copy)
        before = self.snapshot()
        engine = copy / "target/release/pane-clarity"
        engine.rename(engine.with_suffix(".held"))  # e.g. cargo clean
        error = self.uninstall(copy, status=1)
        self.assertIn("the engine is missing", error)
        self.assertEqual(self.snapshot(), before)
        engine.with_suffix(".held").rename(engine)
        self.uninstall(copy)
        self.assertEqual(self.snapshot(), start)

    def test_disabling_a_window_framed_by_an_earlier_version(self):
        # Earlier versions also set the window's border lines and indicators
        # (state version 1): undone unless the user has changed them since.
        copy = self.engine_copy()
        self.run_tmux("split-window", "-h")
        self.load_always(copy)
        window = self.run_tmux("display-message", "-p", "#{window_id}")
        state = self.run_tmux("show-option", "-wqv", "-t", window, "@pane_clarity_layout")
        self.run_tmux("set-option", "-w", "-t", window, "@pane_clarity_layout", state.replace('"version":2', '"version":1'))
        self.run_tmux("set-option", "-w", "-t", window, "pane-border-lines", "single")
        self.run_tmux("set-option", "-w", "-t", window, "pane-border-indicators", "both")  # the user's since
        subprocess.run(["python3", str(copy / "scripts/always_mode.py"), "disable"], env=self.env(SYSTEM_PATH), check=True)
        self.assertEqual(self.run_tmux("show-option", "-wqv", "-t", window, "pane-border-lines"), "")
        self.assertEqual(self.run_tmux("show-option", "-wqv", "-t", window, "pane-border-indicators"), "both")

    def test_a_good_load_clears_an_earlier_problem(self):
        self.run_tmux("set-option", "-g", "@pane_clarity_error", "stale")
        self.load()
        self.assertEqual(self.option("@pane_clarity_error"), "")

    # Finding the real tmux --------------------------------------------------------
    def test_no_copy_takes_another_copys_adapter_for_the_real_tmux(self):
        other, copy = self.unbuilt_copy(), self.engine_copy()
        # The other copy's adapter says when it runs (it is still an adapter by
        # location: tmux-pane-clarity.tmux beside bin/), so a wrong choice shows
        # without starting a loop between two adapters.
        (other / "bin/tmux").write_text("#!/bin/sh\necho OTHER-ADAPTER\n")
        version = subprocess.run(["tmux", "-V"], capture_output=True, text=True).stdout.strip()
        # A symlink to the other copy's adapter file, in a folder of its own.
        linked = Path(tempfile.mkdtemp(dir=self.temp, prefix="linked-"))
        (linked / "tmux").symlink_to(other / "bin/tmux")

        def run(*args, path, real=None, cwd=None):
            env = {k: v for k, v in os.environ.items() if k != "PANE_CLARITY_REAL_TMUX"}
            env["PATH"] = path
            if real:
                env["PANE_CLARITY_REAL_TMUX"] = real
            return subprocess.run(args, env=env, cwd=cwd, capture_output=True, text=True, timeout=10)

        for name, command in (("shell adapter", [f"{copy}/bin/tmux", "-V"]),
                              ("engine", [f"{copy}/target/release/pane-clarity", "tmux", "-V"]),
                              ("python", ["python3", "-c", "import common, subprocess; "
                                          "subprocess.run([common.REAL_TMUX, '-V'])"])):
            for layout, adapter in (("bin folder", other / "bin"), ("symlinked file", linked)):
                with self.subTest(name, layout=layout):
                    cwd = copy / "scripts" if name == "python" else None
                    path = f"{adapter}{os.pathsep}{copy}/bin{os.pathsep}{SYSTEM_PATH}"
                    self.assertEqual(run(*command, path=path, cwd=cwd).stdout.strip(), version)
                    # ... nor when configured as the real one.
                    result = run(*command, path=path, cwd=cwd, real=f"{adapter}/tmux")
                    self.assertEqual(result.stdout.strip(), version)
        # Only adapters on PATH: an error, not a loop or another adapter.
        for adapter in (other / "bin", linked):
            with self.subTest("only adapters", adapter=adapter.name):
                result = run(f"{copy}/bin/tmux", "-V", path=f"{adapter}{os.pathsep}{copy}/bin")
                self.assertEqual((result.returncode, result.stdout), (127, ""))
                self.assertIn("only pane clarity adapters on PATH", result.stderr)

    # Integrations ---------------------------------------------------------------
    def recorder(self, name):
        """A script that writes its arguments, one per line, to a file."""
        out = self.temp / f"{name}.args"
        script = self.temp / name
        script.write_text(f'#!/bin/sh\nprintf "%s\\n" "{name}" "$@" > "{out}"\n')
        script.chmod(0o755)
        return script, out

    def shell_function(self, shell, source):
        adapter, adapter_out = self.recorder("adapter")
        fake_dir = Path(tempfile.mkdtemp(dir=self.temp, prefix="real-"))
        real, real_out = self.recorder("tmux")
        shutil.move(real, fake_dir / "tmux")
        path = f"{fake_dir}:{SYSTEM_PATH}"
        command = {"fish": ["fish", "--no-config", "-c", f"source {source}; tmux split-window 'a b'"],
                   "bash": ["bash", "-c", f". {source}; tmux split-window 'a b'"],
                   "zsh": ["zsh", "-f", "-c", f". {source}; tmux split-window 'a b'"]}[shell]
        subprocess.run(command, env={**os.environ, "PATH": path, "PANE_CLARITY_TMUX": str(adapter)}, check=True)
        self.assertEqual(adapter_out.read_text().splitlines(), ["adapter", "split-window", "a b"])
        env = {k: v for k, v in os.environ.items() if k != "PANE_CLARITY_TMUX"}
        subprocess.run(command, env={**env, "PATH": path}, check=True)  # no plugin: plain tmux
        self.assertEqual(real_out.read_text().splitlines(), ["tmux", "split-window", "a b"])

    def test_shell_integrations_route_through_the_adapter(self):
        for shell, source in (("fish", "integrations/tmux.fish"), ("bash", "integrations/tmux.sh"),
                              ("zsh", "integrations/tmux.sh")):
            with self.subTest(shell):
                if not shutil.which(shell):
                    self.skipTest(f"{shell} not installed")
                self.shell_function(shell, ROOT / source)

    def test_neovim_navigation(self):
        if not shutil.which("nvim"):
            self.skipTest("nvim not installed")
        result = subprocess.run(["nvim", "--headless", "-u", "NONE", "-i", "NONE", "-l", "tests/nvim_navigation.lua"],
                                cwd=ROOT, capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
