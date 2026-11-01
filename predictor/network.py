"""Bound upstream traffic across all HTTP sessions in this server process."""
from contextlib import contextmanager
import threading
import time


class RequestGate:
    def __init__(self, interval=.5, concurrent=2):
        self.interval = interval
        self.lock = threading.Lock()
        self.slots = threading.BoundedSemaphore(concurrent)
        self.next_at = 0.

    @contextmanager
    def request(self):
        if not self.slots.acquire(timeout=30):
            raise TimeoutError('Upstream request budget is busy; retry later')
        try:
            with self.lock:
                delay = max(0., self.next_at-time.monotonic())
                self.next_at = max(self.next_at, time.monotonic()) + self.interval
            if delay:
                time.sleep(delay)
            yield
        finally:
            self.slots.release()


UPSTREAM = RequestGate()
