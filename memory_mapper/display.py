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
from typing import List, Optional, Tuple, Union

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
MARK_INDICATOR_STYLE = Style(color="bright_red", bold=True)

BYTES_PER_ROW = 16

ASCII_PRINTABLE_START = 32
ASCII_PRINTABLE_END = 127

# Enable/disable SGR mouse reporting (button presses only).
MOUSE_ENABLE = "\x1b[?1000h\x1b[?1006h"
MOUSE_DISABLE = "\x1b[?1006l\x1b[?1000l"

# How often (seconds) the display re-renders when there is no user input.
DATA_REFRESH_INTERVAL = 0.25
# How often (seconds) the loop polls for keyboard/mouse input.
INPUT_POLL_INTERVAL = 0.02


def _auto_bytes_per_row(
    terminal_width: Optional[int],
    preferred: int,
    overhead: int = 14,
) -> int:
    if terminal_width is None:
        return max(4, preferred)
    estimated = max(4, (terminal_width - overhead) // 3)
    aligned = max(4, estimated - (estimated % 4))
    return max(4, max(preferred, aligned))


def _render_menu(
    tracker: MemoryTracker,
    selected_source: Optional[str],
    status_message: str,
    ascii_mode: bool = False,
    bit_mode: bool = False,
    compact: bool = False,
):
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
        "[bold]Nav:[/bold] [cyan]←↑↓→[/cyan]/click move cursor  [cyan]Space[/cyan] mark  "
        "[cyan]E[/cyan] export marked  [cyan]X[/cyan] export all  [cyan]T[/cyan] ASCII "
        + ("[bright_green]ON[/bright_green]" if ascii_mode else "off")
        + f"  [cyan]B[/cyan] mode:{mode_label}"
        + "  [cyan]+/-[/cyan] highlight time  [cyan]Q[/cyan] quit"
    )
    view_commands = (
        "[bold]View:[/bold] [cyan]M[/cyan] menu  [cyan]K[/cyan] legend  "
        "[cyan]O[/cyan] offsets  [cyan]U[/cyan] cursor info  [cyan]V[/cyan] compact"
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
    menu.add_row(view_commands, "")
    menu.add_row(f"[bold]Status:[/bold] {status_message}", "")
    if compact:
        return menu
    return Panel(menu, border_style="bright_black", padding=(0, 1), title="[bold]Menu[/bold]")


def _render_legend(compact: bool = False):
    legend = Text()
    legend.append("Hard match ", style=HARD_MATCH_STYLE)
    legend.append("  Soft match (1 miss) ", style=SOFT_MATCH_1_STYLE)
    legend.append("  Soft match (2 misses) ", style=SOFT_MATCH_2_STYLE)
    legend.append("  Recently changed ", style=CHANGED_STYLE)
    legend.append("  Cursor ", style=CURSOR_STYLE)
    legend.append("  ")
    legend.append(">", style=MARK_INDICATOR_STYLE)
    legend.append(" Marked")
    if compact:
        return legend
    return Panel(legend, border_style="bright_black", title="[bold]Legend[/bold]")


def _render_status_bar(status_message: str) -> Text:
    """One-line replacement shown when the full menu is hidden."""
    return Text.from_markup(
        "[cyan]M[/cyan] menu  [cyan]Q[/cyan] quit  [bold]Status:[/bold] "
        + status_message
    )


def _byte_style(
    tracker: MemoryTracker,
    index: int,
    cursor_pos: Optional[int] = None,
    bit_mode: bool = False,
):
    if cursor_pos is not None and index == cursor_pos:
        return CURSOR_STYLE
    if bit_mode:
        # Use the best (highest) match level across the 8 bits of this byte.
        match_level = 0
        for bit_idx in range(8):
            level = tracker.bit_scan_match_level(index, bit_idx)
            if level > match_level:
                match_level = level
    else:
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
    bit_mode: bool = False,
    compact: bool = False,
):
    """Render the info panel showing current cursor address details and value history."""
    snapshot = tracker.snapshot
    if snapshot is None or not (0 <= cursor_pos < len(snapshot)):
        body = Text("No data at cursor position", style="dim")
        if compact:
            return body
        return Panel(
            body,
            border_style="bright_black",
            padding=(0, 1),
            title="[bold]Cursor[/bold]",
        )

    value = snapshot[cursor_pos]
    is_marked = cursor_pos in tracker.marked_addresses
    mark_label = " [bright_red bold]> MARKED[/bright_red bold]" if is_marked else ""
    ascii_ch = chr(value) if ASCII_PRINTABLE_START <= value < ASCII_PRINTABLE_END else "·"

    info_line = Text.from_markup(
        f"[bold]Addr:[/bold] 0x{cursor_pos:04X} ({cursor_pos}){mark_label}  "
        f"[bold]Hex:[/bold] 0x{value:02X}  "
        f"[bold]Dec:[/bold] {value}  "
        f"[bold]Bin:[/bold] "
    )
    for bit_idx in range(7, -1, -1):
        bit_val = (value >> bit_idx) & 1
        style = _bit_style(tracker, cursor_pos, bit_idx) if bit_mode else None
        info_line.append(str(bit_val), style=style)
    info_line.append_text(Text.from_markup(f"  [bold]ASCII:[/bold] {ascii_ch}"))

    parts = [info_line]

    history = tracker.get_value_history(cursor_pos)
    if history:
        history_markup = "[bold]History (last 10):[/bold] "
        hist_parts = []
        for ts, val in history[-10:]:
            age = time.monotonic() - ts
            if age < 60:
                age_str = f"{age:.1f}s ago"
            else:
                age_str = f"{age / 60:.1f}m ago"
            hist_parts.append(f"0x{val:02X}({age_str})")
        history_markup += "  ".join(hist_parts)
        parts.append(Text.from_markup(history_markup))

    body = Group(*parts)
    if compact:
        return body
    return Panel(
        body,
        border_style="bright_black",
        padding=(0, 1),
        title="[bold]Cursor[/bold]",
    )


