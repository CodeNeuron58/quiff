"""The race on a local, deterministic target: no network noise, no cold starts.

Serves two pages on 127.0.0.1 and races three modes on each:

    login   a login page (a stand-in for the-internet.herokuapp.com/login): 3 steps
    order   a 6-field pizza order form (a stand-in for httpbin.org/forms/post),
            ending at "Submit order", which Quiff's approval guard stops at

    slow    a screenshot agent: screenshot + one Muse Spark vision call per step,
            pixel clicks
    quiff   Quiff: the action table + one Muse Spark text call per step
    replay  the flow Quiff recorded, re-run with zero model calls

Fairness: both agents use the same model, the same reasoning effort
(--effort, default minimal) and the same token budget (--max-tokens), so the
race compares how each one sees the screen, not how hard each one thinks.
Approvals are granted automatically and counted: a person's reaction time
isn't what is measured, and the screenshot agent has no guard. The race runs
Quiff's loop without the planning call and app guidance `quiff run` adds.

    uv run python spikes/race_local.py --runs 5
    uv run python spikes/race_local.py --runs 5 --tasks order --model open
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))       # spikes/
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # repo root

from quiff.models.meta import MuseDecider, load_env
from quiff.tasks import Task, sandbox_dir

LOGIN_PAGE = """<!doctype html><html><head><title>Login Page</title></head>
<body><h2>Login Page</h2><p>This is a local test target.</p>
<form method="post" action="/authenticate">
  <label>Username <input type="text" name="username"></label><br>
  <label>Password <input type="password" name="password"></label><br>
  <button type="submit"> Login</button>
