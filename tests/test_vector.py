"""Tests for memory_mapper.vector (Vector connection layer)."""

import pytest

from memory_mapper.vector import (
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
