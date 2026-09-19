"""Offline contracts for the layered waits. No browser, no paid APIs."""

import pytest

from jev_ultrafast import events as event_bus
from jev_ultrafast import waits
from jev_ultrafast.browser import StalePage


class FakeBrowser:
    """The members waits uses: call, evaluate, session, and an optional traffic subscription."""

    def __init__(self, frame_url="https://example.test/", ready="complete", network=True):
        self.session = "S1"
        self.traffic = None
        self.frame_url = frame_url
        self.ready = ready
        self.network = network
        self.network_enabled = False
        self.calls = []
        self.evaluations = []

    def call(self, method, **params):
        self.calls.append(method)
        if method == "Page.getFrameTree":
            if self.frame_url is None:
                raise RuntimeError("Page.getFrameTree is not available")
            return {"frameTree": {"frame": {"url": self.frame_url}}}
        if method in ("Network.enable", "Network.disable") and not self.network:
            raise RuntimeError("Network domain unavailable")
        return {}

    def evaluate(self, expression):
        self.evaluations.append(expression)
        if callable(self.ready):
            return self.ready(expression)
        return self.ready


def events(*batches):
    """A `drain_events` stub that yields each batch once, then stays empty."""
    queue = list(batches)
    return lambda: queue.pop(0) if queue else []


def network_event(method, request="r1", session="S1"):
    return {"method": method, "session_id": session, "params": {"requestId": request}}


def screencast_frame(session="S1"):
    return {"method": "Page.screencastFrame", "session_id": session, "params": {}}


def recording_session(browser):
    """A browser whose Network subscription is open, alongside a recorder's for frames."""
    browser.network_enabled = True
    browser.traffic = event_bus.subscribe(prefix="Network.", session=browser.session)
    return event_bus.subscribe(prefix="Page.screencastFrame", session=browser.session)


def test_uncommitted_frame_is_not_a_loaded_document():
    """A frame that never navigated is not loaded, whatever readyState says."""
    browser = FakeBrowser(frame_url="about:blank", ready="complete")
    assert waits.wait_for_document_load(browser, timeout=0.2) is False


@pytest.mark.parametrize("url", ["", ":", "about:blank"])
def test_every_uncommitted_url_form_blocks(url):
    assert waits.wait_for_document_load(FakeBrowser(frame_url=url), timeout=0.15) is False


def test_committed_and_complete_returns_true():
    browser = FakeBrowser(frame_url="https://example.test/", ready="complete")
    assert waits.wait_for_document_load(browser, timeout=1) is True


def test_commit_is_awaited_then_observed():
    """A frame that commits a few polls in is picked up, not missed."""
    browser = FakeBrowser(frame_url="about:blank", ready="complete")
    polls = {"n": 0}
    original = browser.call

    def call(method, **params):
        if method == "Page.getFrameTree":
            polls["n"] += 1
            if polls["n"] >= 3:
                browser.frame_url = "https://example.test/"
        return original(method, **params)

    browser.call = call
    assert waits.wait_for_document_load(browser, timeout=2) is True
    assert polls["n"] >= 3


def test_domcontentloaded_accepts_interactive():
    browser = FakeBrowser(ready="interactive")
    assert waits.wait_for_document_load(browser, timeout=1, until="domcontentloaded") is True
    assert waits.wait_for_document_load(browser, timeout=0.15, until="load") is False


def test_missing_frame_tree_falls_back_to_ready_state():
    browser = FakeBrowser(frame_url=None, ready="complete")
    assert waits.wait_for_document_load(browser, timeout=1) is True


def test_a_document_swapping_mid_poll_is_waited_out():
    state = {"n": 0}

    def ready(_expression):
        state["n"] += 1
        if state["n"] < 3:
            raise StalePage("Document changed during evaluation")
        return "complete"

    assert waits.wait_for_document_load(FakeBrowser(ready=ready), timeout=2) is True


def test_network_idle_enables_the_domain_and_restores_it(monkeypatch):
    """Nothing delivers Network events until the domain is on."""
    monkeypatch.setattr(event_bus, "drain_events", events())
    browser = FakeBrowser()
    assert waits.wait_for_network_idle(browser, timeout=1, idle_ms=10) is True
    assert "Network.enable" in browser.calls
    assert "Network.disable" in browser.calls
    assert browser.network_enabled is False


def test_network_idle_leaves_a_session_owned_domain_enabled(monkeypatch):
    monkeypatch.setattr(event_bus, "drain_events", events())
    browser = FakeBrowser()
    browser.network_enabled = True
    assert waits.wait_for_network_idle(browser, timeout=1, idle_ms=10) is True
    assert "Network.enable" not in browser.calls
    assert "Network.disable" not in browser.calls
    assert browser.network_enabled is True


def test_an_unfinished_request_is_not_idle(monkeypatch):
    monkeypatch.setattr(event_bus, "drain_events", events([network_event("Network.requestWillBeSent")]))
    assert waits.wait_for_network_idle(FakeBrowser(), timeout=0.4, idle_ms=10) is False


