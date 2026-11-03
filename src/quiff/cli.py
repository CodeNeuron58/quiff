"""Command-line entry point, run as `quiff` or `python -m quiff`."""

from __future__ import annotations

import argparse
import asyncio
import sys

from quiff import __version__


def main() -> None:
    parser = argparse.ArgumentParser(prog="quiff", description="Fast hands for your AI.")
    parser.add_argument("--version", action="version", version=f"quiff {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("tasks", help="list the task suite")

    run = commands.add_parser("run", help="run one task from tasks/tasks.toml")
    run.add_argument("task_id", help="task id or prefix, e.g. b01")
    run.add_argument("--scripted", action="store_true",
                     help="use the model-free stand-in instead of Muse Spark")
    _add_run_flags(run)

    do = commands.add_parser(
        "do", help="do your own task: a goal in plain words, in Chrome or a desktop app",
        description="Give Quiff any goal. Browser: --url where to start. Desktop: --app, "
                    "which opens in a fresh task folder (~/quiff-tasks/do-<id>/). Checks "
                    "are optional; without them, the model's own 'done' is the only "
                    "evidence and the result says so. A successful run is saved as a flow: "
                    "the same command with --replay repeats it with zero AI calls.")
    do.add_argument("goal", help='e.g. "Log in with username tomsmith and password ..."')
    where = do.add_mutually_exclusive_group(required=True)
    where.add_argument("--url", help="start page (browser goals)")
    where.add_argument("--app", choices=["notepad", "explorer", "calculator", "excel",
                                         "outlook"], help="desktop app (desktop goals)")
    do.add_argument("--check-url", action="append", default=[], metavar="REGEX",
                    help="pass only if the final URL matches (repeatable)")
    do.add_argument("--check-text", action="append", default=[], metavar="TEXT",
                    help="pass only if the final page contains this text (repeatable)")
    do.add_argument("--check-file", action="append", default=[], metavar="PATH",
                    help="pass only if this file exists in the task folder (repeatable)")
    do.add_argument("--check-answer", metavar="REGEX",
                    help="pass only if Quiff's answer matches")
    _add_run_flags(do)

    batch = commands.add_parser(
        "batch", help="teach once, replay with new data: one goal, every row of a CSV",
        description="Row 1 goes to the model, which teaches a template; rows 2..N replay it "
                    "with their own values and zero AI calls, each row checked by code. A row "
                    "whose replay fails before anything was approved falls back to the model; "
                    "a submitted row is never retried. --replay uses the saved template for "
                    "every row.")
    batch.add_argument("goal", help='goal with {column} placeholders, e.g. "Fill in the order '
                                    'form: name {name}, size {size}. Then submit it."')
    batch.add_argument("--url", required=True, help="start page (may use {column})")
    batch.add_argument("--data", required=True, help="CSV file; its header row names the columns")
    batch.add_argument("--check-url", action="append", default=[], metavar="REGEX",
                       help="every row must end on a matching URL (repeatable)")
    batch.add_argument("--check-text", action="append", default=[], metavar="TEXT",
                       help="every row's final page must contain this; may use {column}")
    batch.add_argument("--rows", type=int, default=0, help="only the first N rows")
    _add_run_flags(batch)

    observe = commands.add_parser("observe", help="print the action table for a URL")
    observe.add_argument("url")
    observe.add_argument("--headed", action="store_true")

    args = parser.parse_args()
    try:
        asyncio.run(_dispatch(args))
    except KeyError as exc:
        raise SystemExit(str(exc.args[0]))
    except Exception as exc:
        raise SystemExit(f"quiff: {exc}")


async def _dispatch(args: argparse.Namespace) -> None:
    if args.command == "tasks":
        from quiff.tasks import load_tasks

        for task in load_tasks():
            print(f"{task.id:22} {task.split:6} {task.app:10} {task.goal}")
        return

    if args.command == "observe":
        from quiff.observe.browser import BrowserObserver

        observer = BrowserObserver(headless=not args.headed)
        try:
            table = await observer.open(args.url)
            print(table.render())
            print(f"\n{len(table.rows)} rows · ~{table.approx_tokens()} tokens · "
                  f"{table.read_ms} ms read")
        finally:
            await observer.stop()
        return

    if args.command == "batch":
        await _run_batch(args)
        return

    if args.command in ("run", "do"):
        from quiff.tasks import get_task, load_tasks

        task = (get_task(args.task_id, load_tasks()) if args.command == "run"
                else adhoc_task(args))
        await _run_task(task, args)
        return


def _add_run_flags(command: argparse.ArgumentParser) -> None:
    """The flags `run` and `do` share."""
    command.add_argument("--headed", action="store_true", help="show the browser window")
    command.add_argument("--max-steps", type=int, default=25)
    command.add_argument("--replay", action="store_true",
                         help="replay the recorded flow with no model calls (recall stage)")
    command.add_argument("--model", choices=["meta", "open"], default="meta",
                         help="meta: Muse Spark on the Meta Model API (default); open: Muse "
                              "Spark Open on a partner host (MUSE_OPEN_* in .env)")
    command.add_argument("--effort", default="minimal",
                         help="reasoning effort for decisions (default: minimal)")
    command.add_argument("--interactive", action="store_true",
                         help="ask in the terminal before irreversible actions")
    command.add_argument("--show", action="store_true",
                         help="show the on-screen overlay (status pill, ghost cursor); "
                              "opens Chrome headed. Needs `npm install` in overlay/ once")
    command.add_argument("--show-scale", type=float, default=1.0,
                         help="overlay size, e.g. 1.5 for screen recordings (default 1.0)")


def adhoc_task(args: argparse.Namespace):
    """A Task for `quiff do`: the goal, where to start, and any checks given.

    The id is a hash of goal + start, so the same command finds its recorded
    flow again for --replay."""
    import hashlib

    from quiff.tasks import Task

    start = args.url or args.app
    key = hashlib.sha1(f"{args.goal}|{start}".encode("utf-8")).hexdigest()[:8]
    checks = ([{"type": "url", "pattern": p} for p in args.check_url]
              + [{"type": "page_text", "contains": t} for t in args.check_text]
              + [{"type": "file", "path": f} for f in args.check_file]
              + ([{"type": "answer", "pattern": args.check_answer}]
                 if args.check_answer else []))
    if args.url:
        return Task(id=f"do-{key}", app="chrome", split="adhoc", goal=args.goal,
                    start={"url": args.url}, checks=checks)
    start_spec = {"app": args.app}
    if args.app == "explorer":
        start_spec["folder"] = "."
    return Task(id=f"do-{key}", app=args.app, split="adhoc", goal=args.goal,
                start=start_spec, checks=checks)


async def _run_task(task, args: argparse.Namespace) -> None:
    """Run one task (from the suite, or from `quiff do`) and report it."""
    from quiff.decide import MatchDecider
    from quiff.recall import FlowRecorder
    from quiff.tasks import run_task

    scripted = getattr(args, "scripted", False)
    show = None
    if args.show:
        # first, before any observer: it makes Quiff read real screen pixels
        from quiff.show.overlay import Overlay, OverlayError

        try:
            show = Overlay(scale=args.show_scale)
        except OverlayError as exc:
            raise SystemExit(f"quiff: {exc}")
        args.headed = True  # the overlay points at a window you can see
    client = None
    rescuer = None
    if args.replay:
        from quiff.recall import ReplayDecider, load_flow

        decider = ReplayDecider(load_flow(task.id))
    elif scripted:
        decider = FlowRecorder(MatchDecider(_script_for(task)), task.id, task.goal)
    else:
        from quiff.models.meta import MuseDecider, display_name, make_client

        client = make_client(args.model)  # one model for the plan, decisions and rescue
        # quick decisions keep a short timeout: a hung minimal call is retried,
        # then rescued, instead of stalling the run for a minute
        quick = make_client(args.model, timeout=_decision_timeout(args.effort))
        name = display_name(client.model)
        if task.app == "chrome":
            rescuer = MuseDecider(client=client, effort="high", max_tokens=16384)
            decider = FlowRecorder(
                MuseDecider(client=quick, effort=args.effort), task.id, task.goal,
            )
            print(f"brain    {name} ({args.effort}) + rescue on high · {client.model}")
        else:
            rescuer = MuseDecider(client=client, effort="high", max_tokens=16384,
                                  allow_wait=False, allow_navigate=False)
            decider = FlowRecorder(
                MuseDecider(client=quick, effort=args.effort, allow_wait=False,
                            allow_navigate=False), task.id, task.goal,
            )
            print(f"brain    {name} ({args.effort}, desktop: no wait/navigate) "
                  f"+ rescue on high · {client.model}")
    if task.app == "chrome":
        from quiff.observe.browser import BrowserObserver

        observer = BrowserObserver(headless=not args.headed)
    else:
        from quiff.observe.desktop import DesktopObserver

        observer = DesktopObserver()
    approver = None
    if args.interactive:
        import asyncio as _aio

        async def approver(reason: str) -> bool:
            prompt = "APPROVAL NEEDED: " + reason + ". Approve? y/n: "
            answer = await _aio.to_thread(input, prompt)
            return answer.strip().lower().startswith("y")

    planned = None
    plan_calls = 0
    if client is not None:
        if show is not None:
            show.state("working", "Planning the task")
        try:
            from quiff.plan import plan

            planned = await plan(task.goal, client=client)
            print("plan     " + " -> ".join(planned[:4]) + (" -> ..." if len(planned) > 4 else ""))
        except Exception as exc:
            print(f"plan     (unavailable: {exc})")
        plan_calls = 1  # the plan request was made, whether or not it succeeded
        if show is not None:
            from quiff.models.meta import display_name

            show.calls_offset = plan_calls
            show.meter(f"Plan: {display_name(client.model)} (high)", 0)

    run = None
    try:
        run = await run_task(task, decider, observer, max_steps=args.max_steps,
                             rescuer=rescuer,
                             approver=approver,
                             planned=planned,
                             show=show)
    finally:
        if show is not None:
            show.state("yourturn", _ending_line(run, plan_calls))
            await asyncio.sleep(3.5)  # let the ending be seen before the window closes
        await observer.stop()
        if show is not None:
            show.close()
    print(f"task     {run.task.id} ({run.task.split})")
    mode = "replay" if args.replay else ("scripted" if scripted else "model")
    print(f"mode     {mode}")
    print(f"steps    {run.loop.steps} · {run.loop.elapsed_s}s · {run.loop.stop_reason}")
    print(f"calls    {run.loop.model_calls + plan_calls} AI calls"
          f"{' (incl. the plan)' if plan_calls else ''} · {run.loop.rescues} rescues")
    if run.loop.answer:
        print(f"answer   {run.loop.answer}")
    for check in run.checks:
        print(f"check    {'PASS' if check.ok else 'FAIL'} [{check.check_type}] {check.detail}")
    if not run.checks:
        print("check    (none given: the model's own 'done' is the only evidence)")
    if getattr(observer, "cleanup_note", ""):
        print(f"cleanup  {observer.cleanup_note}")
    print(f"result   {'PASS' if run.success else 'FAIL'}")
    if run.success and mode == "model" and args.command == "do":
        print("replay   the same command with --replay repeats it with zero AI calls")
    if not run.success:
        raise SystemExit(1)


def _decision_timeout(effort: str) -> float:
    """Seconds a decision call may take: 20 at minimal effort (MuseDecider's own
    default when it makes its client), 60 when the model is asked to think."""
    return 20.0 if effort == "minimal" else 60.0


async def _run_batch(args: argparse.Namespace) -> None:
    """`quiff batch`: teach on row 1, replay the rest, report every row."""
    import json
    import time
    from pathlib import Path

    from quiff.batch import Teacher, batch_id, load_rows, load_template, run_batch
    from quiff.observe.browser import BrowserObserver
    from quiff.recall import FlowRecorder

    rows = load_rows(Path(args.data))
    if args.rows:
        rows = rows[: args.rows]
    checks = ([{"type": "url", "pattern": p} for p in args.check_url]
              + [{"type": "page_text", "contains": t} for t in args.check_text])
    show = None
    if args.show:
        from quiff.show.overlay import Overlay, OverlayError

        try:
            show = Overlay(scale=args.show_scale)
        except OverlayError as exc:
            raise SystemExit(f"quiff: {exc}")
        args.headed = True

    template, teacher = None, None
    if args.replay:
        template = load_template(batch_id(args.goal, args.url))
        print(f"template {len(template.steps)} steps · every row replays with zero AI calls")
    else:
        from quiff.models.meta import MuseDecider, display_name, make_client
        from quiff.plan import plan

        client = make_client(args.model)
        quick = make_client(args.model, timeout=_decision_timeout(args.effort))
        print(f"brain    {display_name(client.model)} ({args.effort}) teaches on row 1 "
              f"+ rescue on high · {client.model}")

        async def teacher(task):
            planned = None
            try:
                planned = await plan(task.goal, client=client)
            except Exception as exc:
                print(f"plan     (unavailable: {exc})")
            return Teacher(
                decider=FlowRecorder(MuseDecider(client=quick, effort=args.effort),
                                     task.id, task.goal),
                rescuer=MuseDecider(client=client, effort="high", max_tokens=16384),
                planned=planned, calls_before=1)

    yes_to_all: set[str] = set()

    async def approver(reason: str) -> bool:
        if reason in yes_to_all:
            print(f"approved {reason} (yes to all rows)")
            return True
        if not args.interactive:
            return False
        answer = await asyncio.to_thread(
            input, f"APPROVAL NEEDED: {reason}. Approve? y = yes, a = yes for every row, n = no: ")
        answer = answer.strip().lower()
        if answer.startswith("a"):
            yes_to_all.add(reason)
            return True
        return answer.startswith("y")

    def on_row(result, total: int) -> None:
        print(f"row {result.row:>3}/{total}  {'PASS' if result.success else 'FAIL'}  "
              f"{result.mode:<28} {result.elapsed_s:>6.1f} s  {result.model_calls} AI calls"
              + ("" if result.success else f"  · {result.stop_reason[:70]}"))

    observer = BrowserObserver(headless=not args.headed)
    result = None
    try:
        result = await run_batch(args.goal, args.url, rows, checks, observer=observer,
                                 teacher=teacher, template=template, approver=approver,
                                 show=show, max_steps=args.max_steps, on_row=on_row)
    finally:
        if show is not None:
            if result is not None:
                show.state("yourturn", f"Batch done · {result.passed}/{len(result.rows)} rows · "
                                       f"{result.model_calls} AI calls · {result.elapsed_s:.1f} s")
            else:
                show.state("yourturn", "Batch stopped · you have control")
            await asyncio.sleep(3.5)
        await observer.stop()
        if show is not None:
            show.close()
    summary = result.summary()
    print(f"batch    {summary['passed']}/{summary['rows']} rows passed · "
          f"{summary['replayed_with_zero_ai_calls']} replayed with zero AI calls · "
          f"{summary['model_calls']} AI calls in total · {summary['elapsed_s']} s")
    if result.template_columns:
        print(f"template {result.template_file} · columns {', '.join(result.template_columns)}")
    out = Path("runs") / f"batch-{time.strftime('%Y-%m-%d-%H%M%S')}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"summary": summary, "goal": args.goal, "url": args.url,
                               "data": str(args.data),
                               "rows": [r.__dict__ for r in result.rows]}, indent=2),
                   encoding="utf-8")
    print(f"saved    {out}")
    if result.passed < len(result.rows):
        raise SystemExit(1)


