#!/usr/bin/env python3
"""
Ctrl+Vision MVP — End-to-End Integration Test

This script programmatically executes the full pipeline:
1. Starts PLC simulator in background
2. Triggers the Edge Camera
3. Generates code via Cloud AI Agent
4. Validates via Digital Twin Check
5. Approves deployment via Gateway
6. Asserts PLC register actually changed

Can run locally (no Docker needed) or against Docker services.
"""

import argparse
import asyncio
import sys
import threading
import time

from pymodbus.client import ModbusTcpClient


def log(msg: str) -> None:
    print(f"[E2E] {msg}", flush=True)


def run_e2e_local(plc_port: int = 15099) -> bool:
    """Run the full E2E test locally using in-process calls."""
    from plc_simulator.server import build_context, run_plc_server
    from gateway_hmi.api import run_local_pipeline, _pipelines

    log("=" * 60)
    log("CTRL+VISION MVP — E2E INTEGRATION TEST (LOCAL)")
    log("=" * 60)

    # ── Step 1: Start PLC Simulator ──────────────────────────────
    log("[1/6] Starting PLC Simulator on port %d..." % plc_port)
    context = build_context({0: 0, 1: 0, 2: 220, 3: 215})
    loop = asyncio.new_event_loop()

    def _run():
        loop.run_until_complete(
            run_plc_server(host="127.0.0.1", port=plc_port, context=context)
        )

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    time.sleep(2)

    # Verify PLC is up
    cli = ModbusTcpClient("127.0.0.1", port=plc_port)
    assert cli.connect(), "FAIL: Cannot connect to PLC simulator"
    initial = cli.read_holding_registers(address=0, count=5)
    assert not initial.isError(), "FAIL: Cannot read PLC registers"
    log(f"  PLC registers (initial): {initial.registers}")
    assert initial.registers[0] == 0, "FAIL: Register 0 should be 0 (OFF)"
    cli.close()
    log("  PLC Simulator: OK")

    # ── Step 2: Trigger Edge Camera (via local function) ─────────
    log("[2/6] Triggering Edge Camera...")
    from edge_mock.camera import _generate_event
    event = _generate_event(equipment_type="Chiller")
    log(f"  Edge event: {event.trigger} | MAC: {event.mac_address}")
    assert event.equipment_type == "Chiller"
    log("  Edge Camera: OK")

    # ── Step 3: Generate Code via Cloud AI Agent ─────────────────
    log("[3/6] Generating Modbus activation code...")
    from cloud_ai_agent.generator import GenerationRequest, generate_code
    req = GenerationRequest(
        equipment_type="Chiller",
        action="activate",
        plc_host="127.0.0.1",
        plc_port=plc_port,
    )
    gen = generate_code(req)
    log(f"  Generated: {gen.language} code ({len(gen.code)} chars)")
    log(f"  RAG sources: {gen.rag_sources}")
    assert "activate_chiller" in gen.code
    assert "ModbusTcpClient" in gen.code
    log("  Code Generation: OK")

    # ── Step 4: Digital Twin Validation ──────────────────────────
    log("[4/6] Running Digital Twin Check...")
    from gateway_hmi.api import run_digital_twin_check
    sim = run_digital_twin_check(gen.code, "127.0.0.1", plc_port)
    log(f"  Passed: {sim.passed} | {sim.message}")
    log(f"  Register before: {sim.register_before} → after: {sim.register_after}")
    log(f"  Execution time: {sim.execution_time_ms}ms")
    assert sim.passed, f"FAIL: Digital twin check failed: {sim.message}"
    log("  Digital Twin: OK")

    # ── Step 5: Full Pipeline (integrated) ───────────────────────
    log("[5/6] Running full local pipeline...")
    _pipelines.clear()
    state = run_local_pipeline(
        equipment_type="Chiller",
        action="activate",
        plc_host="127.0.0.1",
        plc_port=plc_port,
    )
    assert state.edge_event is not None, "FAIL: No edge event"
    assert state.generation_result is not None, "FAIL: No generated code"
    assert state.simulation is not None, "FAIL: No simulation result"
    assert state.simulation.passed, "FAIL: Simulation did not pass"
    log(f"  Pipeline ID: {state.pipeline_id}")
    log("  Full Pipeline: OK")

    # ── Step 6: Verify PLC Register Changed ──────────────────────
    log("[6/6] Verifying PLC register state...")
    cli = ModbusTcpClient("127.0.0.1", port=plc_port)
    assert cli.connect(), "FAIL: Cannot reconnect to PLC"
    final = cli.read_holding_registers(address=0, count=5)
    assert not final.isError(), "FAIL: Cannot read final PLC registers"
    log(f"  PLC registers (final): {final.registers}")
    assert final.registers[0] == 1, (
        f"FAIL: Register 0 should be 1 (ON) after activation, got {final.registers[0]}"
    )
    cli.close()
    log("  PLC Register Verification: OK")

    # ── Step 7: Test Deactivation Cycle ──────────────────────────
    log("[BONUS] Testing deactivation cycle...")
    deact_state = run_local_pipeline(
        equipment_type="Chiller",
        action="deactivate",
        plc_host="127.0.0.1",
        plc_port=plc_port,
    )
    assert deact_state.simulation.passed

    cli = ModbusTcpClient("127.0.0.1", port=plc_port)
    cli.connect()
    deact_read = cli.read_holding_registers(address=0, count=1)
    assert deact_read.registers[0] == 0, "FAIL: Register should be 0 after deactivation"
    cli.close()
    log("  Deactivation Cycle: OK")

    # ── Step 8: Test Structured Text Generation ──────────────────
    log("[BONUS] Testing Structured Text generation...")
    st_req = GenerationRequest(
        equipment_type="Chiller",
        action="activate",
        output_format="structured_text",
    )
    st_gen = generate_code(st_req)
    assert "PROGRAM Activate_Chiller" in st_gen.code
    assert "END_PROGRAM" in st_gen.code
    log(f"  ST code generated: {len(st_gen.code)} chars")
    log("  Structured Text Generation: OK")

    log("=" * 60)
    log("ALL E2E TESTS PASSED")
    log("=" * 60)
    return True


