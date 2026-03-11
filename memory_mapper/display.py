"""Terminal display for memory snapshots using the Rich library."""

import select
import sys
import time

try:
    import termios
    import tty
except ImportError:  # pragma: no cover - non-POSIX fallback
    termios = None
    tty = None
from typing import Optional

from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.style import Style
from rich.table import Table
from rich.text import Text

from .tracker import MemoryTracker, SCAN_MODES

CHANGED_STYLE = Style(bgcolor="yellow", color="black", bold=True)
HARD_MATCH_STYLE = Style(bgcolor="green", color="black", bold=True)
SOFT_MATCH_1_STYLE = Style(bgcolor="bright_cyan", color="black", bold=True)
SOFT_MATCH_2_STYLE = Style(bgcolor="magenta", color="white", bold=True)
HEADER_STYLE = Style(color="bright_cyan", bold=True)
DIM_STYLE = Style(color="grey50")

BYTES_PER_ROW = 16


def _auto_bytes_per_row(terminal_width: Optional[int], preferred: int) -> int:
    if terminal_width is None:
        return max(4, preferred)
    estimated = max(4, (terminal_width - 14) // 3)
    aligned = max(4, estimated - (estimated % 4))
    return max(4, max(preferred, aligned))


def _render_menu(tracker: MemoryTracker, active_filter: Optional[str], status_message: str) -> Panel:
    senders = tracker.known_senders()
    stats = tracker.scan_stats()

    menu = Table.grid(expand=True)
    menu.add_column(ratio=5)
    menu.add_column(ratio=3)

    sender_bits = ["[bold]Sources:[/bold]", "all"]
    for idx, sender in enumerate(senders[:8], start=1):
        sender_bits.append(f"{idx}:{sender}")

    filter_label = active_filter or "all"
    pps = tracker.packets_per_second()
    age = tracker.data_age_seconds()
    age_text = "—" if age is None else f"{age:.1f}s"

    commands = (
        "[bold]Scan:[/bold] [cyan]C[/cyan] changed  [cyan]N[/cyan] unchanged  "
        "[cyan]I[/cyan] increased  [cyan]D[/cyan] decreased  [cyan]A[/cyan] any/not sure  "
        "[cyan]R[/cyan] reset scan  [cyan]Q[/cyan] quit"
    )

    metrics = (
        f"[bold]Filter:[/bold] {filter_label}  [bold]Rate:[/bold] {pps:.1f} pkt/s  [bold]Age:[/bold] {age_text}\n"
        f"[bold]Steps:[/bold] {stats.steps}  [bold]Hard:[/bold] {stats.hard_match_count}  "
        f"[bold]Soft(1 miss):[/bold] {stats.soft_match_count_1}  [bold]Soft(2 misses):[/bold] {stats.soft_match_count_2}"
    )

    menu.add_row(" ".join(sender_bits), metrics)
    menu.add_row(commands, "")
    menu.add_row(f"[bold]Status:[/bold] {status_message}", "")
    return Panel(menu, border_style="bright_black", padding=(0, 1), title="[bold]Menu[/bold]")


def _render_legend() -> Panel:
    legend = Text()
    legend.append("Hard match ", style=HARD_MATCH_STYLE)
    legend.append("  Soft match (1 miss) ", style=SOFT_MATCH_1_STYLE)
    legend.append("  Soft match (2 misses) ", style=SOFT_MATCH_2_STYLE)
    legend.append("  Recently changed ", style=CHANGED_STYLE)
    return Panel(legend, border_style="bright_black", title="[bold]Legend[/bold]")


def _byte_style(tracker: MemoryTracker, index: int):
    match_level = tracker.scan_match_level(index)
    if match_level == 3:
        return HARD_MATCH_STYLE
    if match_level == 2:
        return SOFT_MATCH_1_STYLE
    if match_level == 1:
        return SOFT_MATCH_2_STYLE
    if tracker.recently_changed(index):
        return CHANGED_STYLE
    return None


def render_snapshot(
    tracker: MemoryTracker,
    bytes_per_row: int = BYTES_PER_ROW,
    terminal_width: Optional[int] = None,
    active_filter: Optional[str] = None,
    status_message: str = "Ready",
):
    snapshot = tracker.snapshot
    menu_panel = _render_menu(tracker, active_filter, status_message)

    if snapshot is None:
        body = Text("Waiting for data…", style=DIM_STYLE, justify="center")
        memory_panel = Panel(body, title="[bold]Memory Mapper[/bold]", border_style="bright_blue")
        return Group(memory_panel, _render_legend(), menu_panel)

    tracker.cleanup_old_changes()

    effective_bpr = _auto_bytes_per_row(terminal_width, bytes_per_row)
    size = len(snapshot)

    memory_text = Text()
    header = Text("Off", style="bright_cyan")
    for col in range(effective_bpr):
        header.append(" ")
        header.append(f"{col:02X}", style=HEADER_STYLE)
    memory_text.append(header)

    for row_start in range(0, size, effective_bpr):
        row_bytes = snapshot[row_start : row_start + effective_bpr]
        line = Text("\n")
        line.append(f"{row_start:04X}", style="bright_cyan")
        for col, byte in enumerate(row_bytes):
            line.append(" ")
            line.append(f"{byte:02X}", style=_byte_style(tracker, row_start + col))
        memory_text.append(line)

    status = (
        f"[dim]Packets[/dim] [bold]{tracker.packet_count}[/bold]  "
        f"[dim]Size[/dim] [bold]{size}B[/bold]  "
        f"[dim]Highlight[/dim] [bold]{tracker.highlight_duration:.1f}s[/bold]"
    )

    memory_panel = Panel(
        memory_text,
        title="[bold bright_blue]Memory Mapper[/bold bright_blue]",
        subtitle=status,
        border_style="bright_blue",
        expand=True,
    )
    return Group(memory_panel, _render_legend(), menu_panel)


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
        self.status_message = "Waiting for first snapshot"

    def _capture_keypress(self) -> Optional[str]:
        if not sys.stdin.isatty():
            return None
        readable, _, _ = select.select([sys.stdin], [], [], 0)
        if not readable:
            return None
        return sys.stdin.read(1)

    def _handle_input(self, key: str, stop_event) -> None:
        if not key:
            return
        k = key.lower()
        if k == "q":
            self.status_message = "Quit requested"
            if stop_event is not None:
                stop_event.set()
            return
        if k == "r":
            self.tracker.reset_scan()
            self.status_message = "Scan reset. Baseline captured from current snapshot."
            return
        if k in SCAN_MODES:
            applied = self.tracker.apply_scan(k)
            if applied:
                self.status_message = f"Applied scan filter: {SCAN_MODES[k]}"
            elif k == "a":
                self.status_message = "Captured a fresh baseline (any/not sure)."
            else:
                self.status_message = "Captured baseline; press scan option again after values change."

    def run(self, stop_event=None) -> None:
        can_raw_mode = sys.stdin.isatty() and termios is not None and tty is not None
        stdin_fd = sys.stdin.fileno() if can_raw_mode else None
        old_settings = termios.tcgetattr(stdin_fd) if stdin_fd is not None else None
        if stdin_fd is not None:
            tty.setcbreak(stdin_fd)

        try:
            with Live(
                render_snapshot(
                    self.tracker,
                    self.bytes_per_row,
                    terminal_width=self._console.size.width,
                    active_filter=self.active_filter,
                    status_message=self.status_message,
                ),
                console=self._console,
                refresh_per_second=self.refresh_per_second,
                screen=True,
            ) as live:
                try:
                    interval = 1.0 / self.refresh_per_second
                    while stop_event is None or not stop_event.is_set():
                        time.sleep(interval)
                        key = self._capture_keypress()
                        if key:
                            self._handle_input(key, stop_event)
                        live.update(
                            render_snapshot(
                                self.tracker,
                                self.bytes_per_row,
                                terminal_width=self._console.size.width,
                                active_filter=self.active_filter,
                                status_message=self.status_message,
                            )
                        )
                except KeyboardInterrupt:
                    pass
        finally:
            if stdin_fd is not None and old_settings is not None:
                termios.tcsetattr(stdin_fd, termios.TCSADRAIN, old_settings)
