from collections import OrderedDict, deque
import threading
import time


class MatchupLimiter:
    """Bound CPU work before model inference; never trust client IP headers."""
    def __init__(self, per_ip=20, global_limit=120, seconds=60):
        self.per_ip, self.global_limit, self.seconds = per_ip, global_limit, seconds
        self.clients = OrderedDict()
        self.global_times = deque()
        self.lock = threading.Lock()
        self.concurrent = threading.BoundedSemaphore(2)

    def allow(self, address, at=None):
        at = time.monotonic() if at is None else at
        with self.lock:
            times = self.clients.setdefault(address, deque())
            self.clients.move_to_end(address)
            while len(self.clients) > 4096:
                self.clients.popitem(last=False)
            for queue in (times,self.global_times):
                while queue and queue[0] <= at-self.seconds:
                    queue.popleft()
            if len(times) >= self.per_ip or len(self.global_times) >= self.global_limit:
                return False
            times.append(at); self.global_times.append(at)
            return True
