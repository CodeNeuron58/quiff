"""The race: screenshot agent vs Quiff's action table vs replay, same task.

    uv run python spikes/race.py --runs 3

Slow mode is how today's frontier agents work: screenshot every step, send it
to a big model, click pixel coordinates from the answer. Quiff mode is the
action table + minimal decisions. Replay is a recorded flow with no model
calls. Same task, same checks, same browser. Results go to runs/race-*.json.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
from pathlib import Path

from quiff.act import _press_key, _select_all, combo_label
from quiff.checks import run_checks
from quiff.decide import Decision
from quiff.loop import MAX_STEPS, FastLoop
from quiff.learn import StepLogger
from quiff.models.meta import MuseDecider, muse_spark, load_env
from quiff.observe.browser import BrowserObserver
from quiff.recall import FlowRecorder, ReplayDecider, load_flow
from quiff.tasks import ObserverPage, get_task, load_tasks, sandbox_dir, setup_sandbox

TASK_ID = "b05-test-login"

# One setting for BOTH agents, so the race compares how each one sees the
# screen (a screenshot vs Quiff's action table), not how hard each one thinks.
RACE_EFFORT = "minimal"   # reasoning effort for every decision call, both agents
RACE_MAX_TOKENS = 8192    # token budget per call, both agents (a cap, not a target)


# ---------------------------------------------------------------- slow mode
# A faithful stand-in for a screenshot-based frontier agent: it never sees the
# action table — only pixels — and clicks coordinates the model reads off the
# image. One big-model call per step, no matter how simple the step is.

SLOW_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["click", "type", "press", "done"]},
        "x": {"type": "integer"},
        "y": {"type": "integer"},
        "text": {"type": "string"},
    },
    "required": ["action", "x", "y", "text"],
}


def slow_prompt(goal: str, width: int, height: int, history: list[str]) -> str:
    recent = "\n".join(history[-3:]) or "(none yet)"
    return (
        f"You are a screenshot-based computer agent. The viewport is {width}x{height} "
        f"pixels; the attached screenshot matches these coordinates 1:1.\n"
        f"Goal: {goal}\n\nActions taken so far:\n{recent}\n\n"
        'Reply with JSON only: {"action": "click"|"type"|"press"|"done", "x": pixel, '
        '"y": pixel, "text": "..."} — click at an element\'s centre; "type" clicks the '
        'field first then types "text"; "press" presses the key named in "text" '
        '(e.g. "Enter"); "done" when the goal is complete. Read coordinates off the '
        "screenshot."
    )


async def slow_act(observer, action: str, x: int, y: int, text: str) -> None:
    cdp = observer.connection()
    if x or y:
        for params in (
            # the same hover-then-press sequence Quiff's own clicks use (fair race)
            dict(type="mouseMoved", x=x, y=y),
            dict(type="mousePressed", x=x, y=y, button="left", clickCount=1),
            dict(type="mouseReleased", x=x, y=y, button="left", clickCount=1),
        ):
            await cdp.call("Input.dispatchMouseEvent", **params)
    if action == "type" and text:
        # same typing as Quiff's: replace the field's contents, don't append to them
        await _select_all(cdp)
        await cdp.call("Input.insertText", text=text)
    if action == "press":
        # same key handling as Quiff's: any combination, not just Enter
        await _press_key(cdp, combo_label(text))


def _slow_decide(client, body: dict) -> tuple[dict | None, str | None]:
    """One screenshot-agent request: (decision, None) or (None, what went wrong)."""
    try:
        reply = client._http.post("/chat/completions", json=body)
        reply.raise_for_status()
        choice = reply.json()["choices"][0]
        content = choice["message"]["content"]
        if not content:
            # finish_reason "length" = the reasoning used up the whole token budget
            return None, f"empty reply (finish_reason={choice.get('finish_reason')})"
        return json.loads(content), None
    except Exception as exc:  # HTTP error, timeout, malformed JSON
        return None, f"{type(exc).__name__}: {exc}"[:160]


def slow_request_body(model: str, prompt_text: str, data_uri: str,
                      effort: str | None = RACE_EFFORT,
                      max_tokens: int = RACE_MAX_TOKENS) -> dict:
    """One screenshot-agent request: the same effort and token budget Quiff gets."""
    body = {"model": model, "max_tokens": max_tokens,
            "response_format": {"type": "json_schema",
                                "json_schema": {"name": "slow", "schema": SLOW_SCHEMA}},
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": prompt_text},
                {"type": "image_url", "image_url": {"url": data_uri}},
            ]}]}
    if effort:
        body["reasoning_effort"] = effort
    return body


async def slow_run(client, observer, task, max_steps: int = MAX_STEPS,  # Quiff's step cap too
                   effort: str | None = RACE_EFFORT,
                   max_tokens: int = RACE_MAX_TOKENS) -> dict:
    started = time.perf_counter()
    goal = task.goal
    sandbox = sandbox_dir(task)
    page = ObserverPage(observer)
    history: list[str] = []
    model_calls = 0
    api_errors = 0
    said_done = False
    stop_reason = "max steps"
    trace: list[dict] = []  # what the agent did at each step, so failures can be diagnosed
    shot_size = None

    await observer.open(task.start["url"])
    await observer.wait_settled()
    for _ in range(max_steps):
        try:
            shot = await _screenshot(observer)
        except Exception as exc:
            trace.append({"error": f"screenshot failed: {exc}"[:160]})
            stop_reason = f"screenshot failed: {exc}"[:120]
            break
        w, h = (int(v) for v in observer.window_size.split(","))
        view = {"width": w, "height": h}
        shot_size = _png_size(shot["data"]) or [view["width"], view["height"]]
        data_uri = "data:image/png;base64," + shot["data"]
        # the same effort and budget as Quiff (RACE_EFFORT, RACE_MAX_TOKENS); the
        # prompt states the screenshot's REAL size: coordinates are read off it
        body = slow_request_body(client.model,
                                 slow_prompt(goal, shot_size[0], shot_size[1], history),
                                 data_uri, effort=effort, max_tokens=max_tokens)
        # same failure policy as Quiff's loop: retry once, then stop honestly
        call_started = time.perf_counter()
        parsed, error = await asyncio.to_thread(_slow_decide, client, body)
        model_calls += 1
        if error:
            api_errors += 1
            trace.append({"error": error, "call_ms": _ms(call_started)})
            call_started = time.perf_counter()
            parsed, error = await asyncio.to_thread(_slow_decide, client, body)
            model_calls += 1
            if error:
                api_errors += 1
                trace.append({"error": error, "call_ms": _ms(call_started)})
                stop_reason = f"api error: {error}"
                break
        call_ms = _ms(call_started)
        action = parsed.get("action")
        trace.append({"action": action, "x": parsed.get("x"), "y": parsed.get("y"),
                      "text": str(parsed.get("text", ""))[:40], "call_ms": call_ms})
        if action == "done":
            said_done = True  # the model's own claim; the checks decide
            stop_reason = "model said done"
            break
        if action not in ("click", "type", "press"):
            stop_reason = f"invalid action {action!r}"
            break
        await slow_act(observer, action, int(parsed.get("x", 0)), int(parsed.get("y", 0)),
                       str(parsed.get("text", "")))
        history.append(f"{action} at ({parsed.get('x')},{parsed.get('y')}) "
                       f"{parsed.get('text', '')}".strip())
        await observer.wait_settled()
        try:
            trace[-1]["url_after"] = (await observer.target_info()).get("url", "")
        except Exception:
            pass

    elapsed = time.perf_counter() - started
    checks = await run_checks(task.checks, answer=None, page=page, sandbox=sandbox,
                              started_ts=started)
    return {"mode": "slow", "success": bool(said_done and all(c.ok for c in checks)),
            "elapsed_s": round(elapsed, 2), "model_calls": model_calls, "steps": len(history),
            "stop_reason": stop_reason, "said_done": said_done, "api_errors": api_errors,
            "checks_pass": all(c.ok for c in checks), "screenshot_px": shot_size,
            "viewport_px": [view["width"], view["height"]] if shot_size else None,
            "trace": trace}


async def _screenshot(observer) -> dict:
    """A screenshot with the same resilience Quiff's own reads get: a capture
    requested mid-navigation can hang, so let the page settle and retry."""
    from quiff.observe.browser import BrowserError

    for attempt in range(3):
        try:
            return await observer.call("Page.captureScreenshot", format="png", timeout=15.0)
        except BrowserError:
            if attempt == 2:
                raise
            await observer.wait_settled()


def _ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


def _png_size(b64_png: str) -> list[int] | None:
    """Width and height from a base64 PNG's header (bytes 16-24 of the IHDR chunk)."""
    import base64

    try:
        head = base64.b64decode(b64_png[:64])
        return [int.from_bytes(head[16:20], "big"), int.from_bytes(head[20:24], "big")]
    except Exception:
        return None


