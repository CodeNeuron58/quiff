# Quiff: the evidence

Every number here comes from a file in [`results/`](../results/README.md), and each file records the commit that produced it. Measured on one Windows machine in India, Nov 1–3, 2026, with Meta's `muse-spark-1.3`. [← Back to the README](../README.md)

- [First run vs replay](#first-run-vs-replay)
- [Ten orders from a spreadsheet](#ten-orders-from-a-spreadsheet)
- [What reasoning effort costs](#what-reasoning-effort-costs)
- [Who did the work](#who-did-the-work)
- [Task runs and the task suite](#task-runs-and-the-task-suite)

## First run vs replay

Setup:
- **Two tasks** on local, deterministic pages (`spikes/race_local.py`): a login (3 steps), and a 6-field pizza order form that ends at "Submit order" (a local copy of httpbin's form).
- **Model:** Muse Spark (`muse-spark-1.3`) at minimal reasoning, an 8,192-token budget per call, and a 25-step cap. The rescuer (high reasoning) may step in when the decider is unsure; it never did in these runs.
- **Approvals** are granted automatically and counted: "Submit order" asked once in every order run.
- **Quiff runs without** the planning call and app guidance that `quiff run` adds.
- **Browser:** a fresh headless Chrome for every run; times include launching it.
- **Runs:** 5 per mode per task, measured Nov 3, 2026. Data: [login](../results/race-local-2026-11-03-201914.json), [order form](../results/race-local-2026-11-03-203300.json).

| Task | Mode | Success | Median | p95 | Model calls per run |
|---|---|---|---|---|---|
| Login | **First run**: the action table + one Muse Spark call per step | **5/5** | 15.0 s | 18.6 s | 4 |
| Login | **Replay**: the flow Quiff recorded, re-run | **5/5** | **2.2 s** | 4.3 s | **0** |
| Order form | **First run** | **5/5** | 25.6 s | 30.3 s | 9 |
| Order form | **Replay** | **5/5** | **2.4 s** | 2.6 s | **0** |

What it shows:
- **Every run passed, with every field right.** The model picks a control by its row number, and Quiff works out where that control is on the page.
- **Replay repeats a task with zero model calls:** 6.8× faster than the first run on the login, and 10.7× on the order form.
- **A first run moves at the model's pace.** Each step waits for one Muse Spark reply; the time saved comes from never asking twice.

Caveats:
- **Two simple tasks on one machine,** and model latency varies with load.
- **The race files in `results/` also hold runs of a basic screenshot-and-click loop** we wrote as a baseline. They aren't used for any claim: it's a bare loop, not a tuned screenshot agent, so it says little about screenshot agents in general.
- **Every batch from the day is in [`results/`](../results/README.md),** including earlier ones, with what changed in each.

## Ten orders from a spreadsheet

`quiff batch` on the 10 orders in `tasks/batch/orders.csv` (`spikes/batch_race.py --rows 10`, Nov 3, 2026): Muse Spark at minimal reasoning, an 8,192-token budget and a 25-step cap; one headless Chrome for all rows; approvals granted automatically and counted; the teaching run (row 1) without the planning call. Data: [`results/batch-race-2026-11-03-211217.json`](../results/batch-race-2026-11-03-211217.json).

| Rows passed | Total time | AI calls |
|---|---|---|
| **10/10** | **32.6 s** (row 1: 25.9 s; rows 2–10: about 0.7 s each) | **9**, all on row 1 |

Row 1 taught the template. Rows 2–10 replayed it with their own values, and code checked each row.

## What reasoning effort costs

- **Muse Spark** (`muse-spark-1.3`, Meta Model API), the decision model: one JSON-schema-structured call per step picks the action and row from the numbered table.
  - **Go/no-go benchmark** (`spikes/model_probe.py`, one fixed row-picking prompt, 20 tries per effort, an 8,192-token budget; Nov 3): 100% schema-valid and 100% correct row at minimal, low and high effort; median decision 4.5 s at minimal. On Nov 1 the same probe measured 1.5 s at minimal: model latency varies a lot with load.
  - **Real task runs** (bigger tables, Nov 1–2): about 2.4 s median per decision.
- **Muse Spark at high reasoning:** plans the subgoals and rescues the loop (see above).
- **Muse Spark Open** (open weights, on a partner host: Modal, Baseten or Fireworks, set with `MUSE_OPEN_*` in `.env`): wired into the same decision interface. `--model open` runs the plan, the decisions and the rescues on it, in `quiff run`, `quiff do` and the timing scripts. **Not yet run.**

What the benchmark taught us: for a narrow, well-structured pick, more reasoning buys nothing and costs time. Every effort chose the right row in 20 of 20 tries; low effort took 1.3× and high effort 1.7× as long as minimal, writing 1.8× and 2.4× as many tokens. The decider runs at minimal, and the verification layer catches the rest.

| Effort | Median decision | Right row | Valid JSON | Cut off (budget) | Median output tokens |
|---|---|---|---|---|---|
| minimal | 4.5 s | 100% | 100% | 0% | 286 |
| low | 5.7 s | 100% | 100% | 0% | 512.5 |
| high | 7.6 s | 100% | 100% | 0% | 673.5 |

[Chart](../results/effort-study.svg) · [data](../results/model-probe-2026-11-03-200851.json). An earlier README said only 55% of low-effort replies were valid JSON. That probe capped replies at 512 tokens, and low effort's median reply is about 512 tokens, so about half were cut off mid-answer. With room to finish, low effort is 100% valid.

## Who did the work

Every step Quiff takes is logged with what decided it. `spikes/cascade_stats.py` counts them. Over the runs on the final code (Nov 3, evening: the timing runs, the batch and replay runs, and the live checks; 526 steps):

| Tier | Steps | Share | Median decision |
|---|---|---|---|
| Replay (no model call) | 370 | 70% | — |
| Muse Spark, minimal reasoning | 154 | 29% | 2.7 s |
| Correction round (no model call) | 2 | 0% | — |

The rescuer wasn't needed in these runs. Across every step logged since Oct 31 (2,350, development included), Muse Spark decided 54%, replay 27%, the rescuer 13% and scripts 4%. The planning call isn't a step, so it isn't counted here. Data: [final code](../results/cascade-2026-11-03-final-code.json), [all logs](../results/cascade-2026-11-03-all-logs.json).

## Task runs and the task suite

### Task runs (live, Nov 1–2, 2026, dev machine in India)

The best recorded run per task:

| Task | What the model did | Steps | Time | Verdict |
|---|---|---|---|---|
| b05 site login | typed credentials, submitted | 3 | 18.7 s | PASS (2 code checks) |
| b01 Google search | typed the query, pressed Enter | 3 | 13.3 s | PASS (URL check) |
| b03 Wikipedia fact | went to Wikipedia and answered "1991" (from the model's own knowledge: Quiff doesn't pass page text to the model yet) | 3 | 19.5 s | PASS (answer check) |
| b04 order form | filled 6 fields in batches, stopped at the approval guard on "Submit order", submitted after approval | 8 | 19.1 s | PASS (3 page checks) |
| d01 Notepad save (desktop) | typed the document, drove the Save As dialog via a driver skill | 2 | 20.0 s | PASS (file check) |
| d01 replay | same task, **0 model calls** | 2 | 11.1 s | PASS |

### Task suite status

The suite (`tasks/tasks.toml`) has 20 tasks with automatic checks, split into `dev` and `hidden`.

| Task | Split | Passed with Muse Spark? |
|---|---|---|
| b01 Google search · b03 Wikipedia fact · b04 order form · b05 login | dev | yes |
| d01 Notepad save · d02 new folder · d03 rename (needed a correction round) | dev | yes |
| b06 dropdown · b07 checkboxes · b08 GitHub license · b09 PyPI version | hidden | yes |
| b10 download | hidden | no: the model doesn't pick the download skill |
| b02 · d04–d10 | dev/hidden | not yet attempted with the model |

Honest caveats:
- **"Passed" means passed at least once.** These are development runs, and many failures came before the fixes that made the tasks pass; some tasks (b08, d01) took many attempts. Run `spikes/regression.py` to measure reliability on the current code.
- **b07 and b08 are hidden tasks that passed only after general engine fixes found while running them.** No task-specific tuning was added, but they aren't unseen results. b10 has since gained guidance for its exact case, so a future b10 pass won't count as unseen either.
- **The b09 check** accepts any version-shaped answer.
- **Google rate-limits headless automation;** run browser tasks with `--headed`.
