"""Time-ordered UUIDv7 generation (design DS-03: UUID v7 primary keys)."""

from __future__ import annotations

import os
import threading
import time
import uuid

_lock = threading.Lock()
_last_ms = 0
_counter = 0


def uuid7() -> uuid.UUID:
    """RFC 9562 UUIDv7 with a 12-bit monotonic counter (method 1), so ids created in the same
    millisecond by this process still sort in creation order (e.g. job claim order)."""
    global _last_ms, _counter
    with _lock:
        ts_ms = time.time_ns() // 1_000_000
        if ts_ms <= _last_ms:
            _counter += 1
            if _counter > 0x0FFF:  # counter exhausted: borrow the next millisecond
                _last_ms += 1
                _counter = 0
            ts_ms = _last_ms
        else:
            _last_ms = ts_ms
            _counter = int.from_bytes(os.urandom(2), "big") & 0x01FF  # random start, leaves headroom
        rand_a = _counter
    rand_b = int.from_bytes(os.urandom(8), "big") & ((1 << 62) - 1)
    value = (ts_ms & ((1 << 48) - 1)) << 80
    value |= 0x7 << 76
    value |= rand_a << 64
    value |= 0b10 << 62
    value |= rand_b
    return uuid.UUID(int=value)
