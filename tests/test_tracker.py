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


class TestMemoryTrackerChunkUpdates:
    def test_first_chunk_with_offset_extends_snapshot(self):
        t = MemoryTracker()

        changed = t.update_chunk(4, b"\xAA\xBB")

        assert changed == {4, 5}
        assert t.snapshot == b"\x00\x00\x00\x00\xAA\xBB"

    def test_chunk_update_marks_only_changed_indices(self):
        t = MemoryTracker()
        t.update(b"\x00\x01\x02\x03")

        changed = t.update_chunk(1, b"\x01\xFF")

        assert changed == {2}
        assert t.snapshot == b"\x00\x01\xFF\x03"

    def test_negative_offset_raises_value_error(self):
        t = MemoryTracker()

        with pytest.raises(ValueError):
            t.update_chunk(-1, b"\x00")


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


class TestMemoryTrackerStats:
    def test_packets_per_second_is_zero_with_insufficient_samples(self):
        t = MemoryTracker()
        assert t.packets_per_second() == 0.0
        t.update(b"\x00")
        assert t.packets_per_second() == 0.0

    def test_refreshes_per_second_is_zero_when_no_data(self):
        t = MemoryTracker()
        assert t.refreshes_per_second() == 0.0

    def test_refreshes_per_second_uses_snapshot_size_over_packet_size(self):
        t = MemoryTracker()
        t.packet_times.extend([1.0, 2.0, 3.0])
        t.packet_sizes.extend([8, 8, 8])
        t.snapshot = bytes(32)

        assert t.packets_per_second() == 1.0
        assert t.refreshes_per_second() == 0.25

    def test_data_age_none_when_no_data(self):
        t = MemoryTracker()
        assert t.data_age_seconds() is None

    def test_data_age_increases_after_update(self):
        t = MemoryTracker()
        t.update(b"\x00")
        age = t.data_age_seconds()
        assert age is not None
        assert age >= 0

    def test_known_senders_sorted_by_packet_count(self):
        t = MemoryTracker()
        t.update(b"\x00", sender="10.0.0.2")
        t.update(b"\x00", sender="10.0.0.1")
        t.update(b"\x00", sender="10.0.0.2")
        assert t.known_senders() == ["10.0.0.2", "10.0.0.1"]

    def test_record_sender_only_updates_source_stats(self):
        t = MemoryTracker()

        t.record_sender_only("10.0.0.3")

        assert t.known_senders() == ["10.0.0.3"]
        assert t.snapshot is None
        assert t.packet_count == 0


class TestMemoryTrackerSourceReset:
    def test_reset_for_new_source_clears_active_state_only(self):
        t = MemoryTracker()
        t.update(b"\x00", sender="10.0.0.1")
        t.update_chunk(4, b"\xFF", sender="10.0.0.1")
        t.apply_scan("c")
        t.update(b"\x01", sender="10.0.0.1")
        t.apply_scan("i")

        t.reset_for_new_source()

        assert t.snapshot is None
        assert t.packet_count == 0
        assert t.scan_stats().steps == 0
        assert t.known_senders() == ["10.0.0.1"]


class TestMemoryTrackerScanWorkflow:
    def test_first_scan_captures_baseline_only(self):
        t = MemoryTracker()
        t.update(b"\x01\x02")
        assert not t.apply_scan("c")
        assert t.scan_stats().steps == 0

    def test_scan_hard_and_soft_matches(self):
        t = MemoryTracker()
        t.update(b"\x05\x05\x05")
        t.apply_scan("c")

        t.update(b"\x06\x05\x04")
        assert t.apply_scan("i")

        t.update(b"\x07\x05\x03")
        assert t.apply_scan("i")

        stats = t.scan_stats()
        assert stats.steps == 2
        assert stats.hard_match_count == 1
        assert t.scan_match_level(0) == 3
        assert t.scan_match_level(2) == 1


    def test_any_scan_mode_refreshes_baseline_without_step(self):
        t = MemoryTracker()
        t.update(b"\x01")
        assert not t.apply_scan("c")

        t.update(b"\x02")
        assert not t.apply_scan("a")
        assert t.scan_stats().steps == 0

        t.update(b"\x03")
        assert t.apply_scan("i")
        assert t.scan_stats().steps == 1

    def test_reset_scan_clears_state(self):
        t = MemoryTracker()
        t.update(b"\x01")
        t.apply_scan("c")
        t.update(b"\x02")
        t.apply_scan("i")

        t.reset_scan()

        assert t.scan_stats().steps == 0
        assert t.scan_match_level(0) == 0


