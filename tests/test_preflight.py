"""Tests for the desktop pre-flight and cleanup: the user's windows and notes are never touched."""

import pytest

# the desktop side drives Windows UI Automation (comtypes): Windows only
pytest.importorskip("comtypes", reason="desktop tests need Windows UI Automation")

import quiff.tasks as tasks_mod  # noqa: E402
from quiff.observe import desktop as desktop_mod  # noqa: E402
from quiff.tasks import Task, _preflight_desktop_app  # noqa: E402


def make_task():
    return Task(id="d01-notepad-save", app="notepad", split="dev",
                goal="write", start={"app": "notepad"}, checks=[])


@pytest.fixture
def fake_desktop(monkeypatch):
    """Fake the window helpers; record any attempt to close a window."""
    state = {"closed": [], "windows": [], "titles": {}, "pids": {}}
    monkeypatch.setattr(desktop_mod, "_top_windows_of_pid",
                        lambda pid: [w for w in state["windows"]
                                     if state["pids"].get(w) == pid])
    monkeypatch.setattr(desktop_mod, "_window_title",
                        lambda hwnd: state["titles"].get(hwnd, ""))
    monkeypatch.setattr(desktop_mod, "_visible_windows", lambda: list(state["windows"]))
    monkeypatch.setattr(desktop_mod, "_pid_of", lambda hwnd: state["pids"].get(hwnd, 0))

    class RecordingUser32:
        def PostMessageW(self, hwnd, msg, w, l):
            state["closed"].append(hwnd)
            return 1

        def IsWindow(self, hwnd):
            return hwnd in state["windows"]

    monkeypatch.setattr(desktop_mod, "user32", RecordingUser32())
    return state


def tab_state(local_appdata):
    folder = local_appdata / desktop_mod.NOTEPAD_TAB_STATE
    folder.mkdir(parents=True)
    return folder


def test_preflight_proceeds_when_notepad_is_closed_and_has_no_saved_tabs(
        monkeypatch, fake_desktop):
    monkeypatch.setattr(tasks_mod, "_notepad_pids", lambda: set())
    _preflight_desktop_app(make_task())  # must not raise
    assert fake_desktop["closed"] == []


@pytest.mark.parametrize("title", ["Untitled - Notepad", "*Untitled - Notepad",
                                   "dates.txt - Notepad"])
def test_preflight_refuses_and_never_closes_any_open_notepad_window(
        monkeypatch, fake_desktop, title):
    fake_desktop["windows"] = [101]
    fake_desktop["titles"] = {101: title}
    fake_desktop["pids"] = {101: 55}
    monkeypatch.setattr(tasks_mod, "_notepad_pids", lambda: {55})
    with pytest.raises(RuntimeError, match="Notepad is already open"):
        _preflight_desktop_app(make_task())
    assert fake_desktop["closed"] == []  # not even a blank or unsaved window


def test_preflight_refuses_on_saved_tabs_and_never_deletes_them(
        monkeypatch, fake_desktop, isolated_local_appdata):
    folder = tab_state(isolated_local_appdata)
    note = folder / "user-tab.bin"
    note.write_bytes(b"the user's unsaved notes")
    monkeypatch.setattr(tasks_mod, "_notepad_pids", lambda: set())
    with pytest.raises(RuntimeError, match="saved session tab"):
        _preflight_desktop_app(make_task())
    assert note.read_bytes() == b"the user's unsaved notes"


def test_preflight_ignores_other_apps(monkeypatch, fake_desktop):
    monkeypatch.setattr(tasks_mod, "_notepad_pids", lambda: {55})
    task = Task(id="d02", app="explorer", split="dev", goal="x", checks=[])
    _preflight_desktop_app(task)  # explorer: nothing to check, nothing closed
    assert fake_desktop["closed"] == []


def test_cleanup_removes_only_tab_files_this_run_created(monkeypatch, isolated_local_appdata):
    folder = tab_state(isolated_local_appdata)
    (folder / "old.bin").write_bytes(b"existed before the run")
    before = desktop_mod.notepad_tab_files()
    (folder / "ours.bin").write_bytes(b"quiff's own tab")
    monkeypatch.setattr(desktop_mod, "notepad_pids", lambda: set())
    note = desktop_mod.clean_own_notepad_tabs(before)
    assert "removed 1" in note
    assert (folder / "old.bin").exists()
    assert not (folder / "ours.bin").exists()


def test_cleanup_keeps_new_files_when_another_notepad_window_is_open(
        monkeypatch, isolated_local_appdata):
    folder = tab_state(isolated_local_appdata)
    before = desktop_mod.notepad_tab_files()
    (folder / "maybe-users.bin").write_bytes(b"could be the user's")
    monkeypatch.setattr(desktop_mod, "notepad_pids", lambda: {77})
    note = desktop_mod.clean_own_notepad_tabs(before)
    assert "might be yours" in note
    assert (folder / "maybe-users.bin").exists()
