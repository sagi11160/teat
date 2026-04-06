"""Tests for the PLC Simulator Modbus TCP server."""

import asyncio
import threading
import time

import pytest
from pymodbus.client import ModbusTcpClient

from plc_simulator.server import build_context, run_plc_server

TEST_HOST = "127.0.0.1"
TEST_PORT = 15020


@pytest.fixture(scope="module")
def plc_server():
    """Start the PLC simulator in a background thread for the test module."""
    context = build_context({0: 0, 1: 0, 2: 220, 3: 215})
    loop = asyncio.new_event_loop()

    def _run():
        loop.run_until_complete(
            run_plc_server(host=TEST_HOST, port=TEST_PORT, context=context)
        )

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    time.sleep(1.5)  # Allow server to bind
    yield context


@pytest.fixture()
def client(plc_server):
    """Create a synchronous Modbus TCP client connected to the test server."""
    cli = ModbusTcpClient(TEST_HOST, port=TEST_PORT)
    assert cli.connect(), "Failed to connect to PLC simulator"
    yield cli
    cli.close()


def test_read_default_registers(client):
    """Reading holding registers should return the initialised values."""
    result = client.read_holding_registers(address=0, count=5)
    assert not result.isError(), f"Modbus read error: {result}"
    assert result.registers[0] == 0    # Chiller Cmd OFF
    assert result.registers[2] == 220  # Setpoint 22.0°C
    assert result.registers[3] == 215  # Actual 21.5°C


def test_write_and_readback(client):
    """Writing a value to a holding register and reading it back."""
    write_result = client.write_register(address=0, value=1)
    assert not write_result.isError(), f"Modbus write error: {write_result}"

    read_result = client.read_holding_registers(address=0, count=1)
    assert not read_result.isError()
    assert read_result.registers[0] == 1


def test_write_alarm_register(client):
    """Write a bit-field alarm value and verify it persists."""
    alarm_value = 0b00001010
    write_result = client.write_register(address=4, value=alarm_value)
    assert not write_result.isError()

    read_result = client.read_holding_registers(address=4, count=1)
    assert not read_result.isError()
    assert read_result.registers[0] == alarm_value