class TestMemoryTrackerValueHistory:
    def test_no_history_on_first_update(self):
        t = MemoryTracker()
        t.update(b"\x00\x01")
        assert t.get_value_history(0) == []

    def test_history_recorded_on_change(self):
        t = MemoryTracker()
        t.update(b"\x00\x01")
        t.update(b"\xFF\x01")
        history = t.get_value_history(0)
        assert len(history) == 1
        assert history[0][1] == 0xFF

    def test_unchanged_byte_has_no_history(self):
        t = MemoryTracker()
        t.update(b"\x00\x01")
        t.update(b"\xFF\x01")
        assert t.get_value_history(1) == []

    def test_history_capped_at_max(self):
        t = MemoryTracker()
        t.update(bytes([0]))
        for i in range(1, 15):
            t.update(bytes([i]))
        history = t.get_value_history(0)
        assert len(history) == t.MAX_VALUE_HISTORY

    def test_history_recorded_for_chunk_updates(self):
        t = MemoryTracker()
        t.update(b"\x00\x00\x00\x00")
        t.update_chunk(1, b"\xAA\xBB")
        history_1 = t.get_value_history(1)
        history_2 = t.get_value_history(2)
        assert len(history_1) == 1
        assert history_1[0][1] == 0xAA
        assert len(history_2) == 1
        assert history_2[0][1] == 0xBB

    def test_history_has_timestamps(self):
        t = MemoryTracker()
        t.update(b"\x00")
        t.update(b"\x01")
        history = t.get_value_history(0)
        assert len(history) == 1
        assert isinstance(history[0][0], float)
        assert history[0][0] > 0

    def test_history_cleared_on_source_reset(self):
        t = MemoryTracker()
        t.update(b"\x00", sender="10.0.0.1")
        t.update(b"\xFF", sender="10.0.0.1")
        assert len(t.get_value_history(0)) == 1
        t.reset_for_new_source()
        assert t.get_value_history(0) == []


class TestMemoryTrackerMarkedAddresses:
    def test_toggle_mark_marks_address(self):
        t = MemoryTracker()
        result = t.toggle_mark(5)
        assert result is True
        assert 5 in t.marked_addresses

    def test_toggle_mark_unmarks_address(self):
        t = MemoryTracker()
        t.toggle_mark(5)
        result = t.toggle_mark(5)
        assert result is False
        assert 5 not in t.marked_addresses

    def test_export_marked_empty(self):
        t = MemoryTracker()
        assert t.export_marked() == []

    def test_export_marked_with_snapshot(self):
        t = MemoryTracker()
        t.update(b"\xAA\xBB\xCC\xDD")
        t.toggle_mark(1)
        t.toggle_mark(3)
        result = t.export_marked()
        assert result == [(1, 0xBB), (3, 0xDD)]

    def test_export_marked_no_snapshot(self):
        t = MemoryTracker()
        t.toggle_mark(0)
        result = t.export_marked()
        assert result == [(0, None)]

    def test_marked_cleared_on_source_reset(self):
        t = MemoryTracker()
        t.toggle_mark(0)
        t.toggle_mark(3)
        t.reset_for_new_source()
        assert len(t.marked_addresses) == 0

    def test_export_all_empty_when_no_snapshot(self):
        t = MemoryTracker()
        assert t.export_all() == []

    def test_export_all_returns_all_addresses(self):
        t = MemoryTracker()
        t.update(b"\xAA\xBB\xCC")
        result = t.export_all()
        assert result == [(0, 0xAA), (1, 0xBB), (2, 0xCC)]
