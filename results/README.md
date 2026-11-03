# Results

Measured results, each with the commit that produced it (`provenance` in every file), so anyone can check a number against the code that made it.

## The race (Nov 3, 2026)

`spikes/race_local.py --runs 5`: one task (log in on a local, deterministic copy of a test login page), a fresh headless Chrome per run, three modes:
- **slow:** a screenshot agent (screenshot + one Muse Spark vision call per step, pixel clicks);
- **quiff:** the action table + Muse Spark at minimal reasoning;
- **replay:** the flow Quiff recorded, re-run with no model calls.

| File | Commit | What it is |
|---|---|---|
| `race-local-2026-11-03-0639-final.json` | `0b6e4ed` | **The published result.** Slow 5/5, 27.9 s, 4–5 calls · Quiff 5/5, 15.8 s, 4 calls · Replay 5/5, 2.2 s, 0 calls. Both agents get the same typing, key handling, settle-and-retry, token budget and a correctly sized 1254×800 page |
| `race-local-2026-11-03-0613-fair.json` | `270ab9e` | Before the last fairness fixes. Pages rendered at 754×480 (Chrome ignored the `WxH` window-size spelling) while the screenshot agent was told 1280×900. Its typing appended instead of replacing, and its presses only knew Enter. Slow 4/5 (41.9 s) · Quiff 5/5 (16.7 s) · Replay 5/5 (1.9 s) |
| `race-local-2026-11-03-0601-starved-baseline.json` | `4b5de9e` | Also before the screenshot agent's token budget was raised: at 1,024 tokens its reasoning sometimes used up the budget and returned empty answers, so 3 of its 5 runs failed. Quiff 5/5 (14.3 s) |
| `race-local-2026-11-03-0551-before-fixes.json` | `defa995` | Measured with the README file modified (`uncommitted_changes: true`) and with 66 leaked Chrome processes from earlier runs still on the machine (fixed in `a4291be`). Quiff 5/5 (15.8 s) |

Model latency varies with load: Quiff's median moved between 14.3 s and 16.7 s across these batches. Replay and Quiff passed every run in every batch. Every earlier screenshot-agent failure traced back to a harness handicap, now fixed.

The Nov 2 race numbers that appeared in earlier notes (Quiff 8.0 s, replay 0.79 s) are **not** used. At that time, a "fresh" browser could attach to an earlier run's leaked Chrome on the fixed port 9222 and skip startup, and Quiff's model calls were undercounted (3 instead of 4).
