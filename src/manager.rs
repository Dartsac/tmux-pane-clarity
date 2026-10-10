//! Window manager: reads a window in one round trip, edits the logical tree
//! and renders it as floating frames in one batch.

use crate::json::{self, opt_str, Value};
use crate::tree::{self, frame_boxes, leaves, leaves_mut, lookup, pane, prune, reflow, Node, Pane, Panes, NATIVE_GAPS};
use crate::{cmd, err, Cmd, Error, Output, Tmux, ACTIVE, BUSY, DIRECTIONS, FRAMED, R, STATE};

/// The window's pane-border-status as this plugin last set it, and the
/// window's own value from before (scripts/owned.sh has the same rule).
const BORDER_STATUS: &str = "@pane_clarity_border_status";
const ORIGINAL_BORDER_STATUS: &str = "@pane_clarity_original_border_status";
/// States saved by versions that also set these window options on enable.
const LEGACY_WINDOW_OPTIONS: [(&str, &str); 2] = [("pane-border-lines", "single"), ("pane-border-indicators", "off")];

const PANE_FORMAT: &str = "#{pane_id}\t#{pane_index}\t#{pane_floating_flag}\t#{pane_active}\
                           \t#{pane_left}\t#{pane_top}\t#{pane_width}\t#{pane_height}";

/// The saved window state (@pane_clarity_layout). Unknown keys are kept.
#[derive(Clone, Debug)]
pub struct State {
    version: Value,
    root: Option<Node>,
    active: Option<String>,
    order: Vec<String>,
    last: Option<String>,
    zoom: Option<String>,
    extra: Vec<(String, Value)>,
}

impl State {
    fn parse(raw: &str) -> R<State> {
        let value = json::parse(raw).map_err(Error::Tmux)?;
        let Value::Obj(items) = value else { return err("invalid saved layout") };
        let text = |v: &Value| v.as_str().map(str::to_string);
        let mut state = State {
            version: Value::Int(1), root: None, active: None, order: Vec::new(), last: None, zoom: None, extra: Vec::new(),
        };
        let mut root = None;
        for (key, v) in items {
            match key.as_str() {
                "version" => state.version = v,
                "root" => root = Some(v),
                "active" => state.active = text(&v),
                "order" => {
                    if let Value::Arr(list) = &v {
                        state.order = list.iter().filter_map(text).collect();
                    }
                }
                "last" => state.last = text(&v),
                "zoom" => state.zoom = text(&v),
                _ => state.extra.push((key, v)),
            }
        }
        let root = root.ok_or_else(|| Error::Tmux("saved layout has no root".into()))?;
        state.root = Some(Node::from_json(&root)?);
        Ok(state)
    }

    fn dump(&self) -> String {
        let mut items = vec![
            ("version".to_string(), self.version.clone()),
            ("root".to_string(), self.root.as_ref().map_or(Value::Null, Node::to_json)),
            ("active".to_string(), opt_str(&self.active)),
            ("order".to_string(), Value::Arr(self.order.iter().map(|p| Value::Str(p.clone())).collect())),
            ("last".to_string(), opt_str(&self.last)),
            ("zoom".to_string(), opt_str(&self.zoom)),
        ];
        items.extend(self.extra.iter().cloned());
        Value::Obj(items).dump()
    }

    fn root(&self) -> R<&Node> {
        self.root.as_ref().ok_or_else(|| Error::Tmux("empty layout".into()))
    }

    fn root_mut(&mut self) -> R<&mut Node> {
        self.root.as_mut().ok_or_else(|| Error::Tmux("empty layout".into()))
    }

    fn remember(&mut self, pane: &str) {
        let mut order = vec![pane.to_string()];
        order.extend(self.order.iter().filter(|p| *p != pane).cloned());
        self.last = order.get(1).cloned();
        self.order = order;
        self.active = Some(pane.to_string());
    }
}

