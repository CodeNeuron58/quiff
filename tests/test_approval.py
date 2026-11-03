"""Tests for the approval guard: no bypasses, few false alarms, one key reading."""

from pathlib import Path

import pytest

from quiff.act import Focus, combo_label, needs_approval, parse_combo
from quiff.decide import Decision
from quiff.observe.table import ActionRow, ActionTable


def row(index, role, name):
    return ActionRow(index=index, role=role, name=name, actions=("click",),
                     enabled=True, target=index)


def browser_table(*rows):
    return ActionTable(title="page", rows=tuple(rows), url="http://x/")


def desktop_table(*rows):
    return ActionTable(title="window", rows=tuple(rows))  # desktop tables have no URL


def press(*values):
    return Decision(action="press", values=tuple(values))


# ------------------------------------------------------------- one key reading

@pytest.mark.parametrize("values, expected", [
    (("ctrl+enter",), "ctrl+enter"),
    (("ctrl + enter",), "ctrl+enter"),
    (("Enter+Ctrl",), "ctrl+enter"),
    (("ctrl", "enter"), "ctrl+enter"),
    (("control return",), "ctrl+enter"),
    (("Page Down",), "pagedown"),
    (("shift+Del",), "shift+delete"),
    ((), "enter"),
])
def test_parse_combo_reads_every_spelling_the_same_way(values, expected):
    assert combo_label(values) == expected


def test_parse_combo_separates_modifiers_from_keys():
    assert parse_combo(["alt", "s"]) == (("alt",), ("s",))


# ------------------------------------------------------------- shortcut bypasses

@pytest.mark.parametrize("values", [("ctrl + enter",), ("enter+ctrl",), ("ctrl", "enter"),
                                    ("alt+s",), ("Shift+Delete",)])
def test_risky_shortcuts_need_approval_however_written(values):
    assert needs_approval(press(*values), browser_table()) is not None


def test_enter_in_a_form_with_a_risky_submit_needs_approval():
    focus = Focus(role="textbox", name="Comments", form_submits=("Submit order",))
    reason = needs_approval(press("Enter"), browser_table(), focus=focus)
    assert reason and "Submit order" in reason


def test_enter_in_a_login_form_is_routine():
    focus = Focus(role="textbox", name="Password", form_submits=("Login",))
    assert needs_approval(press("Enter"), browser_table(), focus=focus) is None


def test_enter_or_space_on_a_risky_button_needs_approval():
    focus = Focus(role="button", name="Send")
    assert needs_approval(press("Enter"), browser_table(), focus=focus)
    assert needs_approval(press("space"), browser_table(), focus=focus)


def test_enter_with_unknown_focus_asks_only_if_something_risky_is_on_screen():
    risky = browser_table(row(1, "button", "Submit order"))
    calm = browser_table(row(1, "button", "Login"))
    assert needs_approval(press("Enter"), risky, focus=None)
    assert needs_approval(press("Enter"), calm, focus=None) is None


def test_empty_press_is_judged_as_enter():
    focus = Focus(role="textbox", name="x", form_submits=("Pay now",))
    assert needs_approval(press(), browser_table(), focus=focus)


def test_delete_key_on_desktop_items_needs_approval_but_not_inside_text():
    on_file = Focus(role="list item", name="report.txt")
    in_box = Focus(role="edit", name="Name")
    assert needs_approval(press("Delete"), desktop_table(), focus=on_file)
    assert needs_approval(press("Delete"), desktop_table(), focus=in_box) is None
    assert needs_approval(press("Delete"), browser_table(), focus=None) is None


# ------------------------------------------------------------- clicks: no false alarms

@pytest.mark.parametrize("role, name", [
    ("button", "Submit order"), ("button", "Send"), ("link", "Pay with PayPal"),
    ("menu item", "Delete"), ("button", "Place   order"), ("button", "Check out"),
])
def test_risky_clicks_need_approval(role, name):
    table = browser_table(row(1, role, name))
    assert needs_approval(Decision(action="click", row=1), table)


@pytest.mark.parametrize("role, name", [
    ("textbox", "Postal code"), ("textbox", "Confirm password"), ("button", "PayPal"),
    ("tree item", "Deleted Items"), ("button", "Sender details"), ("list item", "postcard.txt"),
    ("checkbox", "I confirm the terms"), ("button", "Login"),
])
def test_routine_controls_do_not_trigger_the_guard(role, name):
    table = browser_table(row(1, role, name))
    assert needs_approval(Decision(action="click", row=1), table) is None


def test_typing_never_needs_approval_even_into_a_confirm_field():
    table = browser_table(row(1, "textbox", "Confirm password"))
    assert needs_approval(Decision(action="type", row=1, values=("x",)), table) is None


def test_selecting_a_risky_option_needs_approval():
    table = browser_table(row(1, "combobox", "Action"))
    assert needs_approval(Decision(action="select", row=1, values=("Delete account",)), table)
    assert needs_approval(Decision(action="select", row=1, values=("Option 2",)), table) is None


