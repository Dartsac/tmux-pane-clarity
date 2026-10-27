//! Always-framed tmux layouts, one process per action. Key bindings and hooks
//! (installed by scripts/always_mode.py) run one action each; the window's
//! state lives in tmux options and a lock file serializes actions per server.
//!
//!   pane-clarity [-L NAME | -S PATH] ACTION TARGET [ARGS...]
//!   pane-clarity tmux [TMUX ARGS...]          (bin/tmux adapter)

// `cmd![...]` builds owned argv vectors; passing them by reference is intended.
#![allow(clippy::useless_vec)]

mod json;
mod manager;
mod tree;

use std::os::unix::fs::OpenOptionsExt;
use std::os::unix::process::CommandExt;
use std::path::PathBuf;
use std::process::{Command, Stdio};

pub const STATE: &str = "@pane_clarity_layout";
pub const BUSY: &str = "@pane_clarity_busy";
pub const ACTIVE: &str = "@pane_clarity_active";
/// Server-wide record of the panes this plugin frames (space-separated ids,
/// never reused while the server runs). It outlives their windows, so a save
/// hook can still tell that a window which closed mid-save was managed.
pub const FRAMED: &str = "@pane_clarity_framed";
pub const DIRECTIONS: [&str; 4] = ["-L", "-R", "-U", "-D"];
const ALIASES: [(&str, &str); 9] = [
    ("selectp", "select-pane"), ("splitw", "split-window"), ("resizep", "resize-pane"),
    ("selectl", "select-layout"), ("nextl", "next-layout"), ("lastp", "last-pane"),
    ("rotatew", "rotate-window"), ("swapp", "swap-pane"), ("killp", "kill-pane"),
];

#[derive(Debug)]
pub enum Error {
    /// Anything unexpected: the window falls back to native tiled view.
    Tmux(String),
    /// The user's command failed, but the layout was re-rendered.
    Native(String),
}

impl std::fmt::Display for Error {
    fn fmt(&self, f: &mut std::fmt::Formatter) -> std::fmt::Result {
        match self {
            Error::Tmux(s) | Error::Native(s) => f.write_str(s),
        }
    }
}

pub type R<T> = Result<T, Error>;

pub fn err<T>(message: impl Into<String>) -> R<T> {
    Err(Error::Tmux(message.into()))
}

pub fn alias(name: &str) -> &str {
    ALIASES.iter().find(|(a, _)| *a == name).map_or(name, |(_, full)| full)
}

pub fn is_managed(name: &str) -> bool {
    ALIASES.iter().any(|(_, full)| *full == name)
}

/// Whether `path` is a pane clarity bin/tmux adapter: this copy's or another's
/// (every copy has tmux-pane-clarity.tmux beside bin/). As the real tmux, one
/// would exec itself (or another) forever, or start a second engine that
/// waits on this one's lock.
fn is_adapter(path: &std::path::Path) -> bool {
    path.canonicalize()
        .ok()
        .and_then(|tmux| Some(tmux.parent()?.parent()?.join("tmux-pane-clarity.tmux").is_file()))
        .unwrap_or(false)
}

/// The real tmux: $PANE_CLARITY_REAL_TMUX, or the first `tmux` on PATH that
/// is not an adapter.
fn real_tmux() -> Option<String> {
    if let Ok(configured) = std::env::var("PANE_CLARITY_REAL_TMUX") {
        if !configured.is_empty() && !is_adapter(configured.as_ref()) {
            return Some(configured);
        }
    }
    let path = std::env::var_os("PATH")?;
    std::env::split_paths(&path).find_map(|dir| {
        // Relative entries (e.g. "bin") depend on the cwd and can name an adapter.
        if !dir.is_absolute() {
            return None;
        }
        let candidate = dir.join("tmux");
        if is_adapter(&candidate) {
            return None;
        }
        let executable = std::fs::metadata(&candidate)
            .is_ok_and(|m| m.is_file() && std::os::unix::fs::PermissionsExt::mode(&m.permissions()) & 0o111 != 0);
        executable.then(|| candidate.to_string_lossy().into_owned())
    })
}

