"""Offline contracts for the CDP event fan-out. No browser, no paid APIs."""

import threading

from jev_ultrafast import events as event_bus


def event(method, session="S1", request="r1"):
    return {"method": method, "session_id": session, "params": {"requestId": request}}


def batches(*queued):
    """A `drain_events` stub that yields each batch once, then stays empty."""
    remaining = list(queued)
    return lambda: remaining.pop(0) if remaining else []


def test_no_subscription_leaks_after_the_block(monkeypatch):
    monkeypatch.setattr(event_bus, "drain_events", batches())
    with event_bus.subscribe():
        assert len(event_bus._subscriptions) == 1
    assert event_bus._subscriptions == []


def test_a_subscription_is_removed_even_when_the_block_raises(monkeypatch):
    monkeypatch.setattr(event_bus, "drain_events", batches())
    try:
        with event_bus.subscribe():
            raise RuntimeError("the consumer died")
    except RuntimeError:
        pass
    assert event_bus._subscriptions == []


def test_one_drain_reaches_every_subscription(monkeypatch):
    """The regression: a destructive drain let one consumer steal the other's events."""
    frame = event("Page.screencastFrame")
    request = event("Network.requestWillBeSent")
    monkeypatch.setattr(event_bus, "drain_events", batches([frame, request]))
    with event_bus.subscribe(prefix="Network.") as traffic, event_bus.subscribe(prefix="Page.") as screencast:
        # The wait drains first; the recorder must still see its frame.
        assert traffic.drain() == [request]
        assert screencast.drain() == [frame]


def test_a_second_drain_does_not_replay_the_first(monkeypatch):
    monkeypatch.setattr(event_bus, "drain_events", batches([event("Network.requestWillBeSent")]))
    with event_bus.subscribe() as subscription:
        assert len(subscription.drain()) == 1
        assert subscription.drain() == []


def test_events_are_held_until_their_subscription_drains(monkeypatch):
    """A consumer polling at its own pace loses nothing to a faster one."""
    monkeypatch.setattr(
        event_bus,
        "drain_events",
        batches([event("Page.screencastFrame", request="a")], [event("Page.screencastFrame", request="b")]),
    )
    with event_bus.subscribe(prefix="Page.") as slow, event_bus.subscribe(prefix="Page.") as fast:
        fast.drain()
        fast.drain()
        assert [e["params"]["requestId"] for e in slow.drain()] == ["a", "b"]


def test_a_prefix_keeps_unwanted_events_out(monkeypatch):
    monkeypatch.setattr(event_bus, "drain_events", batches([event("Page.screencastFrame")]))
    with event_bus.subscribe(prefix="Network.") as traffic:
        assert traffic.drain() == []


def test_a_session_filter_keeps_another_tab_out(monkeypatch):
    mine, theirs = event("Network.requestWillBeSent"), event("Network.requestWillBeSent", session="OTHER")
    monkeypatch.setattr(event_bus, "drain_events", batches([mine, theirs]))
    with event_bus.subscribe(session="S1") as traffic:
        assert traffic.drain() == [mine]


def test_no_session_filter_takes_every_tab(monkeypatch):
    monkeypatch.setattr(
        event_bus,
        "drain_events",
        batches([event("Network.requestWillBeSent"), event("Network.requestWillBeSent", session="OTHER")]),
    )
    with event_bus.subscribe() as traffic:
        assert len(traffic.drain()) == 2


def test_an_unclaimed_event_is_discarded(monkeypatch):
    """What no subscription wants is dropped, exactly as a direct drain dropped it."""
    monkeypatch.setattr(event_bus, "drain_events", batches([event("Runtime.consoleAPICalled")]))
    with event_bus.subscribe(prefix="Network.") as traffic:
        assert traffic.drain() == []
        assert traffic.dropped == 0


