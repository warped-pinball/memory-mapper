"""Vector board connectivity built on the ``warpedpinball`` library.

Wraps machine discovery, authenticated connections, the memory-broadcast
toggle, and memory writes so the rest of the tool never has to talk to the
library (or the firmware routes) directly.
"""

import os
import threading
import time
from collections import OrderedDict
from typing import Callable, Dict, List, Optional, Sequence, Tuple

try:
    import warpedpinball
except ImportError:  # pragma: no cover - exercised via _require_library tests
    warpedpinball = None

PASSWORD_ENV_VAR = "VECTOR_PASSWORD"
# How long each background discovery round listens for answers. Discovery
# returns early once the registry replies, so rounds are usually much shorter.
DEFAULT_DISCOVER_TIMEOUT = 5.0
# Pause between background discovery rounds.
DISCOVER_REST_INTERVAL = 2.0
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

    def _set_broadcast(
        self, enabled: bool, frequency_ms: int, ip: Optional[str] = None
    ) -> None:
        """Toggle the memory stream, preferring the library wrapper when present.

        warpedpinball gained Machine.set_memory_broadcast() after 0.1.1; on
        older versions fall back to calling the firmware route directly.
        """
        set_broadcast = getattr(self.machine, "set_memory_broadcast", None)
        if callable(set_broadcast):
            set_broadcast(enabled, frequency_ms=frequency_ms, ip=ip)
            return
        if enabled:
            body = {"enable": True, "frequency_ms": clamp_frequency_ms(frequency_ms)}
            if ip is not None:
                body["ip"] = ip
        else:
            body = {"enable": False}
        self.machine.call(TOGGLE_BROADCAST_ROUTE, body=body, authenticated=True)

    def enable_broadcast(
        self,
        frequency_ms: int = DEFAULT_BROADCAST_FREQUENCY_MS,
        ip: Optional[str] = None,
    ) -> None:
        """Start the UDP memory-snapshot stream (authenticated).

        The firmware sends the stream to a single target: ``ip`` when given,
        otherwise the address this request comes from — i.e. this machine,
        which is where the receiver is listening.
        """
        self._set_broadcast(True, frequency_ms, ip=ip)
        self.broadcast_enabled = True

    def disable_broadcast(self) -> None:
        """Stop the UDP memory-snapshot stream (authenticated)."""
        self._set_broadcast(False, DEFAULT_BROADCAST_FREQUENCY_MS)
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


