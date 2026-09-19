"""Key events carrying the identity a page expects: key, code, keyCode, text.

Ported from ego-lite's `package/ego-browser/src/driver/keyboard.ts` (MIT,
CitroLabs), reduced to the dispatch this executor needs. `key_events` is pure:
it turns a combo into the CDP parameter dicts, so the mapping is testable
without a browser and `press` stays a dispatcher.

One correction to the source. It sends the same modifier bitfield on keyUp as
on keyDown, so releasing a modifier reports it as still held; against Chromium,
`press("Shift")` then reads `shiftKey: true` on the keyup. The released key's
own bit is cleared here, which matches a real keyboard.

The source's bridge-timeout probe is not ported: browser_harness answers each
CDP call synchronously, so there is nothing to probe for.
"""

import sys
import time

ALT, CONTROL, META, SHIFT = 1, 2, 4, 8
MODIFIER_BITS = {"Alt": ALT, "Control": CONTROL, "Meta": META, "Shift": SHIFT}
# The accelerator a page binds for select-all, undo and friends.
CONTROL_OR_META = META if sys.platform == "darwin" else CONTROL

# key -> the DOM identity Chromium should report. text is what the key inserts.
KEYS = {
    "Enter": (13, "Enter", "\r"),
    "Tab": (9, "Tab", "\t"),
    "Backspace": (8, "Backspace", ""),
    "Escape": (27, "Escape", ""),
    "Delete": (46, "Delete", ""),
    " ": (32, "Space", " "),
    "ArrowLeft": (37, "ArrowLeft", ""),
    "ArrowUp": (38, "ArrowUp", ""),
    "ArrowRight": (39, "ArrowRight", ""),
    "ArrowDown": (40, "ArrowDown", ""),
    "Home": (36, "Home", ""),
    "End": (35, "End", ""),
    "PageUp": (33, "PageUp", ""),
    "PageDown": (34, "PageDown", ""),
    "Shift": (16, "ShiftLeft", ""),
    "Control": (17, "ControlLeft", ""),
    "Alt": (18, "AltLeft", ""),
    "Meta": (91, "MetaLeft", ""),
}
# A modifier's own bit has to be set on its keyDown and cleared on its keyUp.
MODIFIER_OF = {
    "Shift": SHIFT, "ShiftLeft": SHIFT, "ShiftRight": SHIFT,
    "Control": CONTROL, "ControlLeft": CONTROL, "ControlRight": CONTROL,
    "Alt": ALT, "AltLeft": ALT, "AltRight": ALT,
    "Meta": META, "MetaLeft": META, "MetaRight": META,
}


def key_definition(key):
    """Return (virtual key code, DOM code, inserted text) for one key."""
    if key in KEYS:
        return KEYS[key]
    if len(key) != 1:
        return 0, key, ""
    upper = key.upper()
    if upper.isascii() and upper.isalpha():
        return ord(upper), f"Key{upper}", key
    if key.isascii() and key.isdigit():
        return ord(key), f"Digit{key}", key
    return ord(upper), key, key


def parse_key_combo(combo):
    """Split "Control+a" or "Shift+Tab" into (key, modifier bitfield).

    Modifiers are Alt, Control, Meta, Shift, and ControlOrMeta for the platform
    accelerator. A trailing "+" is the plus key itself: "+", "Shift++".
    """
    if not isinstance(combo, str) or not combo:
        raise ValueError("press needs a key combo")
    parts = combo.split("+")
    key = parts.pop()
    if key == "" and parts:
        key = "+"
        if parts and parts[-1] == "":
            parts.pop()
    modifiers = 0
    for name in parts:
        if name == "ControlOrMeta":
            modifiers |= CONTROL_OR_META
            continue
        if name not in MODIFIER_BITS:
            raise ValueError(f"press: unknown key modifier {name!r}")
        modifiers |= MODIFIER_BITS[name]
    if not key:
        raise ValueError(f"press: no key in combo {combo!r}")
    return key, modifiers


def editing_commands(key, modifiers):
    """Editor-level commands Chromium should run for this key.

    A page can intercept the key event, so the command is what makes the edit
    actually happen rather than only look like it did.
    """
    if modifiers in (CONTROL, META) and key.lower() == "a":
        return ["selectAll"]
    if modifiers == 0 and key == "Backspace":
        return ["deleteBackward"]
    if modifiers == 0 and key == "Delete":
        return ["deleteForward"]
    return None


def key_events(combo):
    """Turn a combo into the keyDown and keyUp parameter dicts, in order."""
    key, modifiers = parse_key_combo(combo)
    virtual_key, code, text = key_definition(key)
    own_bit = MODIFIER_OF.get(code, 0)
    base = {
        "key": key,
        "code": code,
        "windowsVirtualKeyCode": virtual_key,
        "nativeVirtualKeyCode": virtual_key,
    }
    down = {**base, "type": "keyDown", "modifiers": modifiers | own_bit}
    if text:
        down["text"] = text
        down["unmodifiedText"] = text
    commands = editing_commands(key, modifiers)
    if commands:
        down["commands"] = commands
    # Releasing a modifier must not report it as still held.
    return [down, {**base, "type": "keyUp", "modifiers": modifiers & ~own_bit}]


def press(browser, combo, delay=0.0):
    """Dispatch one key press on the browser's session.

    `delay` holds the key between keydown and keyup. It defaults to 0 so the
    executor's own paths cost nothing; a caller waiting on a page to react to
    the keydown passes one.
    """
    down, up = key_events(combo)
    browser.call("Input.dispatchKeyEvent", **down)
    if delay:
        time.sleep(delay)
    browser.call("Input.dispatchKeyEvent", **up)
