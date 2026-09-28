//! The logical tiled layout tree (tmux's version-2 JSON) and its geometry.
//! tests/geometry.py is an independent reference for the frame geometry.

use crate::json::Value;
use crate::{err, R};

pub const MIN: i64 = 3; // one content cell plus both frame edges

/// Cells between neighbouring boxes, per split kind: (`h`, `v`).
/// The stored tree uses tmux's tiled geometry (one separator cell). Rendered
/// frames keep the separator column between side-by-side frames, but stacked
/// frames touch: a row is about twice as tall as a column is wide.
#[derive(Clone, Copy)]
pub struct Gaps {
    h: i64,
    v: i64,
}
pub const NATIVE_GAPS: Gaps = Gaps { h: 1, v: 1 };
pub const FRAME_GAPS: Gaps = Gaps { h: 1, v: 0 };

impl Gaps {
    fn of(self, kind: u8) -> i64 {
        if kind == b'h' { self.h } else { self.v }
    }
}

/// `t` is b'p' (pane), b'h' (children left to right) or b'v' (top to bottom).
#[derive(Clone, Debug, Default)]
pub struct Node {
    pub t: u8,
    pub x: i64,
    pub y: i64,
    pub w: i64,
    pub h: i64,
    pub id: String, // "I"
    pub index: i64, // "i"
    pub c: Vec<Node>,
    pub active: bool,      // "a" (tmux layouts only)
    pub last: Option<i64>, // "l" (tmux layouts only)
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Rect {
    pub x: i64,
    pub y: i64,
    pub w: i64,
    pub h: i64,
}

impl Node {
    pub fn size(&self, kind: u8) -> i64 {
        if kind == b'h' { self.w } else { self.h }
    }

    fn size_mut(&mut self, kind: u8) -> &mut i64 {
        if kind == b'h' { &mut self.w } else { &mut self.h }
    }

    pub fn rect(&self) -> Rect {
        Rect { x: self.x, y: self.y, w: self.w, h: self.h }
    }

    pub fn from_json(value: &Value) -> R<Node> {
        let int = |key: &str| value.get(key).and_then(Value::as_i64);
        let t = match value.get("t").and_then(Value::as_str) {
            Some("p") => b'p',
            Some("h") => b'h',
            Some("v") => b'v',
            other => return err(format!("invalid layout node type: {other:?}")),
        };
        let mut node = Node {
            t,
            x: int("x").unwrap_or(0),
            y: int("y").unwrap_or(0),
            w: int("w").ok_or_else(|| crate::Error::Tmux("layout node without w".into()))?,
            h: int("h").ok_or_else(|| crate::Error::Tmux("layout node without h".into()))?,
            index: int("i").unwrap_or(0),
            active: value.get("a").is_some_and(Value::truthy),
            last: int("l"),
            ..Node::default()
        };
        if t == b'p' {
            node.id = value.get("I").and_then(Value::as_str).ok_or_else(|| crate::Error::Tmux("pane node without I".into()))?.into();
        } else {
            match value.get("c") {
                Some(Value::Arr(children)) => {
                    node.c = children.iter().map(Node::from_json).collect::<R<_>>()?;
                }
                _ => return err("layout container without children"),
            }
        }
        Ok(node)
    }

    pub fn to_json(&self) -> Value {
        let mut items = vec![
            ("t".to_string(), Value::Str((self.t as char).to_string())),
            ("x".to_string(), Value::Int(self.x)),
            ("y".to_string(), Value::Int(self.y)),
            ("w".to_string(), Value::Int(self.w)),
            ("h".to_string(), Value::Int(self.h)),
        ];
        if self.t == b'p' {
            items.push(("I".into(), Value::Str(self.id.clone())));
            items.push(("i".into(), Value::Int(self.index)));
        } else {
            items.push(("c".into(), Value::Arr(self.c.iter().map(Node::to_json).collect())));
        }
        Value::Obj(items)
    }
}

pub fn leaves(node: &Node) -> Vec<&Node> {
    let mut out = Vec::new();
    fn walk<'a>(node: &'a Node, out: &mut Vec<&'a Node>) {
        if node.t == b'p' {
            out.push(node);
        } else {
            for child in &node.c {
                walk(child, out);
            }
        }
    }
    walk(node, &mut out);
    out
}