</form></body></html>"""

FAIL_PAGE = """<html><head><title>Login Failed</title></head>
<body><p>Your username is invalid!</p></body></html>"""

SECURE_PAGE = """<html><head><title>Secure Area</title></head>
<body><div class="flash success">You logged into a secure area!</div>
<h2> Secure Area</h2><p>Welcome to the secure area.</p></body></html>"""

# A local copy of httpbin.org/forms/post: the same fields, labels and button.
ORDER_PAGE = """<!doctype html><html><head><title>Pizza order</title></head>
<body><h2>Pizza order</h2>
<form method="post" action="/order">
<p><label>Customer name: <input name="custname"></label></p>
<p><label>Telephone: <input type="tel" name="custtel"></label></p>
<p><label>E-mail address: <input type="email" name="custemail"></label></p>
<fieldset><legend>Pizza Size</legend>
<p><label><input type="radio" name="size" value="small"> Small</label></p>
<p><label><input type="radio" name="size" value="medium"> Medium</label></p>
<p><label><input type="radio" name="size" value="large"> Large</label></p>
</fieldset>
<fieldset><legend>Pizza Toppings</legend>
<p><label><input type="checkbox" name="topping" value="bacon"> Bacon</label></p>
<p><label><input type="checkbox" name="topping" value="cheese"> Extra Cheese</label></p>
<p><label><input type="checkbox" name="topping" value="onion"> Onion</label></p>
<p><label><input type="checkbox" name="topping" value="mushroom"> Mushroom</label></p>
</fieldset>
<p><label>Delivery instructions: <textarea name="comments"></textarea></label></p>
<p><button>Submit order</button></p>
</form></body></html>"""


def order_result_page(form_body: str) -> str:
    """What the server received, as JSON (the shape httpbin.org/post returns)."""
    from html import escape
    from urllib.parse import parse_qs

    fields = parse_qs(form_body, keep_blank_values=True)
    form = {key: values[0] if len(values) == 1 else values for key, values in fields.items()}
    return ("<html><head><title>Order received</title></head><body><pre>"
            + escape(json.dumps({"form": form}, indent=2)) + "</pre></body></html>")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # keep the race output clean
        pass

    def _send(self, body: str, code: int = 200) -> None:
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        body_bytes = body.encode()
        self.send_header("Content-Length", str(len(body_bytes)))
        self.end_headers()
        self.wfile.write(body_bytes)

    def do_GET(self):
        if self.path.startswith("/order"):
            self._send(ORDER_PAGE)
        elif self.path.startswith("/login"):
            self._send(LOGIN_PAGE)
        elif self.path.startswith("/secure"):
            self._send(SECURE_PAGE)
        else:
            self._send(FAIL_PAGE, 404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        form = self.rfile.read(length).decode()
        if self.path.startswith("/order"):
            self._send(order_result_page(form))
        elif "username=tomsmith" in form and "password=SuperSecretPassword%21" in form:
            self.send_response(302)
            self.send_header("Location", "/secure")
            self.send_header("Content-Length", "0")  # keep-alive needs the length
            self.end_headers()
        else:
            self._send(FAIL_PAGE)


class QuietServer(ThreadingHTTPServer):
    def handle_error(self, request, client_address):
        # Chrome resets its keep-alive connections when the browser closes:
        # expected at the end of every run, not an error worth a traceback
        import sys as _sys

        if isinstance(_sys.exc_info()[1], (ConnectionResetError, ConnectionAbortedError)):
            return
        super().handle_error(request, client_address)


def serve() -> ThreadingHTTPServer:
    server = QuietServer(("127.0.0.1", 8765), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def local_task() -> Task:
    return local_tasks()["login"]


def local_tasks() -> dict[str, Task]:
    return {
        "login": Task(
            id="local-login",
            app="chrome",
            split="dev",
            goal="Log in to the local test site with username tomsmith and password SuperSecretPassword!",
            start={"url": "http://127.0.0.1:8765/login"},
            checks=[
                {"type": "url", "pattern": r"/secure$"},
                {"type": "page_text", "contains": "You logged into a secure area!"},
            ],
        ),
        "order": Task(
            id="local-order",
            app="chrome",
            split="dev",
            goal=("Fill in the pizza order form: name Asha Rao, phone 9876543210, email "
                  "asha@example.com, size medium, toppings cheese and onion, comments "
                  "'Ring the bell'. Then submit it."),
            start={"url": "http://127.0.0.1:8765/order"},
            checks=[
                {"type": "page_text", "contains": '"custname": "Asha Rao"'},
                {"type": "page_text", "contains": '"custtel": "9876543210"'},
                {"type": "page_text", "contains": '"custemail": "asha@example.com"'},
                {"type": "page_text", "contains": '"size": "medium"'},
                {"type": "page_text", "contains": '"cheese"'},
                {"type": "page_text", "contains": '"onion"'},
                {"type": "page_text", "contains": '"comments": "Ring the bell"'},
            ],
        ),
    }


async def race(runs: int, modes: tuple[str, ...] = ("slow", "quiff", "replay"),
               tasks: tuple[str, ...] = ("login", "order"), model: str = "meta",
               effort: str = "minimal", max_tokens: int = 8192) -> list[dict]:
    load_env()
    server = serve()
    results: list[dict] = []
    try:
        from race import quiff_run, replay_run, slow_run
        from quiff.models.meta import make_client
        from quiff.observe.browser import BrowserObserver

        client = make_client(model)  # the SAME model for both agents
        all_tasks = local_tasks()
        for name in tasks:
            task = all_tasks[name]
            # a FRESH browser per run: identical conditions for every mode and
            # every run — the warm-tab input quirk (documented) can't skew it
            runners = {
                "slow": lambda obs, t=task: slow_run(client, obs, t, effort=effort,
                                                     max_tokens=max_tokens),
                "quiff": lambda obs, t=task: quiff_run(obs, t, client=client, effort=effort,
                                                       max_tokens=max_tokens),
                "replay": lambda obs, t=task: replay_run(obs, t),
            }
            for mode in modes:
                for i in range(runs):
                    observer = BrowserObserver(headless=True)
                    started = time.perf_counter()
                    try:
                        result = await runners[mode](observer)
                    except Exception as exc:
                        # one broken run is a failed run, recorded with its reason,
                        # never a crash that loses the whole batch
                        result = {"mode": mode, "success": False,
                                  "elapsed_s": round(time.perf_counter() - started, 2),
                                  "model_calls": None, "steps": None,
                                  "stop_reason": f"crashed: {type(exc).__name__}: {exc}"[:200]}
                    finally:
                        await observer.stop()
                    result["task"] = name
                    results.append(result)
                    print(f"  {name:>5} {mode:>6} run {i + 1}: "
                          f"{'PASS' if result['success'] else 'FAIL'} · {result['elapsed_s']} s"
                          f" · model calls {result.get('model_calls')}")
    finally:
        server.shutdown()
    return results


def summarize(results: list[dict]) -> dict:
    """Per task, per mode: successes, median and p95 time, model calls."""
    summary: dict[str, dict] = {}
    for name in dict.fromkeys(r.get("task", "login") for r in results):
        summary[name] = {}
        for mode in ("slow", "quiff", "replay"):
            rs = [r for r in results if r.get("task", "login") == name and r["mode"] == mode]
            if not rs:
                continue
            times = sorted(r["elapsed_s"] for r in rs)
            summary[name][mode] = {
                "runs": len(rs),
                "successes": sum(1 for r in rs if r["success"]),
                "median_s": statistics.median(times),
                "p95_s": times[min(len(times) - 1, round(0.95 * (len(times) - 1)))],
                "model_calls": [r["model_calls"] for r in rs],
            }
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--modes", nargs="+", default=["slow", "quiff", "replay"],
                        choices=["slow", "quiff", "replay"])
    parser.add_argument("--tasks", nargs="+", default=["login", "order"],
                        choices=["login", "order"])
    parser.add_argument("--model", choices=["meta", "open"], default="meta",
                        help="meta: Muse Spark; open: Muse Spark Open (MUSE_OPEN_* in .env)")
    parser.add_argument("--effort", default="minimal",
                        help="reasoning effort for BOTH agents' decision calls")
    parser.add_argument("--max-tokens", type=int, default=8192,
                        help="token budget per call for BOTH agents")
    args = parser.parse_args()
    results = asyncio.run(race(args.runs, tuple(args.modes), tuple(args.tasks), args.model,
                               args.effort, args.max_tokens))

    summary = summarize(results)
    for name, modes in summary.items():
        for mode, s in modes.items():
            print(f"{name:>5} {mode:>6}: {s['successes']}/{s['runs']} success · "
                  f"median {s['median_s']} s · p95 {s['p95_s']} s · model calls "
                  f"{s['model_calls']}")
    from race import provenance
    from quiff.models.meta import make_client

    settings = {"model": make_client(args.model).model, "model_host": args.model,
                "effort_both_agents": args.effort, "max_tokens_both_agents": args.max_tokens,
                "rescuer_effort": "high", "approvals": "granted automatically and counted",
                "quiff_without": "the planning call and app guidance that `quiff run` adds",
                "browser": "a fresh headless Chrome per run; times include launching it"}
    out = Path("runs") / f"race-local-{time.strftime('%Y-%m-%d-%H%M%S')}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"provenance": provenance(), "settings": settings,
                               "runs_per_mode": args.runs, "results": results,
                               "summary": summary}, indent=2),
                   encoding="utf-8")
    print(f"Saved {out}")


if __name__ == "__main__":
    main()
