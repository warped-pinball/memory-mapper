"""Terminal display for memory snapshots using the Rich library."""

import datetime
import json
import os
import select
import sys
import time

try:
    import termios
    import tty
except ImportError:  # pragma: no cover - non-POSIX fallback
    termios = None
    tty = None

try:
    import msvcrt
except ImportError:  # pragma: no cover - non-Windows fallback
    msvcrt = None
from typing import Optional

from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.style import Style
from rich.table import Table
from rich.text import Text

from .tracker import MemoryTracker, SCAN_MODES, BIT_SCAN_MODES

HIGHLIGHT_DURATION_MIN = 0.5
HIGHLIGHT_DURATION_MAX = 30.0
HIGHLIGHT_DURATION_STEP = 0.5

CHANGED_STYLE = Style(bgcolor="yellow", color="black", bold=True)
HARD_MATCH_STYLE = Style(bgcolor="green", color="black", bold=True)
SOFT_MATCH_1_STYLE = Style(bgcolor="bright_cyan", color="black", bold=True)
SOFT_MATCH_2_STYLE = Style(bgcolor="magenta", color="white", bold=True)
HEADER_STYLE = Style(color="bright_cyan", bold=True)
DIM_STYLE = Style(color="grey50")
CURSOR_STYLE = Style(bgcolor="white", color="black", bold=True)
MARKED_STYLE = Style(bgcolor="bright_red", color="white", bold=True)

BYTES_PER_ROW = 16

ASCII_PRINTABLE_START = 32
ASCII_PRINTABLE_END = 127


