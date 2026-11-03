# Quiff

*Fast hands for your AI.*

Quiff is an engine that drives Windows desktop apps and Chrome. It reads the screen as structured data (the accessibility tree on Windows, the DevTools protocol in Chrome) instead of screenshots. Meta's Muse Spark picks each action from a numbered table of controls, plain code checks every result, irreversible actions wait for a human, and a task Quiff has completed once can be replayed with no model call at all.

> **Status: hackathon build (Nov 2026).** Working end to end on the tasks listed below, in the browser and on the Windows desktop, with Meta's models in the loop. Pre-alpha; Windows + Chrome only.

## Why

Today's computer-use agents are slow because a large model thinks before and after every click, not because clicking is slow. In the OSWorld-Human study (MLSys 2026), large-model calls took 90–97% of agent time, and one agent took 12 minutes to change line spacing. Quiff keeps the model's job small and lets code do the rest.

## How it works

| Stage | What it does |
|---|---|
| **Plan** | Muse Spark (high reasoning) splits the task into subgoals: one call at the start. |
| **Observe** | The screen becomes one numbered action table, e.g. `[12] button "Save" · enabled`, read from Windows UI Automation or Chrome's DevTools protocol. No screenshots. |
| **Decide** | Muse Spark (minimal reasoning) answers one JSON-schema request per step: which action, which row, did the last step work, is the subgoal done. |
| **Act** | Accessibility patterns (`Invoke`, `SetValue`) on Windows, DevTools input in Chrome, keyboard input as the fallback. Shared flows (Save As, menus, downloads) are driver skills. Irreversible actions wait for a human (see Safety). |
| **Verify** | Plain code checks the result; the model's "done" is never trusted. When the model says done but the task's checks disagree, the failed checks are fed back for up to two correction rounds. |
| **Learn** | Every step is logged (JSONL). A successful run is saved as a flow. |
| **Replay** | `quiff run <task> --replay` re-runs a saved flow with **no model calls**, re-finding each control by role and name, so it survives layout shifts. (Replay is per task today; per-step recall inside model runs is next.) |

When the minimal decider is unsure, Muse Spark at high reasoning (the rescuer) takes the step. That happens on low confidence, two failed actions in a row, two failed decider calls, too many waits, or a detected loop. It's capped at 3 rescues per run, and a second loop stops the run honestly instead of burning time.

## The Meta models in the loop

- **Muse Spark** (`muse-spark-1.3`, Meta Model API), the decision model: one JSON-schema-structured call per step picks the action and row from the numbered table.
  - **Go/no-go benchmark** (`spikes/model_probe.py`, one fixed row-picking prompt, 20 tries each): ~1.5 s median at minimal reasoning, 100% schema-valid, 100% correct row.
  - **Real task runs** (bigger tables): about 2.4 s median per decision.
- **Muse Spark at high reasoning:** plans the subgoals and rescues the loop (see above).
- **Muse Spark Open** (`meta-models/muse-spark-open-1026` on Baseten): wired into the same decision interface (`muse_spark_open()`), **not yet run**, because Baseten credits didn't arrive during the hackathon.

What the benchmark taught us: at *low* reasoning effort only 55% of replies were valid JSON, against 100% at minimal. For narrow, well-structured picks, more thinking made the output less reliable, so the decider runs at minimal and the verification layer catches the rest.

## Safety by construction

