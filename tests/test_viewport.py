"""What counts as "on screen": DOMSnapshot boxes -> the visible viewport.

The numbers are the ones probed on real Chrome (Nov 3): a headed window on a
200% display, and a 4,000 px tall page scrolled by 1,500 px.
"""

from quiff.observe.browser import _filter_viewport, _snapshot_bounds
from quiff.observe.table import ActionRow


def snapshot(bounds_by_id, scroll=(0, 0)):
    ids = list(bounds_by_id)
    return {"documents": [{
        "nodes": {"backendNodeId": ids},
        "layout": {"nodeIndex": list(range(len(ids))),
                   "bounds": [bounds_by_id[i] for i in ids]},
        "scrollOffsetX": scroll[0], "scrollOffsetY": scroll[1],
    }]}


def button(target, name):
    return ActionRow(index=target, role="button", name=name, actions=("click",),
                     enabled=True, target=target)


def test_device_pixels_become_css_pixels():
    # headed, 200%: "Submit order" sits at CSS y=599 but the snapshot says 1197
    bounds = _snapshot_bounds(snapshot({7: [16, 1197.375, 184.625, 43]}), 2.0)
    assert bounds[7] == (8, 598.6875, 92.3125, 21.5)


def test_submit_below_the_middle_stays_in_the_table_on_a_scaled_display():
    # the bug: compared in device px against a CSS-px viewport, every control
    # in the lower half of the page was dropped (b04 never saw "Submit order")
    rows = [button(7, "Submit order")]
    bounds = _snapshot_bounds(snapshot({7: [16, 1197.375, 184.625, 43]}), 2.0)
    assert _filter_viewport(rows, bounds, (1267, 807)) == rows


def test_scrolling_reveals_what_was_below_the_fold():
    far = [button(5, "Far")]
    at_top = _snapshot_bounds(snapshot({5: [16, 4016, 100, 40]}, scroll=(0, 0)), 2.0)
    assert _filter_viewport(far, at_top, (1267, 807)) == []
    scrolled = _snapshot_bounds(snapshot({5: [16, 4016, 100, 40]}, scroll=(0, 3000)), 2.0)
    assert scrolled[5][1] == 508  # where getBoundingClientRect puts it
    assert _filter_viewport(far, scrolled, (1267, 807)) == far


def test_scrolled_past_controls_leave_the_table():
    top = [button(3, "Top link")]
    bounds = _snapshot_bounds(snapshot({3: [8, 100, 80, 20]}, scroll=(0, 1500)), 1.0)
    assert _filter_viewport(top, bounds, (1254, 800)) == []


def test_headless_numbers_pass_through_unchanged():
    bounds = _snapshot_bounds(snapshot({2: [8, 594.1875, 92.3125, 21]}), 1.0)
    assert bounds[2] == (8, 594.1875, 92.3125, 21)