fn parse_panes(rows: &str) -> R<Panes> {
    rows.lines()
        .map(|line| {
            let fields: Vec<&str> = line.split('\t').collect();
            let [id, index, floating, active, x, y, w, h] = fields[..] else {
                return err(format!("unexpected list-panes row: {line:?}"));
            };
            let int = |s: &str| s.parse::<i64>().map_err(|_| Error::Tmux(format!("invalid number {s:?}")));
            Ok((id.to_string(), Pane {
                index: int(index)?, floating: floating == "1", active: active == "1",
                x: int(x)?, y: int(y)?, w: int(w)?, h: int(h)?,
            }))
        })
        .collect()
}

pub struct Manager<'a> {
    t: &'a Tmux,
    pub released: bool,
    pub window: String,
    target: String,
    size: (i64, i64),
    zoomed: bool,
    tiled: Node,
    pub state: Option<State>,
    panes: Panes,
    framed: Vec<String>, // FRAMED, as read
}

impl<'a> Manager<'a> {
    pub fn new(t: &'a Tmux, target: &str) -> R<Manager<'a>> {
        let mut m = Manager {
            t, released: false, window: String::new(), target: String::new(), size: (0, 0), zoomed: false,
            tiled: Node::default(), state: None, panes: Vec::new(), framed: Vec::new(),
        };
        if let Err(e) = m.read(target, true) {
            if m.window.is_empty() {
                t.unset(target, BUSY); // nothing usable: at most the flag was set
            } else {
                // The window exists but its saved state is unusable: fail open
                // rather than leave it busy (which suppresses its hooks).
                m.recover();
                t.unset(&m.window, BUSY);
            }
            return Err(e);
        }
        Ok(m)
    }

    /// One round trip: window geometry, native layout, saved state and panes.
    fn read(&mut self, target: &str, busy: bool) -> R<()> {
        let head = format!(
            "#{{window_id}}\t#{{pane_id}}\t#{{window_width}}\t#{{window_height}}\t#{{window_zoomed_flag}}\t#{{window_layout}}\t#{{{STATE}}}\t#{{{FRAMED}}}"
        );
        let mut args = if busy { cmd!["set-option", "-w", "-t", target, BUSY, "1", ";"] } else { Vec::new() };
        args.extend(cmd!["display-message", "-p", "-t", target, head, ";", "list-panes", "-t", target, "-F", PANE_FORMAT]);
        let out = self.t.text(&args)?;
        let (first, rows) = out.split_once('\n').unwrap_or((&out, ""));
        let fields: Vec<&str> = first.split('\t').collect();
        let [window, pane, w, h, zoomed, tiled, raw, framed] = fields[..] else {
            return err(format!("unexpected window description: {first:?}"));
        };
        let int = |s: &str| s.parse::<i64>().map_err(|_| Error::Tmux(format!("invalid number {s:?}")));
        self.window = window.into();
        self.target = pane.into();
        self.size = (int(w)?, int(h)?);
        self.zoomed = zoomed == "1";
        self.framed = framed.split_whitespace().map(str::to_string).collect();
        self.panes = parse_panes(rows)?; // before anything that can fail: recover() needs them
        let layout = json::parse(tiled).map_err(Error::Tmux)?;
        self.tiled = Node::from_json(layout.get("L").ok_or_else(|| Error::Tmux("layout without L".into()))?)?;
        self.state = if raw.is_empty() { None } else { Some(State::parse(raw)?) };
        Ok(())
    }

    fn active(&self) -> R<String> {
        self.panes.iter().find(|(_, info)| info.active).map(|(p, _)| p.clone())
            .ok_or_else(|| Error::Tmux("no active pane".into()))
    }

    fn any_floating(&self) -> bool {
        self.panes.iter().any(|(_, info)| info.floating)
    }

    fn has_pane(&self, id: &str) -> bool {
        pane(&self.panes, id).is_some()
    }

    fn saved(&self) -> R<State> {
        self.state.clone().ok_or_else(|| Error::Tmux("window is not managed".into()))
    }

    /// An action's last batch: also clears the busy flag, saving a round trip.
    /// If the batch fails, the caller clears the flag instead.
    fn finish(&mut self, mut commands: Vec<Cmd>) -> R<()> {
        commands.push(cmd!["set-option", "-wu", "-t", self.window, BUSY]);
        self.t.batch(&commands)?;
        self.released = true;
        Ok(())
    }

    fn save_commands(&self, state: &State) -> Vec<Cmd> {
        let mut commands = vec![
            cmd!["set-option", "-w", "-t", self.window, STATE, state.dump()],
            cmd!["set-option", "-w", "-t", self.window, ACTIVE, state.active.clone().unwrap_or_default()],
        ];
        let leaves = state.root.as_ref().map(|root| leaves(root).into_iter().map(|n| n.id.clone()).collect()).unwrap_or_default();
        commands.extend(self.framed_commands(leaves, &[]));
        commands
    }

    /// Record `added` as framed and `removed` as not; nothing if unchanged.
    fn framed_commands(&self, added: Vec<String>, removed: &[String]) -> Option<Cmd> {
        let mut framed: Vec<String> = self.framed.iter().filter(|p| !removed.contains(p)).cloned().collect();
        framed.extend(added.into_iter().filter(|p| !self.framed.contains(p)));
        framed.dedup();
        if framed == self.framed {
            None
        } else if framed.is_empty() {
            Some(cmd!["set-option", "-gu", FRAMED])
        } else {
            Some(cmd!["set-option", "-g", FRAMED, framed.join(" ")])
        }
    }

    /// The window leaves management: its panes are no longer framed.
    fn unframed_command(&self) -> Option<Cmd> {
        let mut ids: Vec<String> = self.panes.iter().map(|(p, _)| p.clone()).collect();
        if let Some(root) = self.state.as_ref().and_then(|s| s.root.as_ref()) {
            ids.extend(leaves(root).into_iter().map(|n| n.id.clone()));
        }
        self.framed_commands(Vec::new(), &ids)
    }

    /// Match the tree to the window's actual panes, then fit it to the window.
    fn reconcile(&self, mut state: State) -> R<State> {
        // With nothing floating (e.g. a lone pane was split) tmux's layout is exact.
        let ids: Vec<String> = self.panes.iter().map(|(p, _)| p.clone()).collect();
        let mut root = if self.any_floating() { prune(state.root()?, &ids) } else { Some(self.tiled.clone()) };
        for id in &ids {
            if lookup(&tree::boxes(root.as_ref()), id).is_none() {
                root = tree::adopt(root.as_ref(), id, &self.panes, state.active.as_deref(), self.size)?;
            }
        }
        let root = root.ok_or_else(|| Error::Tmux("empty layout".into()))?;
        state.root = Some(reflow(&root, 0, 0, self.size.0, self.size.1, NATIVE_GAPS)?);
        state.order.retain(|p| self.has_pane(p));
        if state.zoom.as_deref().is_some_and(|z| !self.has_pane(z)) {
            state.zoom = None;
        }
        if !state.active.as_deref().is_some_and(|a| self.has_pane(a)) {
            state.remember(&self.active()?);
        }
        Ok(state)
    }

    /// Render the tree as floats with only the changes needed, in one call.
    /// A lone pane stays natively tiled, without a frame, until it is split.
    /// The state is saved first so recovery information precedes mutation.
    fn frame(&mut self, mut state: State, extra: Vec<Cmd>) -> R<()> {
        let (width, height) = self.size;
        state.root = Some(reflow(state.root()?, 0, 0, width, height, NATIVE_GAPS)?); // validate before mutating
        let single = self.panes.len() == 1;
        if single {
            state.zoom = None;
        }
        let zoom = state.zoom.clone();
        let mut commands = self.save_commands(&state);
        commands.extend(extra);
        if self.zoomed {
            commands.push(cmd!["resize-pane", "-Z", "-t", self.window]);
        }
        if single {
            let (id, info) = &self.panes[0];
            if info.floating {
                commands.push(cmd!["join-pane", "-d", "-s", id, "-t", id]);
            }
            return self.finish(commands);
        }
        for (id, b) in frame_boxes(state.root()?, self.size)? {
            let (x, y, w, h) = if Some(&id) == zoom.as_ref() { (0, 0, width, height) } else { (b.x, b.y, b.w, b.h) };
            let now = *pane(&self.panes, &id).ok_or_else(|| Error::Tmux(format!("unknown pane {id}")))?;
            if !now.floating {
                commands.push(cmd!["break-pane", "-dW", "-s", id, "-x", w, "-y", h, "-X", x, "-Y", y]);
            } else if (now.x, now.y, now.w, now.h) != (x + 1, y + 1, w - 2, h - 2) {
                // tmux clamps floats to the window: size, position, then size again.
                let resize = cmd!["resize-pane", "-t", id, "-x", w, "-y", h];
                commands.push(resize.clone());
                commands.push(cmd!["move-pane", "-t", id, "-X", x, "-Y", y]);
                commands.push(resize);
            }
        }
        if let Some(z) = &zoom {
            commands.push(cmd!["move-pane", "-t", z, "-P", "front"]);
        }
        if let Some(active) = &state.active {
            if *active != self.active()? {
                commands.push(cmd!["select-pane", "-t", active]);
            }
        }
        self.finish(commands)
    }

    /// Return every pane to native tiled view in the tree's geometry.
    fn tile(&self, state: &State) -> R<()> {
        let mut root = reflow(state.root()?, 0, 0, self.size.0, self.size.1, NATIVE_GAPS)?;
        for leaf in leaves_mut(&mut root) {
            leaf.index = pane(&self.panes, &leaf.id).ok_or_else(|| Error::Tmux(leaf.id.clone()))?.index;
        }
        let mut commands = Vec::new();
        if self.zoomed {
            commands.push(cmd!["resize-pane", "-Z", "-t", self.window]);
        }
        for (id, info) in &self.panes {
            if info.floating {
                commands.push(cmd!["join-pane", "-d", "-s", id, "-t", id]);
            }
        }
        let layout = Value::Obj(vec![("V".into(), Value::Int(2)), ("L".into(), root.to_json())]);
        commands.push(cmd!["select-layout", "-t", self.window, layout.dump()]);
        commands.push(cmd!["select-pane", "-t", state.active.clone().unwrap_or_default()]);
        self.t.batch(&commands)
    }

    /// Set the window's pane-border-status to `value`, unless the user has set
    /// it since this plugin did; the first time, keep the window's own value.
    fn border_status(&self, value: &str) -> R<Vec<Cmd>> {
        let format = format!("#{{{BORDER_STATUS}}}\t#{{pane-border-status}}");
        let now = self.t.text(&cmd!["display-message", "-p", "-t", self.window, format])?;
        let (set, current) = now.split_once('\t').unwrap_or((&now, ""));
        let mut commands = Vec::new();
        if set.is_empty() {
            let own = self.t.text(&cmd!["show-option", "-wqv", "-t", self.window, "pane-border-status"])?;
            if !own.is_empty() {
                commands.push(cmd!["set-option", "-w", "-t", self.window, ORIGINAL_BORDER_STATUS, own]);
            }
        } else if set != current {
            return Ok(commands);
        }
        commands.push(cmd!["set-option", "-w", "-t", self.window, "pane-border-status", value]);
        commands.push(cmd!["set-option", "-w", "-t", self.window, BORDER_STATUS, value]);
        Ok(commands)
    }

    /// The status a tiled window shows in zoom mode: a title line between splits.
    fn tiled_border_status(&self) -> R<Vec<Cmd>> {
        self.border_status(if self.panes.len() > 1 { "top" } else { "off" })
    }

    // Actions ------------------------------------------------------------------

    pub fn dispatch(&mut self, action: &str, args: &[String]) -> R<Option<Output>> {
        if action == "enable" {
            self.enable()?;
            return Ok(None);
        }
        if action == "command" {
            let (name, rest) = args.split_first().ok_or_else(|| Error::Tmux("command: missing tmux command".into()))?;
            return self.command(name, rest);
        }
        if self.state.is_none() {
            return Ok(None); // not (or no longer) managed
        }
        match action {
            "disable" => self.disable()?,
            "refresh" | "hook" => self.refresh()?,
            "zoom" => self.zoom()?,
            "focus" => self.focus()?,
            "fit-window" => {
                let int = |i: usize| -> R<i64> {
                    args.get(i).and_then(|s| s.parse().ok()).ok_or_else(|| Error::Tmux("fit-window: expected WIDTH HEIGHT".into()))
                };
                self.fit(int(0)?, int(1)?)?
            }
            "mouse-resize" => self.mouse_resize()?,
            other => return err(format!("Unknown action: {other}")),
        }
        Ok(None)
    }

    fn enable(&mut self) -> R<()> {
        if self.state.is_some() {
            return self.refresh();
        }
        if self.any_floating() {
            return err("Return existing floating/framed-zoom panes to tiled view before enabling always-framed mode.");
        }
        let mut ranked: Vec<&Node> = leaves(&self.tiled);
        ranked.sort_by_key(|n| if n.active { -1 } else { n.last.unwrap_or(1_000_000) });
        let order: Vec<String> = ranked.iter().map(|n| n.id.clone()).collect();
        let state = State {
            version: Value::Int(2), root: Some(self.tiled.clone()), active: Some(self.active()?),
            last: order.get(1).cloned(), order, zoom: None, extra: Vec::new(),
        };
        // Frames have their own edges: no title line.
        let extra = self.border_status("off")?;
        self.frame(state, extra)
    }

    fn disable(&mut self) -> R<()> {
        let state = self.reconcile(self.saved()?)?;
        self.tile(&state)?;
        let mut commands = vec![
            cmd!["set-option", "-wu", "-t", self.window, STATE],
            cmd!["set-option", "-wu", "-t", self.window, ACTIVE],
        ];
        // pane-border-status is the plugin's in zoom mode too: set for the
        // tiled window, if it is still the plugin's.
        commands.extend(self.tiled_border_status()?);
        if state.version == Value::Int(1) {
            // Enabled by an earlier version, which also set these: undo them
            // unless the user has changed them since.
            let format = LEGACY_WINDOW_OPTIONS.map(|(option, _)| format!("#{{{option}}}")).join("\t");
            let now = self.t.text(&cmd!["display-message", "-p", "-t", self.window, format])?;
            for ((option, value), current) in LEGACY_WINDOW_OPTIONS.iter().zip(now.split('\t')) {
                if current == *value {
                    commands.push(cmd!["set-option", "-wu", "-t", self.window, option]);
                }
            }
        }
        commands.extend(self.unframed_command());
        self.finish(commands)
    }

    fn refresh(&mut self) -> R<()> {
        let mut state = self.reconcile(self.saved()?)?;
        state.remember(&self.active()?);
        self.frame(state, Vec::new())
    }

    /// tmux 3.8 never shrinks a window whose panes all float, so a smaller
    /// terminal would leave frames off-screen. Tiling one pane lets tmux size
    /// the window for its clients again; then frame at the new size.
    fn fit(&mut self, width: i64, height: i64) -> R<()> {
        if (self.size.0 <= width && self.size.1 <= height) || !self.any_floating() {
            return Ok(());
        }
        let first = self.panes[0].0.clone();
        self.t.run(&cmd!["join-pane", "-d", "-s", first, "-t", first])?;
        let window = self.window.clone();
        self.read(&window, false)?;
        self.refresh()
    }

    fn focus(&mut self) -> R<()> {
        let active = self.active()?;
        let mut state = self.saved()?;
        if state.active.as_deref() != Some(active.as_str()) {
            state.remember(&active);
            let commands = self.save_commands(&state);
            self.finish(commands)?;
        }
        Ok(())
    }

    fn select(&mut self, direction: &str) -> R<()> {
        let mut state = self.reconcile(self.saved()?)?;
        let active = self.active()?;
        if state.active.as_deref() != Some(active.as_str()) {
            state.remember(&active);
        }
        let boxes = tree::boxes(state.root.as_ref());
        let dest = if direction == "-l" {
            state.last.clone()
        } else {
            tree::neighbour(&boxes, &self.target, direction, self.size, &state.order)?
        };
        if let Some(dest) = dest {
            if lookup(&boxes, &dest).is_some() && dest != active {
                state.remember(&dest);
                state.zoom = None;
            }
        }
        self.frame(state, Vec::new())
    }

    fn zoom(&mut self) -> R<()> {
        let mut state = self.reconcile(self.saved()?)?;
        let target = self.target.clone();
        state.remember(&target);
        state.zoom = if state.zoom.as_deref().is_some_and(|z| !z.is_empty()) { None } else { Some(target) };
        self.frame(state, Vec::new())
    }

    fn resize(&mut self, direction: &str, amount: i64) -> R<()> {
        let mut state = self.reconcile(self.saved()?)?;
        state.zoom = None;
        let target = self.target.clone();
        tree::resize_pane(state.root_mut()?, &target, direction, amount);
        self.frame(state, Vec::new())
    }

    /// Reconcile a native floating-border drag into the tree. A drag that only
    /// moved the float snaps back.
    fn mouse_resize(&mut self) -> R<()> {
        let mut state = self.reconcile(self.saved()?)?;
        let framed = frame_boxes(state.root()?, self.size)?;
        let (b, now) = (lookup(&framed, &self.target).copied(), pane(&self.panes, &self.target).copied());
        if let (Some(b), Some(now)) = (b, now) {
            if now.floating && !state.zoom.as_deref().is_some_and(|z| !z.is_empty()) {
                let (left, right) = (now.x - 1 - b.x, now.x + now.w + 1 - (b.x + b.w));
                let (top, bottom) = (now.y - 1 - b.y, now.y + now.h + 1 - (b.y + b.h));
                if (left, top) != (right, bottom) {
                    let target = self.target.clone();
                    let root = state.root_mut()?;
                    for (kind, after, delta) in [(b'h', false, left), (b'h', true, right), (b'v', false, top), (b'v', true, bottom)] {
                        if delta != 0 {
                            tree::move_edge(root, &target, kind, after, delta);
                        }
                    }
                }
            }
        }
        self.frame(state, Vec::new())
    }

    /// Run a tmux command natively, then frame the result.
    fn native(&mut self, command: &str, args: &[String], tile: bool) -> R<Option<Output>> {
        let mut state = self.reconcile(self.saved()?)?;
        state.remember(&self.active()?);
        state.zoom = None;
        if tile {
            self.tile(&state)?;
        }
        let mut argv = cmd![command];
        argv.extend(args.iter().cloned());
        let result = self.t.run_unchecked(&argv);
        let window = self.window.clone();
        if self.read(&window, false).is_err() {
            return Ok(Some(result)); // the window closed
        }
        if tile {
            state.root = Some(self.tiled.clone());
        }
        let mut state = self.reconcile(state)?;
        state.remember(&self.active()?);
        self.frame(state, Vec::new())?;
        if result.code != 0 {
            return Err(Error::Native(result.stderr.trim().to_string()));
        }
        Ok(Some(result))
    }

    fn command(&mut self, name: &str, args: &[String]) -> R<Option<Output>> {
        let swapped = if name == "swap-pane" { self.swap_source(args)? } else { None };
        let incoming = self.target.clone(); // the swap's -t pane; re-reading moves target
        let result = self.run_command(name, args);
        if let Some((window, outgoing)) = swapped {
            // Reported, not propagated: this window's own result stands.
            if let Err(e) = self.carry_over(&window, &outgoing, &incoming) {
                crate::report(self.t, &e);
            }
        }
        result
    }

    /// The source pane and window of a swap-pane from another window (-s).
    fn swap_source(&self, args: &[String]) -> R<Option<(String, String)>> {
        let Some(source) = crate::option_value("swap-pane", args, 's') else { return Ok(None) };
        let out = self.t.text(&cmd!["display-message", "-p", "-t", source, "#{window_id}\t#{pane_id}"])?;
        Ok(match out.split_once('\t') {
            Some((window, pane)) if window != self.window => Some((window.to_string(), pane.to_string())),
            _ => None,
        })
    }

    /// After a cross-window swap, `incoming` sits where `outgoing` was in
    /// `window`: give it that frame, then render `window`. Decided by where
    /// the panes are now, not by the command's status: a swap that failed
    /// moved nothing, and one that moved them may still report an error.
    fn carry_over(&self, window: &str, outgoing: &str, incoming: &str) -> R<()> {
        let at = |pane: &str| self.t.text(&cmd!["display-message", "-p", "-t", pane, "#{window_id}"]);
        if at(incoming)? != window || at(outgoing)? == window {
            return Ok(());
        }
        // A pane that moved into an unmanaged window is no longer framed.
        if self.state.is_none() {
            self.retire(outgoing)?;
        }
        let raw = self.t.text(&cmd!["show-option", "-wqv", "-t", window, STATE])?;
        if raw.is_empty() {
            return self.retire(incoming);
        }
        let mut state = State::parse(&raw)?;
        let rename = |p: &mut String| {
            if p == outgoing {
                *p = incoming.to_string();
            }
        };
        for leaf in leaves_mut(state.root_mut()?) {
            rename(&mut leaf.id);
        }
        state.order.iter_mut().for_each(rename);
        state.active.iter_mut().chain(state.last.iter_mut()).chain(state.zoom.iter_mut()).for_each(rename);
        self.t.run(&cmd!["set-option", "-w", "-t", window, STATE, state.dump()])?;
        crate::locked(self.t, "refresh", window, &[]).map(|_| ())
    }

    /// Drop `pane` from FRAMED, read afresh (another window's action may
    /// have changed it since this one read it).
    fn retire(&self, pane: &str) -> R<()> {
        let framed = self.t.text(&cmd!["show-option", "-gqv", FRAMED])?;
        let kept: Vec<&str> = framed.split_whitespace().filter(|p| *p != pane).collect();
        if kept.len() != framed.split_whitespace().count() {
            self.t.run(&if kept.is_empty() { cmd!["set-option", "-gu", FRAMED] } else { cmd!["set-option", "-g", FRAMED, kept.join(" ")] })?;
        }
        Ok(())
    }

    fn run_command(&mut self, name: &str, args: &[String]) -> R<Option<Output>> {
        if self.state.is_none() {
            let mut argv = cmd![name];
            argv.extend(args.iter().cloned());
            let result = self.t.run_unchecked(&argv);
            if result.code != 0 {
                return Err(Error::Native(result.stderr.trim().to_string()));
            }
            return Ok(Some(result));
        }
        if name == "select-pane" {
            if let Some(direction) = args.iter().find(|a| DIRECTIONS.contains(&a.as_str()) || *a == "-l") {
                self.select(&direction.clone())?;
                return Ok(None);
            }
        }
        if name == "last-pane" {
            self.select("-l")?;
            return Ok(None);
        }
        if name == "resize-pane" {
            if args.iter().any(|a| a == "-Z") {
                self.zoom()?;
                return Ok(None);
            }
            if let Some((direction, amount)) = tree::parse_resize(args, &self.target) {
                self.resize(&direction, amount)?;
                return Ok(None);
            }
        }
        let mut args = args.to_vec();
        if !args.iter().any(|a| a == "-t" || (a.starts_with("-t") && a.len() > 2)) {
            args.splice(0..0, cmd!["-t", self.target]);
        }
        // Closing a pane needs no tiling: its frame's space goes to a neighbour.
        self.native(name, &args, name != "kill-pane")
    }

    /// Best-effort fail-open: programs survive and normal tmux works again.
    pub fn recover(&mut self) {
        let window = self.window.clone();
        let _ = self.read(&window, false);
        for (id, info) in &self.panes {
            if info.floating {
                self.t.run_unchecked(&cmd!["join-pane", "-d", "-s", id, "-t", id]);
            }
        }
        let attempt = (|| -> R<()> {
            if let Some(state) = &self.state {
                let ids: Vec<String> = self.panes.iter().map(|(p, _)| p.clone()).collect();
                let root = prune(state.root()?, &ids);
                let mut tree_ids: Vec<String> = tree::boxes(root.as_ref()).into_iter().map(|(p, _)| p).collect();
                let mut pane_ids = ids;
                tree_ids.sort();
                tree_ids.dedup();
                pane_ids.sort();
                if tree_ids == pane_ids {
                    self.read(&window, false)?;
                    let state = self.saved()?;
                    self.tile(&state)?;
                }
            }
            Ok(())
        })();
        let _ = attempt;
        if let Some(command) = self.unframed_command() {
            self.t.run_unchecked(&command);
        }
        self.t.unset(&self.window, STATE);
        self.t.unset(&self.window, ACTIVE);
        for command in self.tiled_border_status().unwrap_or_default() {
            self.t.run_unchecked(&command);
        }
    }
}
