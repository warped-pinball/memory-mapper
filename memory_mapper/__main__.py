"""Entry point for the memory-mapper CLI tool."""

import argparse
import getpass
import os
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
PASSWORD_ENV_VAR = "VECTOR_PASSWORD"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="memory-mapper",
        description=(
            "Discover Warped Pinball Vector boards, enable their memory "
            "broadcast, and display the live memory snapshots in the "
            "terminal, highlighting recently-changed bytes."
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
            "Vector machine to connect to, by LAN name (partial names work) or "
            "IP address. By default machines are discovered on the network and "
            "you pick one."
        ),
    )
    parser.add_argument(
        "--password",
        default=None,
        help=(
            "Vector password, used to enable the memory broadcast and to write "
            f"memory (falls back to ${PASSWORD_ENV_VAR}, then an interactive "
            "prompt)."
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
            "How long to wait for machines to answer discovery "
            f"(default: {vector.DEFAULT_DISCOVER_TIMEOUT:g})"
        ),
    )
    parser.add_argument(
        "--listen-only",
        action="store_true",
        help=(
            "Skip discovery and machine control entirely; just listen for "
            "broadcasts (the pre-1.4 behavior). Enable the broadcast toggle in "
            "the Vector web UI yourself; memory writes are unavailable."
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
            "Only process packets from this sender IP. Defaults to the "
            "connected machine's IP, or the first detected sender in "
            "listen-only mode."
        ),
    )
    return parser


def _choose_machine(machines):
    """Interactively pick one machine from a discovery result."""
    print("Discovered Vector machines:")
    for idx, machine in enumerate(machines, start=1):
        print(f"  {idx}. {machine.name} ({machine.ip})")
    while True:
        try:
            answer = input(f"Select a machine [1-{len(machines)}]: ").strip()
        except EOFError:
            return None
        if not answer:
            continue
        if answer.isdigit() and 1 <= int(answer) <= len(machines):
            return machines[int(answer) - 1]
        print(f"Please enter a number between 1 and {len(machines)}.")


def _resolve_password(cli_password):
    """CLI flag, then $VECTOR_PASSWORD, then an interactive prompt."""
    password = cli_password or os.environ.get(PASSWORD_ENV_VAR)
    if password:
        return password
    if not sys.stdin.isatty():
        return None
    print(
        "A password is needed to enable the memory broadcast on the machine "
        "(and to write memory)."
    )
    try:
        password = getpass.getpass("Vector password (Enter to skip): ")
    except (EOFError, KeyboardInterrupt):
        return None
    return password or None


def _establish_connection(args):
    """Discover/connect to a machine and enable its memory broadcast.

    Returns a VectorConnection, or None to continue in listen-only mode.
    Raises SystemExit with a message when connecting is impossible.
    """
    if args.machine:
        print(f"Connecting to {args.machine}…")
        connection = vector.connect_machine(
            args.machine, timeout=args.discover_timeout
        )
    else:
        print(
            f"Discovering Vector machines (up to {args.discover_timeout:g}s)…"
        )
        machines = vector.discover_machines(timeout=args.discover_timeout)
        if not machines:
            raise SystemExit(
                "No Vector machines found on the network. Make sure the "
                "machine is powered on and on the same network, or connect "
                "directly with --machine <ip>, or use --listen-only."
            )
        if len(machines) == 1:
            chosen = machines[0]
            print(f"Found one machine: {chosen.name} ({chosen.ip})")
        elif sys.stdin.isatty():
            chosen = _choose_machine(machines)
            if chosen is None:
                raise SystemExit("No machine selected.")
        else:
            names = ", ".join(f"{m.name} ({m.ip})" for m in machines)
            raise SystemExit(
                f"Multiple machines found ({names}) and no terminal to pick "
                "one; re-run with --machine <name or ip>."
            )
        connection = vector.connection_from_discovered(chosen)

    connection.machine.password = _resolve_password(args.password)
    if not connection.has_password():
        print(
            "No password provided; continuing in listen-only mode. Enable "
            '"Broadcast Memory Snapshots" in the Vector web UI yourself. '
            "Memory writes are unavailable."
        )
        connection.close()
        return None

    print(f"Enabling memory broadcast on {connection.label}…")
    try:
        connection.enable_broadcast(frequency_ms=args.frequency_ms)
    except Exception as exc:
        connection.close()
        raise SystemExit(
            f"Could not enable the memory broadcast on {connection.label}: "
            f"{exc}"
        )
    return connection


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

    connection = None
    if not args.listen_only:
        try:
            connection = _establish_connection(args)
        except vector.VectorUnavailableError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        except SystemExit as exc:
            if exc.code is not None and not isinstance(exc.code, int):
                print(exc.code, file=sys.stderr)
                return 1
            raise
        except KeyboardInterrupt:
            return 130

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
    if selected_source is None and connection is not None:
        selected_source = connection.ip

    writer = None
    if connection is not None:
        writer = connection.write_memory

    display = MemoryDisplay(
        tracker,
        bytes_per_row=args.bytes_per_row,
        selected_source=selected_source,
        writer=writer,
        machine_label=connection.label if connection is not None else None,
    )
    if connection is not None:
        display.status_message = (
            f"Connected to {connection.label}; waiting for first snapshot"
        )

    receiver_thread = start_receiver(args.group, args.port, on_packet, stop_event)

    try:
        display.run(stop_event=stop_event)
    except KeyboardInterrupt:
        pass
    finally:
        stop_event.set()
        receiver_thread.join(timeout=2.0)
        if connection is not None:
            if connection.broadcast_enabled and not args.keep_broadcasting:
                try:
                    connection.disable_broadcast()
                    print(f"Disabled memory broadcast on {connection.label}.")
                except Exception as exc:
                    print(
                        f"Could not disable the memory broadcast on "
                        f"{connection.label}: {exc}",
                        file=sys.stderr,
                    )
            connection.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