# ---------------------------------------------------------------- quiff + replay

def auto_approver():
    """The race's stand-in for a person at the approval guard: it says yes at
    once and counts how often it was asked. A human's reaction time is not
    what the race measures, and the screenshot agent has no guard at all."""
    asked: list[str] = []

    async def approve(reason: str) -> bool:
        asked.append(reason)
        return True

    approve.asked = asked
    return approve


async def _loop_run(observer, decider, task, mode: str, rescuer=None, approver=None) -> dict:
    started = time.perf_counter()
    sandbox = setup_sandbox(task)
    logger = StepLogger(Path("runs") / f"race-{time.strftime('%Y%m%d-%H%M%S')}-{task.id}",
                        task.id)
    approver = approver or auto_approver()
    loop = FastLoop(observer, decider, logger, rescuer=rescuer, approver=approver)
    result = await loop.run(task.goal, start_url=task.start["url"], subgoal=task.goal)
    from quiff.verify import wait_stable_url

    await wait_stable_url(observer)
    page = ObserverPage(observer)
    checks = await run_checks(task.checks, answer=result.answer, page=page, sandbox=sandbox,
                              started_ts=started)
    if not all(c.ok for c in checks):
        print(f"    [{mode}] check details: " +
              "; ".join(f"{c.check_type}={c.ok}:{c.detail[:60]}" for c in checks))
    return {"mode": mode,
            "success": bool(all(c.ok for c in checks)),
            "elapsed_s": round(time.perf_counter() - started, 2),
            # every model request (decisions incl. the final "done", retries,
            # rescues); replay counts zero because it calls no model
            "model_calls": result.model_calls, "steps": result.steps,
            "stop_reason": result.stop_reason, "rescues": result.rescues,
            "auto_approvals": len(getattr(approver, "asked", []))}


