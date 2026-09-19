"""One drain of the daemon's CDP buffer, fanned out to every consumer.

`browser_harness.helpers.drain_events` is destructive: it empties the daemon's
shared buffer and hands the events to whoever asked. Two consumers calling it
concurrently -- a WAIT polling for network idle and the recorder persisting
screencast frames -- steal each other's events. The wait then reports idle with
requests still in flight, and the recorder drops frames it never acknowledged,
so the screencast stalls.

Every consumer subscribes here instead. A subscription owns a queue; a drain
empties the daemon's buffer once, under the module lock, and copies each event
into every queue that wants it. What no subscription wants is discarded, exactly
as a direct drain discarded it.

A subscription either queues its events or folds them into state as they
arrive. Queues are bounded: a consumer that stops draining (a capture thread
that died, say) would otherwise hold every screencast frame of the run in
memory; instead its oldest events fall off and `Subscription.dropped` counts
them, so the loss is reported rather than silent. A consumer that only needs
what the stream implies -- what is still in flight -- passes a `sink` and keeps
that state instead, which no bound can evict and no backlog can grow.

A subscription only receives what arrives while it is open, and any consumer's
pump moves the whole buffer. So a consumer that needs events from before its own
work started -- an idle check counting requests that began before the WAIT
decision, while the recorder's thread pumps for frames throughout -- subscribes
for the session, not for the wait. `Browser` does, with the sink that keeps its
network state; `wait_for_network_idle` reads that state.
"""

import threading
from collections import deque

from browser_harness.helpers import drain_events

MAX_QUEUED = 512

_lock = threading.Lock()
_subscriptions = []


class Subscription:
    """A consumer's own view of the event stream. Open one with `subscribe`."""

    def __init__(self, prefix="", session=None, maxlen=MAX_QUEUED, sink=None):
        self.prefix = prefix
        self.session = session
        self.sink = sink
        self.dropped = 0
        self._queue = deque(maxlen=maxlen)

    def close(self):
        """Stop receiving events. Idempotent, so a session can close an open one blindly."""
        with _lock:
            if self in _subscriptions:
                _subscriptions.remove(self)
            self._queue.clear()

    def __enter__(self):
        return self

    def __exit__(self, *_exception):
        self.close()

    def wants(self, event):
        if self.session is not None and event.get("session_id") != self.session:
            return False
        return event.get("method", "").startswith(self.prefix)

    def offer(self, event):
        if self.sink is not None:
            self.sink(event)  # Runs under the module lock: keep it short, never pump from it.
            return
        if len(self._queue) == self._queue.maxlen:
            self.dropped += 1  # The oldest event is about to fall off the queue.
        self._queue.append(event)

    def drain(self):
        """Pump, then hand over this subscription's events. Always empty with a sink."""
        with _lock:
            _fan_out()
            events = list(self._queue)
            self._queue.clear()
        return events


def pump():
    """Move the daemon's buffer into the subscriptions, returning nothing.

    What a sink consumer calls: its state is already folded in by the time this
    returns, so there is nothing to hand back.
    """
    with _lock:
        _fan_out()


def _fan_out():
    """Move the daemon's buffer into the subscriptions. The caller holds the lock."""
    for event in drain_events():
        for subscription in _subscriptions:
            if subscription.wants(event):
                subscription.offer(event)


def subscribe(prefix="", session=None, maxlen=MAX_QUEUED, sink=None):
    """Open a subscription to every event matching `prefix` (and `session`, when given).

    Usable as a context manager, or held open across a session and closed by hand.
    Events that arrived before it opened are not held for it, so subscribe before
    the traffic starts -- before `Page.startScreencast`, before the action whose
    requests a wait will count.

    With a `sink`, each matching event is handed to it as it is fanned out and
    nothing is queued: the consumer keeps the state the stream implies, which
    survives any length of backlog. Without one, events queue until drained.
    """
    subscription = Subscription(prefix=prefix, session=session, maxlen=maxlen, sink=sink)
    with _lock:
        _subscriptions.append(subscription)
    return subscription
