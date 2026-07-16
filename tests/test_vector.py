"""Tests for memory_mapper.vector (Vector connection layer and manager)."""

import time

import pytest

from memory_mapper.vector import (
    VectorManager,
    BROADCAST_FREQUENCY_MAX_MS,
    BROADCAST_FREQUENCY_MIN_MS,
    TOGGLE_BROADCAST_ROUTE,
    VectorConnection,
    clamp_frequency_ms,
)


class FakeMachine:
    """Records the calls a VectorConnection makes to the library Machine."""

    def __init__(self, password="hunter2"):
        self.password = password
        self.calls = []
        self.writes = []
        self.closed = False

    def call(self, path, body=None, authenticated=False):
        self.calls.append((path, body, authenticated))

    def write_bytes(self, offset, data):
        self.writes.append((offset, data))

    def close(self):
        self.closed = True


class TestClampFrequency:
    def test_within_bounds_unchanged(self):
        assert clamp_frequency_ms(100) == 100

    def test_clamps_low(self):
        assert clamp_frequency_ms(1) == BROADCAST_FREQUENCY_MIN_MS

    def test_clamps_high(self):
        assert clamp_frequency_ms(10**9) == BROADCAST_FREQUENCY_MAX_MS


class TestVectorConnection:
    def _connection(self, **kwargs):
        machine = FakeMachine(**kwargs)
        return VectorConnection(machine, ip="10.0.0.5", name="elvira"), machine

    def test_label_includes_name_and_ip(self):
        conn, _ = self._connection()
        assert conn.label == "elvira (10.0.0.5)"

    def test_label_without_name(self):
        conn = VectorConnection(FakeMachine(), ip="10.0.0.5")
        assert conn.label == "10.0.0.5"

    def test_enable_broadcast_calls_toggle_route(self):
        conn, machine = self._connection()
        conn.enable_broadcast(frequency_ms=250)
        assert machine.calls == [
            (
                TOGGLE_BROADCAST_ROUTE,
                {"enable": True, "frequency_ms": 250},
                True,
            )
        ]
        assert conn.broadcast_enabled is True

    def test_enable_broadcast_clamps_frequency(self):
        conn, machine = self._connection()
        conn.enable_broadcast(frequency_ms=1)
        assert machine.calls[0][1]["frequency_ms"] == BROADCAST_FREQUENCY_MIN_MS

    def test_disable_broadcast(self):
        conn, machine = self._connection()
        conn.enable_broadcast()
        conn.disable_broadcast()
        assert machine.calls[-1] == (
            TOGGLE_BROADCAST_ROUTE,
            {"enable": False},
            True,
        )
        assert conn.broadcast_enabled is False

    def test_write_memory_passes_through(self):
        conn, machine = self._connection()
        conn.write_memory(0x2134, [5, 0x12])
        assert machine.writes == [(0x2134, [5, 0x12])]

    def test_write_memory_rejects_empty(self):
        conn, _ = self._connection()
        with pytest.raises(ValueError):
            conn.write_memory(0, [])

    def test_write_memory_rejects_out_of_range(self):
        conn, _ = self._connection()
        with pytest.raises(ValueError):
            conn.write_memory(0, [256])
        with pytest.raises(ValueError):
            conn.write_memory(0, [-1])

    def test_write_memory_rejects_negative_offset(self):
        conn, _ = self._connection()
        with pytest.raises(ValueError):
            conn.write_memory(-1, [0])

    def test_has_password(self):
        conn, _ = self._connection()
        assert conn.has_password() is True
        conn_no, _ = self._connection(password=None)
        assert conn_no.has_password() is False

    def test_close_closes_machine(self):
        conn, machine = self._connection()
        conn.close()
        assert machine.closed is True


class FakeDiscovered:
    def __init__(self, ip, name):
        self.ip = ip
        self.name = name


def make_manager(machines=None, password=None, target=None, monkeypatch=None):
    """A VectorManager with stubbed discovery/connect; worker not started."""
    fake_machines = {}  # ip -> FakeMachine

    def discover_fn(timeout):
        return [FakeDiscovered(ip, name) for ip, name in (machines or {}).items()]

    def connect_fn(ip, password=None, timeout=None):
        fake = FakeMachine(password=password)
        fake_machines[ip] = fake
        return VectorConnection(fake, ip=ip)

    manager = VectorManager(
        password=password,
        target=target,
        discover_fn=discover_fn,
        connect_fn=connect_fn,
    )
    if monkeypatch is not None:
        monkeypatch.delenv("VECTOR_PASSWORD", raising=False)
    return manager, fake_machines


