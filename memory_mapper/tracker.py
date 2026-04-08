"""Tracks memory snapshots, sender stats, and recently changed byte addresses."""

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Dict, List, Optional, Set, Tuple

SCAN_MODES = {
    "c": "changed",
    "n": "unchanged",
    "i": "increased",
    "d": "decreased",
    "a": "any / not sure",
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

    MAX_VALUE_HISTORY = 10

    def __init__(self, highlight_duration: float = 3.0) -> None:
        self.highlight_duration = highlight_duration
        self.snapshot: Optional[bytes] = None
        self.change_times: Dict[int, float] = {}
        self.packet_count: int = 0
        self.last_update: Optional[float] = None
        self.packet_times: Deque[float] = deque(maxlen=256)
        self.packet_sizes: Deque[int] = deque(maxlen=256)
        self.sender_packet_counts: Dict[str, int] = {}
        self.latest_sender: Optional[str] = None

        self.scan_baseline: Optional[bytes] = None
        self.scan_steps: list[str] = []
        self.scan_hits: Dict[int, int] = {}
        self.scan_total: Dict[int, int] = {}

        self.marked_addresses: Set[int] = set()
        self.value_history: Dict[int, Deque[Tuple[float, int]]] = {}

    def _record_value_change(self, index: int, new_value: int, timestamp: float) -> None:
        """Record a value change in the history for the given address."""
        if index not in self.value_history:
            self.value_history[index] = deque(maxlen=self.MAX_VALUE_HISTORY)
        self.value_history[index].append((timestamp, new_value))

    def toggle_mark(self, address: int) -> bool:
        """Toggle the marked state of an address. Returns True if now marked."""
        if address in self.marked_addresses:
            self.marked_addresses.discard(address)
            return False
        self.marked_addresses.add(address)
        return True

    def get_value_history(self, index: int) -> List[Tuple[float, int]]:
        """Return the last N value-change entries for the given address."""
        if index not in self.value_history:
            return []
        return list(self.value_history[index])

    def export_marked(self) -> List[Tuple[int, Optional[int]]]:
        """Return sorted list of (address, current_value) for all marked addresses."""
        result: List[Tuple[int, Optional[int]]] = []
        for addr in sorted(self.marked_addresses):
            if self.snapshot is not None and addr < len(self.snapshot):
                result.append((addr, self.snapshot[addr]))
            else:
                result.append((addr, None))
        return result

    def export_all(self) -> List[Tuple[int, int]]:
        """Return list of (address, current_value) for every byte in the snapshot."""
        if self.snapshot is None:
            return []
        return [(addr, self.snapshot[addr]) for addr in range(len(self.snapshot))]

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
                    self._record_value_change(i, data[i], now)
            for i in range(min_len, len(data)):
                changed.add(i)
                self.change_times[i] = now
                self._record_value_change(i, data[i], now)

        self.snapshot = bytes(data)
        self.packet_count += 1
        self.last_update = now
        self.packet_times.append(now)
        self.packet_sizes.append(len(data))
        if sender:
            self._record_sender(sender)
        return changed

    def update_chunk(
        self,
        offset: int,
        chunk: bytes,
        sender: Optional[str] = None,
    ) -> Set[int]:
        """Apply a chunk update at *offset* and return changed byte indices."""
        if offset < 0:
            raise ValueError("offset must be >= 0")

        now = time.monotonic()
        changed: Set[int] = set()

        if self.snapshot is None:
            snapshot_data = bytearray(offset + len(chunk))
        else:
            snapshot_data = bytearray(self.snapshot)
            needed = offset + len(chunk)
            if needed > len(snapshot_data):
                snapshot_data.extend(b"\x00" * (needed - len(snapshot_data)))

        for index, value in enumerate(chunk):
            absolute_index = offset + index
            if snapshot_data[absolute_index] != value:
                changed.add(absolute_index)
                self.change_times[absolute_index] = now
                self._record_value_change(absolute_index, value, now)
            snapshot_data[absolute_index] = value

        self.snapshot = bytes(snapshot_data)
        self.packet_count += 1
        self.last_update = now
        self.packet_times.append(now)
        self.packet_sizes.append(len(chunk))
        if sender:
            self._record_sender(sender)
        return changed

    def record_sender_only(self, sender: str) -> None:
        """Record sender activity without mutating the active snapshot."""
        self._record_sender(sender)

    def _record_sender(self, sender: str) -> None:
        self.latest_sender = sender
        self.sender_packet_counts[sender] = self.sender_packet_counts.get(sender, 0) + 1

    def apply_scan(self, mode_key: str) -> bool:
        """Apply an iterative scan comparison against the previous captured snapshot."""
        if self.snapshot is None or mode_key not in SCAN_MODES:
            return False

        if mode_key == "a":
            self.scan_baseline = bytes(self.snapshot)
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

    def refreshes_per_second(self) -> float:
        """Estimate memory-map refresh rate in Hz from packet rate and packet size."""
        if self.snapshot is None or len(self.snapshot) == 0 or not self.packet_sizes:
            return 0.0

        average_packet_size = sum(self.packet_sizes) / len(self.packet_sizes)
        if average_packet_size <= 0:
            return 0.0

        packets_per_full_map = len(self.snapshot) / average_packet_size
        if packets_per_full_map <= 0:
            return 0.0

        return self.packets_per_second() / packets_per_full_map

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

    def reset_for_new_source(self) -> None:
        """Reset active memory/scan state when switching to a different source."""
        self.snapshot = None
        self.change_times = {}
        self.packet_count = 0
        self.last_update = None
        self.packet_times.clear()
        self.packet_sizes.clear()
        self.scan_baseline = None
        self.scan_steps = []
        self.scan_hits = {}
        self.scan_total = {}
        self.marked_addresses = set()
        self.value_history = {}
