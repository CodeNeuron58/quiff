"""Re-test tasks on the current code: N runs each, one summary with provenance.

    uv run python spikes/regression.py --runs 3              # the default demo set
    uv run python spikes/regression.py --runs 3 --only b05 d01

Every run goes through the real CLI (`quiff run <id> --headed`), so it tests
exactly what a judge would run. Desktop tasks open their own Notepad/Explorer
windows: close Notepad first and don't type while they run. d01 is followed by
replays of the flow it just recorded. b04 needs a human y/n, so run it by hand:

    uv run quiff run b04 --interactive --headed

The summary is printed and saved to results/regression-<time>.json.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # spikes/, for race.provenance

DEFAULT_TASKS = ["b05", "b03", "b01", "d02", "d03", "d01"]
STEPS_LINE = re.compile(r"^steps\s+(\d+)\s+.\s+([\d.]+)s\s+.\s+(.*)$")


def run_once(task: str, extra: list[str]) -> dict:
    started = time.perf_counter()
    proc = subprocess.run(
        ["uv", "run", "quiff", "run", task, "--headed", *extra],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600,
    )
    out = proc.stdout + proc.stderr
    record = {"task": task, "mode": "replay" if "--replay" in extra else "model",
              "passed": "result   PASS" in out, "wall_s": round(time.perf_counter() - started, 1)}
    for line in out.splitlines():
        match = STEPS_LINE.match(line.strip())
        if match:
            record.update(steps=int(match[1]), loop_s=float(match[2]), stop_reason=match[3])
    if not record["passed"]:
        failed = [l.strip() for l in out.splitlines() if l.startswith(("check    FAIL", "quiff:"))]
        last_line = (out.strip().splitlines() or [""])[-1]
        record["why"] = ("; ".join(failed) or last_line)[:300]
    return record


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--only", nargs="*", help="task ids or prefixes (default: the demo set)")
    args = parser.parse_args()

    from race import provenance

    results: list[dict] = []
    for task in args.only or DEFAULT_TASKS:
        for i in range(args.runs):
            record = run_once(task, [])
            results.append(record)
            print(f"{task:5} model  run {i + 1}: {'PASS' if record['passed'] else 'FAIL'} "
                  f"{record.get('loop_s', '?')} s  {record.get('why', '')}", flush=True)
        if task.startswith("d01") and any(r["passed"] for r in results if r["task"] == task):
            for i in range(args.runs):  # replay the flow the passing run just recorded
                record = run_once(task, ["--replay"])
                results.append(record)
                print(f"{task:5} replay run {i + 1}: {'PASS' if record['passed'] else 'FAIL'} "
                      f"{record.get('loop_s', '?')} s  {record.get('why', '')}", flush=True)

    summary = {}
    for record in results:
        key = f"{record['task']} {record['mode']}"
        entry = summary.setdefault(key, {"runs": 0, "passed": 0, "times": []})
        entry["runs"] += 1
        entry["passed"] += record["passed"]
        if record["passed"] and "loop_s" in record:
            entry["times"].append(record["loop_s"])
    print("\nsummary")
    for key, entry in summary.items():
        median = statistics.median(entry["times"]) if entry["times"] else None
        entry["median_s"] = median
        print(f"  {key:14} {entry['passed']}/{entry['runs']} passed"
              + (f"  median {median} s" if median is not None else ""))

    out = Path("results") / f"regression-{time.strftime('%Y-%m-%d-%H%M%S')}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps({"provenance": provenance(), "runs_per_task": args.runs,
                               "summary": summary, "results": results}, indent=2),
                   encoding="utf-8")
    print(f"\nSaved {out}")


if __name__ == "__main__":
    main()
