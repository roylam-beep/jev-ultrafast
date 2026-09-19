"""Layered waits over one owned CDP session. Signals, not fixed sleeps.

Ported from ego-lite's `package/ego-browser/src/driver/waits.ts` and `load.ts`
(MIT, CitroLabs), reduced to the layers this loop can act on and rebound to the
agent's own session.

The network layer is what a WAIT decision needs: without it the executor sleeps
a fixed slice and the model spends another decision to look again. Two
corrections to the source were needed to make it report the truth here:

- The Network domain has to be enabled or no event is ever delivered, and an
  idle check then reports idle having observed nothing.
- Events are filtered by the agent's session id. `browser_harness` ships an
  equivalent `wait_for_network_idle`, but it filters to the daemon's *active*
  tab; the agent owns a background target, so its traffic would be discarded
  and any foreground tab's traffic counted in its place.
- The daemon's event buffer is drained destructively, so the idle check reads it
  through `events.subscribe` rather than directly. A concurrent consumer -- the
  recorder's screencast thread -- would otherwise take the Network events this
  check needs, and lose the frames it needs to this check. The subscription is
  the session's own (`Browser.traffic`), open from before the first navigation:
  a wait that subscribed only for its own duration would still miss every
  request that started before the model chose WAIT, because the recorder's
  pumps discard what nothing is subscribed to.
- What that subscription keeps is the state, not the events. A queue of events
  can only be bounded or unbounded, and both lie here: bounded, a busy page
  evicts the start of a request that is still pending and the next wait reports
  idle over it; unbounded, one page grows it without limit.

`wait_for_document_load` is the document layer. `Page.navigate` returns only
once the navigation has committed, so the executor's own startup poll does not
need it; it is here for callers that navigate through page actions, where
`until="domcontentloaded"` and the uncommitted-frame check both apply.
"""

import threading
import time
from contextlib import contextmanager

from .browser import StalePage
from .events import pump, subscribe

DEFAULT_TIMEOUT = 10.0
DOCUMENT_TIMEOUT = 15.0
IDLE_MS = 500
POLL = 0.05

# A frame reports one of these while the requested document has not committed.
UNCOMMITTED = {"", ":", "about:blank"}
IN_FLIGHT_STARTED = "Network.requestWillBeSent"
IN_FLIGHT_SETTLED = ("Network.loadingFinished", "Network.loadingFailed")


def wait_for_document_load(browser, timeout=DOCUMENT_TIMEOUT, until="load"):
    """Wait until the frame has committed and the document reached `until`.

    `until` is "load" (readyState complete) or "domcontentloaded" (interactive).
    Returns True once reached, False on timeout. Never raises on a document that
    swaps mid-poll; that is the condition being waited out.

    The commit check only rejects a frame that never navigated (no URL, or still
    about:blank). Once a navigation commits, the frame reports its new URL
    immediately, so this is not a guard against a half-loaded document --
    readyState is.
    """
    ready = {"interactive", "complete"} if until == "domcontentloaded" else {"complete"}
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if _committed(browser) and browser.evaluate("document.readyState") in ready:
                return True
        except (StalePage, RuntimeError):
            pass  # The document is swapping. Keep polling until the deadline.
        time.sleep(POLL)
    return False


def _committed(browser):
    try:
        frame = browser.call("Page.getFrameTree").get("frameTree", {}).get("frame", {})
    except (RuntimeError, AttributeError):
        return True  # Page.getFrameTree is unavailable; readyState is all there is.
    return (frame.get("url") or "") not in UNCOMMITTED


