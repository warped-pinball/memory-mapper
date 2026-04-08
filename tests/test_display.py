"""Tests for memory_mapper.display (rendering logic)."""

from rich.console import Group

from memory_mapper.display import MemoryDisplay, _auto_bytes_per_row, render_snapshot
from memory_mapper.tracker import MemoryTracker


class TestRenderSnapshotNoData:
    def test_returns_panel(self):
        t = MemoryTracker()
        result = render_snapshot(t)
        assert isinstance(result, Group)

    def test_waiting_message_when_empty(self):
        t = MemoryTracker()
        panel = render_snapshot(t)
        from rich.console import Console
        from io import StringIO

        buf = StringIO()
        console = Console(file=buf, width=120)
        console.print(panel)
        output = buf.getvalue()
        assert "Waiting" in output


class TestRenderSnapshotWithData:
    def test_returns_panel_with_data(self):
        t = MemoryTracker()
        t.update(b"\x00\x01\x02\x03")
        panel = render_snapshot(t)
        assert isinstance(panel, Group)

    def test_offset_shown(self):
        t = MemoryTracker()
        t.update(bytes(32))
        from rich.console import Console
        from io import StringIO

        buf = StringIO()
        console = Console(file=buf, width=200)
        console.print(render_snapshot(t))
        output = buf.getvalue()
        assert "0000" in output
        assert "0010" in output

    def test_packet_count_in_subtitle(self):
        t = MemoryTracker()
        for _ in range(3):
            t.update(b"\x00")
        from rich.console import Console
        from io import StringIO

        buf = StringIO()
        console = Console(file=buf, width=200)
        console.print(render_snapshot(t))
        output = buf.getvalue()
        assert "Packets" in output
        assert "3" in output

    def test_expands_bytes_per_row_for_wider_terminal(self):
        assert _auto_bytes_per_row(40, 8) == 8
        assert _auto_bytes_per_row(120, 8) > 8

    def test_menu_shows_sources_and_rate(self):
        t = MemoryTracker()
        t.update(b"\x00", sender="10.0.0.1")
        from rich.console import Console
        from io import StringIO

        buf = StringIO()
        console = Console(file=buf, width=120)
        console.print(render_snapshot(t, selected_source="10.0.0.1"))
        output = buf.getvalue()
        assert "Menu" in output
        assert "Sources" in output
        assert "10.0.0.1" in output
        assert "Rate" in output
        assert "Refresh" in output
        assert "Hz" in output
        assert "Age" in output


    def test_menu_shows_any_scan_option(self):
        t = MemoryTracker()
        t.update(b"\x00")
        from rich.console import Console
        from io import StringIO

        buf = StringIO()
        console = Console(file=buf, width=120)
        console.print(render_snapshot(t))
        output = buf.getvalue()
        assert "A any/not sure" in output

    def test_changed_bytes_render_without_error(self):
        t = MemoryTracker(highlight_duration=5.0)
        t.update(b"\x00" * 16)
        t.update(b"\xFF" * 16)
        from rich.console import Console
        from io import StringIO

        buf = StringIO()
        console = Console(file=buf, width=200)
        console.print(render_snapshot(t))

    def test_memory_bytes_use_single_spaces(self):
        t = MemoryTracker()
        t.update(bytes(range(16)))
        from rich.console import Console
        from io import StringIO

        buf = StringIO()
        console = Console(file=buf, width=120)
        console.print(render_snapshot(t))
        output = buf.getvalue()
        assert "00 01 02 03" in output
        assert "00  01" not in output

    def test_layout_order_memory_then_legend_then_menu(self):
        t = MemoryTracker()
        t.update(b"\x00")
        from rich.console import Console
        from io import StringIO

        buf = StringIO()
        console = Console(file=buf, width=120)
        console.print(render_snapshot(t))
        output = buf.getvalue()

        memory_index = output.find("Memory Mapper")
        legend_index = output.find("Legend")
        menu_index = output.find("Menu")

        assert memory_index != -1
        assert legend_index != -1
        assert menu_index != -1
        assert memory_index < legend_index < menu_index


