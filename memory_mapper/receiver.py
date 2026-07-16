"""UDP multicast receiver that feeds incoming payloads to a callback."""

import socket
import struct
import threading
from typing import Callable, Optional


def _join_multicast_group(sock: socket.socket, group: str) -> None:
    """Join a multicast group on all available interfaces."""
    mreq = struct.pack("4sL", socket.inet_aton(group), socket.INADDR_ANY)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)


def create_socket(group: str, port: int) -> socket.socket:
    """Create and configure a UDP multicast socket."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    # SO_REUSEPORT is not available on all platforms (e.g. Windows)
    if hasattr(socket, "SO_REUSEPORT"):
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except OSError:
            pass
    # Bind to all interfaces. Current firmware unicasts the memory stream
    # straight to this host, which this socket receives on any interface.
    sock.bind(("0.0.0.0", port))
    # Joining the legacy multicast group keeps older firmware (which
    # broadcast instead of unicasting) working; not fatal if it fails.
    try:
        _join_multicast_group(sock, group)
    except OSError:
        pass
    return sock


def receive_loop(
    group: str,
    port: int,
    callback: Callable[[bytes, str], None],
    stop_event: Optional[threading.Event] = None,
) -> None:
    """
    Block and receive UDP multicast datagrams, calling *callback(data, sender_addr)*
    for each one.  Returns when *stop_event* is set or a KeyboardInterrupt is raised.
    """
    sock = create_socket(group, port)
    sock.settimeout(1.0)  # allows periodic stop_event checks

    try:
        while stop_event is None or not stop_event.is_set():
            try:
                data, addr = sock.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError:
                break
            callback(data, addr[0])
    finally:
        sock.close()


def start_receiver(
    group: str,
    port: int,
    callback: Callable[[bytes, str], None],
    stop_event: Optional[threading.Event] = None,
) -> threading.Thread:
    """
    Start the UDP multicast receiver in a background daemon thread.

    Returns the running Thread so the caller can join it if needed.
    """
    thread = threading.Thread(
        target=receive_loop,
        args=(group, port, callback, stop_event),
        daemon=True,
        name="udp-receiver",
    )
    thread.start()
    return thread
