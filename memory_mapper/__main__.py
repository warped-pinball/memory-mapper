"""Entry point for the memory-mapper CLI tool."""

import argparse
import ipaddress
import sys
import threading

if __package__ in (None, ""):
    from memory_mapper import __version__, vector
    from memory_mapper.display import MemoryDisplay
    from memory_mapper.receiver import start_receiver
    from memory_mapper.tracker import MemoryTracker
else:
    from . import __version__, vector
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
            "Listen for UDP memory snapshots from Warped Pinball Vector "
            "boards and display them in the terminal, highlighting "
            "recently-changed bytes. Machines are discovered in the "
            "background and their memory broadcast is enabled automatically."
        ),
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    parser.add_argument(
        "--machine",
        default=None,
        metavar="NAME_OR_IP",
        help=(
            "Vector machine to focus on, by LAN name (partial names work) or "
            "IP address. By default the first machine discovered on the "
            "network is used."
        ),
    )
    parser.add_argument(
        "--password",
        default=None,
        help=(
            "Vector password, used to enable the memory broadcast and to "
            f"write memory (falls back to ${vector.PASSWORD_ENV_VAR}; "
            "otherwise the app prompts when it's needed)."
        ),
    )
    parser.add_argument(
        "--frequency-ms",
        type=int,
        default=vector.DEFAULT_BROADCAST_FREQUENCY_MS,
        metavar="MS",
        help=(
            "How often (milliseconds) the machine broadcasts memory snapshots "
            f"(default: {vector.DEFAULT_BROADCAST_FREQUENCY_MS}, clamped to "
            f"{vector.BROADCAST_FREQUENCY_MIN_MS}-{vector.BROADCAST_FREQUENCY_MAX_MS})"
        ),
    )
    parser.add_argument(
        "--discover-timeout",
        type=float,
        default=vector.DEFAULT_DISCOVER_TIMEOUT,
        metavar="SECONDS",
        help=(
            "How long each background discovery round listens for answers "
            f"(default: {vector.DEFAULT_DISCOVER_TIMEOUT:g})"
        ),
    )
    parser.add_argument(
        "--listen-only",
        action="store_true",
        help=(
            "Never discover or control machines; just listen for broadcasts. "
            "Enable the broadcast toggle in the Vector web UI yourself; "
            "memory writes are unavailable."
        ),
    )
    parser.add_argument(
        "--keep-broadcasting",
        action="store_true",
        help="Leave the memory broadcast enabled on the machine when exiting.",
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
            "Only process packets from this sender IP. By default, the first "
            "detected sender is selected."
        ),
    )
    return parser


def _is_ip(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        return False


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.highlight_duration <= 0:
        parser.error("--highlight-duration must be a positive number")
    if args.bytes_per_row < 1:
        parser.error("--bytes-per-row must be at least 1")
    if not (1 <= args.port <= 65535):
        parser.error("--port must be between 1 and 65535")
    if args.frequency_ms < 1:
        parser.error("--frequency-ms must be a positive number")
    if args.discover_timeout <= 0:
        parser.error("--discover-timeout must be a positive number")

    manager = None
    if not args.listen_only:
        if vector.warpedpinball is None:
            print(
                "Warning: the 'warpedpinball' library is not installed; "
                "running in listen-only mode. Install it with: pip install "
                "warpedpinball",
                file=sys.stderr,
            )
        else:
            manager = vector.VectorManager(
                password=args.password,
                frequency_ms=args.frequency_ms,
                discover_timeout=args.discover_timeout,
                target=args.machine,
            )
            manager.start()

    tracker = MemoryTracker(highlight_duration=args.highlight_duration)

    def on_packet(data: bytes, sender: str) -> None:
        tracker.record_sender_only(sender)
        if display.selected_source is None:
            display.selected_source = sender
            tracker.reset_for_new_source()
            display.status_message = f"Auto-selected source 1: {sender} (fresh state)"
        if sender != display.selected_source:
            return

        if len(data) >= 4:
            offset = int.from_bytes(data[:4], "big")
            tracker.update_chunk(offset, data[4:], sender=sender)
        else:
            tracker.update(data, sender=sender)

    stop_event = threading.Event()

    selected_source = args.source_filter
    if selected_source is None and args.machine and _is_ip(args.machine):
        selected_source = args.machine

    writer = None
    if manager is not None:
        def writer(offset, values):
            ip = display.selected_source or manager.pick_target()
            if ip is None:
                raise RuntimeError("no Vector machine discovered yet")
            manager.write(ip, offset, values)

    display = MemoryDisplay(
        tracker,
        bytes_per_row=args.bytes_per_row,
        selected_source=selected_source,
        writer=writer,
        manager=manager,
    )

    receiver_thread = start_receiver(args.group, args.port, on_packet, stop_event)

    try:
        display.run(stop_event=stop_event)
    except KeyboardInterrupt:
        pass
    finally:
        stop_event.set()
        receiver_thread.join(timeout=2.0)
        if manager is not None:
            manager.shutdown(disable_broadcasts=not args.keep_broadcasting)

    return 0


if __name__ == "__main__":
    sys.exit(main())
