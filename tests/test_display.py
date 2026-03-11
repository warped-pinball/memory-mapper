"""Tests for memory_mapper.display (rendering logic)."""

import pytest

from rich.panel import Panel
from rich.text import Text

from memory_mapper.display import render_snapshot, CHANGED_STYLE
from memory_mapper.tracker import MemoryTracker


class TestRenderSnapshotNoData:
    def test_returns_panel(self):
        t = MemoryTracker()
        result = render_snapshot(t)
        assert isinstance(result, Panel)

    def test_waiting_message_when_empty(self):
        t = MemoryTracker()
        panel = render_snapshot(t)
        # Renderable should stringify without error
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
        assert isinstance(panel, Panel)

    def test_offset_shown(self):
        t = MemoryTracker()
        t.update(bytes(32))
        from rich.console import Console
        from io import StringIO
        buf = StringIO()
        console = Console(file=buf, width=200)
        console.print(render_snapshot(t))
        output = buf.getvalue()
        assert "0x0000" in output
        assert "0x0010" in output

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
        assert "3" in output

    def test_custom_bytes_per_row(self):
        t = MemoryTracker()
        t.update(bytes(32))
        from rich.console import Console
        from io import StringIO
        buf = StringIO()
        console = Console(file=buf, width=200)
        console.print(render_snapshot(t, bytes_per_row=8))
        output = buf.getvalue()
        # Second row offset should be 0x0008 with 8-byte rows
        assert "0x0008" in output

    def test_changed_bytes_render_without_error(self):
        t = MemoryTracker(highlight_duration=5.0)
        t.update(b"\x00" * 16)
        t.update(b"\xFF" * 16)
        from rich.console import Console
        from io import StringIO
        buf = StringIO()
        console = Console(file=buf, width=200)
        # Should not raise
        console.print(render_snapshot(t))


class TestRenderSnapshotASCII:
    def test_printable_chars_displayed(self):
        t = MemoryTracker()
        t.update(b"Hello!")
        from rich.console import Console
        from io import StringIO
        buf = StringIO()
        console = Console(file=buf, width=200)
        console.print(render_snapshot(t))
        output = buf.getvalue()
        assert "Hello!" in output

    def test_non_printable_shown_as_dot(self):
        t = MemoryTracker()
        t.update(b"\x00\x01\x1f")
        from rich.console import Console
        from io import StringIO
        buf = StringIO()
        console = Console(file=buf, width=200)
        console.print(render_snapshot(t))
        output = buf.getvalue()
        assert "..." in output
