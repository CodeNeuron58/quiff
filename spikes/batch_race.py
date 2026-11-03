"""The batch race: the same rows of data through the same order form, two ways.

    slow    the screenshot agent does every row: a screenshot and one Muse Spark
            vision call per step, pixel clicks
    quiff   `quiff batch`: Muse Spark does row 1 and teaches a template; rows
            2..N replay it with their own values and zero AI calls

Both use the same model, reasoning effort and token budget, one headless
Chrome for all rows, and the same per-row checks. Approvals are granted
automatically and counted (the screenshot agent has no guard). Quiff's
teacher runs without the planning call here, like the screenshot agent.

    uv run python spikes/batch_race.py --rows 10
    uv run python spikes/batch_race.py --rows 10 --model open
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))       # spikes/
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # repo root

from quiff.batch import Teacher, load_rows, row_task, run_batch
from quiff.models.meta import MuseDecider, load_env, make_client
from quiff.recall import FlowRecorder

DATA = Path(__file__).resolve().parent.parent / "tasks" / "batch" / "orders.csv"
START = "http://127.0.0.1:8765/order"
GOAL = ("Fill in the pizza order form: name {name}, phone {phone}, email {email}, size {size}, "
        "toppings cheese and onion, comments '{comments}'. Then submit it.")
CHECKS = [{"type": "page_text", "contains": text} for text in (
    '"custname": "{name}"', '"custtel": "{phone}"', '"custemail": "{email}"',
    '"size": "{size}"', '"cheese"', '"onion"', '"comments": "{comments}"')]


async def slow_batch(client, rows, effort, max_tokens, headed=False) -> list[dict]:
    from quiff.batch import batch_id
    from quiff.observe.browser import BrowserObserver
    from race import slow_run

    bid = batch_id(GOAL, START) + "-slow"
    observer = BrowserObserver(headless=not headed)
    results = []
    try:
        for index, row in enumerate(rows, 1):
            task = row_task(bid, index, GOAL, START, CHECKS, row)
            started = time.perf_counter()
            try:
                result = await slow_run(client, observer, task, effort=effort,  # Quiff's step cap
                                        max_tokens=max_tokens)
            except Exception as exc:
                result = {"success": False, "model_calls": None,
                          "elapsed_s": round(time.perf_counter() - started, 2),
                          "stop_reason": f"crashed: {type(exc).__name__}: {exc}"[:200]}
            result = {"row": index, "mode": "slow", "success": result["success"],
                      "elapsed_s": result["elapsed_s"], "model_calls": result["model_calls"],
                      "stop_reason": result.get("stop_reason", "")}
            print(f"  slow  row {index:>2}: {'PASS' if result['success'] else 'FAIL'} · "
                  f"{result['elapsed_s']} s · {result['model_calls']} AI calls")
            results.append(result)
    finally:
        await observer.stop()
    return results


async def quiff_batch(client, rows, effort, max_tokens, headed=False) -> list[dict]:
    from quiff.observe.browser import BrowserObserver
    from race import auto_approver

    async def teacher(task):
        return Teacher(
            decider=FlowRecorder(MuseDecider(client=client, effort=effort,
                                             max_tokens=max_tokens), task.id, task.goal),
            rescuer=MuseDecider(client=client, effort="high", max_tokens=max_tokens))

    observer = BrowserObserver(headless=not headed)
    try:
        result = await run_batch(
            GOAL, START, rows, CHECKS, observer=observer, teacher=teacher,
            approver=auto_approver(), flows_dir=Path(tempfile.mkdtemp()),  # taught fresh
            on_row=lambda r, n: print(f"  quiff row {r.row:>2}: "
                                      f"{'PASS' if r.success else 'FAIL'} · {r.elapsed_s} s · "
                                      f"{r.model_calls} AI calls · {r.mode}"))
    finally:
        await observer.stop()
    return [{"row": r.row, "mode": "quiff", "row_mode": r.mode, "success": r.success,
             "elapsed_s": r.elapsed_s, "model_calls": r.model_calls,
             "stop_reason": r.stop_reason} for r in result.rows]


def totals(results: list[dict]) -> dict:
    times = [r["elapsed_s"] for r in results]
    return {"rows": len(results), "passed": sum(1 for r in results if r["success"]),
            "total_s": round(sum(times), 2), "median_row_s": statistics.median(times),
            "model_calls": sum(r["model_calls"] or 0 for r in results)}


async def main_async(args) -> dict:
    import race_local
    from race import provenance

    load_env()
    rows = load_rows(DATA)[: args.rows]
    client = make_client(args.model)
    server = race_local.serve()
    results: dict[str, list[dict]] = {}
    try:
        for mode in args.modes:
            print(f"{mode}:")
            runner = slow_batch if mode == "slow" else quiff_batch
            results[mode] = await runner(client, rows, args.effort, args.max_tokens, args.headed)
    finally:
        server.shutdown()
    summary = {mode: totals(rs) for mode, rs in results.items()}
    for mode, s in summary.items():
        print(f"{mode:>6}: {s['passed']}/{s['rows']} rows · {s['total_s']} s in total · "
              f"{s['model_calls']} AI calls")
    if "slow" in summary and "quiff" in summary and summary["quiff"]["total_s"]:
        print(f"quiff was {summary['slow']['total_s'] / summary['quiff']['total_s']:.1f}x faster "
              f"with {summary['slow']['model_calls'] - summary['quiff']['model_calls']} "
              f"fewer AI calls")
    return {"provenance": provenance(),
            "settings": {"model": client.model, "effort_both": args.effort,
                         "max_tokens_both": args.max_tokens, "rows": len(rows),
                         "data": "tasks/batch/orders.csv",
                         "approvals": "granted automatically and counted",
                         "browser": "one headless Chrome per mode for all rows",
                         "quiff_teacher": "no planning call (like the screenshot agent)"},
            "results": results, "summary": summary}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--rows", type=int, default=10)
    parser.add_argument("--modes", nargs="+", default=["slow", "quiff"], choices=["slow", "quiff"])
    parser.add_argument("--model", choices=["meta", "open"], default="meta")
    parser.add_argument("--effort", default="minimal")
    parser.add_argument("--max-tokens", type=int, default=8192)
    parser.add_argument("--headed", action="store_true",
                        help="show the browser (for recording a demo; publish headless numbers)")
    args = parser.parse_args()
    out = asyncio.run(main_async(args))
    path = Path("runs") / f"batch-race-{time.strftime('%Y-%m-%d-%H%M%S')}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"Saved {path}")


if __name__ == "__main__":
    main()
