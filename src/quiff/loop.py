"""The fast loop that runs once per step: observe -> recall -> decide -> act -> verify.

The frontier model planned the subgoals up front; this loop only asks the small
decision model (or, for now, a scripted decider) to pick the next row. Recall
replay and frontier rescue plug into the same step order.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field, replace
from typing import Sequence

import re

from .act import ActResult, act, focus_info, needs_approval, parse_combo
from .decide import Decision, Decider
from .learn import StepLogger
from .observe.browser import BrowserObserver
from .observe.table import ActionTable
from .show import Show, describe, target_rect
from .verify import LoopDetector

MAX_STEPS = 25
MAX_WAITS = 5
MAX_CORRECTIONS = 2
LOW_CONFIDENCE = 0.5
MAX_RESCUES = 3  # the slow high-effort model may take at most this many steps per run


@dataclass
class StepRecord:
    step: int
    decision: Decision
    act: ActResult | None
    table_rows: int
    table_tokens: int
    decide_ms: int
    observe_ms: int


@dataclass
class LoopResult:
    success: bool
    steps: int
    elapsed_s: float
    stop_reason: str
    answer: str | None = None
    steps_log: list[StepRecord] = field(default_factory=list)
    model_calls: int = 0  # every request to a model: decisions, retries, rescues (not replay)
    rescues: int = 0


class FastLoop:
    """Runs the per-step loop against one open browser tab.

    `rescuer` is the frontier model's second chance: when acting fails twice in
    a row or the loop detector fires, the rescuer (if given) may take the step;
    without one the loop stops and says why.
    """

    def __init__(
        self,
        observer,
        decider: Decider,
        logger: StepLogger | None = None,
        *,
        max_steps: int = MAX_STEPS,
        rescuer: Decider | None = None,
        approver=None,
        verify_after_done=None,
        write_root=None,
        show: Show | None = None,
    ) -> None:
        self.observer = observer
        self.show = show or Show()  # the on-screen overlay; the base does nothing
        self.decider = decider
        self.logger = logger
        self.max_steps = max_steps
        self.rescuer = rescuer
        self.approver = approver
        self.verify_after_done = verify_after_done
        self.write_root = write_root
        """The folder the task may write NEW files into without asking (the
        sandbox); saving anywhere else, or overwriting, needs approval."""
        """async callable -> (passed, feedback); lets the referee correct the
        model once when it declares done but the checks disagree."""
        """async callable (reason) -> bool; the only way past the approval guard."""

    async def run(
        self,
        goal: str,
        *,
        start_url: str | None = None,
        subgoal: str | None = None,
    ) -> LoopResult:
        started = time.perf_counter()
        history: list[Decision] = []
        detector = LoopDetector()
        failed_acts = 0
        answer: str | None = None
        self.show.state("working", "Reading the screen")

        if start_url:
            observe_start = time.perf_counter()
            table = await self.observer.open(start_url)
            observe_ms = int((time.perf_counter() - observe_start) * 1000)
        else:
            observe_start = time.perf_counter()
            table = await self.observer.read()
            observe_ms = int((time.perf_counter() - observe_start) * 1000)
        if self.show.active and hasattr(self.observer, "bring_to_front"):
            # the overlay must point at a window you can see
            if not await self.observer.bring_to_front():
                self.show.state("unsure", "Chrome is behind another window · click it to watch")

        stop_reason = "max steps reached"
        success = False
        waits = 0
        force_rescue = False
        rescue_why = ""
        corrected = False
        last_result: str | None = None
        performed: dict[tuple[str, str], int] = {}
        self._model_calls = 0
        self._rescues = 0
        loop_rescued = False

        for step_no in range(1, self.max_steps + 1):
            decide_start = time.perf_counter()
            progress = _progress_text(performed)
            try:
                decision = await self._ask_decider(goal, subgoal, table, history,
                                                   last_result, progress)
            except Exception as first_error:
                # a transient 429/timeout must not kill the run: retry once,
                # then let the rescuer (a second model) take the step
                await asyncio.sleep(2.0)
                try:
                    decision = await self._ask_decider(goal, subgoal, table, history,
                                                       last_result, progress)
                except Exception:
                    if not self._can_rescue():
                        stop_reason = f"decider failed twice: {first_error}"
                        success = False
                        break
                    try:
                        decision = await self._ask_rescuer(goal, subgoal, table, history,
                                                           last_result, progress,
                                                           why="the decision model failed")
                    except Exception as exc:
                        stop_reason = f"decider and rescuer failed: {first_error} / {exc}"
                        success = False
                        break
            decide_ms = int((time.perf_counter() - decide_start) * 1000)

            if (force_rescue or decision.confidence < LOW_CONFIDENCE) and self._can_rescue():
                try:
                    decision = await self._ask_rescuer(goal, subgoal, table, history,
                                                       last_result, progress,
                                                       why=rescue_why if force_rescue
                                                       else "low confidence")
                except Exception as exc:
                    decision = Decision(action="done", subgoal_done=False, confidence=0.0,
                                        note=f"rescuer failed: {exc}")
            force_rescue = False

            if decision.action == "wait":
                # The decider expects a screen that isn't there yet (a dialog
                # still building); re-observe instead of acting. Past the
                # budget, ask the rescuer (while its budget lasts) to act instead.
                waits += 1
                if waits > MAX_WAITS and self._can_rescue():
                    try:
                        decision = await self._ask_rescuer(goal, subgoal, table, history,
                                                           last_result, progress,
                                                           why="the screen kept changing")
                    except Exception as exc:
                        decision = Decision(action="done", subgoal_done=False, confidence=0.0,
                                            note=f"rescuer failed: {exc}")
                if decision.action == "wait":
                    self.show.state("working", "Waiting for the screen to settle")
                    self._log(step_no, decision, None, table, decide_ms, observe_ms)
                    if waits > 2 * MAX_WAITS:
                        stop_reason = "waited too long for the expected screen"
                        break
                    await self.observer.wait_settled()
                    table = await self.observer.read()
                    continue

            if decision.action == "done":
                if self.verify_after_done is not None and corrected < MAX_CORRECTIONS:
                    passed, feedback = await self.verify_after_done()
                    if passed:
                        success = True
                        stop_reason = "done, checks pass"
                        self._log(step_no, decision, None, table, decide_ms, observe_ms)
                        break
                    corrected += 1
                    last_result = f"the checks disagree — the task is NOT complete: {feedback}. Fix it."
                    stop_reason = "correction round after failed checks"
                    self.show.state("unsure", "Checks disagree · correcting")
                    decision = Decision(action="wait", note="checks failed; correcting")
                    self._log(step_no, decision, None, table, decide_ms, observe_ms)
                    await self.observer.wait_settled()
                    table = await self.observer.read()
                    continue
                success = bool(decision.subgoal_done is not False)
                stop_reason = decision.note or "decider said done"
                self._log(step_no, decision, None, table, decide_ms, observe_ms)
                break
            if decision.action == "answer":
                answer = decision.answer
                success = answer is not None
                stop_reason = "answered"
                self._log(step_no, decision, None, table, decide_ms, observe_ms)
                break

            focus = None
            if decision.action == "press":
                mods, keys = parse_combo(decision.values)
                if not mods and keys in ((), ("enter",), ("space",), ("delete",)):
                    # what the key lands on decides what it does
                    focus = await focus_info(self.observer)
            approval_reason = needs_approval(decision, table, focus=focus,
                                             write_root=self.write_root)
            row = table.by_index(decision.row) if decision.row is not None else None
            line, chip = describe(decision, row)
            if "rescued" in (decision.note or ""):
                chip = "Rescuer · " + chip
            rect = (await target_rect(self.observer, row)
                    if self.show.active and row is not None else None)
            if approval_reason:
                self.show.state("approval", line, guard=approval_reason)
                if rect is not None:  # parks on the target without clicking
                    self.show.target(rect, "Needs approval · " + chip.split(" · ")[-1],
                                     kind="approval", ripple=False)
                approved = False
                if self.approver is not None:
                    try:
                        approved = bool(await self.approver(approval_reason))
                    except Exception:
                        approved = False
                if not approved:
                    stop_reason = f"needs approval: {approval_reason}"
                    self._log(step_no, decision, None, table, decide_ms, observe_ms)
                    break
                decision = replace(
                    decision, note=f"{decision.note} · approved by user".strip(" ·")
                )

            self.show.state("working", line)
            if rect is not None:
                # the action lands as the ghost cursor arrives
                arrival = self.show.target(rect, chip, kind="working",
                                           ripple=decision.action in ("click", "type", "select"))
                await asyncio.sleep(arrival)
            else:
                self.show.clear_target()
            result = await act(self.observer, table, decision)
            history.append(decision)
            if result.ok and decision.row is not None:
                row = table.by_index(decision.row)
                if row is not None:
                    key = (decision.action, f"{row.role} '{row.name[:24]}'")
                    performed[key] = performed.get(key, 0) + 1
            last_result = ("OK: " if result.ok else "FAILED: ") + result.detail
            self._log(step_no, decision, result, table, decide_ms, observe_ms)

            if not result.ok:
                failed_acts += 1
                if failed_acts >= 2:
                    if self._can_rescue():
                        force_rescue = True  # the frontier model picks the next step
                        rescue_why = "an action failed twice"
                        failed_acts = 0
                    else:
                        stop_reason = f"acting failed twice: {result.detail}"
                        success = False
                        break
            else:
                failed_acts = 0

            row = table.by_index(decision.row) if decision.row is not None else None
            ref = row.ref if row is not None else decision.action
            if detector.check(decision.action, ref or "", table.title):
                if not loop_rescued and self._can_rescue():
                    force_rescue = True  # one rescue for the first loop, then stop
                    rescue_why = "the same step repeated"
                    loop_rescued = True
                else:
                    stop_reason = ("loop detected: the same action repeats on the same "
                                   "screen" + (" even after rescue" if loop_rescued else ""))
                    success = False
                    break

            observe_start = time.perf_counter()
            await self.observer.wait_settled()
            before = (table.url, table.title)
            table = await self.observer.read()
            observe_ms = int((time.perf_counter() - observe_start) * 1000)
            if (table.url, table.title) != before:
                self.show.clear_target()  # the box would point into a page that's gone

        elapsed = time.perf_counter() - started
        return LoopResult(
            success=success,
            steps=len(history),
            elapsed_s=round(elapsed, 2),
            stop_reason=stop_reason,
            answer=answer,
            model_calls=self._model_calls,
            rescues=self._rescues,
        )

    def _can_rescue(self) -> bool:
        return self.rescuer is not None and self._rescues < MAX_RESCUES

    async def _ask_decider(self, goal, subgoal, table, history, last_result, progress):
        if _is_model(self.decider):
            self._model_calls += 1  # counted even if the call fails: the request was made
        return await self.decider.decide(goal, subgoal or "", table, history,
                                         last_result=last_result, progress=progress)

    async def _ask_rescuer(self, goal, subgoal, table, history, last_result, progress,
                           why: str = "low confidence"):
        self._rescues += 1
        self.show.state("unsure", f"Rescuer took over · {why}")
        if _is_model(self.rescuer):
            self._model_calls += 1
        decision = await self.rescuer.decide(goal, subgoal or "", table, history,
                                             last_result=last_result, progress=progress)
        return replace(decision, note=f"{decision.note} · rescued".strip(" ·"))

    def _log(
        self,
        step_no: int,
        decision: Decision,
        result: ActResult | None,
        table: ActionTable,
        decide_ms: int,
        observe_ms: int,
    ) -> None:
        if self.logger is None:
            return
        self.logger.log(
            {
                "kind": "step",
                "step": step_no,
                "table": {"title": table.title, "url": table.url, "rows": len(table.rows),
                          "approx_tokens": table.approx_tokens()},
                "decision": {
                    "action": decision.action,
                    "row": decision.row,
                    "values": _redacted(decision, table),
                    "confidence": decision.confidence,
                    "note": decision.note,
                },
                "act": None if result is None else
                       {"ok": result.ok, "detail": result.detail, "ms": result.ms},
                "latency_ms": {"observe": observe_ms, "decide": decide_ms},
            }
        )


SENSITIVE_FIELD = re.compile(
    r"pass(word)?|pwd|otp|secret|token|api[- _]?key|card|cvv|cvc|pin|cc[-_ ]?(num|csc|exp)|credit|ssn|social[- _]security", re.I
)


def _is_model(decider) -> bool:
    """Replay and scripted deciders cost no model call; wrappers ask their inner decider."""
    return bool(getattr(decider, "is_model", True))


def _progress_text(performed: dict[tuple[str, str], int]) -> str:
    """Cua #4313: models cannot count from history — the runner counts its
    own successful actions and states the tally outright."""
    if not performed:
        return "Task progress (counted from this run's own completed actions): nothing completed yet."
    items = "; ".join(f"{action} on {target}: done {n}x"
                      for (action, target), n in sorted(performed.items()))
    return (f"Task progress (counted from this run's own completed actions, "
            f"not read from the app): {items}.")


def _redacted(decision: Decision, table: ActionTable) -> list[str]:
    """Values as safe to write to disk: text typed into a field whose name
    looks like a credential is masked. Everything else logs as-is."""
    if decision.action != "type" or decision.row is None:
        return list(decision.values)
    row = table.by_index(decision.row)
    if row is not None and SENSITIVE_FIELD.search(row.role + " " + row.name):
        return ["[REDACTED]"] * len(decision.values)
    return list(decision.values)