def _auto_bytes_per_row(terminal_width: Optional[int], preferred: int) -> int:
    if terminal_width is None:
        return max(4, preferred)
    estimated = max(4, (terminal_width - 14) // 3)
    aligned = max(4, estimated - (estimated % 4))
    return max(4, max(preferred, aligned))


def _render_menu(
    tracker: MemoryTracker,
    selected_source: Optional[str],
    status_message: str,
    ascii_mode: bool = False,
    bit_mode: bool = False,
) -> Panel:
    senders = tracker.known_senders()
    stats = tracker.bit_scan_stats() if bit_mode else tracker.scan_stats()

    menu = Table.grid(expand=True)
    menu.add_column(ratio=5)
    menu.add_column(ratio=3)

    sender_bits = ["[bold]Sources:[/bold]"]
    for idx, sender in enumerate(senders[:9], start=1):
        if sender == selected_source:
            sender_bits.append(f"[bold bright_blue]{idx}[/bold bright_blue]:{sender}*")
        else:
            sender_bits.append(f"[bright_blue]{idx}[/bright_blue]:{sender}")

    filter_label = selected_source or "(waiting for first source)"
    pps = tracker.packets_per_second()
    hertz = tracker.refreshes_per_second()
    age = tracker.data_age_seconds()
    age_text = "—" if age is None else f"{age:.1f}s"

    mode_label = "[bright_green]BIT[/bright_green]" if bit_mode else "BYTE"

    if bit_mode:
        scan_commands = (
            "[bold]Scan:[/bold] [cyan]C[/cyan] changed  [cyan]N[/cyan] unchanged  "
            "[cyan]S[/cyan] set(=1)  [cyan]L[/cyan] cleared(=0)  [cyan]A[/cyan] any/not sure  "
            "[cyan]R[/cyan] reset scan"
        )
    else:
        scan_commands = (
            "[bold]Scan:[/bold] [cyan]C[/cyan] changed  [cyan]N[/cyan] unchanged  "
            "[cyan]I[/cyan] increased  [cyan]D[/cyan] decreased  [cyan]A[/cyan] any/not sure  "
            "[cyan]R[/cyan] reset scan"
        )
    nav_commands = (
        "[bold]Nav:[/bold] [cyan]←↑↓→[/cyan] move cursor  [cyan]Space[/cyan] mark  "
        "[cyan]E[/cyan] export marked  [cyan]X[/cyan] export all  [cyan]T[/cyan] ASCII "
        + ("[bright_green]ON[/bright_green]" if ascii_mode else "off")
        + f"  [cyan]B[/cyan] mode:{mode_label}"
        + "  [cyan]+/-[/cyan] highlight time  [cyan]Q[/cyan] quit"
    )

    metrics = (
        f"[bold]Selected:[/bold] {filter_label}  [bold]Rate:[/bold] {pps:.1f} pkt/s  "
        f"[bold]Refresh:[/bold] {hertz:.2f} Hz  [bold]Age:[/bold] {age_text}\n"
        f"[bold]Steps:[/bold] {stats.steps}  [bold]Hard:[/bold] {stats.hard_match_count}  "
        f"[bold]Soft(1 miss):[/bold] {stats.soft_match_count_1}  [bold]Soft(2 misses):[/bold] {stats.soft_match_count_2}"
    )

    menu.add_row(" ".join(sender_bits), metrics)
    menu.add_row(scan_commands, "")
    menu.add_row(nav_commands, "")
    menu.add_row(f"[bold]Status:[/bold] {status_message}", "")
    return Panel(menu, border_style="bright_black", padding=(0, 1), title="[bold]Menu[/bold]")


def _render_legend() -> Panel:
    legend = Text()
    legend.append("Hard match ", style=HARD_MATCH_STYLE)
    legend.append("  Soft match (1 miss) ", style=SOFT_MATCH_1_STYLE)
    legend.append("  Soft match (2 misses) ", style=SOFT_MATCH_2_STYLE)
    legend.append("  Recently changed ", style=CHANGED_STYLE)
    legend.append("  Cursor ", style=CURSOR_STYLE)
    legend.append("  Marked ", style=MARKED_STYLE)
    return Panel(legend, border_style="bright_black", title="[bold]Legend[/bold]")


def _byte_style(
    tracker: MemoryTracker,
    index: int,
    cursor_pos: Optional[int] = None,
):
    if cursor_pos is not None and index == cursor_pos:
        return CURSOR_STYLE
    if index in tracker.marked_addresses:
        return MARKED_STYLE
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


def _bit_style(
    tracker: MemoryTracker,
    byte_idx: int,
    bit_idx: int,
) -> Optional[Style]:
    """Return the highlight style for an individual bit based on bit scan results."""
    match_level = tracker.bit_scan_match_level(byte_idx, bit_idx)
    if match_level == 3:
        return HARD_MATCH_STYLE
    if match_level == 2:
        return SOFT_MATCH_1_STYLE
    if match_level == 1:
        return SOFT_MATCH_2_STYLE
    return None


def _render_cursor_info(
    tracker: MemoryTracker,
    cursor_pos: int,
) -> Panel:
    """Render the info panel showing current cursor address details and value history."""
    snapshot = tracker.snapshot
    info = Text()
    if snapshot is not None and 0 <= cursor_pos < len(snapshot):
        value = snapshot[cursor_pos]
        is_marked = cursor_pos in tracker.marked_addresses
        mark_label = " [bright_red]★ MARKED[/bright_red]" if is_marked else ""
        info_markup = (
            f"[bold]Addr:[/bold] 0x{cursor_pos:04X} ({cursor_pos}){mark_label}  "
            f"[bold]Hex:[/bold] 0x{value:02X}  "
            f"[bold]Dec:[/bold] {value}  "
            f"[bold]Bin:[/bold] {value:08b}  "
            f"[bold]ASCII:[/bold] {chr(value) if ASCII_PRINTABLE_START <= value < ASCII_PRINTABLE_END else '·'}"
        )
        history = tracker.get_value_history(cursor_pos)
        if history:
            info_markup += "\n[bold]History (last 10):[/bold] "
            parts = []
            for ts, val in history[-10:]:
                age = time.monotonic() - ts
                if age < 60:
                    age_str = f"{age:.1f}s ago"
                else:
                    age_str = f"{age / 60:.1f}m ago"
                parts.append(f"0x{val:02X}({age_str})")
            info_markup += "  ".join(parts)
    else:
        info_markup = "[dim]No data at cursor position[/dim]"

    return Panel(
        info_markup,
        border_style="bright_black",
        padding=(0, 1),
        title="[bold]Cursor[/bold]",
    )


BITS_PER_ROW = 4  # number of bytes shown per row in bit mode


def _render_bit_cursor_info(
    tracker: MemoryTracker,
    cursor_pos: int,
) -> Panel:
    """Render cursor info panel for bit mode, showing per-bit match details."""
    snapshot = tracker.snapshot
    if snapshot is not None and 0 <= cursor_pos < len(snapshot):
        value = snapshot[cursor_pos]
        is_marked = cursor_pos in tracker.marked_addresses
        mark_label = " [bright_red]★ MARKED[/bright_red]" if is_marked else ""
        info_markup = (
            f"[bold]Addr:[/bold] 0x{cursor_pos:04X} ({cursor_pos}){mark_label}  "
            f"[bold]Hex:[/bold] 0x{value:02X}  "
            f"[bold]Dec:[/bold] {value}"
        )
        # Show each bit with its match level
        bits_text = Text()
        bits_text.append("\nBits: ", style="bold")
        for bit_idx in range(7, -1, -1):
            bit_val = (value >> bit_idx) & 1
            style = _bit_style(tracker, cursor_pos, bit_idx)
            bits_text.append(str(bit_val), style=style)
        bits_text.append("  (MSB←→LSB)")

        return Panel(
            Group(info_markup, bits_text),
            border_style="bright_black",
            padding=(0, 1),
            title="[bold]Cursor (Bit Mode)[/bold]",
        )
    else:
        return Panel(
            "[dim]No data at cursor position[/dim]",
            border_style="bright_black",
            padding=(0, 1),
            title="[bold]Cursor (Bit Mode)[/bold]",
        )


def _render_bit_view(
    tracker: MemoryTracker,
    cursor_pos: Optional[int] = None,
) -> Panel:
    """Render the memory panel in bit mode, showing a filtered table of bits."""
    snapshot = tracker.snapshot
    if snapshot is None:
        body = Text("Waiting for data…", style=DIM_STYLE, justify="center")
        return Panel(body, title="[bold bright_blue]Memory Mapper (Bit Mode)[/bold bright_blue]",
                     border_style="bright_blue")

    has_scan = len(tracker.bit_scan_total) > 0
    if has_scan:
        visible_bytes = tracker.get_bit_scan_matching_bytes()
    else:
        visible_bytes = list(range(len(snapshot)))

    if not visible_bytes:
        body = Text("No matching bits found. Adjust scan filters or reset.",
                     style=DIM_STYLE, justify="center")
        matched = len(tracker.bit_scan_total)
        status = (
            f"[dim]Packets[/dim] [bold]{tracker.packet_count}[/bold]  "
            f"[dim]Size[/dim] [bold]{len(snapshot)}B[/bold]  "
            f"[dim]Bits scanned[/dim] [bold]{matched}[/bold]  "
            f"[dim]Matching bytes[/dim] [bold]0[/bold]"
        )
        return Panel(body, title="[bold bright_blue]Memory Mapper (Bit Mode)[/bold bright_blue]",
                     subtitle=status, border_style="bright_blue", expand=True)

    # Build a table: Address | Hex | Bit7 Bit6 Bit5 Bit4 Bit3 Bit2 Bit1 Bit0
    table = Table(show_header=True, header_style=HEADER_STYLE, expand=True,
                  show_lines=False, pad_edge=False)
    table.add_column("Address", style="bright_cyan", width=8)
    table.add_column("Hex", width=4)
    for bit_idx in range(7, -1, -1):
        table.add_column(f"b{bit_idx}", width=3, justify="center")

    for byte_idx in visible_bytes:
        if byte_idx >= len(snapshot):
            continue
        value = snapshot[byte_idx]
        is_cursor = cursor_pos is not None and byte_idx == cursor_pos
        is_marked = byte_idx in tracker.marked_addresses

        addr_style = CURSOR_STYLE if is_cursor else (MARKED_STYLE if is_marked else None)
        addr_text = Text(f"0x{byte_idx:04X}", style=addr_style)
        hex_text = Text(f"{value:02X}", style=addr_style)

        bit_cells = []
        for bit_idx in range(7, -1, -1):
            bit_val = (value >> bit_idx) & 1
            style = _bit_style(tracker, byte_idx, bit_idx)
            if is_cursor and style is None:
                style = CURSOR_STYLE
            bit_cells.append(Text(str(bit_val), style=style))

        table.add_row(addr_text, hex_text, *bit_cells)

    marked_count = len(tracker.marked_addresses)
    status = (
        f"[dim]Packets[/dim] [bold]{tracker.packet_count}[/bold]  "
        f"[dim]Size[/dim] [bold]{len(snapshot)}B[/bold]  "
        f"[dim]Highlight[/dim] [bold]{tracker.highlight_duration:.1f}s[/bold]  "
        f"[dim]Marked[/dim] [bold]{marked_count}[/bold]  "
        f"[dim]Showing[/dim] [bold]{len(visible_bytes)}[/bold] bytes"
    )
    return Panel(table,
                 title="[bold bright_blue]Memory Mapper (Bit Mode)[/bold bright_blue]",
                 subtitle=status, border_style="bright_blue", expand=True)


def render_snapshot(
    tracker: MemoryTracker,
    bytes_per_row: int = BYTES_PER_ROW,
    terminal_width: Optional[int] = None,
    selected_source: Optional[str] = None,
    status_message: str = "Ready",
    cursor_pos: Optional[int] = None,
    ascii_mode: bool = False,
    bit_mode: bool = False,
):
    snapshot = tracker.snapshot
    menu_panel = _render_menu(
        tracker, selected_source, status_message,
        ascii_mode=ascii_mode, bit_mode=bit_mode,
    )

    if bit_mode:
        memory_panel = _render_bit_view(tracker, cursor_pos=cursor_pos)
        panels = [memory_panel]
        if cursor_pos is not None:
            panels.append(_render_bit_cursor_info(tracker, cursor_pos))
        panels.append(_render_legend())
        panels.append(menu_panel)
        return Group(*panels)

    if snapshot is None:
        body = Text("Waiting for data…", style=DIM_STYLE, justify="center")
        memory_panel = Panel(body, title="[bold]Memory Mapper[/bold]", border_style="bright_blue")
        return Group(memory_panel, _render_legend(), menu_panel)

    tracker.cleanup_old_changes()

    effective_bpr = _auto_bytes_per_row(terminal_width, bytes_per_row)
    size = len(snapshot)

    memory_text = Text()
    # Header: "Off " is 4 chars to align with 4-char row offsets like "0000"
    header = Text(" Off", style="bright_cyan")
    for col in range(effective_bpr):
        header.append(" ")
        header.append(f"{col:02X}", style=HEADER_STYLE)
    memory_text.append(header)

    for row_start in range(0, size, effective_bpr):
        row_bytes = snapshot[row_start : row_start + effective_bpr]
        line = Text("\n")
        line.append(f"{row_start:04X}", style="bright_cyan")
        for col, byte_val in enumerate(row_bytes):
            line.append(" ")
            if ascii_mode:
                ch = chr(byte_val) if ASCII_PRINTABLE_START <= byte_val < ASCII_PRINTABLE_END else "·"
                line.append(
                    f" {ch}",
                    style=_byte_style(tracker, row_start + col, cursor_pos),
                )
            else:
                line.append(
                    f"{byte_val:02X}",
                    style=_byte_style(tracker, row_start + col, cursor_pos),
                )
        memory_text.append(line)

    marked_count = len(tracker.marked_addresses)
    status = (
        f"[dim]Packets[/dim] [bold]{tracker.packet_count}[/bold]  "
        f"[dim]Size[/dim] [bold]{size}B[/bold]  "
        f"[dim]Highlight[/dim] [bold]{tracker.highlight_duration:.1f}s[/bold]  "
        f"[dim]Marked[/dim] [bold]{marked_count}[/bold]"
    )

    memory_panel = Panel(
        memory_text,
        title="[bold bright_blue]Memory Mapper[/bold bright_blue]",
        subtitle=status,
        border_style="bright_blue",
        expand=True,
    )

    panels = [memory_panel]
    if cursor_pos is not None:
        panels.append(_render_cursor_info(tracker, cursor_pos))
    panels.append(_render_legend())
    panels.append(menu_panel)
    return Group(*panels)


class MemoryDisplay:
    """Live-updating terminal display backed by a MemoryTracker."""

    def __init__(
        self,
        tracker: MemoryTracker,
        bytes_per_row: int = BYTES_PER_ROW,
        refresh_per_second: float = 4.0,
        selected_source: Optional[str] = None,
    ) -> None:
        self.tracker = tracker
        self.bytes_per_row = bytes_per_row
        self.refresh_per_second = refresh_per_second
        self.selected_source = selected_source
        self._console = Console()
        self.status_message = "Waiting for first snapshot"
        self.cursor_pos: int = 0
        self.ascii_mode: bool = False
        self.bit_mode: bool = False
        self._effective_bpr: int = bytes_per_row

    def _capture_keypress(self) -> Optional[str]:
        if not sys.stdin.isatty():
            return None
        if msvcrt is not None:
            if not msvcrt.kbhit():
                return None
            ch = msvcrt.getwch()
            if ch in ("\x00", "\xe0"):
                ch2 = msvcrt.getwch()
                arrow_map = {"H": "UP", "P": "DOWN", "K": "LEFT", "M": "RIGHT"}
                return arrow_map.get(ch2, ch2)
            return ch
        try:
            readable, _, _ = select.select([sys.stdin], [], [], 0)
        except (OSError, ValueError):
            return None
        if not readable:
            return None
        ch = sys.stdin.read(1)
        if ch == "\x1b":
            try:
                readable2, _, _ = select.select([sys.stdin], [], [], 0.01)
            except (OSError, ValueError):
                return ch
            if readable2:
                ch2 = sys.stdin.read(1)
                if ch2 == "[":
                    try:
                        readable3, _, _ = select.select([sys.stdin], [], [], 0.01)
                    except (OSError, ValueError):
                        return ch
                    if readable3:
                        ch3 = sys.stdin.read(1)
                        arrow_map = {"A": "UP", "B": "DOWN", "C": "RIGHT", "D": "LEFT"}
                        return arrow_map.get(ch3, ch3)
        return ch

    def _handle_input(self, key: str, stop_event) -> None:
        if not key:
            return

        if key in ("UP", "DOWN", "LEFT", "RIGHT"):
            self._move_cursor(key)
            return

        k = key.lower()
        if k == "q":
            self.status_message = "Quit requested"
            if stop_event is not None:
                stop_event.set()
            return
        if k == " ":
            self._toggle_mark()
            return
        if k == "e":
            self._export_marked()
            return
        if k == "x":
            self._export_all()
            return
        if k == "t":
            self.ascii_mode = not self.ascii_mode
            self.status_message = f"ASCII view {'enabled' if self.ascii_mode else 'disabled'}"
            return
        if k == "b":
            self.bit_mode = not self.bit_mode
            self.status_message = f"Bit mode {'enabled' if self.bit_mode else 'disabled'}"
            return
        if k in ("+", "="):
            new_val = min(
                HIGHLIGHT_DURATION_MAX,
                self.tracker.highlight_duration + HIGHLIGHT_DURATION_STEP,
            )
            self.tracker.highlight_duration = new_val
            self.status_message = f"Highlight duration: {new_val:.1f}s"
            return
        if k in ("-", "_"):
            new_val = max(
                HIGHLIGHT_DURATION_MIN,
                self.tracker.highlight_duration - HIGHLIGHT_DURATION_STEP,
            )
            self.tracker.highlight_duration = new_val
            self.status_message = f"Highlight duration: {new_val:.1f}s"
            return
        if k == "r":
            if self.bit_mode:
                self.tracker.reset_bit_scan()
                self.status_message = "Bit scan reset. Baseline captured from current snapshot."
            else:
                self.tracker.reset_scan()
                self.status_message = "Scan reset. Baseline captured from current snapshot."
            return
        if k.isdigit() and k != "0":
            self._select_source(int(k))
            return
        if self.bit_mode and k in BIT_SCAN_MODES:
            applied = self.tracker.apply_bit_scan(k)
            if applied:
                self.status_message = f"Applied bit scan filter: {BIT_SCAN_MODES[k]}"
            elif k == "a":
                self.status_message = "Captured a fresh baseline (any/not sure)."
            else:
                self.status_message = "Captured baseline; press scan option again after values change."
            return
        if not self.bit_mode and k in SCAN_MODES:
            applied = self.tracker.apply_scan(k)
            if applied:
                self.status_message = f"Applied scan filter: {SCAN_MODES[k]}"
            elif k == "a":
                self.status_message = "Captured a fresh baseline (any/not sure)."
            else:
                self.status_message = "Captured baseline; press scan option again after values change."

    def _move_cursor(self, direction: str) -> None:
        size = len(self.tracker.snapshot) if self.tracker.snapshot else 0
        if size == 0:
            return
        bpr = self._effective_bpr
        if direction == "LEFT":
            self.cursor_pos = max(0, self.cursor_pos - 1)
        elif direction == "RIGHT":
            self.cursor_pos = min(size - 1, self.cursor_pos + 1)
        elif direction == "UP":
            self.cursor_pos = max(0, self.cursor_pos - bpr)
        elif direction == "DOWN":
            self.cursor_pos = min(size - 1, self.cursor_pos + bpr)

    def _toggle_mark(self) -> None:
        snapshot = self.tracker.snapshot
        if snapshot is None or self.cursor_pos >= len(snapshot):
            self.status_message = "No data at cursor position"
            return
        marked = self.tracker.toggle_mark(self.cursor_pos)
        if marked:
            self.status_message = f"Marked address 0x{self.cursor_pos:04X}"
        else:
            self.status_message = f"Unmarked address 0x{self.cursor_pos:04X}"

    def _export_marked(self) -> None:
        entries = self.tracker.export_marked()
        if not entries:
            self.status_message = "No marked addresses to export"
            return
        export_data = []
        for addr, val in entries:
            entry: dict = {"address": f"0x{addr:04X}"}
            entry["value"] = f"0x{val:02X}" if val is not None else None
            export_data.append(entry)

        filename = f"marked_addresses_{datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%d_%H%M%S')}.json"
        filepath = os.path.join(os.getcwd(), filename)
        with open(filepath, "w") as f:
            json.dump(export_data, f, indent=2)
        self.status_message = f"Exported {len(entries)} marked addresses to {filename}"

    def _export_all(self) -> None:
        entries = self.tracker.export_all()
        if not entries:
            self.status_message = "No data to export"
            return
        export_data = []
        for addr, val in entries:
            export_data.append({"address": f"0x{addr:04X}", "value": f"0x{val:02X}"})

        filename = f"all_addresses_{datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%d_%H%M%S')}.json"
        filepath = os.path.join(os.getcwd(), filename)
        with open(filepath, "w") as f:
            json.dump(export_data, f, indent=2)
        self.status_message = f"Exported {len(entries)} addresses to {filename}"

    def _select_source(self, source_number: int) -> None:
        senders = self.tracker.known_senders()
        index = source_number - 1
        if 0 <= index < len(senders):
            new_source = senders[index]
            if new_source != self.selected_source:
                self.selected_source = new_source
                self.tracker.reset_for_new_source()
                self.cursor_pos = 0
                self.status_message = f"Selected source {source_number}: {self.selected_source}. Reset state for new source."
            else:
                self.status_message = f"Selected source {source_number}: {self.selected_source}"
        else:
            self.status_message = f"Source {source_number} is unavailable"

    def run(self, stop_event=None) -> None:
        can_raw_mode = sys.stdin.isatty() and termios is not None and tty is not None
        stdin_fd = sys.stdin.fileno() if can_raw_mode else None
        old_settings = termios.tcgetattr(stdin_fd) if stdin_fd is not None else None
        if stdin_fd is not None:
            tty.setcbreak(stdin_fd)

        try:
            self._effective_bpr = _auto_bytes_per_row(
                self._console.size.width, self.bytes_per_row
            )
            with Live(
                render_snapshot(
                    self.tracker,
                    self.bytes_per_row,
                    terminal_width=self._console.size.width,
                    selected_source=self.selected_source,
                    status_message=self.status_message,
                    cursor_pos=self.cursor_pos,
                    ascii_mode=self.ascii_mode,
                    bit_mode=self.bit_mode,
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
                        self._effective_bpr = _auto_bytes_per_row(
                            self._console.size.width, self.bytes_per_row
                        )
                        live.update(
                            render_snapshot(
                                self.tracker,
                                self.bytes_per_row,
                                terminal_width=self._console.size.width,
                                selected_source=self.selected_source,
                                status_message=self.status_message,
                                cursor_pos=self.cursor_pos,
                                ascii_mode=self.ascii_mode,
                                bit_mode=self.bit_mode,
                            )
                        )
                except KeyboardInterrupt:
                    pass
        finally:
            if stdin_fd is not None and old_settings is not None:
                termios.tcsetattr(stdin_fd, termios.TCSADRAIN, old_settings)
