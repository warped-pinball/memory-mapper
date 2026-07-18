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
from typing import Callable, List, Optional, Sequence, Tuple, Union

from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.style import Style
from rich.table import Table
from rich.text import Text

from . import __version__, vector
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

WRITE_WARNING = (
    "Caution, writing values to memory can have unexpected or harmful "
    "effects, do so with caution."
)

# How long (seconds) to wait for packets before auto-opening the password
# prompt — a machine that is already broadcasting answers well within this.
PASSWORD_PROMPT_GRACE = 2.0

# How often (seconds) the display re-renders when there is no user input.
DATA_REFRESH_INTERVAL = 0.25
# How often (seconds) the loop polls for keyboard input.
INPUT_POLL_INTERVAL = 0.02


def parse_write_values(text: str) -> List[int]:
    """Parse user input into byte values.

    Accepts whitespace-separated tokens, each decimal ("5") or 0x-prefixed
    hex ("0x2F"). Raises ValueError for anything unparseable or out of the
    0-255 byte range, and for empty input.
    """
    tokens = text.split()
    if not tokens:
        raise ValueError("no value entered")
    values: List[int] = []
    for token in tokens:
        try:
            value = int(token, 0)
        except ValueError:
            raise ValueError(
                f"invalid value {token!r} (use decimal like 5 or hex like 0x2F)"
            ) from None
        if not (0 <= value <= 255):
            raise ValueError(f"value {token!r} is outside the byte range 0-255")
        values.append(value)
    return values


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


def _source_label(ip: str, source_names) -> str:
    name = source_names.get(ip) if source_names else None
    return f"{name}({ip})" if name else ip


# Per-source streaming state -> (menu label color, waiting-screen wording).
SOURCE_STATUS_STYLES = {
    "streaming": ("bright_green", "streaming"),
    "starting": ("yellow", "starting…"),
    "failed": ("bright_red", "failed — check password (P)"),
    "off": ("dim", "not streaming"),
}


def _styled_source_label(source: str, source_names, source_status) -> str:
    """Markup for one source, colored by its streaming status."""
    label = _source_label(source, source_names)
    status = (source_status or {}).get(source)
    color = SOURCE_STATUS_STYLES.get(status, (None, None))[0]
    return f"[{color}]{label}[/{color}]" if color else label


def _source_list_lines(source_names, sources, source_status, selected_source):
    """Markup lines listing discovered machines and their streaming status."""
    lines = ["[bold]Vector machines on your network:[/bold]"]
    for idx, ip in enumerate(sources[:9], start=1):
        name = (source_names or {}).get(ip)
        status = (source_status or {}).get(ip, "off")
        color, wording = SOURCE_STATUS_STYLES.get(status, ("dim", status or "unknown"))
        machine = f"{name}  {ip}" if name else ip
        marker = " [bold]*[/bold]" if ip == selected_source else ""
        lines.append(
            f"[bright_blue]{idx}[/bright_blue]  {machine}  "
            f"[{color}]● {wording}[/{color}]{marker}"
        )
    lines.append("")
    lines.append(
        "[grey50]Press a machine's number to start streaming from it[/grey50]"
    )
    return lines


def _render_sources_panel(
    tracker: MemoryTracker,
    selected_source: Optional[str] = None,
    source_names=None,
    sources=None,
    source_status=None,
    compact: bool = False,
):
    """The source list shown in place of the memory panel (toggled with S)."""
    listed = sources if sources is not None else tracker.known_senders()
    if listed:
        body = Text.from_markup(
            "\n".join(
                _source_list_lines(
                    source_names, listed, source_status, selected_source
                )
            ),
            justify="center",
        )
    else:
        body = Text(
            "Searching the network for Vector machines…",
            style=DIM_STYLE,
            justify="center",
        )
    if compact:
        return body
    return Panel(
        body,
        title="[bold bright_blue]Sources[/bold bright_blue]",
        border_style="bright_blue",
        expand=True,
    )


