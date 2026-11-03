"""Batch: templates from row 1, replays for the rest, and no double submissions."""

import pytest

from quiff.batch import Teacher, bind, load_rows, render, run_batch, templatize
from quiff.decide import Decision
from quiff.observe.table import ActionRow, ActionTable
from quiff.recall import Flow, FlowRecorder, FlowStep, ReplayDecider
from fakes import FakeBrowserObserver, FakeDecider

ROW1 = {"name": "Asha Rao", "phone": "9876543210", "size": "medium", "note": "Ring the bell"}


def recorded_flow():
    return Flow(goal="g", task_id="t", recorded_at="now", steps=[
        FlowStep(action="type", role="textbox", name="Customer name:", values=("Asha Rao",)),
        FlowStep(action="type", role="textbox", name="Telephone:", values=("9876543210",)),
        FlowStep(action="click", role="radio", name="Medium"),
        FlowStep(action="click", role="checkbox", name="Onion"),
        FlowStep(action="type", role="textbox", name="Comments",
                 values=("Please: Ring the bell twice",)),
        FlowStep(action="click", role="button", name="Submit order"),
    ])


def test_load_rows_reads_the_header_and_strips(tmp_path):
    data = tmp_path / "rows.csv"
    data.write_text("﻿name , size\n Asha Rao , medium\nVikram,large\n", encoding="utf-8")
    assert load_rows(data) == [{"name": "Asha Rao", "size": "medium"},
                               {"name": "Vikram", "size": "large"}]


def test_load_rows_refuses_an_empty_file(tmp_path):
    data = tmp_path / "rows.csv"
    data.write_text("name,size\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_rows(data)


def test_render_fills_placeholders_and_leaves_other_braces():
    assert render('"custname": "{name}" {x}', {"name": "Asha"}) == '"custname": "Asha" {x}'


def test_templatize_replaces_row_values_with_columns():
    template, used = templatize(recorded_flow(), ROW1)
    values = [s.values for s in template.steps]
    names = [s.name for s in template.steps]
    assert values[0] == ("{name}",) and values[1] == ("{phone}",)
    assert names[2] == "{size}"                     # radio "Medium" for size "medium"
    assert names[3] == "Onion"                      # not from the row: stays as recorded
    assert values[4] == ("Please: {note} twice",)   # a long value inside a longer text
    assert names[5] == "Submit order"
    assert used == ["name", "note", "phone", "size"]


def test_bind_fills_the_template_for_another_row():
    template, _ = templatize(recorded_flow(), ROW1)
    row2 = {"name": "Vikram Nair", "phone": "9812345670", "size": "large", "note": "Call me"}
    flow = bind(template, row2, "batch-x-r2")
    assert flow.task_id == "batch-x-r2"
    assert [s.values for s in flow.steps][:2] == [("Vikram Nair",), ("9812345670",)]
    assert flow.steps[2].name == "large"
    assert flow.steps[4].values == ("Please: Call me twice",)


async def test_replay_finds_a_bound_choice_whatever_its_case():
    table = ActionTable(title="t", url="http://x/", rows=(
        ActionRow(index=1, role="radio", name="Large", actions=("click",), enabled=True),))
    flow = Flow(goal="g", task_id="t", steps=[FlowStep(action="click", role="radio",
                                                       name="large")])
    decision = await ReplayDecider(flow).decide("g", "g", table, [])
    assert (decision.action, decision.row) == ("click", 1)


# ---------------------------------------------------------------- run_batch, with fakes

class SubmittingObserver(FakeBrowserObserver):
    """The page moves to /done once something was clicked (the order went in),
    so a check on the URL tells a submitted row from one that wasn't."""

    def __init__(self, rows):
        super().__init__(rows)
        self.since = 0  # CDP calls made before this page was opened

    async def open(self, url):
        self.since = len(self.cdp.calls)
        return await super().open(url)

    async def target_info(self):
        clicked = any(method == "Input.dispatchMouseEvent" and params.get("type") == "mousePressed"
                      for method, params in self.cdp.calls[self.since:])
        return {"url": "http://x/done" if clicked else "http://x/", "title": "Test page"}


SUBMITTED = [{"type": "url", "pattern": "/done$"}]


def form_rows():
    return (ActionRow(index=1, role="textbox", name="Username", actions=("type",),
                      enabled=True, target=1),
            ActionRow(index=2, role="button", name="Submit order", actions=("click",),
                      enabled=True, target=2))


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)                 # runs/ and flows land in tmp
    monkeypatch.setenv("HOME", str(tmp_path))   # task sandboxes too
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    return tmp_path


