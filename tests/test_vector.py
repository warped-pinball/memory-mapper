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

    def test_enable_prefers_library_wrapper_when_present(self):
        class WrapperMachine(FakeMachine):
            def __init__(self):
                super().__init__()
                self.toggles = []

            def set_memory_broadcast(self, enabled, frequency_ms=100, ip=None):
                self.toggles.append((enabled, frequency_ms, ip))

        machine = WrapperMachine()
        conn = VectorConnection(machine, ip="10.0.0.5")
        conn.enable_broadcast(frequency_ms=250)
        conn.enable_broadcast(frequency_ms=250, ip="192.168.1.20")
        conn.disable_broadcast()
        assert machine.toggles == [
            (True, 250, None),
            (True, 250, "192.168.1.20"),
            (False, 100, None),
        ]
        assert machine.calls == []  # raw route fallback never used

    def test_enable_raw_route_includes_ip_when_given(self):
        conn, machine = self._connection()
        conn.enable_broadcast(frequency_ms=100, ip="192.168.1.20")
        assert machine.calls == [
            (
                TOGGLE_BROADCAST_ROUTE,
                {"enable": True, "frequency_ms": 100, "ip": "192.168.1.20"},
                True,
            )
        ]

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


class FakeResponse:
    def __init__(self, status_code=200, text="{}"):
        self.status_code = status_code
        self.text = text


class FakeSession:
    def __init__(self):
        self.requests = []
        self.responses = []

    def request(self, method, url, data=None, headers=None, timeout=None):
        self.requests.append(
            {"method": method, "url": url, "data": data, "headers": headers}
        )
        return self.responses.pop(0) if self.responses else FakeResponse()


class FakeTransport:
    """Just enough of warpedpinball's HttpTransport for _signed_call."""

    def __init__(self):
        self.base_url = "http://10.0.0.5"
        self.timeout = 10.0
        self._session = FakeSession()
        self.challenges = 0

    def _fetch_challenge(self):
        self.challenges += 1
        return f"challenge{self.challenges}"


class FakeMachineWithTransport(FakeMachine):
    def __init__(self, password=""):
        super().__init__(password=password)
        self.transport = FakeTransport()


class TestEmptyPasswordSigning:
    def test_enable_with_empty_password_signs_via_library_auth(self):
        from warpedpinball import auth

        machine = FakeMachineWithTransport(password="")
        conn = VectorConnection(machine, ip="10.0.0.5", password="")
        conn.enable_broadcast(frequency_ms=250)

        # The library's guarded high-level path is bypassed entirely.
        assert machine.calls == []
        reqs = machine.transport._session.requests
        assert len(reqs) == 1
        req = reqs[0]
        assert req["method"] == "POST"
        assert req["url"] == "http://10.0.0.5" + TOGGLE_BROADCAST_ROUTE
        assert req["data"] == b'{"enable":true,"frequency_ms":250}'
        # The request is signed with an empty-string HMAC key, using the
        # library's own auth module.
        expected = auth.sign(
            "", "challenge1", TOGGLE_BROADCAST_ROUTE, req["data"].decode()
        )
        assert req["headers"]["x-auth-hmac"] == expected
        assert req["headers"]["x-auth-challenge"] == "challenge1"
        assert conn.broadcast_enabled is True

    def test_disable_with_empty_password_signs(self):
        machine = FakeMachineWithTransport(password="")
        conn = VectorConnection(machine, ip="10.0.0.5", password="")
        conn.disable_broadcast()
        req = machine.transport._session.requests[-1]
        assert req["data"] == b'{"enable":false}'
        assert conn.broadcast_enabled is False

    def test_write_with_empty_password_signs_via_library_auth(self):
        machine = FakeMachineWithTransport(password="")
        conn = VectorConnection(machine, ip="10.0.0.5", password="")
        conn.write_memory(0x10, [5, 6])

        # The library's write_bytes (which refuses empty passwords) is not used.
        assert machine.writes == []
        reqs = machine.transport._session.requests
        assert len(reqs) == 1
        assert reqs[0]["url"] == "http://10.0.0.5/api/address/write"
        assert reqs[0]["data"] == b'{"offset":16,"values":[5,6]}'

    def test_non_empty_password_still_uses_library(self):
        # A real password takes the library's high-level path untouched.
        machine = FakeMachineWithTransport(password="hunter2")
        conn = VectorConnection(machine, ip="10.0.0.5", password="hunter2")
        conn.enable_broadcast(frequency_ms=100)
        conn.write_memory(0, [1])
        assert machine.transport._session.requests == []
        assert machine.calls[0][0] == TOGGLE_BROADCAST_ROUTE
        assert machine.writes == [(0, [1])]

    def test_signed_call_retries_once_on_stale_challenge(self):
        machine = FakeMachineWithTransport(password="")
        # First response: stale challenge (retryable 401); second: success.
        machine.transport._session.responses = [
            FakeResponse(401, '{"error": "challenge expired"}'),
            FakeResponse(200, "{}"),
        ]
        conn = VectorConnection(machine, ip="10.0.0.5", password="")
        conn.enable_broadcast()
        # Two attempts, each with a freshly fetched challenge.
        assert len(machine.transport._session.requests) == 2
        assert machine.transport.challenges == 2


class TestManagerEmptyPassword:
    def test_empty_password_is_kept_and_counts_as_set(self, monkeypatch):
        monkeypatch.delenv("VECTOR_PASSWORD", raising=False)
        manager = VectorManager(password="")
        assert manager.password == ""
        assert manager.has_password() is True

    def test_set_empty_password_counts_as_set(self, monkeypatch):
        monkeypatch.delenv("VECTOR_PASSWORD", raising=False)
        manager = VectorManager()
        assert manager.has_password() is False
        manager.set_password("")
        assert manager.password == ""
        assert manager.has_password() is True


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
        # A plain re-request is deduped, but force retries immediately.
        manager.request_enable("10.0.0.5")
        with manager._lock:
            assert manager._enable_state["10.0.0.5"][0] == "failed"
        manager.request_enable("10.0.0.5", force=True)
        with manager._lock:
            assert manager._enable_state["10.0.0.5"][0] == "pending"

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
