"""The Quiff app's engine room: turn what you asked for into a `quiff` command, run
one at a time, follow its steps live from the step log, and remember chores.

Every run is the real CLI (`python -m quiff do|batch|run ...`), so the app adds
no second way of doing things: what works in the terminal works here.
"""

from __future__ import annotations

import csv
import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

RUNS = Path("runs")
APP_DIR = RUNS / "app"          # runs/ is git-ignored: chores and uploads stay on this machine
UPLOADS = APP_DIR / "uploads"
CHORES_FILE = APP_DIR / "chores.json"
APPS = {"notepad": "Notepad", "explorer": "File Explorer"}
EXAMPLE = "example-login"       # a recorded login that replays with no API key
SHOW_SCALE = "1.25"


# ---------------------------------------------------------------- commands

def augment_goal(sentence: str, columns: list[str]) -> str:
    """A spreadsheet chore's goal: the sentence plus every column as a {placeholder}
    (unless the sentence already uses them), so each row's values reach the model."""
    sentence = " ".join(sentence.split())
    if any("{" + column + "}" in sentence for column in columns):
        return sentence
    values = ", ".join(f'{column} "{{{column}}}"' for column in columns)
    return f"{sentence.rstrip('.')}. Use this row's values: {values}."


def command_for(chore: "Chore", *, csv_path: str = "", show: bool = True,
                replay: bool = False) -> list[str]:
    """The exact `quiff` command line for a chore."""
    base = [sys.executable, "-m", "quiff"]
    look = ["--show", "--show-scale", SHOW_SCALE] if show else ["--headed"]
    if chore.kind == "example":
        return base + ["run", "b05", "--replay"] + look
    if chore.kind == "sheet":
        command = base + ["batch", augment_goal(chore.sentence, chore.columns),
                          "--url", chore.where, "--data", csv_path or chore.csv]
        if chore.check_first and chore.columns:
            command += ["--check-text", "{" + chore.columns[0] + "}"]
    elif chore.kind == "web":
        command = base + ["do", chore.sentence, "--url", chore.where]
    elif chore.kind == "app":
        command = base + ["do", chore.sentence, "--app", chore.where]
    else:
        raise ValueError(f"unknown chore kind {chore.kind!r}")
    command += ["--interactive"] + look
    if replay:
        command.append("--replay")
    return command


# ---------------------------------------------------------------- chores

@dataclass
class Chore:
    id: str
    title: str
    sentence: str
    kind: str                 # web | app | sheet | example
    where: str                # a URL, or an app key (notepad, explorer)
    csv: str = ""             # sheet chores: the file it last ran with
    columns: list[str] = field(default_factory=list)
    check_first: bool = True  # sheet chores: each row's page must show its first column's value
    calls: int = 0            # AI calls it took to learn
    created: str = ""
    last: str = ""
    runs: int = 0


def example_chore() -> Chore:
    return Chore(id=EXAMPLE, title="Log in to a test site (a recorded example)",
                 sentence="Log in with username tomsmith", kind="example",
                 where="https://the-internet.herokuapp.com/login",
                 calls=4, created="shipped with Quiff")


def load_chores() -> list[Chore]:
    try:
        raw = json.loads(CHORES_FILE.read_text(encoding="utf-8"))
        chores = [Chore(**{k: v for k, v in item.items() if k in Chore.__dataclass_fields__})
                  for item in raw]
    except (OSError, ValueError, TypeError):
        chores = []
    return chores


def save_chores(chores: list[Chore]) -> None:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    CHORES_FILE.write_text(json.dumps([asdict(c) for c in chores], indent=2, ensure_ascii=False),
                           encoding="utf-8")


def find_chore(chore_id: str) -> Chore | None:
    if chore_id == EXAMPLE:
        return example_chore()
    return next((c for c in load_chores() if c.id == chore_id), None)


def remember(chore: Chore, calls: int) -> Chore:
    """Save a chore that was just taught (the same sentence and place replace an older one)."""
    chores = [c for c in load_chores()
              if not (c.sentence == chore.sentence and c.where == chore.where and c.kind == chore.kind)]
    now = time.strftime("%Y-%m-%d %H:%M")
    chore.calls, chore.created, chore.last, chore.runs = calls, now, now, 1
    save_chores([chore] + chores)
    return chore


def touch(chore_id: str, csv_path: str = "") -> None:
    chores = load_chores()
    for chore in chores:
        if chore.id == chore_id:
            chore.last = time.strftime("%Y-%m-%d %H:%M")
            chore.runs += 1
            if csv_path:
                chore.csv = csv_path
    save_chores(chores)


