"""The Quiff app (quiff app): commands, spreadsheets, the page's API and a run's life."""

from __future__ import annotations

import http.client
import json
import sys
import threading
import time

import pytest

from quiff import app
from quiff.app import runner

FAKE_RUN = r'''
import json, pathlib, sys, time
folder = pathlib.Path("runs") / (time.strftime("%Y-%m-%d-%H%M%S") + "-do-fake")
folder.mkdir(parents=True, exist_ok=True)
log = folder / "steps.jsonl"
step = {"kind": "step", "step": 1,
        "decision": {"action": "type", "values": ["Asha Rao"], "brain": "Muse Spark (minimal)", "ms": 2400},
        "act": {"ok": True, "detail": 'typed into [5] textbox "Customer name"', "ms": 30}}
log.write_text(json.dumps(step) + "\n")
print("brain    Muse Spark (minimal) + rescue on high", flush=True)
time.sleep(0.8)
sys.stdout.write('APPROVAL NEEDED: Clicking "Submit order" submits it for good. Approve? y/n: ')
sys.stdout.flush()
answer = sys.stdin.readline().strip()
ok = answer == "y"
with log.open("a") as handle:
    handle.write(json.dumps({"kind": "run_summary", "success": ok,
                             "checks": [{"type": "page_text", "ok": ok, "detail": "x"}]}) + "\n")
print("calls    3 AI calls (incl. the plan) · 0 rescues")
print("check    " + ("PASS" if ok else "FAIL") + " [page_text] page text contains 'Asha Rao'")
print("result   " + ("PASS" if ok else "FAIL"))
sys.exit(0 if ok else 1)
'''


# ---------------------------------------------------------------- commands and chores

def test_goal_gets_every_column_unless_it_names_them():
    assert runner.augment_goal("Fill in the order and submit it.", ["name", "size"]) == \
        'Fill in the order and submit it. Use this row\'s values: name "{name}", size "{size}".'
    assert runner.augment_goal("Order a {size} pizza", ["name", "size"]) == "Order a {size} pizza"


def test_commands_are_the_real_cli():
    web = runner.Chore(id="a", title="t", sentence="Log in", kind="web", where="https://x.test/login")
    assert runner.command_for(web, show=False)[2:] == ["quiff", "do", "Log in", "--url", "https://x.test/login",
                                                     "--interactive", "--headed"]
    assert runner.command_for(web, show=True, replay=True)[-4:] == ["--show", "--show-scale", "1.25", "--replay"]
    desk = runner.Chore(id="b", title="t", sentence="Save a note", kind="app", where="notepad")
    assert "--app" in runner.command_for(desk) and "notepad" in runner.command_for(desk)
    sheet = runner.Chore(id="c", title="t", sentence="Fill it in", kind="sheet", where="http://127.0.0.1:8766/o",
                         csv="runs/app/uploads/x.csv", columns=["store", "qty"])
    command = runner.command_for(sheet, csv_path="runs/app/uploads/y.csv", show=False, replay=True)
    assert command[3] == "batch" and command[command.index("--data") + 1] == "runs/app/uploads/y.csv"
    assert command[command.index("--check-text") + 1] == "{store}"
    assert command[-1] == "--replay"
    example = runner.example_chore()
    assert runner.command_for(example, show=False)[3:6] == ["run", "b05", "--replay"]


def test_new_chore_checks_what_it_is_given():
    with pytest.raises(ValueError):
        runner.new_chore("   ", "url", "x.test", None, True)
    with pytest.raises(ValueError):
        runner.new_chore("Fill it", "url", "", None, True)
    with pytest.raises(ValueError):
        runner.new_chore("Fill it", "app", "paint", None, True)
    with pytest.raises(ValueError):
        runner.new_chore("Fill it", "app", "notepad", {"path": "x.csv", "columns": ["a"]}, True)
    assert runner.new_chore("Fill it", "url", "127.0.0.1:8766/orders/new", None, True).where == \
        "http://127.0.0.1:8766/orders/new"
    assert runner.new_chore("Fill it", "url", "example.com/form", None, True).where == "https://example.com/form"
    sheet = runner.new_chore("Fill it", "url", "x.test", {"path": "p.csv", "columns": ["a", "b"]}, False)
    assert (sheet.kind, sheet.columns, sheet.check_first) == ("sheet", ["a", "b"], False)


