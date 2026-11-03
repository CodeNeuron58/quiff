"""Batch: teach once, replay with new data.

Row 1 of a CSV goes to the model: Muse Spark plans and decides each step, and
the approval guard asks before anything irreversible. The flow it recorded
becomes a template: every value it typed or chose that came from row 1 is
replaced by its column name ({name}, {size}, ...). Rows 2..N bind the template
to their own values and replay it with zero model calls, and each row is
checked by code like any task.

Safety: a row whose replay fails BEFORE anything was approved (nothing was
submitted) falls back to the model. A row that was submitted but fails its
checks is reported and never retried, so nothing is ever submitted twice.
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
import time
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Awaitable, Callable

from .recall import FLOWS_DIR, Flow, FlowStep, ReplayDecider
from .tasks import Task, run_task


# ---------------------------------------------------------------- data and templates

def load_rows(path: Path) -> list[dict[str, str]]:
    """The CSV's rows as {column: value}; the header row names the columns."""
    with Path(path).open(newline="", encoding="utf-8-sig") as handle:
        rows = [{key.strip(): (value or "").strip() for key, value in row.items() if key}
                for row in csv.DictReader(handle)]
    if not rows:
        raise ValueError(f"{path}: no data rows under the header")
    return rows


def render(text: str, row: dict[str, str]) -> str:
    """Fill {column} placeholders with the row's values (other braces stay as they are)."""
    for column, value in row.items():
        text = text.replace("{" + column + "}", value)
    return text


def _norm(text: str) -> str:
    return " ".join(str(text).split()).casefold()


def _template_value(value: str, row: dict[str, str], columns: list[str]) -> tuple[str, list[str]]:
    for column in columns:  # the whole value came from one column
        if value == row[column]:
            return "{" + column + "}", [column]
    long = [column for column in columns if len(row[column]) >= 4]
    if not long:
        return value, []
    used: list[str] = []
    by_value = {row[column]: column for column in reversed(long)}

    def swap(match: re.Match) -> str:
        column = by_value[match.group(0)]
        used.append(column)
        return "{" + column + "}"

    # one pass, longest values first, so a placeholder is never rewritten
    pattern = re.compile("|".join(re.escape(row[column]) for column in long))
    return pattern.sub(swap, value), used


def templatize(flow: Flow, row: dict[str, str]) -> tuple[Flow, list[str]]:
    """Turn the flow recorded on `row` into a template for any row.

    Typed values (or parts of them, for values of 4+ characters) that equal a
    column's value become {column}; so does the name of a clicked or selected
    control when it matches a value (a radio "Medium" for size "medium").
    Returns the template and the columns it uses.
    """
    columns = sorted((c for c, v in row.items() if len(v) >= 2), key=lambda c: -len(row[c]))
    used: list[str] = []
    steps: list[FlowStep] = []
    for step in flow.steps:
        values = []
        for value in step.values:
            templated, columns_used = _template_value(value, row, columns)
            values.append(templated)
            used.extend(columns_used)
        name = step.name
        if step.action in ("click", "select") and name:
            for column in columns:
                if _norm(name) == _norm(row[column]):
                    name = "{" + column + "}"
                    used.append(column)
                    break
        steps.append(replace(step, values=tuple(values), name=name))
    template = Flow(goal=flow.goal, task_id=flow.task_id, steps=steps,
                    recorded_at=flow.recorded_at)
    return template, sorted(set(used))


def bind(template: Flow, row: dict[str, str], task_id: str) -> Flow:
    """The template with one row's values filled in, ready to replay."""
    steps = [replace(step, values=tuple(render(v, row) for v in step.values),
                     name=render(step.name, row)) for step in template.steps]
    return Flow(goal=render(template.goal, row), task_id=task_id, steps=steps,
                recorded_at=template.recorded_at)


def batch_id(goal_template: str, start_url: str) -> str:
    return "batch-" + hashlib.sha1(f"{goal_template}|{start_url}".encode("utf-8")).hexdigest()[:8]


def template_path(bid: str, directory: Path = FLOWS_DIR) -> Path:
    return directory / f"{bid}.template.json"