def render_snapshot(
    tracker: MemoryTracker,
    bytes_per_row: int = BYTES_PER_ROW,
    terminal_width: Optional[int] = None,
    selected_source: Optional[str] = None,
    status_message: str = "Ready",
    cursor_pos: Optional[int] = None,
    ascii_mode: bool = False,
    bit_mode: bool = False,
    show_offsets: bool = True,
    show_legend: bool = True,
    show_menu: bool = True,
    show_cursor_info: bool = True,
    compact: bool = False,
):
    snapshot = tracker.snapshot

    title = (
        "[bold bright_blue]Memory Mapper"
        + (" (Bit Mode)" if bit_mode else "")
        + "[/bold bright_blue]"
    )

    if snapshot is None:
        body = Text("Waiting for data…", style=DIM_STYLE, justify="center")
        memory_panel = Panel(body, title=title, border_style="bright_blue")
        parts: list = [memory_panel]
        if show_legend:
            parts.append(_render_legend(compact=compact))
        if show_menu:
            parts.append(
                _render_menu(
                    tracker, selected_source, status_message,
                    ascii_mode=ascii_mode, bit_mode=bit_mode, compact=compact,
                )
            )
        else:
            parts.append(_render_status_bar(status_message))
        return Group(*parts)

    tracker.cleanup_old_changes()

    overhead = _layout_overhead(show_offsets=show_offsets, compact=compact)
    effective_bpr = _auto_bytes_per_row(terminal_width, bytes_per_row, overhead)
    size = len(snapshot)

    memory_text = Text()
    if show_offsets:
        # Header: "Off " is 4 chars to align with 4-char row offsets like "0000"
        header = Text(" Off", style="bright_cyan")
        for col in range(effective_bpr):
            header.append(" ")
            header.append(f"{col:02X}", style=HEADER_STYLE)
        memory_text.append(header)

    first_row = True
    for row_start in range(0, size, effective_bpr):
        row_bytes = snapshot[row_start : row_start + effective_bpr]
        if show_offsets or not first_row:
            line = Text("\n")
        else:
            line = Text()
        first_row = False
        if show_offsets:
            line.append(f"{row_start:04X}", style="bright_cyan")
        for col, byte_val in enumerate(row_bytes):
            addr = row_start + col
            is_marked = addr in tracker.marked_addresses
            if is_marked:
                line.append(">", style=MARK_INDICATOR_STYLE)
            else:
                line.append(" ")
            if ascii_mode:
                ch = chr(byte_val) if ASCII_PRINTABLE_START <= byte_val < ASCII_PRINTABLE_END else "·"
                line.append(
                    f" {ch}",
                    style=_byte_style(tracker, addr, cursor_pos, bit_mode=bit_mode),
                )
            else:
                line.append(
                    f"{byte_val:02X}",
                    style=_byte_style(tracker, addr, cursor_pos, bit_mode=bit_mode),
                )
        memory_text.append(line)

    marked_count = len(tracker.marked_addresses)
    status = (
        f"[dim]Packets[/dim] [bold]{tracker.packet_count}[/bold]  "
        f"[dim]Size[/dim] [bold]{size}B[/bold]  "
        f"[dim]Highlight[/dim] [bold]{tracker.highlight_duration:.1f}s[/bold]  "
        f"[dim]Marked[/dim] [bold]{marked_count}[/bold]"
    )

    if compact:
        memory_renderable: Union[Text, Panel] = memory_text
    else:
        memory_renderable = Panel(
            memory_text,
            title=title,
            subtitle=status,
            border_style="bright_blue",
            expand=True,
        )

    panels: list = [memory_renderable]
    if show_cursor_info and cursor_pos is not None:
        panels.append(
            _render_cursor_info(tracker, cursor_pos, bit_mode=bit_mode, compact=compact)
        )
    if show_legend:
        panels.append(_render_legend(compact=compact))
    if show_menu:
        panels.append(
            _render_menu(
                tracker, selected_source, status_message,
                ascii_mode=ascii_mode, bit_mode=bit_mode, compact=compact,
            )
        )
    else:
        panels.append(_render_status_bar(status_message))
    return Group(*panels)


