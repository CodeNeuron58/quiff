# Quiff

*Fast hands for your AI.*

Quiff is an engine that drives Windows desktop apps and Chrome. It reads the screen as structured data (the accessibility tree on Windows, the DevTools protocol in Chrome) instead of screenshots. Meta's Muse Spark picks each action from a numbered table of controls, plain code checks every result, irreversible actions wait for a human, and a task Quiff has completed once can be replayed with no model call at all.

> **Status: hackathon build (Nov 2026).** Working end to end on the tasks listed below, in the browser and on the Windows desktop, with Meta's models in the loop. Pre-alpha; Windows + Chrome only.

## Why

Today's computer-use agents are slow because a large model thinks before and after every click, not because clicking is slow. In the OSWorld-Human study (MLSys 2026), large-model calls took 90–97% of agent time, and one agent took 12 minutes to change line spacing. Quiff keeps the model's job small and lets code do the rest.

## How it works

| Stage | What it does |
|---|---|
| **Plan** | Muse Spark (high reasoning) writes a short plan of subgoals once, at the start. Every decision sees the plan as context; the loop doesn't step through the subgoals one by one. |
| **Observe** | The screen becomes one numbered action table, e.g. `[12] button "Save" · enabled`, read from Windows UI Automation or Chrome's DevTools protocol. No screenshots. |
| **Decide** | Muse Spark (minimal reasoning) answers one JSON-schema request per step: which action, which row, did the last step work, is the subgoal done. |
| **Act** | Accessibility patterns (`Invoke`, `SetValue`) on Windows, DevTools input in Chrome, keyboard input as the fallback. Shared flows (Save As, menus, downloads) are driver skills. Irreversible actions wait for a human (see Safety). |
| **Verify** | Plain code checks the result; the model's "done" is never trusted. When the model says done but the task's checks disagree, the failed checks are fed back for up to two correction rounds. |
| **Learn** | Every step is logged (JSONL). A successful run is saved as a flow. |
| **Replay** | `quiff run <task> --replay` re-runs a saved flow with **no model calls**, re-finding each control by role and name, so it survives layout shifts. (Replay is per task today; per-step recall inside model runs is next.) |

When the minimal decider is unsure, Muse Spark at high reasoning (the rescuer) takes the step. That happens on low confidence, two failed actions in a row, two failed decider calls, too many waits, or a detected loop. It's capped at 3 rescues per run, and a second loop stops the run honestly instead of burning time.

## The Meta models in the loop

- **Muse Spark** (`muse-spark-1.3`, Meta Model API), the decision model: one JSON-schema-structured call per step picks the action and row from the numbered table.
  - **Go/no-go benchmark** (`spikes/model_probe.py`, one fixed row-picking prompt, 20 tries per effort, an 8,192-token budget; Nov 3): 100% schema-valid and 100% correct row at minimal, low and high effort; median decision 4.5 s at minimal. On Nov 1 the same probe measured 1.5 s at minimal: model latency varies a lot with load.
  - **Real task runs** (bigger tables, Nov 1–2): about 2.4 s median per decision.
- **Muse Spark at high reasoning:** plans the subgoals and rescues the loop (see above).
- **Muse Spark Open** (open weights, on a partner host: Modal, Baseten or Fireworks, set with `MUSE_OPEN_*` in `.env`): wired into the same decision interface. `--model open` runs the plan, the decisions and the rescues on it, in `quiff run`, `quiff do` and the race. **Not yet run.**

What the benchmark taught us: for a narrow, well-structured pick, more reasoning buys nothing and costs time. Every effort chose the right row in 20 of 20 tries; low effort took 1.3× and high effort 1.7× as long as minimal, writing 1.8× and 2.4× as many tokens. The decider runs at minimal, and the verification layer catches the rest.

| Effort | Median decision | Right row | Valid JSON | Cut off (budget) | Median output tokens |
|---|---|---|---|---|---|
| minimal | 4.5 s | 100% | 100% | 0% | 286 |
| low | 5.7 s | 100% | 100% | 0% | 512.5 |
| high | 7.6 s | 100% | 100% | 0% | 673.5 |

[Chart](results/effort-study.svg) · [data](results/model-probe-2026-11-03-200851.json). An earlier README said only 55% of low-effort replies were valid JSON. That probe capped replies at 512 tokens, and low effort's median reply is about 512 tokens, so about half were cut off mid-answer. With room to finish, low effort is 100% valid.

## Safety by construction