async def quiff_run(observer, task, client=None, effort: str = RACE_EFFORT,
                    max_tokens: int = RACE_MAX_TOKENS) -> dict:
    # records what it did: the replay mode replays a flow Quiff recorded in this race
    decider = FlowRecorder(MuseDecider(client=client, effort=effort, max_tokens=max_tokens),
                           task.id, task.goal)
    # the rescuer thinks hard by design; it only steps in when the decider is
    # unsure or stuck (the result's "rescues" says how often that happened)
    rescuer = MuseDecider(client=client, effort="high", max_tokens=max_tokens)
    result = await _loop_run(observer, decider, task, "quiff", rescuer=rescuer)
    if result["success"]:
        decider.save_if_ran(True)
    return result


async def replay_run(observer, task) -> dict:
    try:
        flow = load_flow(task.id)
    except (FileNotFoundError, OSError):
        return {"mode": "replay", "success": False, "elapsed_s": 0.0, "model_calls": 0,
                "steps": 0, "stop_reason": "no recorded flow: no Quiff run succeeded"}
    return await _loop_run(observer, ReplayDecider(flow), task, "replay")


def provenance() -> dict:
    """Which code produced these numbers, so they can be checked later."""
    import subprocess

    def git(*args: str) -> str:
        try:
            return subprocess.run(["git", *args], capture_output=True, text=True,
                                  timeout=10).stdout.strip()
        except Exception:
            return ""

    return {"commit": git("rev-parse", "--short", "HEAD"),
            "uncommitted_changes": bool(git("status", "--porcelain", "--untracked-files=no")),
            "measured_at": time.strftime("%Y-%m-%d %H:%M:%S %z")}


# ---------------------------------------------------------------- main

async def race(runs: int) -> dict:
    load_env()
    task = get_task(TASK_ID, load_tasks())
    client = muse_spark()
    results: list[dict] = []

    observer = BrowserObserver(headless=True)
    try:
        for i in range(runs):
            results.append(await slow_run(client, observer, task))
        for i in range(runs):
            results.append(await quiff_run(observer, task, client=client))
        for i in range(runs):
            results.append(await replay_run(observer, task))
    finally:
        await observer.stop()

    summary = {}
    for mode in ("slow", "quiff", "replay"):
        rs = [r for r in results if r["mode"] == mode]
        times = [r["elapsed_s"] for r in rs]
        summary[mode] = {
            "runs": len(rs),
            "successes": sum(1 for r in rs if r["success"]),
            "median_s": statistics.median(times) if times else None,
            "model_calls": [r["model_calls"] for r in rs],
        }
        print(f"{mode:>7}: {summary[mode]['successes']}/{len(rs)} success · "
              f"median {summary[mode]['median_s']} s · model calls "
              f"{summary[mode]['model_calls']}")
    return {"task": TASK_ID, "runs_per_mode": runs, "provenance": provenance(),
            "results": results, "summary": summary}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=3)
    args = parser.parse_args()
    out = asyncio.run(race(args.runs))
    path = Path("runs") / f"race-{time.strftime('%Y-%m-%d-%H%M%S')}.json"
    path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"Saved {path}")


if __name__ == "__main__":
    main()