def teacher_for(rows, made):
    async def teacher(task):
        row = rows[len(made)]
        made.append(task.id)
        decider = FakeDecider([Decision(action="type", row=1, values=(row["user"],)),
                               Decision(action="click", row=2),
                               Decision(action="done", subgoal_done=True)])
        return Teacher(decider=FlowRecorder(decider, task.id, task.goal))
    return teacher


async def approve(reason):
    return True


async def test_row_one_teaches_and_the_rest_replay_with_zero_calls(isolated):
    rows = [{"user": "tomsmith"}, {"user": "alice"}, {"user": "bobby"}]
    observer = SubmittingObserver(form_rows())
    made: list[str] = []
    result = await run_batch("Order for {user}", "http://x/", rows, SUBMITTED,
                             observer=observer, teacher=teacher_for(rows, made),
                             approver=approve, flows_dir=isolated / "flows")
    assert [r.mode for r in result.rows] == ["model (taught the template)", "replay", "replay"]
    assert all(r.success for r in result.rows)
    assert [r.model_calls for r in result.rows] == [3, 0, 0]
    assert [r.approvals for r in result.rows] == [1, 1, 1]  # the guard asked on every row
    assert result.template_columns == ["user"]
    typed = [p.get("text") for m, p in observer.cdp.calls if m == "Input.insertText"]
    assert typed == ["tomsmith", "alice", "bobby"]
    assert made == ["batch-" + result.batch_id.split("-")[1] + "-r1"]  # taught once


async def test_a_replay_that_fails_before_any_approval_falls_back_to_the_model(isolated):
    rows = [{"user": "tomsmith", "button": "Submit order"},
            {"user": "alice", "button": "Missing button"}]
    observer = SubmittingObserver(form_rows())
    made: list[str] = []
    template = Flow(goal="g", task_id="t", steps=[
        FlowStep(action="type", role="textbox", name="Username", values=("{user}",)),
        FlowStep(action="click", role="button", name="{button}")])
    result = await run_batch("Order for {user}", "http://x/", rows[1:], SUBMITTED,
                             observer=observer, teacher=teacher_for(rows[1:], made),
                             template=template, approver=approve,
                             flows_dir=isolated / "flows")
    only = result.rows[0]
    assert (only.mode, only.success) == ("model (replay failed)", True)
    assert only.model_calls == 3 and len(made) == 1


async def test_a_submitted_row_that_fails_its_checks_is_never_retried(isolated):
    rows = [{"user": "tomsmith", "expect": "/done$"}, {"user": "alice", "expect": "nomatch"}]
    observer = SubmittingObserver(form_rows())
    made: list[str] = []
    result = await run_batch("Order for {user}", "http://x/", rows,
                             [{"type": "url", "pattern": "{expect}"}], observer=observer,
                             teacher=teacher_for(rows, made), approver=approve,
                             flows_dir=isolated / "flows")
    second = result.rows[1]
    assert (second.mode, second.success, second.approvals) == ("replay", False, 1)
    assert len(made) == 1  # the model was never asked to redo a submitted row


async def test_when_row_one_fails_nothing_else_runs(isolated):
    rows = [{"user": "tomsmith"}, {"user": "alice"}]
    observer = SubmittingObserver(form_rows())

    async def refuse(reason):
        return False  # not approved: row 1 never submits, so there's no template

    result = await run_batch("Order for {user}", "http://x/", rows, SUBMITTED,
                             observer=observer, teacher=teacher_for(rows, []),
                             approver=refuse, flows_dir=isolated / "flows")
    assert [r.mode for r in result.rows] == ["model (taught the template)", "not run"]
    assert not any(r.success for r in result.rows)


async def test_the_meter_says_which_row_is_running(isolated):
    # found live: the loop replaced "Row k of N" at once, so on screen a replay
    # row looked like any other run; the meter now carries the row
    from quiff.show import Show

    class Meters(Show):
        def __init__(self):
            self.readings = []

        def meter(self, text, calls):
            self.readings.append((self.context, text))

    rows = [{"user": "tomsmith"}, {"user": "alice"}]
    show = Meters()
    await run_batch("Order for {user}", "http://x/", rows, SUBMITTED,
                    observer=SubmittingObserver(form_rows()), teacher=teacher_for(rows, []),
                    approver=approve, show=show, flows_dir=isolated / "flows")
    contexts = {context for context, _ in show.readings}
    assert contexts == {"Row 1 of 2", "Row 2 of 2"}
    assert ("Row 2 of 2", "Replay") in show.readings
