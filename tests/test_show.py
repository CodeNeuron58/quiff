"""Show mode: what the overlay is told, without starting the overlay app."""

import pytest

from quiff.decide import Decision
from quiff.loop import FastLoop
from quiff.observe.table import ActionRow
from quiff.show import Show, describe, viewport_to_screen
from fakes import FakeBrowserObserver, FakeDecider, username_password_rows


def row(role, name, index=1):
    return ActionRow(index=index, role=role, name=name, actions=("click",), enabled=True,
                     target=index)


# ------------------------------------------------------------------ describe

def test_describe_click():
    assert describe(Decision(action="click", row=1), row("button", "Attach")) == \
        ('Clicking "Attach"', "Click · Attach")


def test_describe_type_shows_plain_text():
    line, chip = describe(Decision(action="type", row=1, values=("langchain",)),
                          row("combobox", "Search"))
    assert line == 'Typing into "Search"'
    assert chip == 'Type · "langchain"'


@pytest.mark.parametrize("name", ["Password", "One-time code (OTP)", "Card number", "API key"])
def test_describe_never_puts_a_secret_on_screen(name):
    line, chip = describe(Decision(action="type", row=1, values=("hunter2",)),
                          row("textbox", name))
    assert "hunter2" not in line + chip
    assert "••••••" in chip


def test_describe_press_and_save():
    assert describe(Decision(action="press", values=("ctrl+s",)), None) == \
        ("Pressing Ctrl+S", "Press · Ctrl+S")
    line, chip = describe(Decision(action="save_as", values=(r"C:\work\hello.txt",)), None)
    assert line == "Saving as hello.txt"


def test_describe_drops_icon_font_glyphs():
    assert describe(Decision(action="click", row=1), row("button", " Login"))[0] == \
        'Clicking "Login"'


def test_describe_shortens_long_names():
    line, _ = describe(Decision(action="click", row=1), row("link", "x" * 80))
    assert len(line) < 50 and line.endswith('…"')


# ------------------------------------------------------------------ coordinates

def test_viewport_to_screen_exact_viewport():
    # the page fills the area exactly; 1.5x display scaling
    area = (100, 200, 100 + 1280 * 3 // 2, 200 + 800 * 3 // 2)
    assert viewport_to_screen((10, 20, 30, 40), area, 1.5, (1280, 800)) == (115, 230, 160, 290)


def test_viewport_to_screen_client_area_with_toolbar():
    # Chrome's client area also holds the tab strip and toolbar above the page;
    # the page sits at its bottom, so the answer is the same
    area = (100, 80, 100 + 1920, 200 + 1200)
    assert viewport_to_screen((10, 20, 30, 40), area, 1.5, (1280, 800)) == (115, 230, 160, 290)


# ------------------------------------------------------------------ the loop's calls

class RecordingShow(Show):
    active = True

    def __init__(self):
        self.events = []

    def state(self, kind, line, guard=""):
        self.events.append(("state", kind, line, guard))

    def target(self, rect, chip, kind="working", ripple=True):
        self.events.append(("target", rect, chip, kind, ripple))
        return 0.0

    def clear_target(self):
        self.events.append(("clear",))


class OnScreenObserver(FakeBrowserObserver):
    """A headed page: the viewport is at (0, 100)-(1000, 700); every element
    is at CSS (10, 20) and 100 x 30."""

    def __init__(self, rows):
        super().__init__(rows)
        self.clicked = 0

    def viewport_screen_rect(self):
        return (0, 100, 1000, 700)

    async def call(self, method, timeout=15.0, **params):
        fn = params.get("functionDeclaration", "")
        if method == "Runtime.callFunctionOn" and "innerWidth" in fn:
            return {"result": {"value": {"x": 10, "y": 20, "w": 100, "h": 30,
                                         "dpr": 1, "iw": 1000, "ih": 600}}}
        if method == "Input.dispatchMouseEvent" and params.get("type") == "mousePressed":
            self.clicked += 1
        return await super().call(method, timeout=timeout, **params)


@pytest.mark.asyncio
async def test_loop_tells_the_show_what_it_does_before_doing_it():
    show = RecordingShow()
    observer = OnScreenObserver(username_password_rows())
    decider = FakeDecider([Decision(action="type", row=1, values=("tomsmith",)),
                           Decision(action="type", row=2, values=("SuperSecret!",)),
                           Decision(action="click", row=3),
                           Decision(action="done", subgoal_done=True)])
    loop = FastLoop(observer, decider, None, show=show)
    result = await loop.run("log in", start_url="http://x/", subgoal="log in")
    assert result.success

    assert show.events[0] == ("state", "working", "Reading the screen", "")
    targets = [e for e in show.events if e[0] == "target"]
    expected = (10, 120, 110, 150)  # CSS (10, 20) + the viewport's top-left (0, 100)
    assert [t[1] for t in targets] == [expected] * 3
    assert [t[2] for t in targets] == ['Type · "tomsmith"', "Type · ••••••", "Click · Login"]
    assert all(t[3] == "working" and t[4] for t in targets)
    assert "SuperSecret!" not in repr(show.events)
    # each action's sentence goes up before its cursor moves
    first_target = show.events.index(targets[0])
    assert show.events[first_target - 1] == ("state", "working", 'Typing into "Username"', "")


@pytest.mark.asyncio
async def test_approval_parks_the_cursor_without_clicking():
    show = RecordingShow()
    rows = (ActionRow(index=1, role="button", name="Delete account", actions=("click",),
                      enabled=True, target=1),)
    observer = OnScreenObserver(rows)
    asked = []

    async def approver(reason):
        asked.append(reason)
        return False

    loop = FastLoop(observer, FakeDecider([Decision(action="click", row=1)]), None,
                    approver=approver, show=show)
    result = await loop.run("delete it", start_url="http://x/", subgoal="delete it")
    assert not result.success and result.stop_reason.startswith("needs approval")
    assert asked
    approval = [e for e in show.events if e[0] == "state" and e[1] == "approval"]
    assert approval and approval[0][3] == asked[0]  # the guard line names the reason
    targets = [e for e in show.events if e[0] == "target"]
    assert targets == [("target", (10, 120, 110, 150), "Needs approval · Delete account",
                        "approval", False)]
    assert observer.clicked == 0


@pytest.mark.asyncio
async def test_the_base_show_costs_nothing():
    # without --show nothing looks up element positions (no extra page calls)
    observer = OnScreenObserver(username_password_rows())
    loop = FastLoop(observer, FakeDecider([Decision(action="click", row=3),
                                           Decision(action="done", subgoal_done=True)]), None)
    await loop.run("click", start_url="http://x/", subgoal="click")
    assert not any("innerWidth" in str(params) for _, params in observer.cdp.calls)
