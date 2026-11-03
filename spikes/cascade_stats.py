"""How much of the work each tier did: replay (no model), Muse Spark at minimal
effort, the rescuer at high effort, or Muse Spark Open.

    uv run python spikes/cascade_stats.py                 # every step log under runs/
    uv run python spikes/cascade_stats.py runs/2026-11-04-*   # just some runs

Reads the JSONL step logs Quiff writes for every run (runs/<run>/steps.jsonl)
and counts each decided step under the tier that decided it, with the median
decision time. Logs older than the `brain` field are classified from the
step's note (replayed / rescued / the model name). Prints a Markdown table and
writes runs/cascade-<time>.json.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import time
from collections import defaultdict
from pathlib import Path


# The model id a decision's note carries (" · muse-spark-1.3 · 1532 ms"). The
# open-weights id contains "spark-open"; matching the bare word "open" would
# count notes like "Save dialog is open" as Muse Spark Open.
_OPEN_ID = re.compile(r"spark[-_ ]open")
_SPARK_ID = re.compile(r"muse[-_ ]spark")


def tier(decision: dict) -> str:
    brain = decision.get("brain") or ""
    note = (decision.get("note") or "").lower()
    if not brain:  # logs from before the brain field: read the note
        if _OPEN_ID.search(note):
            brain = "Muse Spark Open"
        elif _SPARK_ID.search(note):
            brain = "Muse Spark"
    if brain == "Replay" or (not brain and (note.startswith(("replayed", "waiting for "))
                                            or note == "flow finished")):
        return "Replay (no model call)"
    if brain == "Script" or (not brain and (note.startswith("match '")
                                            or note in ("", "script finished"))):
        return "Script (no model call)"
    if not brain and note.startswith("checks failed"):
        return "Correction round (no model call)"
    if not brain:
        return "Unknown"
    return f"Rescuer: {brain}" if "rescued" in note else brain


def collect(paths: list[Path]) -> dict[str, list[int | None]]:
    times: dict[str, list[int | None]] = defaultdict(list)
    files: list[Path] = []
    for path in paths:
        files.extend([path] if path.is_file() else sorted(path.rglob("steps.jsonl")))
    for file in files:
        for line in file.read_text(encoding="utf-8").splitlines():
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if record.get("kind") != "step":
                continue
            decision = record.get("decision") or {}
            decide_ms = (record.get("latency_ms") or {}).get("decide")
            times[tier(decision)].append(decision.get("ms") or decide_ms)
    return times


def table_markdown(times: dict[str, list[int | None]]) -> str:
    total = sum(len(v) for v in times.values()) or 1
    rows = ["| Tier | Steps | Share | Median decision |", "|---|---|---|---|"]
    for name, values in sorted(times.items(), key=lambda kv: -len(kv[1])):
        timed = [v for v in values if v]
        median = f"{statistics.median(timed) / 1000:.1f} s" if timed and "no model" not in name else "—"
        rows.append(f"| {name} | {len(values)} | {100 * len(values) / total:.0f}% | {median} |")
    return "\n".join(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("paths", nargs="*", type=Path, default=[Path("runs")])
    args = parser.parse_args()
    times = collect(args.paths)
    if not times:
        raise SystemExit("no step logs found (runs/<run>/steps.jsonl)")
    print(table_markdown(times))
    out = Path("runs") / f"cascade-{time.strftime('%Y-%m-%d-%H%M%S')}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({tier_name: {"steps": len(v), "decision_ms": v}
                               for tier_name, v in times.items()}, indent=2), encoding="utf-8")
    print(f"\nSaved {out}")


if __name__ == "__main__":
    main()