def _ending_line(run, extra_calls: int = 0) -> str:
    """The pill's last words: what happened, now that control is back with you,
    and what it cost (AI calls, including the plan, and seconds)."""
    if run is None:
        return "Run stopped · you have control"
    calls = run.loop.model_calls + extra_calls
    cost = f" · {calls} AI call{'' if calls == 1 else 's'} · {run.loop.elapsed_s:.1f} s"
    if run.success:
        return ("Done · all checks pass" if run.checks else "Done · no checks given") + cost
    reason = run.loop.stop_reason
    if reason.startswith("needs approval"):
        return "Stopped · not approved · you have control"
    return f"Not done · {reason}"


def _script_for(task) -> list[MatchStep]:
    """The model-free scripts for the dev tasks the loop already supports.

    These stand in for the decision model until the Meta API key lands; each is
    the path a competent model would take, expressed as row matches.
    """
    from quiff.decide import MatchStep

    scripts: dict[str, list[MatchStep]] = {
        "b01-google-search": [
            MatchStep("type", "combobox", "search", ("langchain",)),
            MatchStep("press", values=("Enter",)),
        ],
        "b02-wikipedia-article": [
            # The header search input renders 0x0 until the site's JS moves it,
            # so go through the Search page like a person would.
            MatchStep("click", "link", "search"),
            MatchStep("type", "searchbox", "search", ("Kolkata",)),
            MatchStep("press", values=("Enter",)),
            MatchStep("click", "link", "Kolkata"),
        ],
        "b05-test-login": [
            MatchStep("type", "textbox", "user", ("tomsmith",)),
            MatchStep("type", "textbox", "pass", ("SuperSecretPassword!",)),
            MatchStep("click", "button", "login"),
        ],
    }
    # Desktop: d01 — type into Notepad's document, then save into the sandbox with
    # save_as, the same verified primitive the model uses. (Typing a path into the
    # Save dialog's box doesn't stick on Windows 11: it saves under its own name.)
    if task.app != "chrome":
        from quiff.tasks import sandbox_dir

        target = sandbox_dir(task) / "hello.txt"
        return [
            MatchStep("type", "document", "", ("Hello from Quiff",)),
            MatchStep("save_as", values=(str(target),)),
        ]
    for task_id, steps in scripts.items():
        if task.id == task_id or task.id.startswith(task_id):
            return steps
    raise KeyError(f"no model-free script for {task.id}; the decision model takes over here")