class NetworkActivity:
    """The session's live network state, folded from events as they are fanned out.

    Holds what is in flight and when traffic was last seen, not the events that
    said so, so it costs what is actually in flight and no backlog can evict a
    request that has not settled.

    Written from whichever thread pumps -- the recorder's capture thread, during
    a recorded run -- and read by the agent's, so both go through its lock.
    """

    def __init__(self):
        self.in_flight = set()
        self.last_activity = time.monotonic()
        self._lock = threading.Lock()

    def __call__(self, event):
        method = event.get("method", "")
        request = (event.get("params") or {}).get("requestId")
        with self._lock:
            if method == IN_FLIGHT_STARTED:
                self.in_flight.add(request)
            elif method in IN_FLIGHT_SETTLED:
                self.in_flight.discard(request)
            self.last_activity = time.monotonic()

    def idle(self, idle_ms):
        """True when nothing is in flight and no event arrived for `idle_ms`."""
        with self._lock:
            quiet_ms = (time.monotonic() - self.last_activity) * 1000
            return not self.in_flight and quiet_ms >= idle_ms


def network_subscription(session):
    """Open a session-long Network subscription that keeps its state, not its events."""
    return subscribe(prefix="Network.", session=session, sink=NetworkActivity())


@contextmanager
def network_events(browser):
    """Yield this session's live network state, enabling the domain if it is off.

    Nothing delivers Network events until the domain is enabled, so an idle check
    without this reports idle having observed no traffic at all.

    A session that enabled the domain in its constructor also opened a
    subscription there, and that state is used: it has been accumulating since
    before the page loaded, so a request that started before the WAIT decision is
    still counted. Only a session without one (a bridge lacking the domain, a
    test double) pays for a subscription that lives no longer than the wait, and
    sees only what arrives during it.
    """
    owned = not getattr(browser, "network_enabled", False)
    if owned:
        try:
            browser.call("Network.enable")
            browser.network_enabled = True
        except RuntimeError:
            owned = False  # A bridge without the Network domain still gets its idle window.
    traffic = getattr(browser, "traffic", None)
    try:
        if traffic is not None:
            yield traffic.sink
        else:
            with network_subscription(browser.session) as traffic:
                yield traffic.sink
    finally:
        if owned:
            browser.network_enabled = False
            try:
                browser.call("Network.disable")
            except RuntimeError:
                pass  # Best-effort; the next wait enables the domain again.


def wait_for_network_idle(browser, timeout=DEFAULT_TIMEOUT, idle_ms=IDLE_MS):
    """Wait until nothing is in flight and no Network event arrived for `idle_ms`.

    Returns True on an idle window, False on timeout. The state is this session's
    only; another tab's traffic cannot hold this wait busy, and a concurrent
    consumer of the daemon's buffer cannot starve it.

    On a session subscription the state predates the call, so a request that
    started before the WAIT decision still counts as in flight, one that started
    and finished before it has already cancelled itself out, and a page that has
    been quiet for longer than `idle_ms` returns on the first poll.
    """
    deadline = time.monotonic() + timeout
    with network_events(browser) as activity:
        while time.monotonic() < deadline:
            pump()  # Fans out into the sink; the state is current when this returns.
            if activity.idle(idle_ms):
                return True
            time.sleep(POLL)
    return False


def wait_for_function(browser, expression, timeout=DEFAULT_TIMEOUT, polling=0.1):
    """Poll a page expression until it returns a truthy value.

    Returns that value, or None on timeout. The expression is code this package
    owns; the model never reaches it.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            value = browser.evaluate(f"({expression})")
        except (StalePage, RuntimeError):
            value = None
        if value:
            return value
        time.sleep(polling)
    return None


def wait_for_load_state(browser, load_state="load", timeout=None, idle_ms=IDLE_MS):
    """Dispatch to the document or network layer. Returns True when reached."""
    if load_state == "networkidle":
        return wait_for_network_idle(browser, timeout=timeout or DEFAULT_TIMEOUT, idle_ms=idle_ms)
    if load_state not in {"load", "domcontentloaded"}:
        raise ValueError(f"Unknown load state: {load_state}")
    return wait_for_document_load(browser, timeout=timeout or DOCUMENT_TIMEOUT, until=load_state)