class TestVectorManager:
    def test_discovery_populates_machines_and_notices(self, monkeypatch):
        manager, _ = make_manager(
            machines={"10.0.0.5": "elvira"}, monkeypatch=monkeypatch
        )
        manager._discover_once()
        assert manager.machines() == {"10.0.0.5": "elvira"}
        assert manager.machine_name("10.0.0.5") == "elvira"
        notices = manager.pop_notices()
        assert notices == ["Discovered elvira (10.0.0.5)"]
        # Re-discovery of the same machine is silent.
        manager._discover_once()
        assert manager.pop_notices() == []

    def test_discovery_error_reported_once(self, monkeypatch):
        def boom(timeout):
            raise RuntimeError("no network")

        manager = VectorManager(discover_fn=boom, connect_fn=lambda *a, **k: None)
        manager._discover_once()
        manager._discover_once()
        assert manager.pop_notices() == ["Discovery error: no network"]

    def test_pick_target_first_by_default(self, monkeypatch):
        manager, _ = make_manager(
            machines={"10.0.0.5": "elvira", "10.0.0.6": "taxi"},
            monkeypatch=monkeypatch,
        )
        manager._discover_once()
        assert manager.pick_target() == "10.0.0.5"

    def test_pick_target_matches_name_and_ip(self, monkeypatch):
        manager, _ = make_manager(
            machines={"10.0.0.5": "elvira", "10.0.0.6": "taxi"},
            target="tax",
            monkeypatch=monkeypatch,
        )
        manager._discover_once()
        assert manager.pick_target() == "10.0.0.6"
        manager.target = "10.0.0.5"
        assert manager.pick_target() == "10.0.0.5"
        manager.target = "no-such-machine"
        assert manager.pick_target() is None

    def test_enable_with_password(self, monkeypatch):
        manager, fakes = make_manager(
            machines={"10.0.0.5": "elvira"},
            password="hunter2",
            monkeypatch=monkeypatch,
        )
        manager._discover_once()
        manager.pop_notices()  # drain the discovery notice
        manager.request_enable("10.0.0.5")
        manager._process_enable_requests()
        assert manager.broadcast_enabled("10.0.0.5") is True
        assert fakes["10.0.0.5"].calls == [
            (
                TOGGLE_BROADCAST_ROUTE,
                {"enable": True, "frequency_ms": 100},
                True,
            )
        ]
        assert manager.pop_notices() == [
            "Enabled memory broadcast on elvira (10.0.0.5)"
        ]
        # A repeated request is a no-op.
        manager.request_enable("10.0.0.5")
        manager._process_enable_requests()
        assert len(fakes["10.0.0.5"].calls) == 1

    def test_enable_without_password_fails_until_password_set(self, monkeypatch):
        manager, fakes = make_manager(
            machines={"10.0.0.5": "elvira"}, monkeypatch=monkeypatch
        )
        manager._discover_once()
        manager.request_enable("10.0.0.5")
        manager._process_enable_requests()
        assert manager.broadcast_enabled("10.0.0.5") is False
        assert "Password needed" in manager.pop_notices()[-1]
        # Re-requesting with the same (missing) password is deduped.
        manager.request_enable("10.0.0.5")
        manager._process_enable_requests()
        assert fakes == {}
        # Setting a password retries automatically.
        manager.set_password("hunter2")
        manager._process_enable_requests()
        assert manager.broadcast_enabled("10.0.0.5") is True

    def test_enable_failure_reported(self, monkeypatch):
        class FailingMachine(FakeMachine):
            def call(self, *args, **kwargs):
                raise RuntimeError("auth rejected")

        def connect_fn(ip, password=None, timeout=None):
            return VectorConnection(FailingMachine(), ip=ip)

        manager = VectorManager(
            password="wrong",
            discover_fn=lambda timeout: [FakeDiscovered("10.0.0.5", "elvira")],
            connect_fn=connect_fn,
        )
        manager._discover_once()
        manager.pop_notices()
        manager.request_enable("10.0.0.5")
        manager._process_enable_requests()
        assert manager.broadcast_enabled("10.0.0.5") is False
        assert "Could not enable" in manager.pop_notices()[-1]

    def test_write_goes_through_connection(self, monkeypatch):
        manager, fakes = make_manager(
            machines={"10.0.0.5": "elvira"},
            password="hunter2",
            monkeypatch=monkeypatch,
        )
        manager.write("10.0.0.5", 0x2134, [5])
        assert fakes["10.0.0.5"].writes == [(0x2134, [5])]

    def test_shutdown_disables_enabled_broadcasts(self, monkeypatch):
        manager, fakes = make_manager(
            machines={"10.0.0.5": "elvira"},
            password="hunter2",
            monkeypatch=monkeypatch,
        )
        manager._discover_once()
        manager.request_enable("10.0.0.5")
        manager._process_enable_requests()
        manager.shutdown(disable_broadcasts=True)
        fake = fakes["10.0.0.5"]
        assert fake.calls[-1] == (TOGGLE_BROADCAST_ROUTE, {"enable": False}, True)
        assert fake.closed is True

    def test_shutdown_keeps_broadcast_when_asked(self, monkeypatch):
        manager, fakes = make_manager(
            machines={"10.0.0.5": "elvira"},
            password="hunter2",
            monkeypatch=monkeypatch,
        )
        manager._discover_once()
        manager.request_enable("10.0.0.5")
        manager._process_enable_requests()
        manager.shutdown(disable_broadcasts=False)
        assert all(call[1].get("enable") for call in fakes["10.0.0.5"].calls)

    def test_worker_thread_discovers_and_enables(self, monkeypatch):
        manager, fakes = make_manager(
            machines={"10.0.0.5": "elvira"},
            password="hunter2",
            monkeypatch=monkeypatch,
        )
        manager.start()
        try:
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline and not manager.machines():
                time.sleep(0.01)
            assert manager.machines() == {"10.0.0.5": "elvira"}
            manager.request_enable("10.0.0.5")
            while time.monotonic() < deadline and not manager.broadcast_enabled(
                "10.0.0.5"
            ):
                time.sleep(0.01)
            assert manager.broadcast_enabled("10.0.0.5") is True
        finally:
            manager.shutdown(disable_broadcasts=False)
