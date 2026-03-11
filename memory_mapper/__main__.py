"""Entry point for the memory-mapper CLI tool."""

import argparse
import sys
import threading

if __package__ in (None, ""):
    from memory_mapper import __version__
    from memory_mapper.display import MemoryDisplay
    from memory_mapper.receiver import start_receiver
    from memory_mapper.tracker import MemoryTracker
else:
    from . import __version__
    from .display import MemoryDisplay
    from .receiver import start_receiver
    from .tracker import MemoryTracker

DEFAULT_MULTICAST_GROUP = "239.255.0.0"
DEFAULT_PORT = 2040
DEFAULT_HIGHLIGHT_DURATION = 3.0
DEFAULT_BYTES_PER_ROW = 16


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="memory-mapper",
        description=(
            "Listen for UDP multicast memory snapshots and display them in the "
            "terminal, highlighting recently-changed bytes."
        ),
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    parser.add_argument(
        "--group",
        default=DEFAULT_MULTICAST_GROUP,
        help=f"Multicast group address to join (default: {DEFAULT_MULTICAST_GROUP})",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help=f"UDP port to listen on (default: {DEFAULT_PORT})",
    )
    parser.add_argument(
        "--highlight-duration",
        type=float,
        default=DEFAULT_HIGHLIGHT_DURATION,
        metavar="SECONDS",
        help=(
            "How long (in seconds) to keep a changed byte highlighted "
            f"(default: {DEFAULT_HIGHLIGHT_DURATION})"
        ),
    )
    parser.add_argument(
        "--bytes-per-row",
        type=int,
        default=DEFAULT_BYTES_PER_ROW,
        metavar="N",
        help=(
            "Minimum number of bytes displayed per row; rows auto-expand when terminal "
            f"is wider (default: {DEFAULT_BYTES_PER_ROW})"
        ),
    )
    parser.add_argument(
        "--source-filter",
        default=None,
        help=(
            "Only process packets from this sender IP. By default, the first detected "
            "sender is selected."
        ),
    )
    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.highlight_duration <= 0:
        parser.error("--highlight-duration must be a positive number")
    if args.bytes_per_row < 1:
        parser.error("--bytes-per-row must be at least 1")
    if not (1 <= args.port <= 65535):
        parser.error("--port must be between 1 and 65535")

    tracker = MemoryTracker(highlight_duration=args.highlight_duration)

    def on_packet(data: bytes, sender: str) -> None:
        tracker.record_sender_only(sender)
        if display.selected_source is None:
            display.selected_source = sender
            display.status_message = f"Auto-selected source 1: {sender}"
        if sender != display.selected_source:
            return

        if len(data) >= 4:
            offset = int.from_bytes(data[:4], "big")
            tracker.update_chunk(offset, data[4:], sender=sender)
        else:
            tracker.update(data, sender=sender)

    stop_event = threading.Event()

    display = MemoryDisplay(
        tracker,
        bytes_per_row=args.bytes_per_row,
        selected_source=args.source_filter,
    )

    receiver_thread = start_receiver(args.group, args.port, on_packet, stop_event)

    try:
        display.run(stop_event=stop_event)
    except KeyboardInterrupt:
        pass
    finally:
        stop_event.set()
        receiver_thread.join(timeout=2.0)

    return 0


if __name__ == "__main__":
    sys.exit(main())
