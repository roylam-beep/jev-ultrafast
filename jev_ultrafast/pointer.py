"""Pointer input over one owned CDP session.

Ported from ego-lite's `package/ego-browser/src/driver/pointer.ts` (MIT,
CitroLabs). The part that matters here is its wheel handling: CDP delivers
`Input.dispatchMouseEvent` of type `mouseWheel` only to a foreground, focused
target, and the agent owns a background tab. Against Chromium a background tab
drops the event entirely and `scrollY` never moves, so `scroll_expression`
dispatches a real WheelEvent and performs the scroll itself.

`Emulation.setFocusEmulationEnabled` is not enough: it makes `visibilityState`
report "visible" and `document.hasFocus()` report true while the wheel is still
dropped, so the source's `isVisibleAndFocused` probe would pick the broken path
here. The synthetic path is unconditional instead, and costs the same one call.

An untrusted WheelEvent performs no default scroll, so the scroll is explicit —
which means it moves the document, not a nested scroller. Nested scrolling is
already outside this MVP. A page that calls `preventDefault()` on the wheel
keeps the page in place, exactly as a real wheel would.

The source's dispatch-timeout probes are not ported: browser_harness answers
each CDP call synchronously.
"""

import json
import time

# CDP's buttons bitmask. Chromium derives this from `button` for a plain click,
# but a multi-button drag has to state what is still held.
BUTTON_BITS = {"left": 1, "right": 2, "middle": 4}


def pressed_buttons(button):
    """The buttons bitmask while `button` is held."""
    if button not in BUTTON_BITS:
        raise ValueError(f"unsupported mouse button: {button!r}")
    return BUTTON_BITS[button]


def scroll_expression(delta_y, delta_x=0, x=None, y=None):
    """Build the page expression that wheels at (x, y), defaulting to the centre.

    Returns an expression evaluating to True only if the page actually moved: a
    handler that calls `preventDefault()` leaves it in place and reports False.
    """
    args = json.dumps([delta_x, delta_y, x, y])
    return """((dx, dy, px, py) => {
      const x = px === null ? innerWidth / 2 : px, y = py === null ? innerHeight / 2 : py;
      const target = document.elementFromPoint(x, y) || document.scrollingElement || document.body;
      if (!target) return false;
      const fromX = scrollX, fromY = scrollY;
      const allowed = target.dispatchEvent(new WheelEvent('wheel',
        {bubbles: true, cancelable: true, deltaX: dx, deltaY: dy, clientX: x, clientY: y}));
      if (allowed) window.scrollBy(dx, dy);
      return allowed && (scrollX !== fromX || scrollY !== fromY);
    })(...""" + args + ")"


def scroll(browser, delta_y, delta_x=0, x=None, y=None):
    """Wheel at a viewport point. True when the page moved."""
    return bool(browser.evaluate(scroll_expression(delta_y, delta_x, x, y)))


def click_events(x, y, button="left", click_count=1):
    """The press and release parameter dicts for a click at (x, y).

    No leading `mouseMoved`: Chromium synthesises the enter/over sequence from
    the press position, verified against a hover-gated menu, so the extra call
    would buy nothing per click.
    """
    held = pressed_buttons(button)
    base = {"x": x, "y": y, "button": button, "clickCount": click_count}
    return [
        {**base, "type": "mousePressed", "buttons": held},
        {**base, "type": "mouseReleased", "buttons": 0},
    ]


def click(browser, x, y, button="left", click_count=1, delay=0.0):
    """Press and release at (x, y). `delay` holds the button between the two."""
    press, release = click_events(x, y, button, click_count)
    browser.call("Input.dispatchMouseEvent", **press)
    if delay:
        time.sleep(delay)
    browser.call("Input.dispatchMouseEvent", **release)


def dblclick(browser, x, y, button="left", delay=0.0):
    """Two presses at the same point, the second carrying clickCount 2."""
    click(browser, x, y, button=button, click_count=1, delay=delay)
    click(browser, x, y, button=button, click_count=2, delay=delay)


def hover(browser, x, y):
    """Move the pointer to (x, y) with no button held."""
    browser.call(
        "Input.dispatchMouseEvent", type="mouseMoved", x=x, y=y, button="none", buttons=0
    )


def drag(browser, points, button="left", delay=0.0):
    """Press at the first point, move through the rest, release at the last."""
    points = list(points)
    if len(points) < 2:
        raise ValueError("drag needs at least two points")
    held = pressed_buttons(button)
    (start_x, start_y), (end_x, end_y) = points[0], points[-1]
    browser.call(
        "Input.dispatchMouseEvent",
        type="mousePressed",
        x=start_x,
        y=start_y,
        button=button,
        buttons=held,
        clickCount=1,
    )
    for point_x, point_y in points[1:]:
        if delay:
            time.sleep(delay)
        browser.call(
            "Input.dispatchMouseEvent",
            type="mouseMoved",
            x=point_x,
            y=point_y,
            button=button,
            buttons=held,
        )
    browser.call(
        "Input.dispatchMouseEvent",
        type="mouseReleased",
        x=end_x,
        y=end_y,
        button=button,
        buttons=0,
        clickCount=1,
    )