def test_spreadsheets_are_checked(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    good = runner.save_upload("orders.csv", b"store,quantity\nSharma Store,12\nPatel,5\n")
    assert (good["name"], good["rows"], good["columns"], good["short"]) == \
        ("orders.csv", 2, ["store", "quantity"], [])
    short = runner.save_upload("o.csv", b"store,quantity\nSharma Store,5\n")
    assert short["short"] == ["quantity"]  # row 1's "5" couldn't become {quantity}
    for name, data in [("o.txt", b"a\n1\n"), ("o.csv", b"a,a\n1,2\n"), ("o.csv", b"a,b\n"),
                       ("o.csv", b",b\n1,2\n"), ("o.csv", b"x" * 2_000_001)]:
        with pytest.raises(ValueError):
            runner.save_upload(name, data)


def test_steps_read_like_sentences():
    step = {"kind": "step", "decision": {"action": "select", "values": ["Basmati rice 25 kg"],
                                         "brain": "Replay", "ms": None},
            "act": {"ok": True, "detail": 'selected "Basmati rice 25 kg" in [8] combobox "Item"', "ms": 40}}
    entry = runner.describe(step, 3)
    assert entry["text"] == "Choose “Basmati rice 25 kg” in “Item”"
    assert (entry["who"], entry["model"], entry["secs"], entry["row"]) == ("Replay", False, "40 ms", 3)
    model = runner.describe({"kind": "step", "decision": {"action": "click", "brain": "Muse Spark (minimal)", "ms": 2412},
                             "act": {"ok": True, "detail": 'clicked [21] button "Submit order"', "ms": 80}}, None)
    assert (model["text"], model["secs"], model["model"]) == ("Click “Submit order”", "2.4 s", True)
    summary = runner.describe({"kind": "run_summary", "success": True, "checks": [{"ok": True}]}, 4)
    assert summary["text"] == "Row 4 checked: every check passed" and summary["kind"] == "check"
    assert runner.describe({"kind": "run_summary", "success": True, "checks": []}, None) is None


def test_summary_reads_the_last_lines():
    found = runner._summary(["row   1/10  PASS ...",
                             "batch    10/10 rows passed · 9 replayed with zero AI calls · 9 AI calls in total · 32.6 s"])
    assert found == {"rows_passed": 10, "rows": 10, "calls": 9, "seconds": 32.6}
    found = runner._summary(["steps    3 · 15.0s · done", "calls    4 AI calls (incl. the plan) · 0 rescues",
                             "check    (none given: the model's own 'done' is the only evidence)"])
    assert found == {"seconds": 15.0, "calls": 4, "no_checks": True}


# ---------------------------------------------------------------- the page's API

@pytest.fixture
def served(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("META_API_KEY", "test-key")
    server = app.AppServer(0, token="tok")
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server
    if server.run and server.run.exit_code is None:
        server.run.proc.kill()
    server.shutdown()
    server.server_close()


def call(server, method, path, body=None, token="tok", host=None):
    port = server.server_address[1]
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    headers = {"Host": host or f"127.0.0.1:{port}"}
    if token:
        headers["X-Quiff-Token"] = token
    data = body if isinstance(body, bytes) else (json.dumps(body).encode() if body is not None else None)
    conn.request(method, path, body=data, headers=headers)
    response = conn.getresponse()
    raw = response.read()
    conn.close()
    try:
        return response.status, json.loads(raw)
    except ValueError:
        return response.status, raw


def test_only_this_page_may_use_the_api(served):
    assert call(served, "GET", "/", token=None)[0] == 200
    assert call(served, "GET", "/", token=None, host="evil.test")[0] == 403
    assert call(served, "GET", "/api/info", token=None)[0] == 403
    assert call(served, "GET", "/api/info", token="wrong")[0] == 403
    assert call(served, "GET", "/api/info", host="evil.test:80")[0] == 403
    status, data = call(served, "GET", "/api/info")
    assert status == 200 and data["has_key"] is True


def test_a_run_shows_steps_asks_and_is_saved(served, monkeypatch):
    monkeypatch.setattr(runner, "command_for", lambda *a, **k: [sys.executable, "-c", FAKE_RUN])
    status, data = call(served, "POST", "/api/run", {"mode": "teach", "sentence": "Order for Asha",
                                                     "where": {"type": "url", "value": "127.0.0.1:9/order"}})
    assert status == 200, data
    snap = wait_for(served, "approval")
    assert snap["approval"]["reason"] == 'Clicking "Submit order" submits it for good'
    assert snap["approval"]["every_row"] is False
    assert call(served, "POST", "/api/run", {"mode": "teach", "sentence": "x",
                                             "where": {"type": "url", "value": "x.test"}})[0] == 400
    assert call(served, "POST", "/api/answer", {"answer": "y"})[0] == 200
    snap = wait_for(served, "done")
    texts = [s["text"] for s in call(served, "GET", "/api/run?since=0")[1]["steps"]]
    assert "Type “Asha Rao” into “Customer name”" in texts and "checked: every check passed" in texts
    assert snap["summary"]["calls"] == 3 and snap["calls"] == 3
    chores = [c for c in call(served, "GET", "/api/chores")[1]["chores"] if c["kind"] != "example"]
    assert [c["title"] for c in chores] == ["Order for Asha"] and chores[0]["calls"] == 3


def test_saying_no_stops_the_run(served, monkeypatch):
    monkeypatch.setattr(runner, "command_for", lambda *a, **k: [sys.executable, "-c", FAKE_RUN])
    call(served, "POST", "/api/run", {"mode": "teach", "sentence": "Order for Asha",
                                      "where": {"type": "url", "value": "x.test"}})
    wait_for(served, "approval")
    call(served, "POST", "/api/answer", {"answer": "n"})
    assert wait_for(served, "stopped")["state"] == "stopped"
    assert [c for c in call(served, "GET", "/api/chores")[1]["chores"] if c["kind"] != "example"] == []


def test_a_spreadsheet_must_be_one_the_app_saved(served):
    status, data = call(served, "POST", "/api/run", {"mode": "teach", "sentence": "Fill it",
                                                     "where": {"type": "url", "value": "x.test"},
                                                     "sheet": {"path": "/etc/passwd"}})
    assert status == 400 and "spreadsheet" in data["error"]


def wait_for(server, state, seconds=15):
    deadline = time.time() + seconds
    snap = {}
    while time.time() < deadline:
        snap = call(server, "GET", "/api/run")[1]
        if snap.get("state") == state:
            return snap
        time.sleep(0.1)
    raise AssertionError(f"never reached {state}: {snap}")