class TestSourceSelection:
    def test_menu_does_not_show_all_source_option(self):
        t = MemoryTracker()
        t.update(b"\x00", sender="10.0.0.1")
        t.update(b"\x00", sender="10.0.0.2")

        from rich.console import Console
        from io import StringIO

        buf = StringIO()
        console = Console(file=buf, width=140)
        console.print(render_snapshot(t, selected_source="10.0.0.1"))
        output = buf.getvalue()

        assert "all sources" not in output.lower()
        assert "0:all" not in output.lower()
        assert "1:10.0.0.1" in output
        assert "2:10.0.0.2" in output


    def test_switching_source_resets_tracker_state(self):
        t = MemoryTracker()
        t.update(b"\x00", sender="10.0.0.1")
        t.update(b"\x00", sender="10.0.0.2")
        t.update_chunk(5, b"\xFF", sender="10.0.0.1")

        display = MemoryDisplay(t, selected_source="10.0.0.1")

        display._handle_input("2", stop_event=None)

        assert display.selected_source == "10.0.0.2"
        assert t.snapshot is None
        assert t.packet_count == 0
        assert "Reset state for new source" in display.status_message
    def test_number_key_selects_source(self):
        t = MemoryTracker()
        t.update(b"\x00", sender="10.0.0.1")
        t.update(b"\x00", sender="10.0.0.2")
        display = MemoryDisplay(t, selected_source="10.0.0.1")

        display._handle_input("2", stop_event=None)

        assert display.selected_source == "10.0.0.2"
        assert "Selected source 2" in display.status_message


class TestCaptureKeypress:
    def test_capture_keypress_handles_select_oserror(self, monkeypatch):
        t = MemoryTracker()
        display = MemoryDisplay(t)

        class FakeStdin:
            def isatty(self):
                return True

        monkeypatch.setattr("memory_mapper.display.sys.stdin", FakeStdin())
        monkeypatch.setattr(
            "memory_mapper.display.select.select",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("not a socket")),
        )

        assert display._capture_keypress() is None


class TestCursorNavigation:
    def test_cursor_starts_at_zero(self):
        t = MemoryTracker()
        display = MemoryDisplay(t)
        assert display.cursor_pos == 0

    def test_cursor_moves_right(self):
        t = MemoryTracker()
        t.update(bytes(32))
        display = MemoryDisplay(t)
        display._handle_input("RIGHT", stop_event=None)
        assert display.cursor_pos == 1

    def test_cursor_moves_left(self):
        t = MemoryTracker()
        t.update(bytes(32))
        display = MemoryDisplay(t)
        display.cursor_pos = 5
        display._handle_input("LEFT", stop_event=None)
        assert display.cursor_pos == 4

    def test_cursor_does_not_go_below_zero(self):
        t = MemoryTracker()
        t.update(bytes(32))
        display = MemoryDisplay(t)
        display._handle_input("LEFT", stop_event=None)
        assert display.cursor_pos == 0

    def test_cursor_does_not_exceed_size(self):
        t = MemoryTracker()
        t.update(bytes(4))
        display = MemoryDisplay(t)
        display.cursor_pos = 3
        display._handle_input("RIGHT", stop_event=None)
        assert display.cursor_pos == 3

    def test_cursor_moves_down(self):
        t = MemoryTracker()
        t.update(bytes(64))
        display = MemoryDisplay(t, bytes_per_row=16)
        display._effective_bpr = 16
        display._handle_input("DOWN", stop_event=None)
        assert display.cursor_pos == 16

    def test_cursor_moves_up(self):
        t = MemoryTracker()
        t.update(bytes(64))
        display = MemoryDisplay(t, bytes_per_row=16)
        display._effective_bpr = 16
        display.cursor_pos = 20
        display._handle_input("UP", stop_event=None)
        assert display.cursor_pos == 4

    def test_cursor_no_movement_with_no_data(self):
        t = MemoryTracker()
        display = MemoryDisplay(t)
        display._handle_input("RIGHT", stop_event=None)
        assert display.cursor_pos == 0