pub struct Output {
    pub code: i32,
    pub stdout: String,
    pub stderr: String,
}

pub type Cmd = Vec<String>;

#[macro_export]
macro_rules! cmd {
    ($($arg:expr),* $(,)?) => { vec![$($arg.to_string()),*] };
}

/// tmux commands, one tmux client per call.
pub struct Tmux {
    pub real: String,
    pub flags: Vec<String>,
}

impl Tmux {
    pub fn new(real: &str, flags: &[String]) -> Tmux {
        Tmux { real: real.into(), flags: flags.to_vec() }
    }

    pub fn run_unchecked(&self, args: &[String]) -> Output {
        match Command::new(&self.real).args(&self.flags).args(args).stdin(Stdio::null()).output() {
            Ok(out) => Output {
                code: out.status.code().unwrap_or(-1),
                stdout: String::from_utf8_lossy(&out.stdout).into_owned(),
                stderr: String::from_utf8_lossy(&out.stderr).into_owned(),
            },
            Err(e) => Output { code: -1, stdout: String::new(), stderr: format!("{}: {e}", self.real) },
        }
    }

    pub fn run(&self, args: &[String]) -> R<Output> {
        let out = self.run_unchecked(args);
        if out.code != 0 {
            let message = [out.stderr.trim(), out.stdout.trim()].into_iter().find(|s| !s.is_empty());
            return err(message.map_or_else(|| format!("{args:?}"), str::to_string));
        }
        Ok(out)
    }

    pub fn text(&self, args: &[String]) -> R<String> {
        Ok(self.run(args)?.stdout.trim_end_matches('\n').to_string())
    }

    /// Run commands in one client round trip; tmux stops at the first failure.
    pub fn batch(&self, commands: &[Cmd]) -> R<()> {
        let mut argv = Vec::new();
        for command in commands {
            if !argv.is_empty() {
                argv.push(";".to_string());
            }
            argv.extend(command.iter().cloned());
        }
        if !argv.is_empty() {
            self.run(&argv)?;
        }
        Ok(())
    }

    pub fn unset(&self, window: &str, name: &str) {
        self.run_unchecked(&cmd!["set-option", "-wu", "-t", window, name]);
    }

    fn socket(&self) -> R<String> {
        if self.flags.is_empty() {
            if let Ok(tmux) = std::env::var("TMUX") {
                if !tmux.is_empty() {
                    return Ok(tmux.split(',').next().unwrap_or_default().to_string());
                }
            }
        }
        self.text(&cmd!["display-message", "-p", "#{socket_path}"])
    }
}

/// Actions started by tmux hooks. They can run *inside* another action: a
/// native command the engine runs under the lock (e.g. a cross-window
/// `kill-pane`) fires after-* hooks that block that command until they
/// finish, so a hook waiting for the lock would deadlock the server's
/// layout handling (and every later hook and engine key binding).
const DEFERRABLE: [&str; 3] = ["hook", "focus", "fit-window"];
/// Set on a deferred re-run, which may wait for the lock: nothing waits on it.
const DEFERRED_ENV: &str = "PANE_CLARITY_DEFERRED";