def _render_menu(
    tracker: MemoryTracker,
    selected_source: Optional[str],
    status_message: str,
    ascii_mode: bool = False,
    bit_mode: bool = False,
    compact: bool = False,
    source_names=None,
    sources=None,
    source_status=None,
):
    senders = tracker.known_senders()
    listed_sources = sources if sources is not None else senders
    stats = tracker.bit_scan_stats() if bit_mode else tracker.scan_stats()

    menu = Table.grid(expand=True)
    menu.add_column(ratio=5)
    menu.add_column(ratio=3)

    if source_status is None:
        # No manager: everything listed is an active sender.
        source_status = {ip: "streaming" for ip in senders}

    sender_bits = ["[bold]Sources:[/bold]"]
    for idx, source in enumerate(listed_sources[:9], start=1):
        label = _styled_source_label(source, source_names, source_status)
        if source == selected_source:
            sender_bits.append(
                f"[bold bright_blue]{idx}[/bold bright_blue]:{label}[bold]*[/bold]"
            )
        else:
            sender_bits.append(f"[bright_blue]{idx}[/bright_blue]:{label}")

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
        "[cyan]W[/cyan] write  [cyan]P[/cyan] password  "
        "[cyan]E[/cyan] export marked  [cyan]X[/cyan] export all  [cyan]T[/cyan] ASCII "
        + ("[bright_green]ON[/bright_green]" if ascii_mode else "off")
        + f"  [cyan]B[/cyan] mode:{mode_label}"
        + "  [cyan]+/-[/cyan] highlight time  [cyan]Q[/cyan] quit"
    )
    view_commands = (
        "[bold]View:[/bold] [cyan]M[/cyan] menu  [cyan]K[/cyan] legend  "
        "[cyan]O[/cyan] offsets  [cyan]U[/cyan] cursor info  [cyan]V[/cyan] compact  "
        "[cyan]S[/cyan] sources  [cyan]?[/cyan] about"
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


def _render_about(compact: bool = False):
    """Render the About panel showing tool and library versions."""
    lib_version = vector.library_version()
    lib_line = lib_version if lib_version else "not installed"

    body = Text()
    body.append("Memory Mapper\n", style="bold bright_cyan")
    body.append("Live memory snapshot viewer for Warped Pinball Vector boards.\n\n")
    body.append("memory-mapper version:  ", style="bold")
    body.append(f"{__version__}\n")
    body.append("warpedpinball library:  ", style="bold")
    body.append(f"{lib_line}\n")
    body.append("\nPress ", style="dim")
    body.append("?", style="cyan")
    body.append(" or ", style="dim")
    body.append("Esc", style="cyan")
    body.append(" to close.", style="dim")

    if compact:
        return body
    return Panel(
        body,
        border_style="bright_cyan",
        padding=(0, 1),
        title="[bold]About[/bold]",
    )


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
    show_sources: bool = False,
    compact: bool = False,
    source_names=None,
    sources=None,
    source_status=None,
):
    snapshot = tracker.snapshot

    title = (
        "[bold bright_blue]Memory Mapper"
        + (" (Bit Mode)" if bit_mode else "")
        + "[/bold bright_blue]"
    )

    # The sources view replaces the memory panel entirely, whether or not data
    # is streaming, so the menu/legend still frame it below.
    if show_sources:
        parts: list = [
            _render_sources_panel(
                tracker,
                selected_source=selected_source,
                source_names=source_names,
                sources=sources,
                source_status=source_status,
                compact=compact,
            )
        ]
        if show_legend:
            parts.append(_render_legend(compact=compact))
        if show_menu:
            parts.append(
                _render_menu(
                    tracker, selected_source, status_message,
                    ascii_mode=ascii_mode, bit_mode=bit_mode, compact=compact,
                    source_names=source_names, sources=sources,
                    source_status=source_status,
                )
            )
        else:
            parts.append(_render_status_bar(status_message))
        return Group(*parts)

    if snapshot is None:
        listed = sources if sources is not None else tracker.known_senders()
        if listed:
            lines = ["[grey50]Waiting for data…[/grey50]", ""]
            lines += _source_list_lines(
                source_names, listed, source_status, selected_source
            )
            body = Text.from_markup("\n".join(lines), justify="center")
        else:
            body = Text(
                "Waiting for data…\nSearching the network for Vector machines…",
                style=DIM_STYLE,
                justify="center",
            )
        memory_panel = Panel(body, title=title, border_style="bright_blue")
        parts: list = [memory_panel]
        if show_legend:
            parts.append(_render_legend(compact=compact))
        if show_menu:
            parts.append(
                _render_menu(
                    tracker, selected_source, status_message,
                    ascii_mode=ascii_mode, bit_mode=bit_mode, compact=compact,
                    source_names=source_names, sources=sources,
                    source_status=source_status,
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
                source_names=source_names, sources=sources,
                source_status=source_status,
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
        writer: Optional[Callable[[int, Sequence[int]], None]] = None,
        machine_label: Optional[str] = None,
        manager=None,
    ) -> None:
        self.tracker = tracker
        self.bytes_per_row = bytes_per_row
        self.refresh_per_second = refresh_per_second
        self.selected_source = selected_source
        self.writer = writer
        self.machine_label = machine_label
        self.manager = manager
        # Write flow state: None (inactive), "value" (typing values), or
        # "confirm" (waiting on the safety confirmation).
        self.write_stage: Optional[str] = None
        self.write_buffer: str = ""
        self.write_addr: int = 0
        self.write_values: List[int] = []
        # Password prompt state (opened when an authenticated action needs it).
        self.password_stage: Optional[str] = None
        self.password_buffer: str = ""
        self._password_next: Optional[str] = None  # "write" to resume a write
        self._password_declined: bool = False
        self._password_prompted: bool = False
        self._last_tick: float = 0.0
        self._no_data_since: float = time.monotonic()
        self._console = Console()
        self.status_message = "Waiting for first snapshot"
        self.cursor_pos: int = 0
        self.ascii_mode: bool = False
        self.bit_mode: bool = False
        self.show_offsets: bool = True
        self.show_legend: bool = True
        self.show_menu: bool = True
        self.show_cursor_info: bool = True
        self.show_about: bool = False
        self.show_sources: bool = False
        self.compact: bool = False
        self._effective_bpr: int = bytes_per_row

    def _poll_events(self, timeout: float) -> List[Tuple]:
        """Return pending input events as ("KEY", key) tuples.

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

    def _handle_input(self, key: str, stop_event) -> None:
        if not key:
            return

        if self.password_stage is not None:
            self._handle_password_key(key)
            return
        if self.write_stage is not None:
            self._handle_write_key(key)
            return

        # The About overlay is modal-ish: Esc closes it while it's open.
        if self.show_about and key == "\x1b":
            self.show_about = False
            self.status_message = "About hidden"
            return
        if key == "?":
            self.show_about = not self.show_about
            self.status_message = f"About {'shown' if self.show_about else 'hidden'}"
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
        if k == "w":
            self._begin_write()
            return
        if k == "p" and self.manager is not None:
            self._open_password_prompt()
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
        # "S" swaps the memory panel for the source list. In bit mode "s" is the
        # set(=1) scan filter, so the sources toggle yields to it there.
        if k == "s" and not self.bit_mode:
            self.show_sources = not self.show_sources
            self.status_message = f"Sources {'shown' if self.show_sources else 'hidden'}"
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

    def _begin_write(self) -> None:
        if self.writer is None:
            self.status_message = (
                "Writes unavailable: not connected to a Vector machine "
                "(start without --listen-only and provide a password)"
            )
            return
        snapshot = self.tracker.snapshot
        if snapshot is None or not (0 <= self.cursor_pos < len(snapshot)):
            self.status_message = "No data at cursor position"
            return
        if self.manager is not None and not self.manager.has_password():
            self._password_next = "write"
            self._open_password_prompt()
            return
        self.write_stage = "value"
        self.write_addr = self.cursor_pos
        self.write_buffer = ""
        self.write_values = []
        self.status_message = f"Writing to 0x{self.write_addr:04X} — enter value(s)"

    def _current_machine_label(self) -> str:
        if self.manager is not None:
            ip = self.selected_source or self.manager.pick_target()
            if ip:
                return self.manager.label(ip)
        return self.machine_label or self.selected_source or "(unknown machine)"

    def _open_password_prompt(self) -> None:
        self.password_stage = "input"
        self.password_buffer = ""
        self.status_message = "Enter the Vector password (Esc to skip)"

    def _handle_password_key(self, key: str) -> None:
        if key == "\x1b":
            self.password_stage = None
            self.password_buffer = ""
            self._password_declined = True
            self._password_next = None
            self.status_message = (
                "Password entry skipped — press P to enter it later"
            )
            return
        if key in ("\r", "\n"):
            # Submit whatever was typed — an empty password is valid, so Enter
            # always confirms. Skipping is done with Esc (handled above).
            password = self.password_buffer
            self.password_stage = None
            self.password_buffer = ""
            self._password_declined = False
            if self.manager is not None:
                self.manager.set_password(password)
            if self._password_next == "write":
                self._password_next = None
                self._begin_write()
            else:
                if self.manager is not None:
                    target = self.selected_source or self.manager.pick_target()
                    if target is not None:
                        self.manager.request_enable(target)
                self.status_message = "Password set"
            return
        if key in ("\x7f", "\x08"):
            self.password_buffer = self.password_buffer[:-1]
            return
        if len(key) == 1 and key.isprintable():
            self.password_buffer += key

    def _render_password_panel(self):
        masked = "•" * len(self.password_buffer)
        body = Text.from_markup(
            f"[bold]Machine:[/bold] {self._current_machine_label()}\n"
            "The Vector password is needed to enable the memory broadcast "
            "and to write memory.\n"
            f"[bold]Password:[/bold] {masked}[blink]▏[/blink]\n"
            "[dim]Enter to submit, Esc to skip[/dim]"
        )
        return Panel(
            body,
            border_style="yellow",
            padding=(0, 1),
            title="[bold yellow]Vector Password[/bold yellow]",
        )

    def _background_tick(self) -> bool:
        """Drain manager notices and drive the auto-enable flow.

        Returns True when the UI needs a re-render.
        """
        if self.manager is None:
            return False
        now = time.monotonic()
        if now - self._last_tick < 0.25:
            return False
        self._last_tick = now

        changed = False
        for notice in self.manager.pop_notices():
            self.status_message = notice
            changed = True

        # Until data flows, keep nudging the target machine to broadcast.
        if (
            self.tracker.packet_count == 0
            and self.password_stage is None
            and self.write_stage is None
        ):
            target = self.selected_source or self.manager.pick_target()
            if target is not None and self.manager.machine_name(target) is not None:
                if self.manager.has_password():
                    self.manager.request_enable(target)
                elif (
                    not self._password_declined
                    and not self._password_prompted
                    and now - self._no_data_since >= PASSWORD_PROMPT_GRACE
                ):
                    self._password_prompted = True
                    self._open_password_prompt()
                    changed = True
        return changed

    def _handle_write_key(self, key: str) -> None:
        if key == "\x1b":  # ESC cancels at either stage
            self._cancel_write()
            return
        if self.write_stage == "value":
            if key in ("\r", "\n"):
                try:
                    self.write_values = parse_write_values(self.write_buffer)
                except ValueError as exc:
                    self.status_message = f"Invalid input: {exc}"
                    return
                self.write_stage = "confirm"
                self.status_message = "Confirm write: press Y to write, any other key to cancel"
                return
            if key in ("\x7f", "\x08"):  # backspace
                self.write_buffer = self.write_buffer[:-1]
                return
            if len(key) == 1 and (key.isalnum() or key == " "):
                self.write_buffer += key
            return
        if self.write_stage == "confirm":
            if key.lower() == "y":
                self._perform_write()
            else:
                self._cancel_write()

    def _cancel_write(self) -> None:
        self.write_stage = None
        self.write_buffer = ""
        self.write_values = []
        self.status_message = "Write cancelled"

    def _perform_write(self) -> None:
        addr, values = self.write_addr, self.write_values
        self.write_stage = None
        self.write_buffer = ""
        self.write_values = []
        try:
            self.writer(addr, values)
        except Exception as exc:  # surface device/auth errors in the status bar
            self.status_message = f"Write failed: {exc}"
        else:
            self.status_message = (
                f"Wrote {len(values)} byte{'s' if len(values) != 1 else ''} "
                f"at 0x{addr:04X}"
            )

    def _render_write_panel(self):
        machine = self._current_machine_label()
        if self.write_stage == "value":
            body = Text.from_markup(
                f"[bold]Machine:[/bold] {machine}  "
                f"[bold]Address:[/bold] 0x{self.write_addr:04X} ({self.write_addr})\n"
                "Enter byte value(s), separated by spaces — decimal like 5 "
                "or hex like 0x2F. Multiple values write to consecutive "
                "addresses.\n"
                f"[bold]Value(s):[/bold] {self.write_buffer}[blink]▏[/blink]\n"
                "[dim]Enter to continue, Esc to cancel[/dim]"
            )
            return Panel(
                body,
                border_style="yellow",
                padding=(0, 1),
                title="[bold yellow]Write Memory[/bold yellow]",
            )

        snapshot = self.tracker.snapshot
        current_parts = []
        for i in range(len(self.write_values)):
            addr = self.write_addr + i
            if snapshot is not None and 0 <= addr < len(snapshot):
                current_parts.append(f"0x{snapshot[addr]:02X}")
            else:
                current_parts.append("--")
        new_parts = [f"0x{v:02X}" for v in self.write_values]
        body = Text.from_markup(
            f"[bold red]⚠ {WRITE_WARNING}[/bold red]\n"
            f"[bold]Machine:[/bold] {machine}\n"
            f"[bold]Address:[/bold] 0x{self.write_addr:04X} ({self.write_addr})  "
            f"[bold]Bytes:[/bold] {len(self.write_values)}\n"
            f"[bold]Current value(s):[/bold] {' '.join(current_parts)}\n"
            f"[bold]New value(s):[/bold] {' '.join(new_parts)}\n"
            "[bold]Press Y to confirm the write, any other key to cancel.[/bold]"
        )
        return Panel(
            body,
            border_style="red",
            padding=(0, 1),
            title="[bold red]Confirm Memory Write[/bold red]",
        )

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

    def _source_list(self) -> List[str]:
        """Active senders first, then discovered machines that are silent."""
        sources = self.tracker.known_senders()
        if self.manager is not None:
            for ip in self.manager.machines():
                if ip not in sources:
                    sources.append(ip)
        return sources

    def _source_statuses(self, sources: List[str]) -> dict:
        """Streaming status per source, for color-coding the UI."""
        statuses = {}
        for ip in sources:
            if ip in self.tracker.sender_packet_counts:
                statuses[ip] = "streaming"
                continue
            state = (
                self.manager.enable_status(ip)
                if self.manager is not None
                else None
            )
            if state in ("pending", "enabled"):
                # Asked (or already told) to stream, no packets yet.
                statuses[ip] = "starting"
            elif state == "failed":
                statuses[ip] = "failed"
            else:
                statuses[ip] = "off"
        return statuses

    def _select_source(self, source_number: int) -> None:
        sources = self._source_list()
        index = source_number - 1
        if not (0 <= index < len(sources)):
            self.status_message = f"Source {source_number} is unavailable"
            return
        new_source = sources[index]
        was_selected = new_source == self.selected_source
        label = (
            self.manager.label(new_source)
            if self.manager is not None
            else new_source
        )
        if new_source != self.selected_source:
            self.selected_source = new_source
            self.tracker.reset_for_new_source()
            self.cursor_pos = 0
            self._no_data_since = time.monotonic()
            self.status_message = (
                f"Selected source {source_number}: {label}. Reset state for new source."
            )
        else:
            self.status_message = f"Selected source {source_number}: {label}"

        # A discovered machine we've never heard a packet from isn't
        # streaming yet — ask it to start (which needs the password).
        # Re-selecting the current machine also re-sends the request, as a
        # way to restart a stream that stopped (e.g. after a reboot).
        needs_broadcast = (
            self.manager is not None
            and self.manager.machine_name(new_source) is not None
            and (
                new_source not in self.tracker.sender_packet_counts
                or was_selected
            )
        )
        if needs_broadcast:
            if self.manager.has_password():
                self.manager.request_enable(new_source, force=True)
                self.status_message = f"Requesting memory broadcast from {label}…"
            else:
                self._open_password_prompt()

    def _render(self):
        base = render_snapshot(
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
            show_sources=self.show_sources,
            compact=self.compact,
            source_names=self.manager.machines() if self.manager else None,
            sources=self._source_list() if self.manager else None,
            source_status=(
                self._source_statuses(self._source_list())
                if self.manager
                else None
            ),
        )
        if self.show_about:
            return Group(base, _render_about(compact=self.compact))
        if self.password_stage is not None:
            return Group(base, self._render_password_panel())
        if self.write_stage is not None:
            return Group(base, self._render_write_panel())
        return base

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
                            dirty = True
                        if stop_event is not None and stop_event.is_set():
                            break
                        if self._background_tick():
                            dirty = True

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
            if stdin_fd is not None and old_settings is not None:
                termios.tcsetattr(stdin_fd, termios.TCSADRAIN, old_settings)