def run_e2e_docker(gateway_url: str = "http://localhost:8000") -> bool:
    """Run E2E test against Docker Compose services."""
    import httpx

    log("=" * 60)
    log("CTRL+VISION MVP — E2E INTEGRATION TEST (DOCKER)")
    log("=" * 60)

    # Step 1: Health checks
    log("[1/5] Checking service health...")
    services = {
        "gateway": gateway_url,
        "edge": "http://localhost:8001",
        "cloud": "http://localhost:8002",
    }
    for name, url in services.items():
        resp = httpx.get(f"{url}/health", timeout=10)
        assert resp.status_code == 200, f"FAIL: {name} health check failed"
        log(f"  {name}: OK")

    # Step 2: Check PLC simulator via Modbus
    log("[2/5] Checking PLC Simulator...")
    cli = ModbusTcpClient("localhost", port=5020)
    assert cli.connect(), "FAIL: Cannot connect to PLC"
    regs = cli.read_holding_registers(address=0, count=5)
    assert not regs.isError(), "FAIL: Cannot read PLC"
    log(f"  PLC registers: {regs.registers}")
    cli.close()

    # Step 3: Trigger pipeline via Gateway API
    log("[3/5] Triggering pipeline via Gateway...")
    resp = httpx.post(
        f"{gateway_url}/pipeline/trigger",
        json={
            "equipment_type": "Chiller",
            "action": "activate",
            "output_format": "python_modbus",
        },
        timeout=30,
    )
    assert resp.status_code == 200, f"FAIL: Pipeline trigger failed: {resp.text}"
    pipeline = resp.json()
    pipeline_id = pipeline["pipeline_id"]
    log(f"  Pipeline ID: {pipeline_id}")
    log(f"  Simulation passed: {pipeline.get('simulation', {}).get('passed')}")

    # Step 4: Approve and Deploy
    if pipeline.get("simulation", {}).get("passed"):
        log("[4/5] Approving pipeline for deployment...")
        resp = httpx.post(f"{gateway_url}/pipeline/{pipeline_id}/approve", timeout=10)
        assert resp.status_code == 200, f"FAIL: Approval failed: {resp.text}"
        log(f"  Deployment status: {resp.json()['status']}")
    else:
        log("[4/5] SKIP: Simulation did not pass, cannot approve")

    # Step 5: Verify PLC register changed
    log("[5/5] Verifying PLC register state...")
    cli = ModbusTcpClient("localhost", port=5020)
    cli.connect()
    final = cli.read_holding_registers(address=0, count=5)
    log(f"  PLC registers (final): {final.registers}")
    assert final.registers[0] == 1, f"FAIL: Register should be 1, got {final.registers[0]}"
    cli.close()

    log("=" * 60)
    log("ALL DOCKER E2E TESTS PASSED")
    log("=" * 60)
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="Ctrl+Vision E2E Test")
    parser.add_argument(
        "--mode",
        choices=["local", "docker"],
        default="local",
        help="Run tests locally (in-process) or against Docker services",
    )
    parser.add_argument("--plc-port", type=int, default=15099, help="PLC port for local mode")
    parser.add_argument("--gateway-url", default="http://localhost:8000", help="Gateway URL for docker mode")
    args = parser.parse_args()

    try:
        if args.mode == "local":
            success = run_e2e_local(plc_port=args.plc_port)
        else:
            success = run_e2e_docker(gateway_url=args.gateway_url)
    except Exception as exc:
        log(f"E2E TEST FAILED: {exc}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