/// Serialize operations per server. The socket's directory is private to the user.
/// With `wait` false, returns None instead of blocking when the lock is held.
fn server_lock(tmux: &Tmux, wait: bool) -> R<Option<std::fs::File>> {
    #[cfg(target_os = "macos")]
    const O_NOFOLLOW: i32 = 0x0100;
    #[cfg(all(target_os = "linux", any(target_arch = "aarch64", target_arch = "arm")))]
    const O_NOFOLLOW: i32 = 0o100000;
    #[cfg(not(any(target_os = "macos", all(target_os = "linux", any(target_arch = "aarch64", target_arch = "arm")))))]
    const O_NOFOLLOW: i32 = 0o400000;
    const LOCK_EX: i32 = 2;
    const LOCK_NB: i32 = 4;
    extern "C" {
        fn flock(fd: i32, operation: i32) -> i32;
    }
    let path = PathBuf::from(tmux.socket()? + ".clarity-lock");
    let file = std::fs::OpenOptions::new()
        .read(true)
        .write(true)
        .create(true)
        .mode(0o600)
        .custom_flags(O_NOFOLLOW)
        .open(&path)
        .map_err(|e| Error::Tmux(format!("{}: {e}", path.display())))?;
    use std::os::fd::AsRawFd;
    let operation = if wait { LOCK_EX } else { LOCK_EX | LOCK_NB };
    loop {
        // SAFETY: plain syscall on a file descriptor we own.
        if unsafe { flock(file.as_raw_fd(), operation) } == 0 {
            return Ok(Some(file));
        }
        let error = std::io::Error::last_os_error();
        match error.kind() {
            std::io::ErrorKind::Interrupted => continue,
            std::io::ErrorKind::WouldBlock if !wait => return Ok(None),
            _ => return err(format!("{}: {error}", path.display())),
        }
    }
}

/// Re-run a hook action in a detached process that waits for the lock, so
/// the tmux command that fired the hook can finish. Its output goes nowhere:
/// an inherited pipe would keep run-shell (and so the hook) waiting.
fn defer(tmux: &Tmux, action: &str, target: &str, args: &[String]) -> R<()> {
    let exe = std::env::current_exe().map_err(|e| Error::Tmux(format!("cannot defer {action}: {e}")))?;
    Command::new(exe)
        .args(&tmux.flags)
        .arg(action)
        .arg(target)
        .args(args)
        .env(DEFERRED_ENV, "1")
        .env("PANE_CLARITY_REAL_TMUX", &tmux.real)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .process_group(0)
        .spawn()
        .map_err(|e| Error::Tmux(format!("cannot defer {action}: {e}")))?;
    Ok(())
}

/// Global tmux flags before the command: -L/-S (and the other value flags).
fn split_flags(args: &[String]) -> (Vec<String>, Vec<String>) {
    let (mut flags, mut i) = (Vec::new(), 0);
    while i < args.len() && args[i].starts_with('-') {
        flags.push(args[i].clone());
        if matches!(args[i].as_str(), "-S" | "-L" | "-f" | "-T" | "-c") && i + 1 < args.len() {
            i += 1;
            flags.push(args[i].clone());
        }
        i += 1;
    }
    (flags, args[i..].to_vec())
}

/// The value of option `-wanted` of a managed command, scanning only the
/// options before its first positional argument, as tmux 3.8 parses them
/// (`list-commands`). Flags that take no value differ per command: `-R` is a
/// direction for select-pane but a style for split-window, and resize-pane's
/// -D/-L/-R/-U take an optional adjustment (a value only if it isn't an option).
pub fn option_value(command: &str, args: &[String], wanted: char) -> Option<String> {
    let (values, optional) = match command {
        "select-pane" => ("Tt", ""),
        "split-window" => ("BcefFlmpRsSTt", ""),
        "resize-pane" => ("txy", "DLRU"),
        "swap-pane" => ("st", ""),
        "kill-pane" => ("ft", ""),
        _ => ("t", ""),
    };
    let mut found = None;
    let mut i = 0;
    while i < args.len() && args[i].len() > 1 && args[i].starts_with('-') && args[i] != "--" {
        let option = &args[i][1..];
        for (n, letter) in option.char_indices() {
            let attached = &option[n + letter.len_utf8()..];
            let value = if values.contains(letter) {
                if attached.is_empty() {
                    i += 1;
                    args.get(i).cloned()
                } else {
                    Some(attached.to_string())
                }
            } else if optional.contains(letter) && attached.is_empty() {
                match args.get(i + 1) {
                    Some(next) if !next.starts_with('-') => {
                        i += 1;
                        Some(next.clone())
                    }
                    _ => None,
                }
            } else if optional.contains(letter) {
                Some(attached.to_string())
            } else {
                continue;
            };
            if letter == wanted {
                found = value;
            }
            break;
        }
        i += 1;
    }
    found
}