- **Approval guard.** Actions that look irreversible wait for a human's yes (`--interactive`); without an approver, the run stops and says why. The guard covers:
  - clicking Submit / Send / Pay / Delete / Place your order / Proceed to payment / Move to Trash / Cancel subscription / Unsubscribe / Merge pull request and the like (whole words, on buttons, links and menu items, so "Postal code", "PayPal" or a dialog's plain "Cancel" don't trigger it);
  - Enter or Space that would trigger such a control, judged by what holds keyboard focus (Enter in a form whose submit button is "Submit order" asks first);
  - Ctrl+Enter, Alt+S and Shift+Delete, plus Delete on desktop items;
  - every download;
  - any Save As that would overwrite a file or write outside the task's folder, and "Yes" in a "Confirm Save As" dialog, however the save was started.
- **What the guard can't see.** It judges controls by their names, so an irreversible button with an unusual name can still get through. Navigating to a URL isn't checked, and a save typed by hand into a Save dialog is only caught at the overwrite confirmation, not when it writes a new file outside the task folder.
- **Only its own windows.** Quiff follows the window it launched and the dialogs that window owns. It refuses to start while Notepad is open, never closes or edits the user's windows, and never sends a keystroke unless its own window is in front.
- **Its own browser.** Every run uses a temporary Chrome profile on a port Chrome picks itself, never an existing browser.
- **Secrets.** Text typed into password-like fields is redacted in step logs. Recorded flows keep real values so replay works; they stay local in the gitignored `runs/` folder.

## Results

### The race: screenshot agent vs Quiff vs replay

