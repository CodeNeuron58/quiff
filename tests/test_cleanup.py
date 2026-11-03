"""A fresh clone can replay without a key; the cascade numbers classify steps right."""

import json
import sys
from pathlib import Path

from quiff.recall import load_flow

SPIKES = Path(__file__).resolve().parent.parent / "spikes"
if str(SPIKES) not in sys.path:
    sys.path.insert(0, str(SPIKES))


def test_a_fresh_clone_finds_the_example_flow(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # no runs/flows here, like a fresh clone
    flow = load_flow("b05-test-login")
    assert [s.action for s in flow.steps] == ["type", "type", "click"]


def test_your_own_recording_wins_over_the_example(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    own = tmp_path / "runs" / "flows"
    own.mkdir(parents=True)
    (own / "b05-test-login.json").write_text(json.dumps(
        {"goal": "mine", "task_id": "b05-test-login", "recorded_at": "now", "steps": []}))
    assert load_flow("b05-test-login").goal == "mine"


def test_cascade_counts_each_tier(tmp_path):
    import cascade_stats

    log = tmp_path / "run" / "steps.jsonl"
    log.parent.mkdir()
    steps = [
        {"kind": "step", "decision": {"brain": "Muse Spark (minimal)", "ms": 1400, "note": ""},
         "latency_ms": {"decide": 1450}},
        {"kind": "step", "decision": {"brain": "Muse Spark (high)", "ms": 5800,
                                      "note": "unsure · rescued"}, "latency_ms": {"decide": 5900}},
        {"kind": "step", "decision": {"note": "replayed button/'Login'"},
         "latency_ms": {"decide": 1}},
        {"kind": "step", "decision": {"note": "ok · muse-spark-1.3 · 1600 ms"},
         "latency_ms": {"decide": 1600}},
        {"kind": "run_summary", "success": True},
    ]
    log.write_text("\n".join(json.dumps(s) for s in steps), encoding="utf-8")
    times = cascade_stats.collect([tmp_path])
    assert {k: len(v) for k, v in times.items()} == {
        "Muse Spark (minimal)": 1, "Rescuer: Muse Spark (high)": 1,
        "Replay (no model call)": 1, "Muse Spark": 1}
    table = cascade_stats.table_markdown(times)
    assert "| Replay (no model call) | 1 | 25% | — |" in table


def test_cascade_reads_the_model_id_not_the_word_open():
    # found live: notes like "Save dialog is open" were counted as Muse Spark Open,
    # a model that had never been run
    import cascade_stats

    tier = cascade_stats.tier
    assert tier({"note": "Save dialog is open · muse-spark-1.3 · 2533 ms"}) == "Muse Spark"
    assert tier({"note": "ok · meta-models/muse-spark-open-1026 · 900 ms"}) == "Muse Spark Open"
    assert tier({"note": "unsure · muse-spark-1.3 · 5000 ms · rescued"}) == "Rescuer: Muse Spark"


def test_cascade_knows_the_steps_that_call_no_model():
    import cascade_stats

    tier = cascade_stats.tier
    assert tier({"note": "flow finished"}) == "Replay (no model call)"
    assert tier({"note": "waiting for edit/'File name:'"}) == "Replay (no model call)"
    assert tier({"note": "match 'textbox'/'user'"}) == "Script (no model call)"
    assert tier({"note": ""}) == "Script (no model call)"
    assert tier({"note": "checks failed; correcting"}) == "Correction round (no model call)"
