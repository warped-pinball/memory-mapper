"""Terminal display for memory snapshots using the Rich library."""

import time
from typing import Optional

from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.style import Style
from rich.table import Table
from rich.text import Text

from .tracker import MemoryTracker

# Colour used to highlight recently-changed bytes
CHANGED_STYLE = Style(bgcolor="yellow", color="black", bold=True)
HEADER_STYLE = Style(color="bright_cyan", bold=True)
DIM_STYLE = Style(color="grey50")

BYTES_PER_ROW = 16


def _auto_bytes_per_row(terminal_width: Optional[int], preferred: int) -> int:
    """Pick a compact row width that expands when the terminal is wider."""
    if terminal_width is None:
        return max(4, preferred)

    # Offset column + byte columns (2 chars plus space) + panel padding allowance
    estimated = max(4, (terminal_width - 14) // 3)
    # Keep rows aligned to 4-byte boundaries for readability.
    aligned = max(4, estimated - (estimated % 4))
    return max(4, max(preferred, aligned))


def _render_menu(tracker: MemoryTracker, active_filter: Optional[str]) -> Panel:
    senders = tracker.known_senders()
    menu = Table.grid(expand=True)
    menu.add_column(ratio=2)
    menu.add_column(ratio=3)

    sender_bits = ["[bold]Sources:[/bold]", "all"]
    for idx, sender in enumerate(senders[:8], start=1):
        sender_bits.append(f"{idx}:{sender}")

    filter_label = active_filter or "all"
    pps = tracker.packets_per_second()
    age = tracker.data_age_seconds()
    age_text = "—" if age is None else f"{age:.1f}s"

    menu.add_row(
        "  ".join(sender_bits),
        (
            f"[bold]Filter:[/bold] {filter_label}    "
            f"[bold]Rate:[/bold] {pps:.1f} pkt/s    "
            f"[bold]Age:[/bold] {age_text}"
        ),
    )
    return Panel(menu, border_style="bright_black", padding=(0, 1), title="[bold]Menu[/bold]")


def render_snapshot(
    tracker: MemoryTracker,
    bytes_per_row: int = BYTES_PER_ROW,
    terminal_width: Optional[int] = None,
    active_filter: Optional[str] = None,
) -> Panel:
    """Build a compact Rich renderable representing the current memory snapshot."""
    snapshot = tracker.snapshot

    if snapshot is None:
        body = Text("Waiting for data…", style=DIM_STYLE, justify="center")
        return Panel(body, title="[bold]Memory Mapper[/bold]", border_style="bright_blue")

    tracker.cleanup_old_changes()

    effective_bpr = _auto_bytes_per_row(terminal_width, bytes_per_row)
    size = len(snapshot)

    table = Table(
        show_header=True,
        header_style=HEADER_STYLE,
        box=None,
        padding=(0, 0),
        collapse_padding=True,
        expand=True,
    )
    table.add_column("Off", style="bright_cyan", no_wrap=True)
    for col in range(effective_bpr):
        table.add_column(f"{col:02X}", no_wrap=True, min_width=2, justify="center")

    for row_start in range(0, size, effective_bpr):
        row_bytes = snapshot[row_start : row_start + effective_bpr]
        cells = [Text(f"{byte:02X}", style=CHANGED_STYLE if tracker.recently_changed(row_start + col) else None)
                 for col, byte in enumerate(row_bytes)]

        while len(cells) < effective_bpr:
            cells.append(Text("  "))

        table.add_row(Text(f"{row_start:04X}", style="bright_cyan"), *cells)

    status = (
        f"[dim]Packets[/dim] [bold]{tracker.packet_count}[/bold]  "
        f"[dim]Size[/dim] [bold]{size}B[/bold]  "
        f"[dim]Highlight[/dim] [bold]{tracker.highlight_duration:.1f}s[/bold]"
    )

    body = Group(table, _render_menu(tracker, active_filter))
    return Panel(
        body,
        title="[bold bright_blue]Memory Mapper[/bold bright_blue]",
        subtitle=status,
        border_style="bright_blue",
        expand=True,
    )


class MemoryDisplay:
    """Live-updating terminal display backed by a MemoryTracker."""

    def __init__(
        self,
        tracker: MemoryTracker,
        bytes_per_row: int = BYTES_PER_ROW,
        refresh_per_second: float = 4.0,
        active_filter: Optional[str] = None,
    ) -> None:
        self.tracker = tracker
        self.bytes_per_row = bytes_per_row
        self.refresh_per_second = refresh_per_second
        self.active_filter = active_filter
        self._console = Console()

    def run(self, stop_event=None) -> None:
        """Block and render the live display until Ctrl-C or *stop_event* is set."""
        with Live(
            render_snapshot(
                self.tracker,
                self.bytes_per_row,
                terminal_width=self._console.size.width,
                active_filter=self.active_filter,
            ),
            console=self._console,
            refresh_per_second=self.refresh_per_second,
            screen=False,
        ) as live:
            try:
                interval = 1.0 / self.refresh_per_second
                while stop_event is None or not stop_event.is_set():
                    time.sleep(interval)
                    live.update(
                        render_snapshot(
                            self.tracker,
                            self.bytes_per_row,
                            terminal_width=self._console.size.width,
                            active_filter=self.active_filter,
                        )
                    )
            except KeyboardInterrupt:
                pass
