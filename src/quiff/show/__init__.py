"""Show mode: an on-screen status pill and a ghost cursor, so people can see what Quiff does.

The engine talks to a `Show`. The base class does nothing (normal runs and
benchmarks pay nothing for it); `quiff.show.overlay.Overlay` drives the real
overlay (the Electron app in overlay/) when `quiff run --show` is used. Quiff
never moves the real mouse: the ghost cursor is drawn on a click-through,
always-on-top window that screen recorders capture.
"""

from __future__ import annotations

import re
from pathlib import Path

from ..decide import Decision
from ..observe.table import ActionRow

# The same pattern as the step log's redaction (loop.SENSITIVE_FIELD; copied
# because loop imports this module): never put a typed secret on screen.
_SECRET_FIELD = re.compile(
    r"pass(word)?|pwd|otp|secret|token|api[- _]?key|card|cvv|cvc|pin|cc[-_ ]?(num|csc|exp)"
    r"|credit|ssn|social[- _]security", re.I)

Rect = tuple[int, int, int, int]  # left, top, right, bottom in screen pixels


class Show:
    """The engine's view of the overlay. This base does nothing."""

    active = False  # True when something is really on screen (worth finding targets for)

    def state(self, kind: str, line: str, guard: str = "") -> None:
        """kind: working | unsure | approval | yourturn; `line` is the pill's sentence."""

    def target(self, rect: Rect, chip: str, kind: str = "working", ripple: bool = True) -> float:
        """Send the ghost cursor to `rect` with a chip; returns the seconds until it
        arrives, so the action lands as the cursor does."""
        return 0.0

    def clear_target(self) -> None:
        """Hide the highlight box (the cursor stays where it is)."""

    def meter(self, text: str, calls: int) -> None:
        """Who decided the current step and how long it took ('Muse Spark (minimal) ·
        1.4 s', 'Replay · no AI call'), plus the run's AI calls so far."""

    def close(self) -> None:
        """Remove everything from the screen."""


def meter_text(decision: Decision) -> str:
    """What made this step's decision and how long it took, said plainly."""
    brain = decision.brain or ""
    if brain == "Replay":
        return "Replay"  # the meter adds "· 0 AI calls": replay never asks a model
    if "rescued" in (decision.note or ""):
        brain = "Rescuer: " + (brain or "frontier model")
    if decision.ms:
        seconds = f"{decision.ms / 1000:.1f} s"
        brain = f"{brain} · {seconds}" if brain else seconds
    return brain


def _basename(path: str) -> str:
    """The file name from a Windows or POSIX path, on any OS."""
    return re.split(r"[\\/]", path.rstrip("\\/"))[-1] if path else ""


def _short(text: str, limit: int = 32) -> str:
    # icon-font glyphs (private-use characters, e.g. the arrow in "<icon> Login") show as boxes
    text = "".join(ch for ch in str(text) if not 0xE000 <= ord(ch) <= 0xF8FF)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def describe(decision: Decision, row: ActionRow | None) -> tuple[str, str]:
    """(pill sentence, chip label) for one decision, e.g. ('Clicking "Attach"', 'Click · Attach')."""
    name = _short(row.name or row.role) if row is not None else ""
    values = " ".join(decision.values)
    if decision.action == "click":
        return f'Clicking "{name}"', f"Click · {name}"
    if decision.action == "type":
        secret = row is not None and _SECRET_FIELD.search(f"{row.role} {row.name}")
        shown = "••••••" if secret else f'"{_short(values, 24)}"'
        return f'Typing into "{name}"', f"Type · {shown}"
    if decision.action == "select":
        return f'Choosing "{_short(values)}"', f"Choose · {_short(values, 24)}"
    if decision.action == "press":
        from ..act import combo_label

        keys = "+".join(part.capitalize() if len(part) > 1 else part.upper()
                        for part in combo_label(decision.values).split("+"))
        return f"Pressing {keys}", f"Press · {keys}"
    if decision.action == "save_as":
        return f"Saving as {_basename(values)}", f"Save · {_basename(values)}"
    if decision.action == "navigate":
        return f"Opening {_short(values, 48)}", "Open"
    if decision.action == "download":
        url = decision.values[0] if decision.values else ""
        return f"Downloading {_short(url.rsplit('/', 1)[-1] or url, 40)}", "Download"
    if decision.action == "answer":
        return f"Answering · {_short(decision.answer or '', 48)}", "Answer"
    if decision.action == "wait":
        return "Waiting for the screen to settle", "Wait"
    return decision.action.capitalize(), decision.action.capitalize()


def viewport_to_screen(css: tuple[float, float, float, float], area: Rect,
                       device_pixel_ratio: float, inner: tuple[float, float]) -> Rect:
    """A page element's box (CSS px, relative to the viewport) in screen pixels.

    `area` is the screen rect that contains the page: either exactly the
    viewport, or Chrome's client area (tab strip and toolbar on top). The page
    sits at the bottom of it and is `inner` (innerWidth, innerHeight) CSS px big,
    so anchoring to the bottom works for both.
    """
    x, y, w, h = css
    dpr = device_pixel_ratio
    left = area[0] + ((area[2] - area[0]) - inner[0] * dpr) / 2
    top = area[3] - inner[1] * dpr
    return (round(left + x * dpr), round(top + y * dpr),
            round(left + (x + w) * dpr), round(top + (y + h) * dpr))


async def target_rect(observer, row: ActionRow) -> Rect | None:
    """Where `row` is on screen, or None if that can't be known (never raises)."""
    try:
        if hasattr(observer, "connection"):  # Chrome
            viewport = observer.viewport_screen_rect()
            if viewport is None or row.target is None:
                return None
            resolved = await observer.call("DOM.resolveNode", backendNodeId=row.target)
            object_id = (resolved.get("object") or {}).get("objectId")
            if not object_id:
                return None
            box = await observer.call(
                "Runtime.callFunctionOn", objectId=object_id, returnByValue=True,
                functionDeclaration=(
                    "function () {"
                    " if (this.scrollIntoViewIfNeeded) this.scrollIntoViewIfNeeded(true);"
                    " else this.scrollIntoView({ block: 'center', behavior: 'instant' });"
                    " const r = this.getBoundingClientRect();"
                    " return { x: r.x, y: r.y, w: r.width, h: r.height,"
                    " dpr: window.devicePixelRatio,"
                    " iw: window.innerWidth, ih: window.innerHeight }; }"),
            )
            value = (box.get("result") or {}).get("value") or {}
            if not value.get("w") or not value.get("h"):
                return None
            return viewport_to_screen((value["x"], value["y"], value["w"], value["h"]),
                                      viewport, float(value.get("dpr") or 1.0),
                                      (float(value["iw"]), float(value["ih"])))
        element = observer.find_row_element(row.target)  # Windows: UIA gives screen pixels
        if element is None:
            return None
        r = element.CurrentBoundingRectangle
        if r.right <= r.left or r.bottom <= r.top:
            return None
        return (r.left, r.top, r.right, r.bottom)
    except Exception:
        return None
