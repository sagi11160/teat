"""Tests for the Gateway HMI — API + Digital Twin validation."""

import asyncio
import threading
import time

import pytest
from fastapi.testclient import TestClient

from plc_simulator.server import build_context, run_plc_server
from gateway_hmi.api import (
    app,
    run_digital_twin_check,
    run_local_pipeline,
    _pipelines,
    PLC_HOST,
)

GW_TEST_PLC_PORT = 15022


@pytest.fixture(scope="module")
def plc_for_gateway():
    """Start a PLC simulator for gateway tests."""
    context = build_context({0: 0, 1: 0, 2: 220, 3: 215})
    loop = asyncio.new_event_loop()

    def _run():
        loop.run_until_complete(
            run_plc_server(host="127.0.0.1", port=GW_TEST_PLC_PORT, context=context)
        )

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    time.sleep(1.5)
    yield context


@pytest.fixture(autouse=True)
def clear_pipelines():
    _pipelines.clear()
    yield
    _pipelines.clear()


client = TestClient(app)


def test_gateway_health():
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["service"] == "gateway_hmi"


def test_hmi_dashboard_loads():
    resp = client.get("/")
    assert resp.status_code == 200
    assert "Ctrl+Vision HMI" in resp.text
    assert "TRIGGER PIPELINE" in resp.text


def test_digital_twin_check_pass(plc_for_gateway):
    """Run generated code against the PLC simulator and verify it passes."""
    from cloud_ai_agent.generator import GenerationRequest, generate_code

    req = GenerationRequest(
        equipment_type="Chiller",
        action="activate",
        plc_host="127.0.0.1",
        plc_port=GW_TEST_PLC_PORT,
    )
    gen = generate_code(req)
    result = run_digital_twin_check(gen.code, "127.0.0.1", GW_TEST_PLC_PORT)
    assert result.passed, f"Digital twin failed: {result.message}"
    assert result.register_after == 1  # Chiller command set to ON


def test_digital_twin_check_bad_code(plc_for_gateway):
    """Bad code should fail the digital twin check gracefully."""
    bad_code = "raise RuntimeError('Intentional failure')"
    result = run_digital_twin_check(bad_code, "127.0.0.1", GW_TEST_PLC_PORT)
    assert not result.passed
    assert "error" in result.message.lower() or "No callable" in result.message


def test_digital_twin_check_no_connection():
    """Connecting to a non-existent PLC should fail gracefully."""
    result = run_digital_twin_check("pass", "127.0.0.1", 19999)
    assert not result.passed
    assert "Cannot connect" in result.message or "No callable" in result.message


def test_local_pipeline_activate(plc_for_gateway):
    """Full local pipeline: generate + validate + check register change."""
    from pymodbus.client import ModbusTcpClient

    # Reset register 0 to 0
    cli = ModbusTcpClient("127.0.0.1", port=GW_TEST_PLC_PORT)
    cli.connect()
    cli.write_register(address=0, value=0)
    cli.close()

    state = run_local_pipeline(
        equipment_type="Chiller",
        action="activate",
        plc_host="127.0.0.1",
        plc_port=GW_TEST_PLC_PORT,
    )

    assert state.edge_event is not None
    assert state.generation_result is not None
    assert state.simulation is not None
    assert state.simulation.passed
    assert state.deployed is False  # Not yet approved


def test_local_pipeline_deactivate(plc_for_gateway):
    """Pipeline for deactivation."""
    state = run_local_pipeline(
        equipment_type="Chiller",
        action="deactivate",
        plc_host="127.0.0.1",
        plc_port=GW_TEST_PLC_PORT,
    )
    assert state.simulation is not None
    assert state.simulation.passed


def test_list_pipelines():
    resp = client.get("/pipelines")
    assert resp.status_code == 200
    assert resp.json() == []


def test_clear_pipelines_endpoint():
    _pipelines.append(None)  # type: ignore
    resp = client.delete("/pipelines")
    assert resp.status_code == 200
    assert len(_pipelines) == 0