/// `bin/tmux` entry for managed command names. Preserves literal argv and
/// explicit server selection (e.g. vim-tmux-navigator's `tmux -S SOCKET`).
fn adapter(real: &str, argv: &[String]) -> i32 {
    let (flags, args) = split_flags(argv);
    let passthrough = || -> i32 {
        let error = Command::new(real).args(&flags).args(&args).exec();
        eprintln!("pane-clarity: {real}: {error}");
        127
    };
    let Some((command, rest)) = args.split_first() else { return passthrough() };
    let command = alias(command);
    if !is_managed(command)
        || rest.iter().any(|a| a == ";")
        || (command == "select-pane" && !rest.iter().any(|a| DIRECTIONS.contains(&a.as_str()) || a == "-l"))
    {
        return passthrough();
    }
    let target = option_value(command, rest, 't').or_else(|| std::env::var("TMUX_PANE").ok()).unwrap_or_default();
    let tmux = Tmux::new(real, &flags);
    let describe = |target: &str| {
        let mut query = cmd!["display-message", "-p"];
        if !target.is_empty() {
            query.extend(cmd!["-t", target]);
        }
        query.push(format!("#{{pane_id}}\t#{{?#{{{STATE}}},1,}}"));
        match tmux.text(&query) {
            Ok(out) => match out.split_once('\t') {
                Some((pane, managed)) => (pane.to_string(), managed == "1"),
                None => (String::new(), false),
            },
            Err(_) => (String::new(), false),
        }
    };
    let (pane, mut managed) = describe(&target);
    if command == "swap-pane" && !managed {
        // A managed source window still needs its frames carried over.
        managed = option_value(command, rest, 's').is_some_and(|source| describe(&source).1);
    }
    if !managed {
        return passthrough();
    }
    let target = if target.is_empty() { pane } else { target };
    let mut action = cmd!["command", target, command];
    action.extend(rest.iter().cloned());
    run(&tmux, &action)
}

pub fn report(tmux: &Tmux, error: &Error) -> i32 {
    let message = format!("Pane clarity: {error}");
    eprintln!("{message}");
    tmux.run_unchecked(&cmd!["display-message", message]);
    1
}

/// One action under the server lock, with fail-open recovery.
fn action(tmux: &Tmux, action: &str, target: &str, args: &[String]) -> i32 {
    let outcome = (|| -> R<()> {
        let wait = !DEFERRABLE.contains(&action) || std::env::var_os(DEFERRED_ENV).is_some();
        let Some(_lock) = server_lock(tmux, wait)? else {
            return defer(tmux, action, target, args);
        };
        if let Some(out) = locked(tmux, action, target, args)? {
            print!("{}", out.stdout);
        }
        Ok(())
    })();
    match outcome {
        Ok(()) => 0,
        Err(error) => report(tmux, &error),
    }
}

/// An action's body; the caller holds the server lock. A managed command's
/// own -t, when given, names the pane or window it acts on.
pub fn locked(tmux: &Tmux, action: &str, target: &str, args: &[String]) -> R<Option<Output>> {
    let explicit = match (action, args.split_first()) {
        ("command", Some((command, rest))) => option_value(alias(command), rest, 't'),
        _ => None,
    };
    let target = explicit.as_deref().unwrap_or(target);
    let mut m = match manager::Manager::new(tmux, target) {
        Ok(m) => m,
        // The window closed before the hook ran.
        Err(_) if matches!(action, "hook" | "focus" | "fit-window") => return Ok(None),
        Err(e) => return Err(e),
    };
    let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| m.dispatch(action, args)))
        .unwrap_or_else(|panic| {
            let text = panic.downcast_ref::<String>().cloned()
                .or_else(|| panic.downcast_ref::<&str>().map(|s| s.to_string()))
                .unwrap_or_else(|| "internal error".into());
            err(text)
        });
    let result = match result {
        Err(Error::Tmux(message)) => {
            if m.state.is_some() {
                m.recover();
            }
            Err(Error::Tmux(message))
        }
        other => other,
    };
    if !m.released {
        tmux.unset(&m.window, BUSY);
    }
    result
}

