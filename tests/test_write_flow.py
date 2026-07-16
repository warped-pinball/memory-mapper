"""Tests for the memory write flow in the display (W key + confirmation)."""

import pytest

from memory_mapper.display import MemoryDisplay, WRITE_WARNING, parse_write_values
from memory_mapper.tracker import MemoryTracker


class TestParseWriteValues:
    def test_single_decimal(self):
        assert parse_write_values("5") == [5]

    def test_single_hex(self):
        assert parse_write_values("0x2F") == [0x2F]

    def test_multiple_mixed(self):
        assert parse_write_values("5 0x10 255") == [5, 16, 255]

    def test_empty_raises(self):
        with pytest.raises(ValueError):
            parse_write_values("   ")

    def test_garbage_raises(self):
        with pytest.raises(ValueError):
            parse_write_values("zz")

    def test_bare_hex_digits_raise(self):
        # "FF" without 0x is ambiguous, so it is rejected rather than guessed.
        with pytest.raises(ValueError):
            parse_write_values("FF")

    def test_out_of_range_raises(self):
        with pytest.raises(ValueError):
            parse_write_values("256")
        with pytest.raises(ValueError):
            parse_write_values("-1")


class RecordingWriter:
    def __init__(self, error=None):
        self.calls = []
        self.error = error

    def __call__(self, offset, values):
        if self.error is not None:
            raise self.error
        self.calls.append((offset, list(values)))


class StubManager:
    """Minimal stand-in for vector.VectorManager in display tests."""

    def __init__(self, machines=None, password=None):
        self._machines = dict(machines or {})
        self._password = password
        self.set_password_calls = []
        self.enable_requests = []
        self.notices = []

    def machines(self):
        return dict(self._machines)

    def machine_name(self, ip):
        return self._machines.get(ip)

    def label(self, ip):
        name = self._machines.get(ip)
        return f"{name} ({ip})" if name else ip

    def pick_target(self):
        return next(iter(self._machines), None)

    def has_password(self):
        return bool(self._password)

    def set_password(self, password):
        self._password = password
        self.set_password_calls.append(password)

    def request_enable(self, ip, force=False):
        self.enable_requests.append(ip)

    def pop_notices(self):
        notices, self.notices = self.notices, []
        return notices


def make_display(writer=None, snapshot=b"\x00\x01\x02\x03", manager=None):
    tracker = MemoryTracker()
    if snapshot is not None:
        tracker.update(snapshot, sender="10.0.0.5")
    return MemoryDisplay(
        tracker,
        writer=writer,
        machine_label="elvira (10.0.0.5)",
        manager=manager,
    )


def send_keys(display, keys):
    for key in keys:
        display._handle_input(key, stop_event=None)