- **Approval guard.** Nothing irreversible runs without a human's yes (`--interactive`); without an approver, the run stops and says why. The guard covers:
  - clicking Submit / Send / Pay / Delete / Place order and the like (whole words, on buttons, links and menu items, so "Postal code" or "PayPal" don't trigger it);
  - Enter or Space that would trigger such a control, judged by what holds keyboard focus (Enter in a form whose submit button is "Submit order" asks first);
  - Ctrl+Enter, Alt+S and Shift+Delete, plus Delete on desktop items;
  - every download;
  - any Save As that would overwrite a file or write outside the task's folder.
- **Only its own windows.** Quiff follows the window it launched and the dialogs that window owns. It refuses to start while Notepad is open, never closes or edits the user's windows, and never sends a keystroke unless its own window is in front.
- **Its own browser.** Every run uses a temporary Chrome profile on a port Chrome picks itself, never an existing browser.
- **Secrets.** Text typed into password-like fields is redacted in step logs. Recorded flows keep real values so replay works; they stay local in the gitignored `runs/` folder.

## Results

### The race: screenshot agent vs Quiff vs replay

Setup:
- **Task:** one login task on a local, deterministic page (`spikes/race_local.py`).
- **Browser:** a fresh headless Chrome for every run.
- **Runs:** 5 per mode, measured Nov 3, 2026 at commit `270ab9e`.
- **Data:** `results/race-local-2026-11-03-0613-fair.json`.

| Mode | How it works | Success | Median time (successful runs) | Model calls per run |
|---|---|---|---|---|
| Screenshot agent | a screenshot + one Muse Spark vision call per step, pixel clicks (how today's computer-use agents work) | 4/5 | 41.9 s | 4–5 |
| **Quiff** | the action table + Muse Spark at minimal reasoning | **5/5** | **16.7 s** | 4 |
| **Replay** | the flow Quiff recorded, re-run | **5/5** | **1.9 s** | **0** |

What it shows:
- **Quiff finished about 2.5× faster** than the screenshot agent, with the same number of model calls: each Quiff call carries a small text table instead of an image.
- **Replay was about 22× faster,** with no model calls at all.
- **The screenshot agent's one failure** was the API timing out on its image calls.

Caveats:
- **It's one task on one machine,** and model latency varies with load. Quiff's median ranged from 14.3 s to 16.7 s across the day's batches.
- **The screenshot agent is our stand-in** built on the same Muse Spark model, not a third-party product.
- **Timings include launching Chrome.**
- **Every batch is in [`results/`](results/README.md),** including two run before the harness was made fair, with what changed.

### Task runs (live, Nov 1–2, 2026, dev machine in India)

The best recorded run per task:

| Task | What the model did | Steps | Time | Verdict |
|---|---|---|---|---|
| b05 site login | typed credentials, submitted | 3 | 18.7 s | PASS (2 code checks) |
| b01 Google search | typed the query, pressed Enter | 3 | 13.3 s | PASS (URL check) |
| b03 research task | navigated Wikipedia, answered "1991" | 3 | 19.5 s | PASS (answer check) |
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

## Try it

Requires Python 3.11+, [uv](https://docs.astral.sh/uv/), Windows, and Chrome.

```sh
uv sync
echo META_API_KEY=your-key > .env      # from ai.developer.meta.com
uv run quiff tasks                     # list the 20-task suite
uv run quiff run b05 --headed          # the model logs into a public test site
uv run quiff run b04 --interactive --headed   # the approval guard asks before "Submit order"
uv run quiff run d01                   # records a replayable flow (close Notepad first)
uv run quiff run d01 --replay          # then replay it: no model calls
uv run python spikes/race_local.py --runs 5    # the race, on a local deterministic page
uv run python spikes/regression.py --runs 3    # re-test the demo tasks on your machine
uv run python spikes/model_probe.py    # the go/no-go benchmark
```

`quiff run` drives a real browser window or app; every step is logged to `runs/` as JSONL.

## What's real vs what's next

**Working today:**
- the browser and Windows loops with Muse Spark;
- driver skills (Save As, menus, downloads) and per-app skill files;
- the approval guard and ownership-based window safety;
- rescue escalation with a budget, and correction rounds from failed checks;
- JSONL step logs, plus flow recording and zero-model-call replay;
- the race and regression harnesses, with provenance.

**Next:**
- per-step recall inside model runs;
- reading page text and content below the fold, for answer tasks;
- an on-screen status bar;
- the remaining tasks (b02, d04–d10) and more runs per task;
- a first Muse Spark Open run;
- a decision model trained on the step logs.

## Built during the hackathon

Everything substantive after the `pre-hackathon` tag was built during the Meta Global AI Developer Hackathon (Oct 27 – Nov 4, 2026): the entire engine, the model layer, the safety layer, the task suite, the probe, and the race and regression harnesses with their results in `results/`. Compare: [pre-hackathon...main](https://github.com/CodeNeuron58/quiff/compare/pre-hackathon...main). Before the tag there was only the package skeleton, the README, and the license.

## License

Licensed under the [Apache License, 2.0](LICENSE). See [NOTICE](NOTICE) for the copyright notice.

The license covers the code, not the name. "Quiff" may not be used as the name of forks or other products (Section 6 of the license).