def new_chore(sentence: str, where_type: str, where: str, sheet: dict | None,
              check_first: bool) -> Chore:
    sentence = " ".join((sentence or "").split())
    if not sentence:
        raise ValueError("Tell Quiff what to do first.")
    if sheet:
        if where_type != "url":
            raise ValueError("Spreadsheet chores run on a website: give its address.")
        kind = "sheet"
    else:
        kind = "web" if where_type == "url" else "app"
    if kind in ("web", "sheet"):
        where = (where or "").strip()
        if not where:
            raise ValueError("Give the website's address.")
        if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", where):
            where = "http://" + where if where.startswith(("127.", "localhost")) else "https://" + where
    elif where not in APPS:
        raise ValueError("Pick Notepad or File Explorer.")
    title = sentence if len(sentence) <= 72 else sentence[:70].rstrip() + "…"
    return Chore(id=uuid.uuid4().hex[:10], title=title, sentence=sentence, kind=kind, where=where,
                 csv=(sheet or {}).get("path", ""), columns=list((sheet or {}).get("columns", [])),
                 check_first=check_first)


# ---------------------------------------------------------------- spreadsheets

def read_sheet(path: Path) -> dict:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.reader(handle)
        header = next(reader, [])
        rows = [r for r in reader if any(cell.strip() for cell in r)]
    columns = [h.strip() for h in header]
    if not columns or not all(columns):
        raise ValueError("The first row of the spreadsheet must name every column.")
    if len(set(columns)) != len(columns):
        raise ValueError("Two columns have the same name.")
    if any("{" in c or "}" in c for c in columns):
        raise ValueError("Column names can't contain { or }.")
    if not rows:
        raise ValueError("The spreadsheet has no rows below its header.")
    # the template is built from row 1: a value under 2 characters there stays fixed for every row
    first = rows[0] + [""] * (len(columns) - len(rows[0]))
    short = [c for c, v in zip(columns, first) if len(v.strip()) < 2]
    return {"path": str(path), "name": re.sub(r"^\d{8}-\d{6}-", "", path.name), "rows": len(rows),
            "columns": columns, "short": short}


def save_upload(name: str, data: bytes) -> dict:
    if len(data) > 2_000_000:
        raise ValueError("That file is over 2 MB: Quiff takes spreadsheets up to 2 MB.")
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", Path(name or "rows.csv").name).strip("-.") or "rows.csv"
    if not safe.lower().endswith(".csv"):
        raise ValueError("Save the spreadsheet as a .csv file first (File → Save As → CSV).")
    UPLOADS.mkdir(parents=True, exist_ok=True)
    path = UPLOADS / f"{time.strftime('%Y%m%d-%H%M%S')}-{safe}"
    path.write_bytes(data)
    try:
        return read_sheet(path)
    except (ValueError, UnicodeDecodeError, csv.Error) as exc:
        path.unlink(missing_ok=True)
        raise ValueError(str(exc) if isinstance(exc, ValueError) else "Quiff couldn't read that file as a CSV.")


# ---------------------------------------------------------------- one run

APPROVAL = "APPROVAL NEEDED: "
ACTIONS = {
    "type": "Type “{value}” into {target}",
    "select": "Choose “{value}” in {target}",
    "click": "Click {target}",
    "press": "Press {value}",
    "navigate": "Open {value}",
    "scroll": "Scroll the page",
    "wait": "Wait for the page",
    "done": "Done",
    "answer": "Answer: {value}",
}


def describe(record: dict, row: int | None) -> dict | None:
    """One step-log line as the app shows it."""
    if record.get("kind") == "run_summary":
        checks = record.get("checks") or []
        ok = bool(record.get("success"))
        if not checks and row is None:
            return None
        text = (f"Row {row} " if row else "") + ("checked: every check passed" if ok and checks
                                                 else "done" if ok else "failed: " + str(record.get("stop_reason", ""))[:90])
        return {"text": text, "who": "Code check", "model": False, "ok": ok, "secs": "", "row": row,
                "kind": "check"}
    if record.get("kind") != "step":
        return None
    decision = record.get("decision") or {}
    act = record.get("act") or {}
    action = decision.get("action") or ""
    values = decision.get("values") or []
    detail = act.get("detail") or ""
    quoted = re.findall(r"\"([^\"]*)\"", detail)
    target = f"“{quoted[-1]}”" if quoted else (re.sub(r"^\w+ (into |in )?\[\d+\] ", "", detail) or "it")
    value = str(values[0]) if values else ""
    text = ACTIONS.get(action, action.capitalize() + " {target}").format(value=value, target=target)
    if value and target == f"“{value}”":  # the log named only the value (a dropdown's option)
        text = text.split(" in “")[0].split(" into “")[0]
    brain = decision.get("brain") or ""
    model = brain not in ("", "Replay", "Script")
    ms = decision.get("ms") if model else act.get("ms")
    return {"text": text, "who": brain or "Quiff", "model": model, "ok": act.get("ok", True),
            "secs": _duration(ms),
            "row": row, "kind": "model" if model else "replay"}