class TestMarkingAndExport:
    def test_space_marks_address(self):
        t = MemoryTracker()
        t.update(bytes(16))
        display = MemoryDisplay(t)
        display.cursor_pos = 5
        display._handle_input(" ", stop_event=None)
        assert 5 in t.marked_addresses
        assert "Marked" in display.status_message

    def test_space_unmarks_address(self):
        t = MemoryTracker()
        t.update(bytes(16))
        display = MemoryDisplay(t)
        display.cursor_pos = 5
        display._handle_input(" ", stop_event=None)
        display._handle_input(" ", stop_event=None)
        assert 5 not in t.marked_addresses
        assert "Unmarked" in display.status_message

    def test_export_no_marked(self):
        t = MemoryTracker()
        t.update(bytes(16))
        display = MemoryDisplay(t)
        display._export_marked()
        assert "No marked" in display.status_message

    def test_export_creates_file(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        t = MemoryTracker()
        t.update(b"\xAA\xBB\xCC")
        t.toggle_mark(1)
        display = MemoryDisplay(t)
        display._export_marked()
        assert "Exported" in display.status_message
        import glob as glob_mod
        files = glob_mod.glob(str(tmp_path / "marked_addresses_*.json"))
        assert len(files) == 1
        import json
        with open(files[0]) as f:
            data = json.load(f)
        assert len(data) == 1
        assert data[0]["address"] == "0x0001"
        assert data[0]["value"] == "0xBB"
        assert "address_dec" not in data[0]
        assert "value_dec" not in data[0]
        assert "value_bin" not in data[0]


class TestExportAll:
    def test_export_all_no_data(self):
        t = MemoryTracker()
        display = MemoryDisplay(t)
        display._export_all()
        assert "No data" in display.status_message

    def test_export_all_creates_file(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        t = MemoryTracker()
        t.update(b"\xAA\xBB\xCC")
        display = MemoryDisplay(t)
        display._export_all()
        assert "Exported 3 addresses" in display.status_message
        import glob as glob_mod
        files = glob_mod.glob(str(tmp_path / "all_addresses_*.json"))
        assert len(files) == 1
        import json
        with open(files[0]) as f:
            data = json.load(f)
        assert len(data) == 3
        assert data[0]["address"] == "0x0000"
        assert data[0]["value"] == "0xAA"
        assert data[1]["address"] == "0x0001"
        assert data[1]["value"] == "0xBB"
        assert data[2]["address"] == "0x0002"
        assert data[2]["value"] == "0xCC"

    def test_x_key_triggers_export_all(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        t = MemoryTracker()
        t.update(b"\x00\x01")
        display = MemoryDisplay(t)
        display._handle_input("x", stop_event=None)
        assert "Exported 2 addresses" in display.status_message


class TestAsciiMode:
    def test_ascii_toggle(self):
        t = MemoryTracker()
        display = MemoryDisplay(t)
        assert display.ascii_mode is False
        display._handle_input("t", stop_event=None)
        assert display.ascii_mode is True
        assert "enabled" in display.status_message
        display._handle_input("t", stop_event=None)
        assert display.ascii_mode is False
        assert "disabled" in display.status_message

    def test_ascii_view_renders(self):
        t = MemoryTracker()
        t.update(b"Hello World!\x00\x01\x02\x03")
        from rich.console import Console
        from io import StringIO

        buf = StringIO()
        console = Console(file=buf, width=200)
        console.print(render_snapshot(t, ascii_mode=True))
        output = buf.getvalue()
        assert "ASCII" in output  # menu shows "T ASCII ON"
        # ASCII chars replace hex values, so individual chars appear but hex does not
        for ch in "HelloWorld!":
            assert ch in output
        # Hex representations should NOT appear in the memory grid
        assert "48" not in output  # 'H' = 0x48
        assert "65" not in output  # 'e' = 0x65

    def test_non_printable_shown_as_dot(self):
        t = MemoryTracker()
        t.update(b"\x00\x01\x7f")
        from rich.console import Console
        from io import StringIO

        buf = StringIO()
        console = Console(file=buf, width=200)
        console.print(render_snapshot(t, ascii_mode=True))
        output = buf.getvalue()
        assert "·" in output


class TestHighlightDurationAdjustment:
    def test_plus_increases_duration(self):
        t = MemoryTracker(highlight_duration=3.0)
        display = MemoryDisplay(t)
        display._handle_input("+", stop_event=None)
        assert t.highlight_duration == 3.5
        assert "3.5s" in display.status_message

    def test_equals_increases_duration(self):
        t = MemoryTracker(highlight_duration=3.0)
        display = MemoryDisplay(t)
        display._handle_input("=", stop_event=None)
        assert t.highlight_duration == 3.5

    def test_minus_decreases_duration(self):
        t = MemoryTracker(highlight_duration=3.0)
        display = MemoryDisplay(t)
        display._handle_input("-", stop_event=None)
        assert t.highlight_duration == 2.5
        assert "2.5s" in display.status_message

    def test_underscore_decreases_duration(self):
        t = MemoryTracker(highlight_duration=3.0)
        display = MemoryDisplay(t)
        display._handle_input("_", stop_event=None)
        assert t.highlight_duration == 2.5

    def test_does_not_go_below_minimum(self):
        t = MemoryTracker(highlight_duration=0.5)
        display = MemoryDisplay(t)
        display._handle_input("-", stop_event=None)
        assert t.highlight_duration == 0.5
        assert "0.5s" in display.status_message

    def test_does_not_go_above_maximum(self):
        t = MemoryTracker(highlight_duration=30.0)
        display = MemoryDisplay(t)
        display._handle_input("+", stop_event=None)
        assert t.highlight_duration == 30.0
        assert "30.0s" in display.status_message

    def test_multiple_adjustments(self):
        t = MemoryTracker(highlight_duration=3.0)
        display = MemoryDisplay(t)
        display._handle_input("+", stop_event=None)
        display._handle_input("+", stop_event=None)
        display._handle_input("+", stop_event=None)
        assert t.highlight_duration == 4.5


class TestCursorInfoPanel:
    def test_cursor_panel_shows_address_info(self):
        t = MemoryTracker()
        t.update(b"\x00\x41\x02")
        from rich.console import Console
        from io import StringIO

        buf = StringIO()
        console = Console(file=buf, width=200)
        console.print(render_snapshot(t, cursor_pos=1))
        output = buf.getvalue()
        assert "Cursor" in output
        assert "0x0001" in output
        assert "0x41" in output
        assert "65" in output
        assert "01000001" in output
        assert "A" in output

    def test_cursor_panel_shows_value_history(self):
        t = MemoryTracker()
        t.update(b"\x00")
        t.update(b"\x01")
        t.update(b"\x02")
        from rich.console import Console
        from io import StringIO

        buf = StringIO()
        console = Console(file=buf, width=200)
        console.print(render_snapshot(t, cursor_pos=0))
        output = buf.getvalue()
        assert "History" in output
        assert "0x01" in output
        assert "0x02" in output

    def test_marked_address_shown_in_cursor_panel(self):
        t = MemoryTracker()
        t.update(b"\x00\x01")
        t.toggle_mark(0)
        from rich.console import Console
        from io import StringIO

        buf = StringIO()
        console = Console(file=buf, width=200)
        console.print(render_snapshot(t, cursor_pos=0))
        output = buf.getvalue()
        assert "MARKED" in output


class TestHeaderAlignment:
    def test_header_offset_column_is_four_chars(self):
        t = MemoryTracker()
        t.update(bytes(32))
        from rich.console import Console
        from io import StringIO

        buf = StringIO()
        console = Console(file=buf, width=200)
        console.print(render_snapshot(t))
        output = buf.getvalue()
        # Header should have " Off" (4 chars) matching "0000" (4 chars)
        assert " Off" in output


class TestRenderWithMarkedStyle:
    def test_marked_bytes_render_without_error(self):
        t = MemoryTracker()
        t.update(b"\x00\x01\x02\x03")
        t.toggle_mark(1)
        t.toggle_mark(2)
        from rich.console import Console
        from io import StringIO

        buf = StringIO()
        console = Console(file=buf, width=200)
        console.print(render_snapshot(t))
        output = buf.getvalue()
        assert "Marked" in output