fn run(tmux: &Tmux, argv: &[String]) -> i32 {
    if argv.len() < 2 {
        eprintln!("usage: pane-clarity [-L NAME|-S PATH] enable|disable|refresh|hook|zoom|focus|fit|mouse-resize|command TARGET [ARGS...]\n       pane-clarity tmux [TMUX ARGS...]");
        return 2;
    }
    let (name, target, args) = (argv[0].as_str(), argv[1].as_str(), &argv[2..]);
    if name == "fit" {
        // Target is a session; args: client width, client height, status option.
        let fit = || -> R<i32> {
            let [width, height, status] = args else { return err("fit: expected WIDTH HEIGHT STATUS") };
            let lines = match status.as_str() {
                "off" => 0,
                "on" => 1,
                other => other.parse().map_err(|_| Error::Tmux(format!("invalid status: {other}")))?,
            };
            let height: i64 = height.parse::<i64>().map_err(|_| Error::Tmux(format!("invalid height: {height}")))? - lines;
            let windows = tmux.text(&cmd!["list-windows", "-t", target, "-F", "#{window_id}"])?;
            Ok(windows
                .split_whitespace()
                .map(|w| action(tmux, "fit-window", w, &cmd![width, height]))
                .max()
                .unwrap_or(0))
        };
        return fit().unwrap_or_else(|e| report(tmux, &e));
    }
    action(tmux, name, target, args)
}

fn main() {
    // Panics are caught per action and reported as a normal failure.
    std::panic::set_hook(Box::new(|_| {}));
    let argv: Vec<String> = std::env::args().skip(1).collect();
    let Some(real) = real_tmux() else {
        eprintln!("pane-clarity: cannot find the real tmux binary (only pane clarity adapters on PATH)");
        std::process::exit(127);
    };
    if argv.first().map(String::as_str) == Some("tmux") {
        std::process::exit(adapter(&real, &argv[1..]));
    }
    let (flags, rest) = split_flags(&argv);
    std::process::exit(run(&Tmux::new(&real, &flags), &rest));
}

#[cfg(test)]
mod tests {
    use super::*;

    fn target(command: &str, args: &str) -> Option<String> {
        let args: Vec<String> = args.split_whitespace().map(String::from).collect();
        option_value(command, &args, 't')
    }

    #[test]
    fn option_value_follows_each_commands_flags() {
        // Directions are plain flags for select-pane...
        assert_eq!(target("select-pane", "-R -t %0").as_deref(), Some("%0"));
        assert_eq!(target("select-pane", "-Zt%2").as_deref(), Some("%2"));
        // ...an optional adjustment for resize-pane...
        assert_eq!(target("resize-pane", "-D -t %0").as_deref(), Some("%0"));
        assert_eq!(target("resize-pane", "-D 3 -t %0").as_deref(), Some("%0"));
        assert_eq!(target("resize-pane", "-D3 -t %0").as_deref(), Some("%0"));
        // ...and -R takes a style for split-window.
        assert_eq!(target("split-window", "-R -t -h").as_deref(), None);
        assert_eq!(target("split-window", "-R fg=red -t %1").as_deref(), Some("%1"));
        // Only options before the first positional argument count.
        assert_eq!(target("split-window", "-h sh -c -t").as_deref(), None);
        assert_eq!(target("swap-pane", "-d -s %3 -t %4").as_deref(), Some("%4"));
        let args: Vec<String> = ["-s", "%3", "-t", "%4"].map(String::from).to_vec();
        assert_eq!(option_value("swap-pane", &args, 's').as_deref(), Some("%3"));
    }
}