class VectorManager:
    """Continuous background discovery plus on-demand machine control.

    A daemon worker thread owns discovery and broadcast toggling so the UI
    never blocks on the network; memory writes stay synchronous so their
    outcome can be reported directly. Thread-safe throughout.
    """

    def __init__(
        self,
        password: Optional[str] = None,
        frequency_ms: int = DEFAULT_BROADCAST_FREQUENCY_MS,
        discover_timeout: float = DEFAULT_DISCOVER_TIMEOUT,
        target: Optional[str] = None,
        discover_fn: Optional[Callable[..., List]] = None,
        connect_fn: Optional[Callable[..., VectorConnection]] = None,
    ):
        self.frequency_ms = frequency_ms
        self.discover_timeout = discover_timeout
        #: Optional machine to focus on (--machine): an IP or a (partial) name.
        self.target = target
        self._discover_fn = discover_fn or discover_machines
        self._connect_fn = connect_fn or connect_machine
        self._password = password or None
        self._password_generation = 0
        self._machines: "OrderedDict[str, str]" = OrderedDict()  # ip -> name
        self._connections: Dict[str, VectorConnection] = {}
        # ip -> (state, password_generation); state: pending/enabled/failed
        self._enable_state: Dict[str, Tuple[str, int]] = {}
        self._notices: List[str] = []
        self._last_discovery_error: Optional[str] = None
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # -- lifecycle -----------------------------------------------------------

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="vector-manager"
        )
        self._thread.start()

    def shutdown(self, disable_broadcasts: bool = True) -> None:
        """Stop the worker, optionally turn off broadcasts we enabled, clean up."""
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
        with self._lock:
            enabled = [
                ip for ip, (state, _) in self._enable_state.items()
                if state == "enabled"
            ]
            connections = list(self._connections.values())
        if disable_broadcasts:
            for ip in enabled:
                try:
                    self._connection(ip).disable_broadcast()
                except Exception:
                    pass  # machine unreachable at exit; nothing more to do
        for connection in connections:
            connection.close()

    # -- credentials -----------------------------------------------------------

    @property
    def password(self) -> Optional[str]:
        return self._password or os.environ.get(PASSWORD_ENV_VAR)

    def has_password(self) -> bool:
        return bool(self.password)

    def set_password(self, password: Optional[str]) -> None:
        """Store a password and retry any machines that failed to enable."""
        with self._lock:
            self._password = password or None
            self._password_generation += 1
            for ip, (state, gen) in list(self._enable_state.items()):
                if state == "failed":
                    self._enable_state[ip] = ("pending", gen)
        self._wake.set()

    # -- discovered machines -----------------------------------------------------

    def machines(self) -> Dict[str, str]:
        """Snapshot of discovered machines as {ip: name}, in discovery order."""
        with self._lock:
            return dict(self._machines)

    def machine_name(self, ip: str) -> Optional[str]:
        with self._lock:
            return self._machines.get(ip)

    def label(self, ip: str) -> str:
        name = self.machine_name(ip)
        return f"{name} ({ip})" if name else ip

    def pick_target(self) -> Optional[str]:
        """The machine to control: the --machine match, or the first found."""
        with self._lock:
            if not self._machines:
                return None
            if self.target:
                wanted = self.target.lower()
                for ip, name in self._machines.items():
                    if ip == self.target or (name and wanted in name.lower()):
                        return ip
                return None
            return next(iter(self._machines))

    # -- broadcast control ---------------------------------------------------------

    def request_enable(self, ip: str, force: bool = False) -> None:
        """Ask the worker to enable the memory broadcast on *ip* (deduped).

        ``force`` re-sends even when the machine failed before with the same
        password, or when we already enabled it (a reboot silently stops the
        stream) — used when the user explicitly selects a machine.
        """
        with self._lock:
            state = self._enable_state.get(ip)
            if state is not None:
                status, generation = state
                if status == "pending":
                    return
                if status == "enabled" and not force:
                    return
                if (
                    status == "failed"
                    and generation == self._password_generation
                    and not force
                ):
                    return  # don't hammer a failing machine until anything changes
            self._enable_state[ip] = ("pending", self._password_generation)
        self._wake.set()

    def broadcast_enabled(self, ip: str) -> bool:
        with self._lock:
            state = self._enable_state.get(ip)
            return state is not None and state[0] == "enabled"

    # -- memory writes ----------------------------------------------------------------

    def write(self, ip: str, offset: int, values: Sequence[int]) -> None:
        """Synchronous authenticated memory write to the machine at *ip*."""
        self._connection(ip).write_memory(offset, values)

    # -- UI notices --------------------------------------------------------------------

    def pop_notices(self) -> List[str]:
        """Drain queued status messages (discoveries, enable results, errors)."""
        with self._lock:
            notices, self._notices = self._notices, []
            return notices

    def _notice(self, message: str) -> None:
        with self._lock:
            self._notices.append(message)

    # -- internals -----------------------------------------------------------------------

    def _connection(self, ip: str) -> VectorConnection:
        with self._lock:
            connection = self._connections.get(ip)
            if connection is None:
                connection = self._connect_fn(ip, password=self.password)
                connection.name = connection.name or self._machines.get(ip)
                self._connections[ip] = connection
            connection.machine.password = self.password
            return connection

    def _run(self) -> None:
        next_discover = 0.0
        while not self._stop.is_set():
            self._process_enable_requests()
            if time.monotonic() >= next_discover:
                self._discover_once()
                next_discover = time.monotonic() + DISCOVER_REST_INTERVAL
            self._wake.wait(timeout=0.25)
            self._wake.clear()

    def _discover_once(self) -> None:
        try:
            found = self._discover_fn(timeout=self.discover_timeout)
        except Exception as exc:
            message = f"Discovery error: {exc}"
            if message != self._last_discovery_error:  # don't spam every round
                self._last_discovery_error = message
                self._notice(message)
            return
        self._last_discovery_error = None
        with self._lock:
            for machine in found:
                if machine.ip not in self._machines:
                    self._notice(f"Discovered {machine.name} ({machine.ip})")
                self._machines[machine.ip] = machine.name

    def _process_enable_requests(self) -> None:
        with self._lock:
            pending = [
                ip for ip, (state, _) in self._enable_state.items()
                if state == "pending"
            ]
        for ip in pending:
            if self._stop.is_set():
                return
            label = self.label(ip)
            if not self.has_password():
                with self._lock:
                    self._enable_state[ip] = ("failed", self._password_generation)
                self._notice(
                    f"Password needed to enable the memory broadcast on {label}"
                )
                continue
            try:
                self._connection(ip).enable_broadcast(self.frequency_ms)
            except Exception as exc:
                with self._lock:
                    self._enable_state[ip] = ("failed", self._password_generation)
                self._notice(
                    f"Could not enable memory broadcast on {label}: {exc}"
                )
            else:
                with self._lock:
                    self._enable_state[ip] = ("enabled", self._password_generation)
                self._notice(f"Enabled memory broadcast on {label}")