# ------------------------------------------------------------- save_as

def test_save_as_a_new_file_inside_the_sandbox_is_routine(tmp_path):
    decision = Decision(action="save_as", values=(str(tmp_path / "hello.txt"),))
    assert needs_approval(decision, desktop_table(), write_root=tmp_path) is None


def test_save_as_that_overwrites_needs_approval(tmp_path):
    existing = tmp_path / "hello.txt"
    existing.write_text("keep me")
    decision = Decision(action="save_as", values=(str(existing),))
    reason = needs_approval(decision, desktop_table(), write_root=tmp_path)
    assert reason and "overwrite" in reason


def test_save_as_outside_the_sandbox_needs_approval(tmp_path):
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    decision = Decision(action="save_as", values=(str(tmp_path / "elsewhere.txt"),))
    reason = needs_approval(decision, desktop_table(), write_root=sandbox)
    assert reason and "outside the task folder" in reason


def test_save_as_without_a_known_sandbox_always_asks(tmp_path):
    decision = Decision(action="save_as", values=(str(tmp_path / "new.txt"),))
    assert needs_approval(decision, desktop_table(), write_root=None)


def test_download_always_asks():
    decision = Decision(action="download", values=("http://x/f.txt", "C:/f.txt"))
    assert needs_approval(decision, browser_table())


# ------------------------------------------------------------- reasons people can read

@pytest.mark.parametrize("name, expected", [
    ("Submit order", 'Clicking "Submit order" submits it for good'),
    ("Delete account", 'Clicking "Delete account" deletes it for good'),
    ("Pay now", 'Clicking "Pay now" spends money'),
    ("Send", 'Clicking "Send" sends it for good'),
])
def test_reasons_say_what_would_happen(name, expected):
    # the reason is what a person reads before answering y/n (terminal and
    # overlay pill); it used to be a bare label like: button "Submit order"
    table = browser_table(row(1, "button", name))
    assert needs_approval(Decision(action="click", row=1), table) == expected


def test_key_reasons_name_keys_the_way_people_write_them():
    assert needs_approval(press("ctrl+enter"), browser_table()) == \
        "Pressing Ctrl+Enter can send a message"
    focus = Focus(role="textbox", name="Email", form_submits=("Submit",))
    assert needs_approval(press("Enter"), browser_table(), focus=focus) == \
        'Pressing Enter triggers "Submit", which submits it for good'
    on_file = Focus(role="listitem", name="report.docx")
    assert needs_approval(press("Delete"), desktop_table(), focus=on_file) == \
        'Pressing Delete deletes "report.docx"'


# ------------------------------------------------------------- the review's misses

@pytest.mark.parametrize("role, name", [
    ("button", "Place your order"), ("button", "Proceed to payment"),
    ("menu item", "Move to Trash"), ("button", "Empty Recycle Bin"),
    ("button", "Merge pull request"), ("button", "Squash and merge"),
    ("button", "Cancel subscription"), ("link", "Unsubscribe"),
    ("button", "Close account"), ("button", "Discard draft"), ("button", "Order now"),
])
def test_the_guard_now_catches_what_the_review_found(role, name):
    table = browser_table(row(1, role, name))
    assert needs_approval(Decision(action="click", row=1), table)


@pytest.mark.parametrize("role, name", [
    ("button", "Cancel"), ("tree item", "Recycle Bin"), ("link", "Order history"),
    ("link", "Subscriptions"), ("button", "Merge cells"), ("link", "Booking details"),
    ("button", "Close"),
])
def test_wider_list_still_leaves_everyday_controls_alone(role, name):
    table = browser_table(row(1, role, name))
    assert needs_approval(Decision(action="click", row=1), table) is None


@pytest.mark.parametrize("name, expected", [
    ("Place your order", 'Clicking "Place your order" spends money'),
    ("Move to Trash", 'Clicking "Move to Trash" deletes it for good'),
    ("Cancel subscription", 'Clicking "Cancel subscription" cancels it for good'),
    ("Merge pull request", 'Clicking "Merge pull request" merges it for good'),
])
def test_new_reasons_say_what_would_happen(name, expected):
    table = browser_table(row(1, "button", name))
    assert needs_approval(Decision(action="click", row=1), table) == expected


def test_yes_in_a_confirm_save_as_dialog_needs_approval():
    # a save typed by hand into the dialog skips the save_as check; the
    # overwrite confirmation is where it is caught
    table = ActionTable(title="Confirm Save As", rows=(row(1, "button", "Yes"),
                                                       row(2, "button", "No")))
    assert needs_approval(Decision(action="click", row=1), table) == \
        'Clicking "Yes" replaces an existing file'
    assert needs_approval(Decision(action="click", row=2), table) is None


def test_yes_elsewhere_is_routine():
    table = ActionTable(title="Notepad", rows=(row(1, "button", "Yes"),))
    assert needs_approval(Decision(action="click", row=1), table) is None