def test_a_finished_request_becomes_idle(monkeypatch):
    monkeypatch.setattr(
        event_bus,
        "drain_events",
        events(
            [network_event("Network.requestWillBeSent")],
            [],
            [network_event("Network.loadingFinished")],
        ),
    )
    assert waits.wait_for_network_idle(FakeBrowser(), timeout=2, idle_ms=10) is True


def test_a_failed_request_also_clears_in_flight(monkeypatch):
    monkeypatch.setattr(
        event_bus,
        "drain_events",
        events([network_event("Network.requestWillBeSent")], [network_event("Network.loadingFailed")]),
    )
    assert waits.wait_for_network_idle(FakeBrowser(), timeout=2, idle_ms=10) is True


def test_another_session_cannot_hold_this_one_busy(monkeypatch):
    """browser_harness filters to the daemon's active tab; the agent owns a background one."""
    monkeypatch.setattr(
        event_bus,
        "drain_events",
        events([network_event("Network.requestWillBeSent", session="OTHER")]),
    )
    assert waits.wait_for_network_idle(FakeBrowser(), timeout=1, idle_ms=10) is True


def test_a_bridge_without_the_network_domain_still_returns(monkeypatch):
    monkeypatch.setattr(event_bus, "drain_events", events())
    browser = FakeBrowser(network=False)
    assert waits.wait_for_network_idle(browser, timeout=1, idle_ms=10) is True
    assert browser.network_enabled is False


def test_wait_for_function_returns_the_first_truthy_value():
    values = iter([None, 0, "ready"])
    browser = FakeBrowser(ready=lambda _e: next(values))
    assert waits.wait_for_function(browser, "window.done", timeout=2, polling=0.01) == "ready"


def test_wait_for_function_times_out_as_none():
    browser = FakeBrowser(ready=lambda _e: False)
    assert waits.wait_for_function(browser, "window.done", timeout=0.15, polling=0.01) is None


def test_wait_for_function_wraps_the_expression():
    browser = FakeBrowser(ready=lambda _e: True)
    waits.wait_for_function(browser, "a || b", timeout=1)
    assert browser.evaluations == ["(a || b)"]


def test_load_state_dispatches(monkeypatch):
    monkeypatch.setattr(event_bus, "drain_events", events())
    browser = FakeBrowser()
    assert waits.wait_for_load_state(browser, "networkidle", timeout=1, idle_ms=10) is True
    assert "Network.enable" in browser.calls
    assert waits.wait_for_load_state(browser, "load", timeout=1) is True
    with pytest.raises(ValueError, match="Unknown load state"):
        waits.wait_for_load_state(browser, "commit")


def test_a_request_that_started_before_the_wait_is_still_counted(monkeypatch):
    """The recorder's drains run continuously; the traffic they pump must survive them."""
    monkeypatch.setattr(
        event_bus,
        "drain_events",
        events([network_event("Network.requestWillBeSent")], [screencast_frame()]),
    )
    browser = FakeBrowser()
    recorder = recording_session(browser)
    try:
        recorder.drain()  # The capture thread pumps the buffer before the model chooses WAIT.
        assert waits.wait_for_network_idle(browser, timeout=0.4, idle_ms=10) is False
    finally:
        recorder.close()
        browser.traffic.close()


def test_a_session_without_a_subscription_only_sees_the_wait_onward(monkeypatch):
    """A bridge with no Network domain has no session subscription; the wait is all it gets."""
    monkeypatch.setattr(
        event_bus,
        "drain_events",
        events([network_event("Network.requestWillBeSent")], [screencast_frame()]),
    )
    recorder = event_bus.subscribe(prefix="Page.screencastFrame", session="S1")
    try:
        recorder.drain()
        assert waits.wait_for_network_idle(FakeBrowser(), timeout=1, idle_ms=10) is True
    finally:
        recorder.close()


def test_the_session_subscription_outlives_the_wait(monkeypatch):
    """A wait borrows the session's subscription; closing it would lose the next wait's backlog."""
    monkeypatch.setattr(event_bus, "drain_events", events())
    browser = FakeBrowser()
    recorder = recording_session(browser)
    try:
        assert waits.wait_for_network_idle(browser, timeout=1, idle_ms=10) is True
        assert browser.traffic in event_bus._subscriptions
    finally:
        recorder.close()
        browser.traffic.close()


def test_a_borrowed_subscription_leaves_the_domain_alone(monkeypatch):
    """The session enabled Network in its constructor; a wait must not disable it underneath."""
    monkeypatch.setattr(event_bus, "drain_events", events())
    browser = FakeBrowser()
    recorder = recording_session(browser)
    try:
        waits.wait_for_network_idle(browser, timeout=1, idle_ms=10)
        assert "Network.disable" not in browser.calls
        assert browser.network_enabled is True
    finally:
        recorder.close()
        browser.traffic.close()