class TestWriteFlow:
    def test_w_without_writer_reports_unavailable(self):
        display = make_display(writer=None)
        send_keys(display, "w")
        assert display.write_stage is None
        assert "unavailable" in display.status_message.lower()

    def test_w_without_data_reports_no_data(self):
        writer = RecordingWriter()
        display = make_display(writer=writer, snapshot=None)
        send_keys(display, "w")
        assert display.write_stage is None

    def test_full_write_flow(self):
        writer = RecordingWriter()
        display = make_display(writer=writer)
        display.cursor_pos = 2
        send_keys(display, ["w", "2", "5", "5", "\r", "y"])
        assert writer.calls == [(2, [255])]
        assert display.write_stage is None
        assert "Wrote 1 byte at 0x0002" in display.status_message

    def test_multi_byte_write(self):
        writer = RecordingWriter()
        display = make_display(writer=writer)
        send_keys(display, ["w"] + list("5 0x10") + ["\r", "y"])
        assert writer.calls == [(0, [5, 16])]

    def test_confirm_stage_shows_warning(self):
        writer = RecordingWriter()
        display = make_display(writer=writer)
        send_keys(display, ["w", "7", "\r"])
        assert display.write_stage == "confirm"
        panel = display._render_write_panel()
        text = panel.renderable.plain
        assert WRITE_WARNING in text
        assert "0x0000" in text
        assert "elvira (10.0.0.5)" in text
        assert writer.calls == []  # nothing written until confirmed

    def test_any_other_key_cancels_confirm(self):
        writer = RecordingWriter()
        display = make_display(writer=writer)
        send_keys(display, ["w", "7", "\r", "n"])
        assert writer.calls == []
        assert display.write_stage is None
        assert "cancelled" in display.status_message.lower()

    def test_escape_cancels_input(self):
        writer = RecordingWriter()
        display = make_display(writer=writer)
        send_keys(display, ["w", "7", "\x1b"])
        assert display.write_stage is None
        assert writer.calls == []

    def test_backspace_edits_buffer(self):
        writer = RecordingWriter()
        display = make_display(writer=writer)
        send_keys(display, ["w", "1", "2", "\x7f"])
        assert display.write_buffer == "1"

    def test_invalid_input_stays_in_value_stage(self):
        writer = RecordingWriter()
        display = make_display(writer=writer)
        send_keys(display, ["w", "z", "\r"])
        assert display.write_stage == "value"
        assert "Invalid input" in display.status_message

    def test_writer_error_reported_in_status(self):
        writer = RecordingWriter(error=RuntimeError("device said no"))
        display = make_display(writer=writer)
        send_keys(display, ["w", "7", "\r", "y"])
        assert display.write_stage is None
        assert "Write failed: device said no" in display.status_message

    def test_normal_keys_ignored_while_writing(self):
        writer = RecordingWriter()
        display = make_display(writer=writer)
        send_keys(display, ["w", "q"])
        # "q" is buffered input during a write, not a quit command...
        assert display.write_stage == "value"
        assert display.write_buffer == "q"


