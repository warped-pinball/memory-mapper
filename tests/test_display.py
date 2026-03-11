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
        assert "Age" in output

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

        assert "all" not in output.lower()
        assert "1:10.0.0.1" in output
        assert "2:10.0.0.2" in output

    def test_number_key_selects_source(self):
        t = MemoryTracker()
        t.update(b"\x00", sender="10.0.0.1")
        t.update(b"\x00", sender="10.0.0.2")
        display = MemoryDisplay(t, selected_source="10.0.0.1")

        display._handle_input("2", stop_event=None)

        assert display.selected_source == "10.0.0.2"
        assert "Selected source 2" in display.status_message