pub fn leaves_mut(node: &mut Node) -> Vec<&mut Node> {
    if node.t == b'p' {
        return vec![node];
    }
    node.c.iter_mut().flat_map(leaves_mut).collect()
}

/// Pane id -> box, in tree order.
pub type Boxes = Vec<(String, Rect)>;

pub fn boxes(root: Option<&Node>) -> Boxes {
    root.map_or_else(Vec::new, |r| leaves(r).into_iter().map(|l| (l.id.clone(), l.rect())).collect())
}

pub fn lookup<'a>(boxes: &'a Boxes, pane: &str) -> Option<&'a Rect> {
    boxes.iter().find(|(p, _)| p == pane).map(|(_, b)| b)
}

/// Where each pane's frame is drawn (see FRAME_GAPS).
pub fn frame_boxes(root: &Node, size: (i64, i64)) -> R<Boxes> {
    Ok(boxes(Some(&reflow(root, 0, 0, size.0, size.1, FRAME_GAPS)?)))
}

pub fn minimum(node: &Node, kind: u8, gaps: Gaps) -> i64 {
    if node.t == b'p' {
        return MIN;
    }
    let values: Vec<i64> = node.c.iter().map(|c| minimum(c, kind, gaps)).collect();
    if node.t == kind {
        values.iter().sum::<i64>() + (values.len() as i64 - 1) * gaps.of(kind)
    } else {
        values.into_iter().max().unwrap_or(0)
    }
}

/// Index of the first maximal item (ties keep the earliest).
fn first_max<T: PartialOrd + Copy>(items: impl Iterator<Item = (usize, T)>) -> Option<usize> {
    let mut best: Option<(usize, T)> = None;
    for (i, key) in items {
        if best.is_none_or(|(_, b)| key > b) {
            best = Some((i, key));
        }
    }
    best.map(|(i, _)| i)
}

pub fn allocate(total: i64, weights: &[i64], floors: &[i64]) -> R<Vec<i64>> {
    if total < floors.iter().sum::<i64>() {
        return err("Window too small for complete frames; use fewer panes or a larger window.");
    }
    let sum_weights: i64 = weights.iter().sum();
    // total * w is exact; the division is correctly rounded.
    let share = |i: usize| (total * weights[i]) as f64 / sum_weights as f64;
    let mut sizes: Vec<i64> = (0..weights.len()).map(|i| floors[i].max(share(i) as i64)).collect();
    while sizes.iter().sum::<i64>() > total {
        let i = first_max((0..sizes.len()).filter(|&i| sizes[i] > floors[i]).map(|i| (i, sizes[i] - floors[i])))
            .ok_or_else(|| crate::Error::Tmux("cannot allocate layout".into()))?;
        sizes[i] -= 1;
    }
    while sizes.iter().sum::<i64>() < total {
        let i = first_max((0..sizes.len()).map(|i| (i, share(i) - sizes[i] as f64)))
            .ok_or_else(|| crate::Error::Tmux("cannot allocate layout".into()))?;
        sizes[i] += 1;
    }
    Ok(sizes)
}

/// Fit the tree into a box; child sizes act as weights. Idempotent.
pub fn reflow(node: &Node, x: i64, y: i64, w: i64, h: i64, gaps: Gaps) -> R<Node> {
    let mut result = Node { t: node.t, x, y, w, h, ..Node::default() };
    if node.t == b'p' {
        if w < MIN || h < MIN {
            return err("Window too small for complete frames.");
        }
        result.id = node.id.clone();
        result.index = node.index;
        return Ok(result);
    }
    let horizontal = node.t == b'h';
    let gap = gaps.of(node.t);
    let count = node.c.len() as i64;
    let weights: Vec<i64> = node.c.iter().map(|c| c.size(node.t).max(1)).collect();
    let floors: Vec<i64> = node.c.iter().map(|c| minimum(c, node.t, gaps)).collect();
    let sizes = allocate((if horizontal { w } else { h }) - (count - 1) * gap, &weights, &floors)?;
    let mut pos = if horizontal { x } else { y };
    for (child, size) in node.c.iter().zip(sizes) {
        result.c.push(if horizontal {
            reflow(child, pos, y, size, h, gaps)?
        } else {
            reflow(child, x, pos, w, size, gaps)?
        });
        pos += size + gap;
    }
    Ok(result)
}