class TestPasswordFlow:
    def test_background_tick_prompts_when_password_needed(self):
        manager = StubManager(machines={"10.0.0.5": "elvira"})
        display = make_display(manager=manager, snapshot=None)
        display._no_data_since = 0.0  # pretend the grace period has passed
        assert display._background_tick() is True
        assert display.password_stage == "input"
        # A second tick doesn't re-open or re-prompt.
        display.password_stage = None
        display._last_tick = 0.0
        display._background_tick()
        assert display.password_stage is None

    def test_background_tick_requests_enable_with_password(self):
        manager = StubManager(machines={"10.0.0.5": "elvira"}, password="pw")
        display = make_display(manager=manager, snapshot=None)
        display._background_tick()
        assert manager.enable_requests == ["10.0.0.5"]
        assert display.password_stage is None

    def test_background_tick_idle_once_data_flows(self):
        manager = StubManager(machines={"10.0.0.5": "elvira"})
        display = make_display(manager=manager)  # snapshot -> packet received
        display._background_tick()
        assert display.password_stage is None
        assert manager.enable_requests == []

    def test_notices_surface_in_status(self):
        manager = StubManager(machines={"10.0.0.5": "elvira"}, password="pw")
        manager.notices.append("Discovered elvira (10.0.0.5)")
        display = make_display(manager=manager)
        assert display._background_tick() is True
        assert display.status_message == "Discovered elvira (10.0.0.5)"

    def test_password_entry_sets_password_and_enables(self):
        manager = StubManager(machines={"10.0.0.5": "elvira"})
        display = make_display(manager=manager, snapshot=None)
        display._no_data_since = 0.0
        display._background_tick()
        send_keys(display, list("hunter2") + ["\r"])
        assert manager.set_password_calls == ["hunter2"]
        assert manager.enable_requests == ["10.0.0.5"]
        assert display.password_stage is None

    def test_password_escape_skips(self):
        manager = StubManager(machines={"10.0.0.5": "elvira"})
        display = make_display(manager=manager, snapshot=None)
        display._no_data_since = 0.0
        display._background_tick()
        send_keys(display, ["\x1b"])
        assert display.password_stage is None
        assert manager.set_password_calls == []
        assert "press P" in display.status_message

    def test_p_key_reopens_prompt(self):
        manager = StubManager(machines={"10.0.0.5": "elvira"})
        display = make_display(manager=manager)
        send_keys(display, ["p"])
        assert display.password_stage == "input"

    def test_source_list_merges_senders_and_discovered(self):
        manager = StubManager(
            machines={"10.0.0.5": "elvira", "10.0.0.6": "taxi"}
        )
        display = make_display(manager=manager)  # sender 10.0.0.5 has data
        assert display._source_list() == ["10.0.0.5", "10.0.0.6"]

    def test_selecting_silent_machine_requests_broadcast(self):
        manager = StubManager(
            machines={"10.0.0.5": "elvira", "10.0.0.6": "taxi"},
            password="pw",
        )
        display = make_display(manager=manager)
        send_keys(display, ["2"])
        assert display.selected_source == "10.0.0.6"
        assert manager.enable_requests == ["10.0.0.6"]
        assert "Requesting memory broadcast from taxi (10.0.0.6)" in display.status_message

    def test_selecting_silent_machine_without_password_prompts(self):
        manager = StubManager(
            machines={"10.0.0.5": "elvira", "10.0.0.6": "taxi"}
        )
        display = make_display(manager=manager)
        send_keys(display, ["2"])
        assert display.selected_source == "10.0.0.6"
        assert display.password_stage == "input"
        assert manager.enable_requests == []
        # Entering the password requests the broadcast on the selected machine.
        send_keys(display, list("pw") + ["\r"])
        assert manager.enable_requests == ["10.0.0.6"]

    def test_selecting_active_sender_does_not_toggle(self):
        manager = StubManager(
            machines={"10.0.0.5": "elvira", "10.0.0.6": "taxi"},
            password="pw",
        )
        display = make_display(manager=manager)
        send_keys(display, ["1"])  # 10.0.0.5 has already sent packets
        assert display.selected_source == "10.0.0.5"
        assert manager.enable_requests == []

    def test_reselecting_current_machine_rerequests_stream(self):
        manager = StubManager(
            machines={"10.0.0.5": "elvira"},
            password="pw",
        )
        display = make_display(manager=manager)
        send_keys(display, ["1"])  # active sender: no request on first select
        assert manager.enable_requests == []
        send_keys(display, ["1"])  # re-select: restart the stream
        assert manager.enable_requests == ["10.0.0.5"]

    def test_selecting_out_of_range_source(self):
        manager = StubManager(machines={"10.0.0.5": "elvira"})
        display = make_display(manager=manager)
        send_keys(display, ["9"])
        assert "unavailable" in display.status_message

    def test_write_prompts_for_password_then_continues(self):
        manager = StubManager(machines={"10.0.0.5": "elvira"})
        writer = RecordingWriter()
        display = make_display(writer=writer, manager=manager)
        send_keys(display, ["w"])
        assert display.password_stage == "input"
        assert display.write_stage is None
        send_keys(display, list("pw") + ["\r"])
        assert manager.set_password_calls == ["pw"]
        assert display.write_stage == "value"
        send_keys(display, ["7", "\r", "y"])
        assert writer.calls == [(0, [7])]

    def test_background_tick_waits_for_grace_period(self):
        manager = StubManager(machines={"10.0.0.5": "elvira"})
        display = make_display(manager=manager, snapshot=None)
        # Fresh display: still within the grace window, so no prompt yet.
        assert display._background_tick() is False
        assert display.password_stage is None

    def test_password_panel_masks_input(self):
        manager = StubManager(machines={"10.0.0.5": "elvira"})
        display = make_display(manager=manager)
        send_keys(display, ["p", "a", "b", "c"])
        panel = display._render_password_panel()
        text = panel.renderable.plain
        assert "abc" not in text
        assert "•••" in text
        assert "elvira (10.0.0.5)" in text
