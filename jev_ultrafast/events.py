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

Queues are bounded. A consumer that stops draining (a capture thread that died,
say) would otherwise hold every screencast frame of the run in memory; instead
its oldest events fall off and `Subscription.dropped` counts them, so the loss
is reported rather than silent.
"""

import threading
from collections import deque
from contextlib import contextmanager

from browser_harness.helpers import drain_events

MAX_QUEUED = 512

_lock = threading.Lock()
_subscriptions = []


class Subscription:
    """A consumer's own view of the event stream. Created by `subscribe`."""

    def __init__(self, prefix="", session=None, maxlen=MAX_QUEUED):
        self.prefix = prefix
        self.session = session
        self.dropped = 0
        self._queue = deque(maxlen=maxlen)

    def wants(self, event):
        if self.session is not None and event.get("session_id") != self.session:
            return False
        return event.get("method", "").startswith(self.prefix)

    def offer(self, event):
        if len(self._queue) == self._queue.maxlen:
            self.dropped += 1  # The oldest event is about to fall off the queue.
        self._queue.append(event)

    def drain(self):
        """Pump the daemon's buffer into every subscription, return this one's events."""
        with _lock:
            _fan_out()
            events = list(self._queue)
            self._queue.clear()
        return events


def _fan_out():
    """Move the daemon's buffer into the subscriptions. The caller holds the lock."""
    for event in drain_events():
        for subscription in _subscriptions:
            if subscription.wants(event):
                subscription.offer(event)


@contextmanager
def subscribe(prefix="", session=None, maxlen=MAX_QUEUED):
    """Receive every event matching `prefix` (and `session`, when given) for the block.

    Events that arrive before the subscription exists are not held for it, so
    subscribe before the traffic starts -- before `Page.startScreencast`, before
    the action whose requests a wait will count.
    """
    subscription = Subscription(prefix=prefix, session=session, maxlen=maxlen)
    with _lock:
        _subscriptions.append(subscription)
    try:
        yield subscription
    finally:
        with _lock:
            _subscriptions.remove(subscription)
