"""Tracks memory snapshots and detects recently changed byte addresses."""

import time
from typing import Dict, Optional, Set


class MemoryTracker:
    """Tracks memory snapshots over time and records which bytes have changed."""

    def __init__(self, highlight_duration: float = 3.0) -> None:
        self.highlight_duration = highlight_duration
        self.snapshot: Optional[bytes] = None
        self.change_times: Dict[int, float] = {}
        self.packet_count: int = 0
        self.last_update: Optional[float] = None

    def update(self, data: bytes) -> Set[int]:
        """Update the stored snapshot and return the set of changed byte indices."""
        now = time.monotonic()
        changed: Set[int] = set()

        if self.snapshot is not None:
            min_len = min(len(self.snapshot), len(data))
            for i in range(min_len):
                if self.snapshot[i] != data[i]:
                    changed.add(i)
                    self.change_times[i] = now
            # Bytes beyond the previous length are treated as new / changed
            for i in range(min_len, len(data)):
                changed.add(i)
                self.change_times[i] = now

        self.snapshot = bytes(data)
        self.packet_count += 1
        self.last_update = now
        return changed

    def recently_changed(self, index: int) -> bool:
        """Return True if the byte at *index* changed within the highlight window."""
        ts = self.change_times.get(index)
        if ts is None:
            return False
        return (time.monotonic() - ts) < self.highlight_duration

    def cleanup_old_changes(self) -> None:
        """Remove expired change records to prevent unbounded memory growth."""
        now = time.monotonic()
        self.change_times = {
            k: v
            for k, v in self.change_times.items()
            if (now - v) < self.highlight_duration
        }