/// Drop missing panes. Like tmux, a closed cell's space goes to the previous
/// sibling (or the next one when it was first).
pub fn prune(node: &Node, members: &[String]) -> Option<Node> {
    if node.t == b'p' {
        return members.contains(&node.id).then(|| node.clone());
    }
    let kind = node.t;
    let mut kept: Vec<Node> = Vec::new();
    let mut carry = 0;
    for child in &node.c {
        match prune(child, members) {
            None => match kept.last_mut() {
                Some(last) => *last.size_mut(kind) += child.size(kind) + 1,
                None => carry += child.size(kind) + 1,
            },
            Some(mut result) => {
                *result.size_mut(kind) += carry;
                kept.push(result);
                carry = 0;
            }
        }
    }
    match kept.len() {
        0 => None,
        1 => {
            let mut only = kept.pop().unwrap();
            only.w = node.w;
            only.h = node.h;
            Some(only)
        }
        _ => Some(Node { c: kept, ..node.clone() }),
    }
}

/// Split `anchor` in half along `kind`, placing the new leaf after/before it.
pub fn insert(node: &Node, anchor: &str, leaf: (&str, i64), kind: u8, after: bool) -> Option<Node> {
    if node.t == b'p' {
        if node.id != anchor {
            return None;
        }
        let half = 1.max((node.size(kind) - 1).div_euclid(2));
        let mut first = node.clone();
        *first.size_mut(kind) = half;
        let mut second = node.clone();
        second.t = b'p';
        second.id = leaf.0.to_string();
        second.index = leaf.1;
        *second.size_mut(kind) = half;
        let pair = if after { vec![first, second] } else { vec![second, first] };
        return Some(Node { t: kind, c: pair, ..node.clone() });
    }
    for (i, child) in node.c.iter().enumerate() {
        if let Some(found) = insert(child, anchor, leaf, kind, after) {
            // Splitting along the parent's own direction adds a sibling, as tmux does.
            let spliced = if child.t == b'p' && node.t == kind { found.c } else { vec![found] };
            let mut c = node.c[..i].to_vec();
            c.extend(spliced);
            c.extend_from_slice(&node.c[i + 1..]);
            return Some(Node { c, ..node.clone() });
        }
    }
    None
}

/// Child indexes from the root down to `pane`.
pub fn path_to(node: &Node, pane: &str) -> Option<Vec<usize>> {
    if node.t == b'p' {
        return (node.id == pane).then(Vec::new);
    }
    for (i, child) in node.c.iter().enumerate() {
        if let Some(mut rest) = path_to(child, pane) {
            rest.insert(0, i);
            return Some(rest);
        }
    }
    None
}

fn node_at_mut<'a>(root: &'a mut Node, path: &[usize]) -> &'a mut Node {
    path.iter().fold(root, |node, &i| &mut node.c[i])
}

/// Move the border between children i and i+1 by `delta` cells. As in tmux,
/// space is taken from the nearest children that can still shrink.
pub fn move_border(container: &mut Node, i: usize, delta: i64) {
    let kind = container.t;
    let cells = &mut container.c;
    let (grow, shrink): (usize, Vec<usize>) =
        if delta > 0 { (i, (i + 1..cells.len()).collect()) } else { (i + 1, (0..=i).rev().collect()) };
    let (needed, mut moved) = (delta.abs(), 0);
    for j in shrink {
        let take = (needed - moved).min(cells[j].size(kind) - minimum(&cells[j], kind, NATIVE_GAPS));
        if take > 0 {
            *cells[j].size_mut(kind) -= take;
            moved += take;
        }
        if moved == needed {
            break;
        }
    }
    *cells[grow].size_mut(kind) += moved;
}

