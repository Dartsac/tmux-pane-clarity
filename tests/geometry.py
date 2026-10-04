"""Reference frame geometry for the tests, written independently of the Rust
engine (src/tree.rs): where each pane's frame belongs for a saved layout tree.

Nodes: {"t": "p"|"h"|"v", "x", "y", "w", "h", "I": pane, "c": [...]}; "h"
places children left to right, "v" top to bottom. The saved tree uses tmux's
tiled geometry (one separator cell); frames keep the separator column between
side-by-side panes, while stacked frames touch.
"""
MIN = 3  # one content cell plus both frame edges
NATIVE_GAPS = {"h": 1, "v": 1}
FRAME_GAPS = {"h": 1, "v": 0}


def leaves(node):
    if node["t"] == "p":
        yield node
    else:
        for child in node["c"]:
            yield from leaves(child)


def boxes(root):
    return {leaf["I"]: leaf for leaf in leaves(root)} if root else {}


def frame_boxes(root, size):
    """Where each pane's frame is drawn."""
    return boxes(reflow(root, 0, 0, *size, FRAME_GAPS))


def minimum(node, kind, gaps):
    if node["t"] == "p":
        return MIN
    values = [minimum(c, kind, gaps) for c in node["c"]]
    return sum(values) + (len(values) - 1) * gaps[kind] if node["t"] == kind else max(values)


def allocate(total, weights, floors):
    """Split `total` cells in proportion to `weights`, never below `floors`;
    ties go to the earliest child."""
    assert total >= sum(floors), "window too small"
    sizes = [max(f, int(total * w / sum(weights))) for w, f in zip(weights, floors)]
    while sum(sizes) > total:
        i = max((i for i in range(len(sizes)) if sizes[i] > floors[i]), key=lambda i: sizes[i] - floors[i])
        sizes[i] -= 1
    while sum(sizes) < total:
        i = max(range(len(sizes)), key=lambda i: total * weights[i] / sum(weights) - sizes[i])
        sizes[i] += 1
    return sizes


def reflow(node, x, y, w, h, gaps=NATIVE_GAPS):
    """Fit the tree into a box; child sizes act as weights."""
    result = {"t": node["t"], "x": x, "y": y, "w": w, "h": h}
    if node["t"] == "p":
        result["I"] = node["I"]
        return result
    horizontal = node["t"] == "h"
    key = "w" if horizontal else "h"
    gap = gaps[node["t"]]
    sizes = allocate((w if horizontal else h) - (len(node["c"]) - 1) * gap,
                     [max(1, c[key]) for c in node["c"]],
                     [minimum(c, node["t"], gaps) for c in node["c"]])
    pos = x if horizontal else y
    result["c"] = []
    for child, size in zip(node["c"], sizes):
        result["c"].append(reflow(child, pos if horizontal else x, y if horizontal else pos,
                                  size if horizontal else w, h if horizontal else size, gaps))
        pos += size + gap
    return result
