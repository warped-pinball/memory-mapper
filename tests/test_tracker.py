"""Tests for memory_mapper.tracker."""

import time

import pytest

from memory_mapper.tracker import MemoryTracker


class TestMemoryTrackerInitialState:
    def test_snapshot_is_none(self):
        t = MemoryTracker()
        assert t.snapshot is None

    def test_packet_count_is_zero(self):
        t = MemoryTracker()
        assert t.packet_count == 0

    def test_last_update_is_none(self):
        t = MemoryTracker()
        assert t.last_update is None

    def test_no_changes_tracked(self):
        t = MemoryTracker()
        assert not t.recently_changed(0)


class TestMemoryTrackerFirstUpdate:
    def test_snapshot_stored(self):
        t = MemoryTracker()
        t.update(b"\x00\x01\x02")
        assert t.snapshot == b"\x00\x01\x02"

    def test_packet_count_incremented(self):
        t = MemoryTracker()
        t.update(b"\xAB")
        assert t.packet_count == 1

    def test_last_update_set(self):
        t = MemoryTracker()
        t.update(b"\x00")
        assert t.last_update is not None

    def test_no_changes_on_first_packet(self):
        """First packet has nothing to compare against, so no changes are reported."""
        t = MemoryTracker()
        changed = t.update(b"\x00\x01\x02\x03")
        assert changed == set()


class TestMemoryTrackerChangeDetection:
    def test_detects_single_change(self):
        t = MemoryTracker()
        t.update(b"\x00\x00\x00")
        changed = t.update(b"\x00\xFF\x00")
        assert changed == {1}

    def test_detects_multiple_changes(self):
        t = MemoryTracker()
        t.update(b"\x00\x01\x02\x03")
        changed = t.update(b"\x00\xFF\x02\xFF")
        assert changed == {1, 3}

    def test_unchanged_bytes_not_in_set(self):
        t = MemoryTracker()
        t.update(b"\x10\x20\x30")
        changed = t.update(b"\x10\x21\x30")
        assert 0 not in changed
        assert 2 not in changed

    def test_identical_packet_has_no_changes(self):
        t = MemoryTracker()
        t.update(b"\xAA\xBB\xCC")
        changed = t.update(b"\xAA\xBB\xCC")
        assert changed == set()

    def test_extended_payload_marks_new_bytes_changed(self):
        t = MemoryTracker()
        t.update(b"\x00\x01")
        changed = t.update(b"\x00\x01\x02\x03")
        assert {2, 3}.issubset(changed)


class TestMemoryTrackerRecentlyChanged:
    def test_recently_changed_true_after_update(self):
        t = MemoryTracker(highlight_duration=5.0)
        t.update(b"\x00")
        t.update(b"\xFF")
        assert t.recently_changed(0)

    def test_recently_changed_false_for_unchanged_byte(self):
        t = MemoryTracker(highlight_duration=5.0)
        t.update(b"\x00\x00")
        t.update(b"\x00\xFF")
        assert not t.recently_changed(0)

    def test_recently_changed_false_after_expiry(self):
        t = MemoryTracker(highlight_duration=0.05)
        t.update(b"\x00")
        t.update(b"\xFF")
        time.sleep(0.1)
        assert not t.recently_changed(0)

    def test_recently_changed_false_for_unknown_index(self):
        t = MemoryTracker()
        assert not t.recently_changed(99)


class TestMemoryTrackerCleanup:
    def test_cleanup_removes_expired_entries(self):
        t = MemoryTracker(highlight_duration=0.05)
        t.update(b"\x00")
        t.update(b"\xFF")
        time.sleep(0.1)
        t.cleanup_old_changes()
        assert t.change_times == {}

    def test_cleanup_keeps_fresh_entries(self):
        t = MemoryTracker(highlight_duration=10.0)
        t.update(b"\x00")
        t.update(b"\xFF")
        t.cleanup_old_changes()
        assert 0 in t.change_times

    def test_packet_count_accumulates(self):
        t = MemoryTracker()
        for _ in range(5):
            t.update(b"\x00")
        assert t.packet_count == 5
