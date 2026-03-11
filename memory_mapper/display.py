"""Terminal display for memory snapshots using the Rich library."""

import time
from datetime import datetime
from typing import Optional

from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.style import Style
from rich.table import Table
from rich.text import Text

from .tracker import MemoryTracker

# Colour used to highlight recently-changed bytes
CHANGED_STYLE = Style(bgcolor="yellow", color="black", bold=True)
HEADER_STYLE = Style(color="bright_cyan", bold=True)
ASCII_STYLE = Style(color="bright_white")
DIM_STYLE = Style(color="grey50")

BYTES_PER_ROW = 16


def render_snapshot(tracker: MemoryTracker, bytes_per_row: int = BYTES_PER_ROW) -> Panel:
    """
    Build a Rich renderable representing the current memory snapshot.

    Recently-changed bytes are highlighted with a yellow background.
    """
    snapshot = tracker.snapshot

    # ── status line ──────────────────────────────────────────────────────────
    if snapshot is None:
        body = Text("Waiting for data…", style=DIM_STYLE, justify="center")
        return Panel(body, title="[bold]Memory Mapper[/bold]", border_style="bright_blue")

    tracker.cleanup_old_changes()

    size = len(snapshot)
    ts = (
        datetime.fromtimestamp(time.time()).strftime("%Y-%m-%d %H:%M:%S")
        if tracker.last_update is not None
        else "—"
    )

    # ── hex table ────────────────────────────────────────────────────────────
    table = Table(
        show_header=True,
        header_style=HEADER_STYLE,
        box=None,
        padding=(0, 1),
        expand=False,
    )
    table.add_column("Offset", style="bright_cyan", no_wrap=True)
    for col in range(bytes_per_row):
        table.add_column(f"{col:02X}", no_wrap=True, min_width=2)
    table.add_column("│", style=DIM_STYLE, no_wrap=True)
    table.add_column("ASCII", no_wrap=True)

    for row_start in range(0, size, bytes_per_row):
        row_bytes = snapshot[row_start : row_start + bytes_per_row]

        offset_text = Text(f"0x{row_start:04X}", style="bright_cyan")

        hex_cells = []
        ascii_text = Text()
        for col, byte in enumerate(row_bytes):
            idx = row_start + col
            changed = tracker.recently_changed(idx)
            style = CHANGED_STYLE if changed else None
            hex_cells.append(Text(f"{byte:02X}", style=style))

            ch = chr(byte) if 0x20 <= byte < 0x7F else "."
            ascii_text.append(ch, style=CHANGED_STYLE if changed else ASCII_STYLE)

        # Pad rows that are shorter than bytes_per_row
        while len(hex_cells) < bytes_per_row:
            hex_cells.append(Text("  "))

        table.add_row(offset_text, *hex_cells, Text("│", style=DIM_STYLE), ascii_text)

    status = (
        f"[dim]Packets received:[/dim] [bold]{tracker.packet_count}[/bold]  "
        f"[dim]Size:[/dim] [bold]{size} B[/bold]  "
        f"[dim]Last update:[/dim] [bold]{ts}[/bold]  "
        f"[dim]Highlight window:[/dim] [bold]{tracker.highlight_duration:.1f}s[/bold]"
    )

    return Panel(
        table,
        title="[bold bright_blue]Memory Mapper[/bold bright_blue]",
        subtitle=status,
        border_style="bright_blue",
    )


class MemoryDisplay:
    """Live-updating terminal display backed by a MemoryTracker."""

    def __init__(
        self,
        tracker: MemoryTracker,
        bytes_per_row: int = BYTES_PER_ROW,
        refresh_per_second: float = 4.0,
    ) -> None:
        self.tracker = tracker
        self.bytes_per_row = bytes_per_row
        self.refresh_per_second = refresh_per_second
        self._console = Console()

    def run(self, stop_event=None) -> None:
        """Block and render the live display until Ctrl-C or *stop_event* is set."""
        with Live(
            render_snapshot(self.tracker, self.bytes_per_row),
            console=self._console,
            refresh_per_second=self.refresh_per_second,
            screen=False,
        ) as live:
            try:
                interval = 1.0 / self.refresh_per_second
                while stop_event is None or not stop_event.is_set():
                    time.sleep(interval)
                    live.update(render_snapshot(self.tracker, self.bytes_per_row))
            except KeyboardInterrupt:
                pass
