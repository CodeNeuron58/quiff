"""The go/no-go test and the reasoning-effort study: how fast and how reliable
is Muse Spark at picking rows, at each reasoning effort?

    uv run python spikes/model_probe.py                       # minimal, low, high
    uv run python spikes/model_probe.py --efforts minimal low --calls 3
    uv run python spikes/model_probe.py --model open          # Muse Spark Open (MUSE_OPEN_* in .env)

Measures, per reasoning effort: median latency of small calls, then structured-
output reliability on a row-picking question with a known right answer. Every
reply is classified as valid JSON, CUT OFF (it hit max_tokens: the budget was
too small, which says nothing about the model), invalid JSON, or an API error,
so a small budget can never pass for an unreliable model. The budget defaults
to 8,192 tokens so even high effort has room. Writes runs/model-probe-*.json.

Verdict rule from the plan: median decision <= ~2 s -> full plan; slower ->
the demo leans on replay and form batching.
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
from pathlib import Path

from quiff.models.meta import (DECISION_SCHEMA, InvalidJSONError, TruncatedError,
                               make_client)
from quiff.observe.table import ActionRow, ActionTable

CLICK_ROLES = {"link", "button"}


def probe_table() -> ActionTable:
    """A small synthetic Google-shaped table. The right answer is row 4."""
    rows = [
        ("link", "About"), ("link", "Gmail"), ("link", "Images"),
        ("textbox", "Search"), ("button", "Search by voice"), ("button", "Search by image"),
        ("button", "Google Search"), ("button", "I'm Feeling Lucky"), ("link", "Sign in"),
        ("button", "Settings"), ("link", "Privacy"), ("link", "Terms"),
    ]
    return ActionTable(
        title="Google", url="https://www.google.com/",
        rows=tuple(
            ActionRow(
                index=i, role=role, name=name,
                actions=("click",) if role in CLICK_ROLES else ("type",),
                enabled=True, ref=f"{role}='{name}'",
            )
            for i, (role, name) in enumerate(rows, 1)
        ),
    )

ROW_PICK_PROMPT = (
    "Goal: {goal}\n"
    "Current subgoal: {subgoal}\n\n"
    "Action table:\n{table}\n\n"
    "Last two actions taken:\n(none yet)\n"
    "Result of the last action: (the last step has not happened yet)\n\n"
    "Reply with JSON only: pick the ONE next action. The user asked to {task}. "
    "Choose the correct row and action. Confidence between 0 and 1."
)


def _percent(values: list) -> float:
    return round(100 * sum(values) / len(values), 1) if values else 0.0


def classify(exc: Exception | None) -> str:
    """valid | cut_off | invalid_json | error, for one structured reply."""
    if exc is None:
        return "valid"
    if isinstance(exc, TruncatedError):
        return "cut_off"
    if isinstance(exc, InvalidJSONError):
        return "invalid_json"
    return "error"


def run(client, efforts: list[str], calls: int, max_tokens: int = 8192,
        picks_per_effort: int = 20) -> dict:
    results = {"model": client.model, "max_tokens": max_tokens, "efforts": {}}
    table = probe_table()
    for effort in efforts:
        # 1) plain latency: tiny prompts
        latencies = []
        errors = 0
        for _ in range(calls):
            try:
                reply = client.chat("Reply with the single word: ok", effort=effort,
                                    max_tokens=max_tokens)
                latencies.append(reply.latency_ms)
            except Exception:
                errors += 1
        # 2) structured row picking: known correct answer (row 4)
        outcomes: dict[str, int] = {"valid": 0, "cut_off": 0, "invalid_json": 0, "error": 0}
        correct, decision_ms, out_tokens = 0, [], []
        for _ in range(picks_per_effort):
            goal = "Search Google for 'langchain'."
            prompt = ROW_PICK_PROMPT.format(
                goal=goal, subgoal=goal, table=table.render(),
                task="search Google for 'langchain' by typing into the search box",
            )
            try:
                parsed, reply = client.structured(
                    prompt, DECISION_SCHEMA, name="decision", effort=effort,
                    max_tokens=max_tokens,
                )
            except Exception as exc:
                outcomes[classify(exc)] += 1
                continue
            outcomes["valid"] += 1
            decision_ms.append(reply.latency_ms)
            out_tokens.append(reply.output_tokens)
            if parsed.get("action") == "type" and parsed.get("row") == 4:
                correct += 1
        attempts = picks_per_effort
        entry = {
            "latency_ms": sorted(latencies),
            "latency_median_ms": statistics.median(latencies) if latencies else None,
            "errors": errors,
            "outcomes": outcomes,
            "valid_json_pct": round(100 * outcomes["valid"] / attempts, 1),
            "cut_off_pct": round(100 * outcomes["cut_off"] / attempts, 1),
            "invalid_json_pct": round(100 * outcomes["invalid_json"] / attempts, 1),
            "correct_row_pct": round(100 * correct / attempts, 1),
            "correct_of_valid_pct": (round(100 * correct / outcomes["valid"], 1)
                                     if outcomes["valid"] else None),
            "decision_median_ms": statistics.median(decision_ms) if decision_ms else None,
            "output_tokens_median": statistics.median(out_tokens) if out_tokens else None,
        }
        results["efforts"][effort] = entry
        print(f"[{effort:>7}] decision median {entry['decision_median_ms']} ms"
              f" · correct row {entry['correct_row_pct']}%"
              f" · valid JSON {entry['valid_json_pct']}%"
              f" · cut off {entry['cut_off_pct']}%"
              f" · invalid JSON {entry['invalid_json_pct']}%"
              f" · output tokens {entry['output_tokens_median']}"
              f" · latency median {entry['latency_median_ms']} ms · errors {errors}")
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--efforts", nargs="+", default=["minimal", "low", "high"])
    parser.add_argument("--calls", type=int, default=10, help="latency calls per effort")
    parser.add_argument("--picks", type=int, default=20, help="row-picking calls per effort")
    parser.add_argument("--max-tokens", type=int, default=8192,
                        help="token budget per reply (default 8192, room for high effort)")
    parser.add_argument("--model", choices=["meta", "open"], default="meta",
                        help="meta: Muse Spark (Meta Model API); open: Muse Spark Open")
    parser.add_argument("--open", action="store_true", help="same as --model open")
    args = parser.parse_args()

    client = make_client("open" if args.open else args.model)
    print(f"Probing {client.model} · max_tokens {args.max_tokens} ...")
    results = run(client, args.efforts, args.calls, args.max_tokens, args.picks)
    try:
        from race import provenance  # spikes/race.py, next to this file

        results["provenance"] = provenance()
    except Exception:
        pass

    best = min(
        (e["decision_median_ms"] for e in results["efforts"].values()
         if e["decision_median_ms"] and e["correct_row_pct"] >= 80),
        default=None,
    )
    verdict = ("GO: fast enough for the full plan"
               if best is not None and best <= 2000
               else "CAUTION: decision latency over 2 s — demo leans on replay + form batching")
    results["verdict"] = verdict
    print(f"\nVERDICT: {verdict}")

    out = Path("runs") / f"model-probe-{time.strftime('%Y-%m-%d-%H%M%S')}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"Saved {out}  (copy it into results/ to publish it; chart: "
          f"uv run python spikes/effort_chart.py {out})")


if __name__ == "__main__":
    main()