/// tmux resize-pane -L/-R/-U/-D: the nearest ancestor split of that axis;
/// the border after the pane's cell, or before it when the cell is last.
pub fn resize_pane(root: &mut Node, pane: &str, direction: &str, amount: i64) {
    let kind = if matches!(direction, "-L" | "-R") { b'h' } else { b'v' };
    let Some(path) = path_to(root, pane) else { return };
    for depth in (0..path.len()).rev() {
        let container = node_at_mut(root, &path[..depth]);
        if container.t == kind {
            let mut i = path[depth];
            if i == container.c.len() - 1 {
                if i == 0 {
                    return;
                }
                i -= 1;
            }
            move_border(container, i, if matches!(direction, "-R" | "-D") { amount } else { -amount });
            return;
        }
    }
}

/// Move one edge of a pane (mouse drag) to the nearest border on that side.
pub fn move_edge(root: &mut Node, pane: &str, kind: u8, after: bool, delta: i64) {
    let Some(path) = path_to(root, pane) else { return };
    for depth in (0..path.len()).rev() {
        let container = node_at_mut(root, &path[..depth]);
        let i = path[depth];
        if container.t == kind && (if after { i + 1 < container.c.len() } else { i > 0 }) {
            move_border(container, if after { i } else { i - 1 }, delta);
            return;
        }
    }
}

/// tmux's directional choice: the most recently used pane sharing the
/// requested edge, with wraparound. Not chosen by cursor position.
pub fn neighbour(tree: &Boxes, source: &str, direction: &str, size: (i64, i64), order: &[String]) -> R<Option<String>> {
    let src = *lookup(tree, source).ok_or_else(|| crate::Error::Tmux(format!("pane {source} is not in the layout")))?;
    let horizontal = matches!(direction, "-L" | "-R");
    let pos = |r: &Rect| if horizontal { r.x } else { r.y };
    let len = |r: &Rect| if horizontal { r.w } else { r.h };
    let cross = |r: &Rect| if horizontal { r.y } else { r.x };
    let cross_len = |r: &Rect| if horizontal { r.h } else { r.w };
    let forward = matches!(direction, "-R" | "-D");
    let limit = if horizontal { size.0 } else { size.1 };
    let mut edge = if forward { pos(&src) + len(&src) + 1 } else { pos(&src) };
    if forward && edge >= limit {
        edge = 0;
    } else if !forward && edge == 0 {
        edge = limit + 1;
    }
    // Rank by most recent use (if a pane were listed twice, its last entry counts).
    let rank = |p: &str| order.iter().rposition(|o| o == p).unwrap_or(order.len());
    Ok(tree
        .iter()
        .filter(|(p, b)| {
            p != source
                && (cross(&src) + cross_len(&src)).min(cross(b) + cross_len(b)) > cross(&src).max(cross(b))
                && (if forward { pos(b) } else { pos(b) + len(b) + 1 }) == edge
        })
        .min_by(|(a, _), (b, _)| (rank(a), a).cmp(&(rank(b), b)))
        .map(|(p, _)| p.clone()))
}

#[derive(Clone, Copy, Debug)]
pub struct Pane {
    pub index: i64,
    pub floating: bool,
    pub active: bool,
    pub x: i64,
    pub y: i64,
    pub w: i64,
    pub h: i64,
}

pub type Panes = Vec<(String, Pane)>;

pub fn pane<'a>(panes: &'a Panes, id: &str) -> Option<&'a Pane> {
    panes.iter().find(|(p, _)| p == id).map(|(_, info)| info)
}

