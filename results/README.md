# Results

Measured results, each with the commit that produced it (`provenance` in every file), so anyone can check a number against the code that made it.

## The race, matched settings (Nov 3, 2026, evening)

`spikes/race_local.py --runs 5`: two tasks (a login, and a 6-field pizza order form ending at "Submit order"), a fresh headless Chrome per run, the same model, reasoning effort (minimal), token budget (8,192) and step cap (25) for both agents; approvals granted automatically and counted.

| File | Commit | What it is |
|---|---|---|
| `race-local-2026-11-03-203300.json` | `f3862b1` + the matched-settings changes (uncommitted when measured; committed with this README) | **The published order-form result**, with equal step caps. Slow 0/5 (ran out of steps: it kept clicking 2–3 px beside the "Extra Cheese" checkbox), 109.6 s, 25–26 calls · Quiff 5/5, 25.6 s, 9 calls · Replay 5/5, 2.4 s, 0 calls |
| `race-local-2026-11-03-201914.json` | same | **The published login result:** Slow 5/5, 16.1 s, 4–5 calls · Quiff 5/5, 15.0 s, 4 calls · Replay 5/5, 2.2 s, 0 calls. Its order-form batch gave the screenshot agent a 12-step cap against Quiff's 25 (0/5, same checkbox misses), so the order numbers come from the re-run above |

What changed from the batches below: the screenshot agent ran at the model's default reasoning effort, Quiff at minimal. With both at minimal, the login gap shrinks from 1.8× to 1.07×.

## The reasoning-effort probe (Nov 3, 2026)

| File | Commit | What it is |
|---|---|---|
| `model-probe-2026-11-03-200851.json` | `f3862b1` + the probe changes (uncommitted when measured) | `spikes/model_probe.py --efforts minimal low high --calls 3`, 20 row picks per effort, an 8,192-token budget. 100% valid JSON and 100% right row at every effort; median decision 4.5 s / 5.7 s / 7.6 s. Chart: `effort-study.svg` |
| `model-probe-2026-11-01-181130.json` | `6a2e496` (Nov 1) | The earlier probe: 100% valid at minimal (1.5 s median), 55% at low. It has no provenance field; at that commit, structured replies were capped at 512 tokens |

Why low effort looked unreliable on Nov 1: replies were capped at 512 tokens; low effort's median reply is about 512 tokens, so about half were cut off mid-answer. The new probe counts cut-off replies apart from invalid JSON.

## The race, earlier batches (Nov 3, 2026, morning)

`spikes/race_local.py --runs 5`: one task (log in on a local, deterministic copy of a test login page), a fresh headless Chrome per run, three modes:
- **slow:** a screenshot agent (screenshot + one Muse Spark vision call per step, pixel clicks);
- **quiff:** the action table + Muse Spark at minimal reasoning;
- **replay:** the flow Quiff recorded, re-run with no model calls.

| File | Commit | What it is |
|---|---|---|
| `race-local-2026-11-03-0639-final.json` | `0b6e4ed` | The result published until the matched-settings race above. Slow 5/5, 27.9 s, 4–5 calls · Quiff 5/5, 15.8 s, 4 calls · Replay 5/5, 2.2 s, 0 calls. Both agents get the same typing, key handling, settle-and-retry, token budget and a correctly sized 1254×800 page |
| `race-local-2026-11-03-0613-fair.json` | `270ab9e` | Before the last fairness fixes. Pages rendered at 754×480 (Chrome ignored the `WxH` window-size spelling) while the screenshot agent was told 1280×900. Its typing appended instead of replacing, and its presses only knew Enter. Slow 4/5 (41.9 s) · Quiff 5/5 (16.7 s) · Replay 5/5 (1.9 s) |
| `race-local-2026-11-03-0601-starved-baseline.json` | `4b5de9e` | Also before the screenshot agent's token budget was raised: at 1,024 tokens its reasoning sometimes used up the budget and returned empty answers, so 3 of its 5 runs failed. Quiff 5/5 (14.3 s) |
| `race-local-2026-11-03-0551-before-fixes.json` | `defa995` | Measured with the README file modified (`uncommitted_changes: true`) and with 66 leaked Chrome processes from earlier runs still on the machine (fixed in `a4291be`). Quiff 5/5 (15.8 s) |

Model latency varies with load: Quiff's median moved between 14.3 s and 16.7 s across these batches. Replay and Quiff passed every run in every batch. Every earlier screenshot-agent failure traced back to a harness handicap, now fixed.

The Nov 2 race numbers that appeared in earlier notes (Quiff 8.0 s, replay 0.79 s) are **not** used. At that time, a "fresh" browser could attach to an earlier run's leaked Chrome on the fixed port 9222 and skip startup, and Quiff's model calls were undercounted (3 instead of 4).
