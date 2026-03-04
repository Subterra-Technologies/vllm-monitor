"""Utilization history tracking and sparkline rendering."""

from __future__ import annotations

import collections
import time


class UtilHistory:
    """Track per-service busy% over 1m / 5m / 15m windows."""

    def __init__(self, refresh: int = 2, windows_sec: tuple[int, ...] = (60, 300, 900)):
        self.windows = windows_sec
        self._refresh = refresh
        self.samples: dict[int, collections.deque] = collections.defaultdict(
            lambda: collections.deque(
                maxlen=max(self.windows) // self._refresh + 10
            )
        )

    def record(self, port: int, is_busy: bool) -> None:
        self.samples[port].append((time.time(), is_busy))

    def avg(self, port: int, window_sec: int) -> float:
        now = time.time()
        cutoff = now - window_sec
        hits = total = 0
        for ts, busy in self.samples[port]:
            if ts >= cutoff:
                total += 1
                hits += int(busy)
        return (hits / total * 100) if total > 0 else 0.0


SPARK_CHARS = "▁▂▃▄▅▆▇█"


class SparklineBuffer:
    """Fixed-width ring buffer that renders unicode sparklines."""

    def __init__(self, width: int = 20):
        self.width = width
        self._buf: collections.deque[float] = collections.deque(maxlen=width)
        self.max_value: float = 0.0

    def push(self, value: float) -> None:
        self._buf.append(value)
        if value > self.max_value:
            self.max_value = value

    def render(self) -> str:
        if not self._buf:
            return ""
        max_val = max(self._buf)
        if max_val <= 0:
            return SPARK_CHARS[0] * len(self._buf)
        chars = []
        for v in self._buf:
            idx = int(v / max_val * (len(SPARK_CHARS) - 1))
            idx = min(idx, len(SPARK_CHARS) - 1)
            chars.append(SPARK_CHARS[idx])
        return "".join(chars)
