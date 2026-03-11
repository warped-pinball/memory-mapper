"""Tests for memory_mapper.display (rendering logic)."""

from rich.console import Group

from memory_mapper.display import _auto_bytes_per_row, render_snapshot
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
        console.print(render_snapshot(t, active_filter="10.0.0.1"))
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