def _duration(ms) -> str:
    if not isinstance(ms, (int, float)) or ms <= 0:
        return ""
    return f"{ms / 1000:.1f} s" if ms >= 1000 else f"{ms:.0f} ms"


class Run:
    """One `quiff` process: its output, its steps, and the approval it may be waiting on."""

    def __init__(self, chore: Chore, mode: str, command: list[str], total_rows: int = 0,
                 csv_path: str = "") -> None:
        self.id = uuid.uuid4().hex[:8]
        self.chore, self.mode, self.command = chore, mode, command
        self.total, self.csv_path = total_rows, csv_path
        self.started = time.time()
        self.state = "running"          # running | approval | done | failed | stopped
        self.approval: dict | None = None
        self.output: list[str] = []
        self.steps: list[dict] = []
        self.calls = 0
        self.exit_code: int | None = None
        self.denied = self.stopping = False
        self.lock = threading.Lock()
        self._offsets: dict[Path, int] = {}
        self._since = time.strftime("%Y-%m-%d-%H%M%S", time.localtime(self.started - 1))
        env = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
        flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if os.name == "nt" else 0
        self.proc = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.STDOUT, env=env, creationflags=flags)
        threading.Thread(target=self._read, daemon=True).start()
        threading.Thread(target=self._follow, daemon=True).start()

    # -- output ----------------------------------------------------------------
    def _read(self) -> None:
        pending = ""
        while True:
            chunk = self.proc.stdout.read1(4096) if hasattr(self.proc.stdout, "read1") \
                else self.proc.stdout.read(1)
            if not chunk:
                break
            pending += chunk.decode("utf-8", "replace")
            at = pending.find(APPROVAL)
            if at != -1 and "Approve?" in pending[at:] and pending.rstrip().endswith(":"):
                before, prompt = pending[:at], pending[at:]
                for line in before.split("\n"):
                    self._line(line)
                reason = prompt[len(APPROVAL):].split(". Approve?")[0].strip()
                with self.lock:
                    self.approval = {"reason": reason, "every_row": "every row" in prompt}
                    self.state = "approval"
                    self.output.append(prompt.strip())
                pending = ""
                continue
            *lines, pending = pending.split("\n")
            for line in lines:
                self._line(line)
        if pending.strip():
            self._line(pending)
        code = self.proc.wait()
        time.sleep(0.4)  # let the follower read the last step lines
        self._scan()
        self._finish(code)

    def _line(self, line: str) -> None:
        line = line.rstrip("\r")
        if line.strip():
            with self.lock:
                self.output.append(line)

    def _finish(self, code: int) -> None:
        with self.lock:
            self.exit_code = code
            self.approval = None
            if self.denied or self.stopping:
                self.state = "stopped"
            else:
                self.state = "done" if code == 0 else "failed"
            reported = self._reported_calls()
            if reported is not None:
                self.calls = reported
        if self.state == "done":
            if self.mode == "teach":
                remember(self.chore, self.calls)
            elif self.chore.kind != "example":
                touch(self.chore.id, self.csv_path)

    def _reported_calls(self) -> int | None:
        for line in reversed(self.output):
            match = re.search(r"(\d+) AI calls in total", line) or re.match(r"calls\s+(\d+) AI calls", line)
            if match:
                return int(match.group(1))
        return None

    # -- steps -----------------------------------------------------------------
    def _follow(self) -> None:
        while self.proc.poll() is None:
            self._scan()
            time.sleep(0.3)

    def _scan(self) -> None:
        try:
            folders = sorted(d for d in RUNS.iterdir()
                             if d.is_dir() and d.name >= self._since and d.name[:4].isdigit())
        except OSError:
            return
        for folder in folders:
            log = folder / "steps.jsonl"
            if not log.exists():
                continue
            offset = self._offsets.get(log, 0)
            try:
                with log.open("rb") as handle:
                    handle.seek(offset)
                    data = handle.read()
            except OSError:
                continue
            end = data.rfind(b"\n") + 1
            if not end:
                continue
            self._offsets[log] = offset + end
            match = re.search(r"-r(\d+)$", folder.name)
            row = int(match.group(1)) if match else None
            for raw in data[:end].decode("utf-8", "replace").splitlines():
                try:
                    entry = describe(json.loads(raw), row)
                except ValueError:
                    entry = None
                if entry:
                    with self.lock:
                        self.steps.append(entry)
                        if entry["model"]:
                            self.calls += 1

    # -- control ---------------------------------------------------------------
    def answer(self, reply: str) -> bool:
        with self.lock:
            if self.state != "approval" or reply not in ("y", "a", "n"):
                return False
            if reply == "a" and not (self.approval or {}).get("every_row"):
                reply = "y"
            if reply == "n":
                self.denied = True
            self.state, self.approval = "running", None
        try:
            self.proc.stdin.write((reply + "\n").encode())
            self.proc.stdin.flush()
        except OSError:
            return False
        return True

    def stop(self) -> None:
        with self.lock:
            if self.exit_code is not None:
                return
            self.stopping = True
        try:
            if os.name == "nt":
                self.proc.send_signal(signal.CTRL_BREAK_EVENT)
            else:
                self.proc.send_signal(signal.SIGINT)
        except (OSError, ValueError):
            pass
        try:
            self.proc.stdin.close()  # a pending approval prompt ends too
        except OSError:
            pass

        def kill_later() -> None:
            try:
                self.proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        threading.Thread(target=kill_later, daemon=True).start()

    # -- what the page shows ---------------------------------------------------
    def snapshot(self, since: int = 0) -> dict:
        with self.lock:
            steps = list(self.steps)
            state, approval, calls = self.state, self.approval, self.calls
            output = list(self.output)
        planned = self.mode == "teach" and self.chore.kind != "example"
        reported = self._reported_calls()
        calls_now = reported if reported is not None else calls + (1 if planned else 0)
        rows_done = len({s["row"] for s in steps if s["kind"] == "check" and s["row"]})
        last = next((s for s in reversed(steps) if s["kind"] != "check" and s["who"] != "Quiff"), None)
        who = (last or {}).get("who") or ("Muse Spark" if planned else "Replay")
        row_now = min(self.total, rows_done + 1) if self.total else 0
        parts = []
        if self.total:
            parts.append(f"Row {self.total if state == 'done' else row_now} of {self.total}")
        parts.append(who + (f" · {last['secs']}" if last and last.get("secs") else ""))
        parts.append(f"{calls_now} AI call" + ("" if calls_now == 1 else "s"))
        progress = None
        if self.total:
            progress = 100 if state == "done" else round(100 * rows_done / self.total)
        headline = (last or {}).get("text") or ("Planning the chore…" if planned else "Starting…")
        if state == "approval" and approval:
            headline = approval["reason"]
        summary = _summary(output)
        if state == "done":
            seconds = f" · {summary['seconds']:.1f} s" if summary.get("seconds") else ""
            if self.total:
                passed = summary.get("rows_passed", self.total)
                headline = f"{passed} of {self.total} rows done"
                parts = [f"{passed} of {self.total} rows", f"{calls_now} AI calls" + seconds]
            else:
                headline = "Done" if self.mode == "teach" else "Done again, exactly as taught"
                parts = [f"{calls_now} AI call" + ("" if calls_now == 1 else "s") + seconds]
        elif state in ("failed", "stopped"):
            headline = "Stopped" if state == "stopped" else "Didn’t finish"
        return {
            "id": self.id, "state": state, "mode": self.mode, "approval": approval,
            "chore": {"id": self.chore.id, "title": self.chore.title, "kind": self.chore.kind},
            "steps": steps[since:], "next": len(steps), "headline": headline,
            "meter": " · ".join(parts), "progress": progress, "calls": calls_now,
            "elapsed": round(time.time() - self.started, 1), "summary": summary,
            "tail": output[-10:] if state in ("failed", "stopped") else [],
        }


def _summary(output: list[str]) -> dict:
    """The facts the last lines report: rows passed, AI calls, time, checks."""
    found: dict = {}
    for line in output:
        batch = re.match(r"batch\s+(\d+)/(\d+) rows passed.*?(\d+) AI calls in total · ([\d.]+) s", line)
        if batch:
            found.update(rows_passed=int(batch.group(1)), rows=int(batch.group(2)),
                         calls=int(batch.group(3)), seconds=float(batch.group(4)))
        steps = re.match(r"steps\s+\d+ · ([\d.]+)s", line)
        if steps:
            found["seconds"] = float(steps.group(1))
        calls = re.match(r"calls\s+(\d+) AI calls", line)
        if calls:
            found["calls"] = int(calls.group(1))
        if line.startswith("check ") and "PASS" in line:
            found["checks_passed"] = found.get("checks_passed", 0) + 1
        if line.startswith("check ") and "FAIL" in line:
            found["checks_failed"] = found.get("checks_failed", 0) + 1
        if line.startswith("check ") and "none given" in line:
            found["no_checks"] = True
    return found
