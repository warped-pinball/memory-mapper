"""Vector board connectivity built on the ``warpedpinball`` library.

Wraps machine discovery, authenticated connections, the memory-broadcast
toggle, and memory writes so the rest of the tool never has to talk to the
library (or the firmware routes) directly.
"""

from typing import List, Optional, Sequence

try:
    import warpedpinball
except ImportError:  # pragma: no cover - exercised via _require_library tests
    warpedpinball = None

DEFAULT_DISCOVER_TIMEOUT = 20.0
DEFAULT_BROADCAST_FREQUENCY_MS = 100
# Firmware clamps to the same bounds; mirroring them avoids surprises.
BROADCAST_FREQUENCY_MIN_MS = 10
BROADCAST_FREQUENCY_MAX_MS = 60000
TOGGLE_BROADCAST_ROUTE = "/api/memory/toggle-broadcast"


class VectorUnavailableError(RuntimeError):
    """Raised when the warpedpinball library is not installed."""


def _require_library():
    if warpedpinball is None:
        raise VectorUnavailableError(
            "The 'warpedpinball' library is required to discover and control "
            "Vector boards. Install it with: pip install warpedpinball "
            "(or run with --listen-only to skip machine control)."
        )
    return warpedpinball


def discover_machines(timeout: float = DEFAULT_DISCOVER_TIMEOUT) -> List:
    """Find Vector boards on the LAN; returns DiscoveredMachine(ip, name)."""
    return _require_library().discover(timeout=timeout)


def clamp_frequency_ms(frequency_ms: int) -> int:
    return max(
        BROADCAST_FREQUENCY_MIN_MS,
        min(BROADCAST_FREQUENCY_MAX_MS, int(frequency_ms)),
    )


class VectorConnection:
    """A connected Vector board plus the operations Memory Mapper needs."""

    def __init__(self, machine, ip: str, name: Optional[str] = None):
        self.machine = machine
        self.ip = ip
        self.name = name
        self.broadcast_enabled = False

    @property
    def label(self) -> str:
        return f"{self.name} ({self.ip})" if self.name else self.ip

    def has_password(self) -> bool:
        return bool(self.machine.password)

    def verify_password(self) -> bool:
        return self.machine.verify_password()

    def enable_broadcast(
        self, frequency_ms: int = DEFAULT_BROADCAST_FREQUENCY_MS
    ) -> None:
        """Turn on the UDP memory-snapshot broadcast (authenticated)."""
        self.machine.call(
            TOGGLE_BROADCAST_ROUTE,
            body={"enable": True, "frequency_ms": clamp_frequency_ms(frequency_ms)},
            authenticated=True,
        )
        self.broadcast_enabled = True

    def disable_broadcast(self) -> None:
        """Turn the UDP memory-snapshot broadcast back off (authenticated)."""
        self.machine.call(
            TOGGLE_BROADCAST_ROUTE,
            body={"enable": False},
            authenticated=True,
        )
        self.broadcast_enabled = False

    def write_memory(self, offset: int, values: Sequence[int]) -> None:
        """Write byte values into SRAM starting at *offset* (authenticated)."""
        if offset < 0:
            raise ValueError("offset must be non-negative")
        if not values:
            raise ValueError("no values to write")
        for value in values:
            if not (0 <= int(value) <= 255):
                raise ValueError(f"byte value out of range 0-255: {value}")
        self.machine.write_bytes(offset, [int(v) for v in values])

    def close(self) -> None:
        try:
            self.machine.close()
        except Exception:
            pass


def connect_machine(
    name_or_ip: str,
    password: Optional[str] = None,
    timeout: float = DEFAULT_DISCOVER_TIMEOUT,
) -> VectorConnection:
    """Connect by LAN name (via UDP discovery) or directly by IP address."""
    wp = _require_library()
    machine = wp.connect(name_or_ip, password=password, timeout=timeout)
    transport = getattr(machine, "transport", None)
    host = getattr(transport, "_host_label", None)
    if not host:
        base_url = getattr(transport, "base_url", "")
        host = base_url.split("://", 1)[-1].rstrip("/") if base_url else name_or_ip
    return VectorConnection(machine, ip=host, name=machine.name)


def connection_from_discovered(machine_info, password: Optional[str] = None) -> VectorConnection:
    """Build a connection straight from a DiscoveredMachine (skips re-discovery)."""
    wp = _require_library()
    machine = wp.connect(machine_info.ip, password=password)
    machine.name = machine_info.name
    return VectorConnection(machine, ip=machine_info.ip, name=machine_info.name)
