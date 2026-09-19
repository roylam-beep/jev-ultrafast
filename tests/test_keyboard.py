"""Offline contracts for key event construction. No browser, no paid APIs."""

from unittest.mock import Mock, call

import pytest

from jev_ultrafast import keyboard
from jev_ultrafast.keyboard import (
    ALT,
    CONTROL,
    META,
    SHIFT,
    editing_commands,
    key_definition,
    key_events,
    parse_key_combo,
    press,
)


@pytest.mark.parametrize(
    "key, virtual_key, code, text",
    [
        ("Enter", 13, "Enter", "\r"),
        ("Escape", 27, "Escape", ""),
        ("Tab", 9, "Tab", "\t"),
        ("ArrowDown", 40, "ArrowDown", ""),
        (" ", 32, "Space", " "),
        ("a", 65, "KeyA", "a"),
        ("A", 65, "KeyA", "A"),
        ("z", 90, "KeyZ", "z"),
        ("7", 55, "Digit7", "7"),
        ("+", 43, "+", "+"),
    ],
)
def test_key_definition(key, virtual_key, code, text):
    assert key_definition(key) == (virtual_key, code, text)


def test_an_unknown_multi_character_key_is_inert():
    assert key_definition("F13") == (0, "F13", "")


@pytest.mark.parametrize(
    "combo, key, modifiers",
    [
        ("Enter", "Enter", 0),
        ("Control+a", "a", CONTROL),
        ("Shift+Tab", "Tab", SHIFT),
        ("Control+Shift+Alt+Meta+k", "k", CONTROL | SHIFT | ALT | META),
        ("+", "+", 0),
        ("Shift++", "+", SHIFT),
    ],
)
def test_parse_key_combo(combo, key, modifiers):
    assert parse_key_combo(combo) == (key, modifiers)


def test_control_or_meta_follows_the_platform(monkeypatch):
    monkeypatch.setattr(keyboard, "CONTROL_OR_META", META)
    assert parse_key_combo("ControlOrMeta+a") == ("a", META)
    monkeypatch.setattr(keyboard, "CONTROL_OR_META", CONTROL)
    assert parse_key_combo("ControlOrMeta+a") == ("a", CONTROL)


@pytest.mark.parametrize("combo", ["Ctrl+a", "Cmd+a", "Super+x"])
def test_an_unknown_modifier_is_rejected(combo):
    with pytest.raises(ValueError, match="unknown key modifier"):
        parse_key_combo(combo)


@pytest.mark.parametrize("combo", ["", None, 0])
def test_an_empty_combo_is_rejected(combo):
    with pytest.raises(ValueError, match="press needs a key combo"):
        parse_key_combo(combo)


@pytest.mark.parametrize(
    "key, modifiers, expected",
    [
        ("a", CONTROL, ["selectAll"]),
        ("A", META, ["selectAll"]),
        ("a", 0, None),
        ("a", CONTROL | SHIFT, None),
        ("Backspace", 0, ["deleteBackward"]),
        ("Backspace", CONTROL, None),
        ("Delete", 0, ["deleteForward"]),
        ("Enter", 0, None),
    ],
)
def test_editing_commands(key, modifiers, expected):
    assert editing_commands(key, modifiers) == expected


def test_a_plain_key_carries_its_identity_both_ways():
    down, up = key_events("Escape")
    assert down == {
        "type": "keyDown",
        "key": "Escape",
        "code": "Escape",
        "windowsVirtualKeyCode": 27,
        "nativeVirtualKeyCode": 27,
        "modifiers": 0,
    }
    assert up == {**down, "type": "keyUp"}


def test_enter_carries_the_text_it_inserts():
    down, _up = key_events("Enter")
    assert down["text"] == "\r"
    assert down["unmodifiedText"] == "\r"


def test_a_key_that_inserts_nothing_sends_no_text():
    down, _up = key_events("ArrowDown")
    assert "text" not in down and "unmodifiedText" not in down


def test_select_all_carries_the_virtual_key_and_the_command():
    """A page checking keyCode 65 must see 65, not the 0 an omitted field means."""
    down, up = key_events("Control+a")
    assert down["windowsVirtualKeyCode"] == 65
    assert down["nativeVirtualKeyCode"] == 65
    assert down["code"] == "KeyA"
    assert down["modifiers"] == CONTROL
    assert down["commands"] == ["selectAll"]
    assert "commands" not in up


def test_releasing_a_modifier_does_not_report_it_as_held():
    """Verified against Chromium: keyUp with the bit set reads shiftKey true."""
    down, up = key_events("Shift")
    assert down["modifiers"] == SHIFT
    assert up["modifiers"] == 0


def test_a_modifier_held_for_another_key_stays_held_through_its_release():
    down, up = key_events("Control+a")
    assert down["modifiers"] == CONTROL
    assert up["modifiers"] == CONTROL


def test_press_dispatches_down_then_up():
    browser = Mock()
    press(browser, "Escape")
    down, up = key_events("Escape")
    assert browser.call.call_args_list == [
        call("Input.dispatchKeyEvent", **down),
        call("Input.dispatchKeyEvent", **up),
    ]


def test_press_holds_the_key_only_when_asked(monkeypatch):
    slept = []
    monkeypatch.setattr(keyboard.time, "sleep", slept.append)
    press(Mock(), "Escape")
    assert slept == []
    press(Mock(), "Escape", delay=0.05)
    assert slept == [0.05]
