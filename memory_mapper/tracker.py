"""Tracks memory snapshots, sender stats, and recently changed byte addresses."""

import time
from collections import deque
from dataclasses import dataclass
from typing import Deque, Dict, Optional, Set

SCAN_MODES = {
    "c": "changed",
    "n": "unchanged",
    "i": "increased",
    "d": "decreased",
}


@dataclass
class ScanStats:
    """Summary of the iterative scan state for UI rendering."""

    steps: int = 0
    compared_bytes: int = 0
    hard_match_count: int = 0
    soft_match_count_1: int = 0
    soft_match_count_2: int = 0


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

        self.scan_baseline: Optional[bytes] = None
        self.scan_steps: list[str] = []
        self.scan_hits: Dict[int, int] = {}
        self.scan_total: Dict[int, int] = {}

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

    def apply_scan(self, mode_key: str) -> bool:
        """Apply an iterative scan comparison against the previous captured snapshot."""
        if self.snapshot is None or mode_key not in SCAN_MODES:
            return False

        if self.scan_baseline is None:
            self.scan_baseline = bytes(self.snapshot)
            return False

        current = self.snapshot
        baseline = self.scan_baseline
        compare_len = min(len(current), len(baseline))
        if compare_len == 0:
            self.scan_baseline = bytes(current)
            return False

        self.scan_steps.append(mode_key)

        for index in range(compare_len):
            old = baseline[index]
            new = current[index]
            matched = self._mode_matches(mode_key, old, new)

            self.scan_total[index] = self.scan_total.get(index, 0) + 1
            if matched:
                self.scan_hits[index] = self.scan_hits.get(index, 0) + 1

        self.scan_baseline = bytes(current)
        return True

    def _mode_matches(self, mode_key: str, old: int, new: int) -> bool:
        if mode_key == "c":
            return new != old
        if mode_key == "n":
            return new == old
        if mode_key == "i":
            return new > old
        if mode_key == "d":
            return new < old
        return False

    def reset_scan(self) -> None:
        self.scan_baseline = bytes(self.snapshot) if self.snapshot is not None else None
        self.scan_steps = []
        self.scan_hits = {}
        self.scan_total = {}

    def scan_stats(self) -> ScanStats:
        compared = len(self.scan_total)
        hard = 0
        soft1 = 0
        soft2 = 0
        for index, total in self.scan_total.items():
            hits = self.scan_hits.get(index, 0)
            misses = total - hits
            if misses == 0 and total > 0:
                hard += 1
            elif misses == 1:
                soft1 += 1
            elif misses == 2:
                soft2 += 1

        return ScanStats(
            steps=len(self.scan_steps),
            compared_bytes=compared,
            hard_match_count=hard,
            soft_match_count_1=soft1,
            soft_match_count_2=soft2,
        )

    def scan_match_level(self, index: int) -> int:
        """Return match level for a byte (0=none, 1=soft miss1, 2=soft miss2, 3=hard)."""
        total = self.scan_total.get(index, 0)
        if total == 0:
            return 0
        hits = self.scan_hits.get(index, 0)
        misses = total - hits
        if misses == 0:
            return 3
        if misses == 1:
            return 2
        if misses == 2:
            return 1
        return 0

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