Setup:
- **Two tasks** on local, deterministic pages (`spikes/race_local.py`): a login (3 steps), and a 6-field pizza order form that ends at "Submit order" (a local copy of httpbin's form).
- **Same model and settings for both agents:** Muse Spark (`muse-spark-1.3`) at minimal reasoning, an 8,192-token budget per call, and a 25-step cap. Quiff's rescuer (high reasoning) may step in when the decider is unsure; it never did in these runs.
- **Approvals** are granted automatically and counted: "Submit order" asked once in every Quiff order run. The screenshot agent has no guard.
- **Quiff runs without** the planning call and app guidance that `quiff run` adds.
- **Browser:** a fresh headless Chrome for every run; times include launching it.
- **Runs:** 5 per mode per task, measured Nov 3, 2026. Data: [login](results/race-local-2026-11-03-201914.json), [order form](results/race-local-2026-11-03-203300.json).

| Task | Mode | Success | Median | p95 | Model calls per run |
|---|---|---|---|---|---|
| Login | Screenshot agent: a screenshot + one Muse Spark vision call per step, pixel clicks | 5/5 | 16.1 s | 18.4 s | 4–5 |
| Login | **Quiff**: the action table + one Muse Spark text call per step | **5/5** | **15.0 s** | 18.6 s | 4 |
| Login | **Replay**: the flow Quiff recorded, re-run | **5/5** | **2.2 s** | 4.3 s | **0** |
| Order form | Screenshot agent | **0/5** | 109.6 s (ran out of steps) | 114.8 s | 25–26 |
| Order form | **Quiff** | **5/5** | **25.6 s** | 30.3 s | 9 |
| Order form | **Replay** | **5/5** | **2.4 s** | 2.6 s | **0** |

What it shows:
- **Login: about the same speed.** With matched settings, Quiff was only 1.07× faster than the screenshot agent (15.0 s vs 16.1 s). The earlier published 1.8× came mostly from the screenshot agent running at the model's default reasoning effort while Quiff ran at minimal.
- **Order form: the screenshot agent never finished.** In all 5 runs it typed the three text fields and chose "Medium", then kept clicking at about (26, 414), 2–3 px above-left of the 13-px "Extra Cheese" checkbox (which spans x 28–41, y 416–429). The click never landed, so it tried again until it ran out of steps. Quiff acts on the control itself, never on coordinates: 5/5, every field right.
- **Replay** repeats a task Quiff has done once, with zero model calls: 7× faster than the screenshot agent on the login, and 10.7× faster than Quiff's own first run on the order form.

Caveats:
- **Two simple tasks on one machine,** and model latency varies with load.
- **The screenshot agent is our stand-in** built on the same Muse Spark model, not a third-party product. It gets the same typing, key handling, settle-and-retry, effort, token budget and step cap as Quiff. A stronger vision model, or a zoom step, might hit the checkbox.
- **The first order batch** gave the screenshot agent a 12-step cap against Quiff's 25. It failed the same way (0/5); the order numbers above come from the re-run with equal caps.
- **Every batch from the day is in [`results/`](results/README.md),** including earlier ones run before the harness was made fair, with what changed in each.

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

## Try it

Requires Python 3.11+, [uv](https://docs.astral.sh/uv/), Windows, and Chrome (plus Node.js for `--show`).

```sh
uv sync
echo META_API_KEY=your-key > .env      # from ai.developer.meta.com
uv run quiff tasks                     # list the 20-task suite
uv run quiff run b05 --headed          # the model logs into a public test site
cd overlay && npm install && cd ..     # once, for --show
uv run quiff run b05 --show            # watch it: status pill and ghost cursor
uv run quiff run b05 --show --show-scale 1.5   # bigger overlay, for screen recordings
uv run quiff run b04 --interactive --headed   # the approval guard asks before "Submit order"
uv run quiff run d01                   # records a replayable flow (close Notepad first)
uv run quiff run d01 --replay          # then replay it: no model calls
uv run quiff run b05 --model open      # the same run on Muse Spark Open (MUSE_OPEN_* in .env)
uv run python spikes/race_local.py --runs 5    # the race: 2 local tasks, both agents on the same settings
uv run python spikes/regression.py --runs 3    # re-test the demo tasks on your machine
uv run python spikes/model_probe.py    # the go/no-go benchmark
```

`quiff run` drives a real browser window or app; every step is logged to `runs/` as JSONL.

### Your own task: `quiff do`

Give Quiff any goal in plain words, with where to start. Checks are optional; without them the model's own "done" is the only evidence, and the result says so.

```sh
uv run quiff do "Log in with username tomsmith and password SuperSecretPassword!" \
    --url https://the-internet.herokuapp.com/login --check-url "secure$" --show
uv run quiff do "Log in with username tomsmith and password SuperSecretPassword!" \
    --url https://the-internet.herokuapp.com/login --replay        # again, zero AI calls
uv run quiff do "Write a shopping list with milk and eggs, save it as list.txt" \
    --app notepad --check-file list.txt                            # desktop: a fresh task folder
```

A successful run is saved as a flow, so the same command with `--replay` repeats it with zero AI calls. Desktop goals start in a fresh folder, `~/quiff-tasks/do-<id>/`.

`--show` puts Quiff's overlay on screen:
- **Status pill:** says what Quiff is doing. Its four states are working, unsure (the rescuer took over), approval needed, and your turn.
- **Ghost cursor:** glides to each target and highlights it just before Quiff acts.
- **Meter:** the pill's right side says what decided each step and how long it took (`Muse Spark (minimal) · 1.4 s`, `Rescuer: Muse Spark (high) · 5.8 s`, `Replay`), with the run's AI calls so far. The last line gives the totals, e.g. `Done · all checks pass · 4 AI calls · 15.8 s` (the plan counts as a call).
- **Size:** `--show-scale 1.5` makes the pill, chips and cursor bigger for screen recordings.
- **Safe to watch:** the real mouse never moves, and typed passwords are never shown. The overlay is click-through, never takes focus, and screen recorders capture it.

The overlay is a small Electron app in `overlay/`. `npm run demo` there previews it without running a task.

## What's real vs what's next

**Working today:**
- the browser and Windows loops with Muse Spark;
- `quiff do`: your own goals, in Chrome or a desktop app, with optional checks;
- driver skills (Save As, menus, downloads) and per-app skill files;
- the approval guard and ownership-based window safety;
- rescue escalation with a budget, and correction rounds from failed checks;
- JSONL step logs, plus flow recording and zero-model-call replay;
- the race and regression harnesses, with provenance;
- the on-screen overlay (`--show`): status pill, ghost cursor, highlight box, approval edge glow.

**Next:**
- per-step recall inside model runs;
- reading page text and content below the fold, for answer tasks;
- a Muse Spark second opinion on any button the approval guard's word list doesn't know;
- the remaining tasks (b02, d04–d10) and more runs per task;
- a first Muse Spark Open run;
- a decision model trained on the step logs.

## Built during the hackathon

Everything substantive after the `pre-hackathon` tag was built during the Meta Global AI Developer Hackathon (Oct 27 – Nov 4, 2026): the entire engine, the model layer, the safety layer, the task suite, the probe, and the race and regression harnesses with their results in `results/`. Compare: [pre-hackathon...main](https://github.com/CodeNeuron58/quiff/compare/pre-hackathon...main). Before the tag there was only the package skeleton, the README, and the license.

## License

Licensed under the [Apache License, 2.0](LICENSE). See [NOTICE](NOTICE) for the copyright notice.

The license covers the code, not the name. "Quiff" may not be used as the name of forks or other products (Section 6 of the license).