def save_template(template: Flow, bid: str, directory: Path = FLOWS_DIR) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = template_path(bid, directory)
    path.write_text(json.dumps(asdict(template), indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def load_template(bid: str, directory: Path = FLOWS_DIR) -> Flow:
    path = template_path(bid, directory)
    if not path.exists():
        raise FileNotFoundError(f"no template for this batch yet ({path}); run it once "
                                f"without --replay so the model can teach it")
    data = json.loads(path.read_text(encoding="utf-8"))
    steps = [FlowStep(values=tuple(step.pop("values")), **step) for step in data.pop("steps")]
    return Flow(**data, steps=steps)


def row_task(bid: str, index: int, goal_template: str, start_url: str,
             check_templates: list[dict], row: dict[str, str]) -> Task:
    checks = [{key: render(value, row) if isinstance(value, str) else value
               for key, value in check.items()} for check in check_templates]
    return Task(id=f"{bid}-r{index}", app="chrome", split="batch",
                goal=render(goal_template, row), start={"url": render(start_url, row)},
                checks=checks)


# ---------------------------------------------------------------- running a batch

@dataclass
class Teacher:
    """What the model needs to do one row: the decider (a FlowRecorder around
    the model, so its steps are recorded), its rescuer, an optional plan, and
    the model calls already spent on that plan."""

    decider: object
    rescuer: object | None = None
    planned: list[str] | None = None
    calls_before: int = 0


@dataclass
class RowResult:
    row: int
    mode: str  # "model (taught the template)", "replay", "model (replay failed)", "not run"
    success: bool
    elapsed_s: float
    model_calls: int
    approvals: int
    stop_reason: str
    checks: list[str] = field(default_factory=list)


@dataclass
class BatchResult:
    batch_id: str
    rows: list[RowResult]
    template_columns: list[str]
    template_file: str
    elapsed_s: float

    @property
    def passed(self) -> int:
        return sum(1 for r in self.rows if r.success)

    @property
    def model_calls(self) -> int:
        return sum(r.model_calls for r in self.rows)

    @property
    def replayed(self) -> int:
        return sum(1 for r in self.rows if r.mode == "replay")

    def summary(self) -> dict:
        return {"batch_id": self.batch_id, "rows": len(self.rows), "passed": self.passed,
                "replayed_with_zero_ai_calls": self.replayed, "model_calls": self.model_calls,
                "elapsed_s": round(self.elapsed_s, 2), "template_columns": self.template_columns,
                "template_file": self.template_file}


class _CountingApprover:
    """Wraps the batch's approver and counts what it granted for one row."""

    def __init__(self, inner):
        self.inner = inner
        self.granted = 0

    async def __call__(self, reason: str) -> bool:
        if self.inner is None:
            return False
        ok = bool(await self.inner(reason))
        self.granted += ok
        return ok


async def run_batch(goal_template: str, start_url: str, rows: list[dict[str, str]],
                    check_templates: list[dict], *, observer,
                    teacher: Callable[[Task], Awaitable[Teacher]] | None,
                    template: Flow | None = None, approver=None, show=None,
                    max_steps: int = 25, flows_dir: Path = FLOWS_DIR,
                    on_row: Callable[[RowResult, int], None] | None = None) -> BatchResult:
    """Teach on row 1 (unless a template is given), then replay rows 2..N."""
    started = time.perf_counter()
    bid = batch_id(goal_template, start_url)
    results: list[RowResult] = []
    columns: list[str] = []
    template_file = str(template_path(bid, flows_dir)) if template is not None else ""
    calls_so_far = 0

    async def model_row(task: Task, gate: _CountingApprover):
        lesson = await teacher(task)
        run = await run_task(task, lesson.decider, observer, max_steps=max_steps,
                             rescuer=lesson.rescuer, approver=gate, planned=lesson.planned,
                             show=show)
        return run, lesson, run.loop.model_calls + lesson.calls_before

    for index, row in enumerate(rows, 1):
        task = row_task(bid, index, goal_template, start_url, check_templates, row)
        gate = _CountingApprover(approver)
        if show is not None:
            if hasattr(show, "calls_offset"):
                show.calls_offset = calls_so_far  # the overlay counts the whole batch
            mode_line = "teaching" if template is None else "replaying"
            # the loop's own lines replace this one at once; the meter keeps the row
            show.context = f"Row {index} of {len(rows)}"
            show.state("working", f"Row {index} of {len(rows)} · {mode_line}")
        row_started = time.perf_counter()
        if template is None:
            if teacher is None:
                raise ValueError("no template and no model to teach one")
            run, lesson, calls = await model_row(task, gate)
            mode = "model (taught the template)"
            if run.success:
                template, columns = templatize(lesson.decider.flow, row)
                template.goal = goal_template  # the template's goal keeps its placeholders
                template_file = str(save_template(template, bid, flows_dir))
        else:
            run = await run_task(task, ReplayDecider(bind(template, row, task.id)), observer,
                                 max_steps=max_steps, approver=gate, show=show)
            calls, mode = run.loop.model_calls, "replay"
            if not run.success and gate.granted == 0 and teacher is not None:
                # nothing was approved, so nothing was submitted: the model may do it
                run, _, calls = await model_row(task, gate)
                mode = "model (replay failed)"
        result = RowResult(row=index, mode=mode, success=run.success,
                           elapsed_s=round(time.perf_counter() - row_started, 2),
                           model_calls=calls, approvals=gate.granted,
                           stop_reason=run.loop.stop_reason,
                           checks=[f"{'PASS' if c.ok else 'FAIL'} {c.detail}" for c in run.checks])
        results.append(result)
        calls_so_far += calls
        if on_row is not None:
            on_row(result, len(rows))
        if template is None:  # row 1 failed: there is nothing to replay
            for later in range(index + 1, len(rows) + 1):
                results.append(RowResult(row=later, mode="not run", success=False,
                                         elapsed_s=0.0, model_calls=0, approvals=0,
                                         stop_reason="the model couldn't do row 1, so "
                                                     "there is no template to replay"))
            break
    return BatchResult(batch_id=bid, rows=results, template_columns=columns,
                       template_file=template_file,
                       elapsed_s=time.perf_counter() - started)
