"""`quiff do`: your own goal, an id that finds its flow again, honest verdicts."""

import argparse
import sys
from types import SimpleNamespace

import pytest

from quiff.cli import adhoc_task, main
from quiff.tasks import TaskRun


def args(goal="Log in", url=None, app=None, **checks):
    return argparse.Namespace(goal=goal, url=url, app=app,
                              check_url=checks.get("check_url", []),
                              check_text=checks.get("check_text", []),
                              check_file=checks.get("check_file", []),
                              check_answer=checks.get("check_answer"))


def test_same_goal_and_start_find_the_same_flow():
    first = adhoc_task(args(url="http://127.0.0.1:8765/login"))
    again = adhoc_task(args(url="http://127.0.0.1:8765/login"))
    other = adhoc_task(args(goal="Log out", url="http://127.0.0.1:8765/login"))
    assert first.id == again.id and first.id.startswith("do-")
    assert other.id != first.id


def test_browser_goal_carries_its_checks():
    task = adhoc_task(args(url="https://x.test/login", check_url=["/secure$"],
                           check_text=["Welcome"], check_answer=r"\d+"))
    assert (task.app, task.start, task.split) == ("chrome", {"url": "https://x.test/login"},
                                                  "adhoc")
    assert task.checks == [{"type": "url", "pattern": "/secure$"},
                           {"type": "page_text", "contains": "Welcome"},
                           {"type": "answer", "pattern": r"\d+"}]


def test_desktop_goal_opens_the_app_in_its_task_folder():
    explorer = adhoc_task(args(goal="Make a folder called reports", app="explorer",
                               check_file=["reports"]))
    assert explorer.start == {"app": "explorer", "folder": "."}
    assert explorer.checks == [{"type": "file", "path": "reports"}]
    notepad = adhoc_task(args(goal="Write a note", app="notepad"))
    assert notepad.start == {"app": "notepad"}  # Notepad isn't handed a folder


def test_without_checks_the_models_done_is_the_verdict():
    loop_done = SimpleNamespace(success=True)
    loop_stuck = SimpleNamespace(success=False)
    assert TaskRun(task=None, loop=loop_done, checks=[]).success is True
    assert TaskRun(task=None, loop=loop_stuck, checks=[]).success is False


def test_with_checks_the_checks_still_decide():
    failed = SimpleNamespace(ok=False)
    assert TaskRun(task=None, loop=SimpleNamespace(success=True), checks=[failed]).success is False


@pytest.mark.parametrize("argv", [
    ["quiff", "do", "Log in"],  # no start
    ["quiff", "do", "Log in", "--url", "https://x.test", "--app", "notepad"],  # both
])
def test_do_needs_exactly_one_place_to_start(monkeypatch, argv):
    monkeypatch.setattr(sys, "argv", argv)
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 2


def test_quick_decisions_keep_their_short_timeout():
    # one shared client used a 60 s timeout for every call; a hung minimal
    # decision must give up after 20 s so the retry and the rescuer can act
    from quiff.cli import _decision_timeout
    from quiff.models.meta import ChatClient

    assert _decision_timeout("minimal") == 20.0
    assert _decision_timeout("high") == 60.0
    client = ChatClient(api_key="k", model="m", base_url="https://example.test/v1",
                        timeout=_decision_timeout("minimal"))
    assert client._http.timeout.read == 20.0
