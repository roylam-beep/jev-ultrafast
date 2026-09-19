"""Offline contracts for pointer input. No browser, no paid APIs."""

from unittest.mock import Mock, call

import pytest

from jev_ultrafast import pointer
from jev_ultrafast.pointer import (
    click,
    click_events,
    dblclick,
    drag,
    hover,
    pressed_buttons,
    scroll,
    scroll_expression,
)


@pytest.mark.parametrize("button, bits", [("left", 1), ("right", 2), ("middle", 4)])
def test_pressed_buttons(button, bits):
    assert pressed_buttons(button) == bits


@pytest.mark.parametrize("button", ["Left", "back", "", None])
def test_an_unsupported_button_is_rejected(button):
    with pytest.raises(ValueError, match="unsupported mouse button"):
        pressed_buttons(button)


def test_a_click_presses_then_releases_with_the_button_held_only_once():
    press, release = click_events(10, 20)
    assert press == {
        "type": "mousePressed",
        "x": 10,
        "y": 20,
        "button": "left",
        "clickCount": 1,
        "buttons": 1,
    }
    assert release == {**press, "type": "mouseReleased", "buttons": 0}


def test_a_click_carries_no_leading_move():
    """Chromium synthesises enter/over from the press; verified on a hover-gated menu."""
    assert [event["type"] for event in click_events(1, 2)] == ["mousePressed", "mouseReleased"]


def test_a_right_click_holds_its_own_button():
    press, release = click_events(5, 5, button="right")
    assert (press["button"], press["buttons"]) == ("right", 2)
    assert release["buttons"] == 0


def test_click_dispatches_both_events():
    browser = Mock()
    click(browser, 3, 4)
    press, release = click_events(3, 4)
    assert browser.call.call_args_list == [
        call("Input.dispatchMouseEvent", **press),
        call("Input.dispatchMouseEvent", **release),
    ]


def test_click_holds_the_button_only_when_asked(monkeypatch):
    slept = []
    monkeypatch.setattr(pointer.time, "sleep", slept.append)
    click(Mock(), 1, 1)
    assert slept == []
    click(Mock(), 1, 1, delay=0.05)
    assert slept == [0.05]


def test_dblclick_sends_a_second_press_carrying_click_count_two():
    browser = Mock()
    dblclick(browser, 7, 8)
    counts = [c.kwargs["clickCount"] for c in browser.call.call_args_list]
    types = [c.kwargs["type"] for c in browser.call.call_args_list]
    assert types == ["mousePressed", "mouseReleased", "mousePressed", "mouseReleased"]
    assert counts == [1, 1, 2, 2]


def test_hover_moves_with_no_button_held():
    browser = Mock()
    hover(browser, 11, 12)
    browser.call.assert_called_once_with(
        "Input.dispatchMouseEvent", type="mouseMoved", x=11, y=12, button="none", buttons=0
    )


def test_drag_holds_the_button_across_every_move():
    browser = Mock()
    drag(browser, [(0, 0), (10, 10), (20, 20)])
    events = [c.kwargs for c in browser.call.call_args_list]
    assert [e["type"] for e in events] == [
        "mousePressed",
        "mouseMoved",
        "mouseMoved",
        "mouseReleased",
    ]
    assert [e["buttons"] for e in events] == [1, 1, 1, 0]
    assert (events[0]["x"], events[0]["y"]) == (0, 0)
    assert (events[-1]["x"], events[-1]["y"]) == (20, 20)


@pytest.mark.parametrize("points", [[], [(1, 1)]])
def test_drag_needs_a_path(points):
    with pytest.raises(ValueError, match="at least two points"):
        drag(Mock(), points)


def test_scroll_never_dispatches_a_cdp_wheel():
    """CDP drops mouseWheel on a background target, and the agent owns one."""
    browser = Mock()
    browser.evaluate.return_value = True
    assert scroll(browser, 560) is True
    browser.call.assert_not_called()
    browser.evaluate.assert_called_once_with(scroll_expression(560))


def test_scroll_reports_a_page_that_did_not_move():
    browser = Mock()
    browser.evaluate.return_value = False
    assert scroll(browser, 560) is False


def test_the_scroll_expression_defaults_to_the_viewport_centre():
    expression = scroll_expression(560)
    assert "innerWidth / 2" in expression
    assert "innerHeight / 2" in expression
    assert expression.endswith("(...[0, 560, null, null])")


def test_the_scroll_expression_takes_an_explicit_point():
    assert scroll_expression(100, delta_x=-20, x=5, y=6).endswith("(...[-20, 100, 5, 6])")


def test_the_scroll_expression_respects_prevent_default():
    expression = scroll_expression(560)
    assert "cancelable: true" in expression
    assert "if (allowed) window.scrollBy(dx, dy);" in expression