def test_a_stalled_consumer_is_bounded_and_counted(monkeypatch):
    """A capture thread that died must not hold every frame of the run in memory."""
    monkeypatch.setattr(
        event_bus,
        "drain_events",
        batches(*[[event("Page.screencastFrame", request=str(n))] for n in range(5)]),
    )
    with event_bus.subscribe(prefix="Page.", maxlen=2) as stalled, event_bus.subscribe() as pump:
        for _ in range(5):
            pump.drain()
        assert [e["params"]["requestId"] for e in stalled.drain()] == ["3", "4"]
        assert stalled.dropped == 3


def test_concurrent_drains_lose_nothing(monkeypatch):
    """Two threads draining at once share one buffer; every event still lands twice."""
    delivered = [[event("Network.requestWillBeSent", request=str(n))] for n in range(200)]
    remaining = list(delivered)
    lock = threading.Lock()

    def drain_events():
        with lock:
            return remaining.pop(0) if remaining else []

    monkeypatch.setattr(event_bus, "drain_events", drain_events)
    with event_bus.subscribe() as first, event_bus.subscribe() as second:
        collected = {}

        def consume(name, subscription):
            seen = []
            while remaining or subscription._queue:
                seen.extend(subscription.drain())
            seen.extend(subscription.drain())
            collected[name] = seen

        threads = [
            threading.Thread(target=consume, args=("first", first)),
            threading.Thread(target=consume, args=("second", second)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

    for seen in collected.values():
        assert [e["params"]["requestId"] for e in seen] == [str(n) for n in range(200)]


def test_a_subscription_held_open_keeps_collecting(monkeypatch):
    """A session's subscription is not scoped to a block; it spans every wait in the run."""
    monkeypatch.setattr(
        event_bus,
        "drain_events",
        batches([event("Network.requestWillBeSent", request="a")], [event("Network.loadingFinished", request="a")]),
    )
    traffic = event_bus.subscribe(prefix="Network.")
    try:
        assert len(traffic.drain()) == 1
        assert len(traffic.drain()) == 1
    finally:
        traffic.close()
    assert event_bus._subscriptions == []


def test_a_closed_subscription_receives_nothing_more(monkeypatch):
    monkeypatch.setattr(event_bus, "drain_events", batches([], [event("Network.requestWillBeSent")]))
    traffic = event_bus.subscribe()
    pump = event_bus.subscribe()
    try:
        traffic.drain()
        traffic.close()
        pump.drain()
        assert traffic.drain() == []
    finally:
        pump.close()


def test_closing_twice_is_a_no_op(monkeypatch):
    monkeypatch.setattr(event_bus, "drain_events", batches())
    traffic = event_bus.subscribe()
    traffic.close()
    traffic.close()
    assert event_bus._subscriptions == []


def test_a_sink_receives_events_and_queues_nothing(monkeypatch):
    """A consumer that keeps state instead of events cannot lose it to the bound."""
    seen = []
    monkeypatch.setattr(event_bus, "drain_events", batches([event("Network.requestWillBeSent")]))
    with event_bus.subscribe(prefix="Network.", sink=seen.append) as traffic:
        assert traffic.drain() == []
        assert [e["method"] for e in seen] == ["Network.requestWillBeSent"]
        assert traffic.dropped == 0


def test_a_sink_is_fed_by_another_consumer_s_pump(monkeypatch):
    """The recorder's pump is what keeps the network state current between waits."""
    seen = []
    monkeypatch.setattr(
        event_bus,
        "drain_events",
        batches([event("Network.requestWillBeSent"), event("Page.screencastFrame")]),
    )
    with event_bus.subscribe(prefix="Network.", sink=seen.append), event_bus.subscribe(prefix="Page.") as recorder:
        assert len(recorder.drain()) == 1
        assert len(seen) == 1


def test_pump_needs_no_consumer_of_its_own(monkeypatch):
    monkeypatch.setattr(event_bus, "drain_events", batches([event("Network.requestWillBeSent")]))
    seen = []
    with event_bus.subscribe(prefix="Network.", sink=seen.append):
        assert event_bus.pump() is None
        assert len(seen) == 1