/// Insert a pane created outside the adapter next to the pane it was split
/// from: the one its float overlaps most, preferring the active pane.
pub fn adopt(root: Option<&Node>, id: &str, panes: &Panes, active: Option<&str>, size: (i64, i64)) -> R<Option<Node>> {
    let new = *pane(panes, id).ok_or_else(|| crate::Error::Tmux(format!("unknown pane {id}")))?;
    let Some(root) = root else {
        return Ok(Some(Node { t: b'p', id: id.into(), index: new.index, x: 0, y: 0, w: size.0, h: size.1, ..Node::default() }));
    };
    let tree = boxes(Some(root));
    let overlap = |b: &Rect| {
        let w = (new.x + new.w + 1).min(b.x + b.w) - (new.x - 1).max(b.x);
        let h = (new.y + new.h + 1).min(b.y + b.h) - (new.y - 1).max(b.y);
        w.max(0) * h.max(0)
    };
    let Some(best) = first_max(tree.iter().enumerate().map(|(i, (p, b))| (i, (overlap(b), Some(p.as_str()) == active))))
    else {
        return err("empty layout");
    };
    let (anchor, anchor_box) = &tree[best];
    let anchor_pane = *pane(panes, anchor).ok_or_else(|| crate::Error::Tmux(format!("unknown pane {anchor}")))?;
    let (dx, dy) = (new.x - anchor_pane.x, new.y - anchor_pane.y);
    let (kind, after) = if dx != 0 || dy != 0 {
        if dx.abs() >= dy.abs() { (b'h', dx > 0) } else { (b'v', dy > 0) }
    } else {
        (if anchor_box.w >= 2 * anchor_box.h { b'h' } else { b'v' }, true)
    };
    Ok(insert(root, anchor, (id, new.index), kind, after))
}

/// (direction, amount) for a plain directional resize-pane, else None.
pub fn parse_resize(args: &[String], target: &str) -> Option<(String, i64)> {
    let digits = |s: &str| !s.is_empty() && s.bytes().all(|b| b.is_ascii_digit());
    let (mut direction, mut amount, mut i) = (None, 1, 0);
    while i < args.len() {
        let arg = &args[i];
        let head = arg.get(..2).unwrap_or(arg);
        if arg == "-t" && i + 1 < args.len() && args[i + 1] == target {
            i += 2;
        } else if matches!(head, "-L" | "-R" | "-U" | "-D") && direction.is_none() {
            direction = Some(head.to_string());
            let mut value = arg[2..].to_string();
            if value.is_empty() && i + 1 < args.len() && digits(&args[i + 1]) {
                value = args[i + 1].clone();
                i += 1;
            }
            if !value.is_empty() && !digits(&value) {
                return None;
            }
            amount = if value.is_empty() { 1 } else { value.parse().ok()? };
            i += 1;
        } else {
            return None;
        }
    }
    direction.map(|d| (d, amount))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn leaf(id: &str, w: i64, h: i64) -> Node {
        Node { t: b'p', id: id.into(), w, h, ..Node::default() }
    }

    #[test]
    fn allocate_matches_python_rounding() {
        // Leftover cells go to the earliest children; floors always hold.
        assert_eq!(allocate(10, &[1, 1, 1], &[3, 3, 3]).unwrap(), vec![4, 3, 3]);
        assert_eq!(allocate(100, &[30, 70], &[3, 3]).unwrap(), vec![30, 70]);
        assert!(allocate(5, &[1, 1], &[3, 3]).is_err());
    }

    #[test]
    fn frames_touch_vertically() {
        let root = Node { t: b'v', w: 80, h: 24, c: vec![leaf("%0", 80, 12), leaf("%1", 80, 11)], ..Node::default() };
        let native = boxes(Some(&reflow(&root, 0, 0, 80, 24, NATIVE_GAPS).unwrap()));
        let framed = frame_boxes(&root, (80, 24)).unwrap();
        assert_eq!(native[1].1.y, native[0].1.h + 1);
        assert_eq!(framed[1].1.y, framed[0].1.h);
    }

    #[test]
    fn parse_resize_forms() {
        let args = |v: &[&str]| v.iter().map(|s| s.to_string()).collect::<Vec<_>>();
        assert_eq!(parse_resize(&args(&["-R", "5"]), "%1"), Some(("-R".into(), 5)));
        assert_eq!(parse_resize(&args(&["-L5", "-t", "%1"]), "%1"), Some(("-L".into(), 5)));
        assert_eq!(parse_resize(&args(&["-x", "5"]), "%1"), None);
        assert_eq!(parse_resize(&args(&["-U"]), "%1"), Some(("-U".into(), 1)));
    }
}