def _layout_overhead(show_offsets: bool, compact: bool) -> int:
    """Horizontal characters consumed by borders/padding/offset column."""
    overhead = 0
    if not compact:
        overhead += 4  # panel borders + padding on both sides
    if show_offsets:
        overhead += 4  # "0000" row-offset column
    return max(overhead, 2)


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
        self.show_offsets: bool = True
        self.show_legend: bool = True
        self.show_menu: bool = True
        self.show_cursor_info: bool = True
        self.compact: bool = False
        self._effective_bpr: int = bytes_per_row

    def _capture_keypress(self) -> Optional[str]:
        """Read a single pending key event (legacy single-event reader)."""
        events = self._poll_events(0)
        for event in events:
            if event[0] == "KEY":
                return event[1]
        return None

    def _poll_events(self, timeout: float) -> List[Tuple]:
        """Return pending input events as ("KEY", key) or ("MOUSE", x, y) tuples.

        Waits up to *timeout* seconds for the first byte, then drains
        everything that is immediately available so held-down keys don't
        queue up behind renders.
        """
        if not sys.stdin.isatty():
            if timeout:
                time.sleep(timeout)
            return []
        if msvcrt is not None:
            deadline = time.monotonic() + timeout
            while not msvcrt.kbhit():
                if time.monotonic() >= deadline:
                    return []
                time.sleep(0.005)
            events: List[Tuple] = []
            while msvcrt.kbhit():
                ch = msvcrt.getwch()
                if ch in ("\x00", "\xe0"):
                    ch2 = msvcrt.getwch()
                    arrow_map = {"H": "UP", "P": "DOWN", "K": "LEFT", "M": "RIGHT"}
                    events.append(("KEY", arrow_map.get(ch2, ch2)))
                else:
                    events.append(("KEY", ch))
            return events

        fd = sys.stdin.fileno()
        try:
            readable, _, _ = select.select([fd], [], [], timeout)
        except (OSError, ValueError):
            return []
        if not readable:
            return []

        data = b""
        while True:
            try:
                chunk = os.read(fd, 256)
            except OSError:
                break
            if not chunk:
                break
            data += chunk
            try:
                more, _, _ = select.select([fd], [], [], 0)
            except (OSError, ValueError):
                break
            if not more:
                break
        return self._parse_input(data.decode("utf-8", errors="replace"), fd)

    def _parse_input(self, buf: str, fd: Optional[int]) -> List[Tuple]:
        events: List[Tuple] = []
        i = 0
        n = len(buf)
        arrow_map = {"A": "UP", "B": "DOWN", "C": "RIGHT", "D": "LEFT"}
        while i < n:
            ch = buf[i]
            if ch != "\x1b":
                events.append(("KEY", ch))
                i += 1
                continue
            # Escape sequence. A bare trailing ESC may be a partial sequence;
            # give the terminal a moment to deliver the rest.
            if i + 1 >= n and fd is not None:
                try:
                    readable, _, _ = select.select([fd], [], [], 0.02)
                    if readable:
                        buf += os.read(fd, 64).decode("utf-8", errors="replace")
                        n = len(buf)
                except (OSError, ValueError):
                    pass
            if i + 1 >= n or buf[i + 1] != "[":
                events.append(("KEY", ch))
                i += 1
                continue
            j = i + 2
            if j < n and buf[j] == "<":
                # SGR mouse event: ESC [ < btn ; x ; y (M|m)
                k = j + 1
                while k < n and buf[k] not in ("M", "m"):
                    k += 1
                if k < n:
                    try:
                        btn_s, x_s, y_s = buf[j + 1 : k].split(";")
                        if buf[k] == "M" and int(btn_s) in (0, 1, 2):
                            events.append(("MOUSE", int(x_s), int(y_s)))
                    except ValueError:
                        pass
                    i = k + 1
                    continue
                i = n
                continue
            if j < n and buf[j] in arrow_map:
                events.append(("KEY", arrow_map[buf[j]]))
                i = j + 1
                continue
            # Unknown CSI sequence: skip to its final byte (0x40-0x7E).
            k = j
            while k < n and not ("\x40" <= buf[k] <= "\x7e"):
                k += 1
            i = min(k + 1, n)
        return events

    def _handle_mouse(self, x: int, y: int) -> None:
        """Move the cursor to the memory byte under a mouse click, if any."""
        snapshot = self.tracker.snapshot
        if not snapshot:
            return
        bpr = self._effective_bpr
        # Rows: panel top border (unless compact), then optional header line,
        # then one line per memory row.
        content_top = 1 if self.compact else 2
        row = y - content_top - (1 if self.show_offsets else 0)
        if row < 0:
            return
        # Columns: panel border + padding (unless compact), then the 4-char
        # row-offset column, then 3 characters per byte (mark spacer + 2).
        left_pad = 0 if self.compact else 2
        rel = x - 1 - left_pad - (4 if self.show_offsets else 0)
        if rel < 0:
            return
        col = rel // 3
        if col >= bpr:
            return
        addr = row * bpr + col
        if addr >= len(snapshot):
            return
        self.cursor_pos = addr
        self.status_message = f"Cursor moved to 0x{addr:04X}"

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
        if k == "m":
            self.show_menu = not self.show_menu
            self.status_message = f"Menu {'shown' if self.show_menu else 'hidden'}"
            return
        if k == "k":
            self.show_legend = not self.show_legend
            self.status_message = f"Legend {'shown' if self.show_legend else 'hidden'}"
            return
        if k == "o":
            self.show_offsets = not self.show_offsets
            self.status_message = f"Offsets {'shown' if self.show_offsets else 'hidden'}"
            return
        if k == "u":
            self.show_cursor_info = not self.show_cursor_info
            self.status_message = (
                f"Cursor info {'shown' if self.show_cursor_info else 'hidden'}"
            )
            return
        if k == "v":
            self.compact = not self.compact
            self.status_message = f"Compact view {'enabled' if self.compact else 'disabled'}"
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

    def _render(self):
        return render_snapshot(
            self.tracker,
            self.bytes_per_row,
            terminal_width=self._console.size.width,
            selected_source=self.selected_source,
            status_message=self.status_message,
            cursor_pos=self.cursor_pos,
            ascii_mode=self.ascii_mode,
            bit_mode=self.bit_mode,
            show_offsets=self.show_offsets,
            show_legend=self.show_legend,
            show_menu=self.show_menu,
            show_cursor_info=self.show_cursor_info,
            compact=self.compact,
        )

    def _update_effective_bpr(self) -> None:
        self._effective_bpr = _auto_bytes_per_row(
            self._console.size.width,
            self.bytes_per_row,
            _layout_overhead(show_offsets=self.show_offsets, compact=self.compact),
        )

    def run(self, stop_event=None) -> None:
        can_raw_mode = sys.stdin.isatty() and termios is not None and tty is not None
        stdin_fd = sys.stdin.fileno() if can_raw_mode else None
        old_settings = termios.tcgetattr(stdin_fd) if stdin_fd is not None else None
        if stdin_fd is not None:
            tty.setcbreak(stdin_fd)
            sys.stdout.write(MOUSE_ENABLE)
            sys.stdout.flush()

        try:
            self._update_effective_bpr()
            with Live(
                self._render(),
                console=self._console,
                auto_refresh=False,
                screen=True,
            ) as live:
                try:
                    data_interval = min(DATA_REFRESH_INTERVAL, 1.0 / self.refresh_per_second)
                    last_render = time.monotonic()
                    last_packet_count = self.tracker.packet_count
                    while stop_event is None or not stop_event.is_set():
                        events = self._poll_events(INPUT_POLL_INTERVAL)
                        dirty = False
                        for event in events:
                            if event[0] == "KEY":
                                self._handle_input(event[1], stop_event)
                            elif event[0] == "MOUSE":
                                self._handle_mouse(event[1], event[2])
                            dirty = True
                        if stop_event is not None and stop_event.is_set():
                            break

                        now = time.monotonic()
                        data_due = now - last_render >= data_interval
                        if not (dirty or data_due):
                            continue
                        if not dirty and self.tracker.packet_count == last_packet_count:
                            # No input and no new packets: only re-render if
                            # change highlights may still be fading.
                            if not self.tracker.change_times:
                                last_render = now
                                continue
                        self._update_effective_bpr()
                        live.update(self._render(), refresh=True)
                        last_render = time.monotonic()
                        last_packet_count = self.tracker.packet_count
                except KeyboardInterrupt:
                    pass
        finally:
            if stdin_fd is not None:
                sys.stdout.write(MOUSE_DISABLE)
                sys.stdout.flush()
            if stdin_fd is not None and old_settings is not None:
                termios.tcsetattr(stdin_fd, termios.TCSADRAIN, old_settings)
