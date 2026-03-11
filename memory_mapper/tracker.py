"""Tracks memory snapshots, sender stats, and recently changed byte addresses."""

import time
from collections import deque
from typing import Deque, Dict, Optional, Set


class MemoryTracker:
    """Tracks memory snapshots over time and records which bytes have changed."""

    def __init__(self, highlight_duration: float = 3.0) -> None:
        self.highlight_duration = highlight_duration
        self.snapshot: Optional[bytes] = None
        self.change_times: Dict[int, float] = {}
        self.packet_count: int = 0
        self.last_update: Optional[float] = None
        self.packet_times: Deque[float] = deque(maxlen=256)
        self.sender_packet_counts: Dict[str, int] = {}
        self.latest_sender: Optional[str] = None

    def update(self, data: bytes, sender: Optional[str] = None) -> Set[int]:
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
        self.packet_times.append(now)
        if sender:
            self.latest_sender = sender
            self.sender_packet_counts[sender] = self.sender_packet_counts.get(sender, 0) + 1
        return changed

    def packets_per_second(self) -> float:
        """Return an average packet frequency over the recent packet window."""
        if len(self.packet_times) < 2:
            return 0.0
        elapsed = self.packet_times[-1] - self.packet_times[0]
        if elapsed <= 0:
            return 0.0
        return (len(self.packet_times) - 1) / elapsed

    def data_age_seconds(self) -> Optional[float]:
        """Return seconds since the latest packet, or None when no data exists."""
        if self.last_update is None:
            return None
        return max(0.0, time.monotonic() - self.last_update)

    def known_senders(self) -> list[str]:
        """Return known sender IPs sorted by descending packet count then IP."""
        return sorted(
            self.sender_packet_counts,
            key=lambda ip: (-self.sender_packet_counts[ip], ip),
        )

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
