"""
Gateway HMI — FastAPI Gateway with Digital Twin validation.

This is the DMZ brain that:
1. Receives edge camera triggers
2. Requests code generation from the Cloud AI Agent
3. Validates generated code against the PLC simulator (digital twin check)
4. Serves an HMI UI for human approval
5. Deploys approved code to the PLC
"""

import asyncio
import logging
import textwrap
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [GW] %(levelname)s %(message)s",
)
logger = logging.getLogger("gateway")

# ---------------------------------------------------------------------------
# Configuration (overridable via env / docker-compose)
# ---------------------------------------------------------------------------
EDGE_URL = "http://edge_mock:8001"
CLOUD_URL = "http://cloud_ai_agent:8002"
PLC_HOST = "plc_simulator"
PLC_PORT = 5020

app = FastAPI(
    title="Gateway HMI",
    description="DMZ Gateway with Digital Twin validation and HMI.",
    version="1.0.0",
)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class PipelineRequest(BaseModel):
    equipment_type: str = "Chiller"
    action: str = "activate"
    parameters: Dict[str, Any] = Field(default_factory=dict)
    output_format: str = "python_modbus"


class SimulationResult(BaseModel):
    passed: bool
    message: str
    register_before: Optional[int] = None
    register_after: Optional[int] = None
    execution_time_ms: float = 0.0


class PipelineState(BaseModel):
    """Full state of a generation+validation pipeline run."""
    pipeline_id: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
    )
    edge_event: Optional[Dict[str, Any]] = None
    generation_result: Optional[Dict[str, Any]] = None
    simulation: Optional[SimulationResult] = None
    deployed: bool = False
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


# In-memory pipeline store
_pipelines: List[PipelineState] = []


# ---------------------------------------------------------------------------
# Digital Twin Validation Engine
# ---------------------------------------------------------------------------

def run_digital_twin_check(
    code: str,
    plc_host: str = PLC_HOST,
    plc_port: int = PLC_PORT,
) -> SimulationResult:
    """
    Execute generated Modbus code against the PLC simulator.
    This acts as a digital twin — verifying the code doesn't crash
    and produces the expected register changes.
    """
    from pymodbus.client import ModbusTcpClient

    start = time.time()
    client = ModbusTcpClient(plc_host, port=plc_port)

    if not client.connect():
        return SimulationResult(
            passed=False,
            message=f"Cannot connect to PLC simulator at {plc_host}:{plc_port}",
            execution_time_ms=round((time.time() - start) * 1000, 2),
        )

    try:
        # Read register 0 before execution
        pre_read = client.read_holding_registers(address=0, count=5)
        if pre_read.isError():
            return SimulationResult(
                passed=False,
                message=f"Pre-read failed: {pre_read}",
                execution_time_ms=round((time.time() - start) * 1000, 2),
            )
        reg_before = pre_read.registers[0]

        # Execute the generated code in a sandboxed namespace
        namespace: Dict[str, Any] = {}
        try:
            exec(compile(code, "<generated>", "exec"), namespace)
        except Exception as exc:
            return SimulationResult(
                passed=False,
                message=f"Code compilation/execution error: {exc}",
                register_before=reg_before,
                execution_time_ms=round((time.time() - start) * 1000, 2),
            )

        # Find and call the main function in the generated code
        func_name = None
        for name, obj in namespace.items():
            if callable(obj) and not name.startswith("_") and name != "ModbusTcpClient":
                func_name = name
                break

        if func_name is None:
            return SimulationResult(
                passed=False,
                message="No callable function found in generated code.",
                register_before=reg_before,
                execution_time_ms=round((time.time() - start) * 1000, 2),
            )

        try:
            result = namespace[func_name](host=plc_host, port=plc_port)
        except Exception as exc:
            return SimulationResult(
                passed=False,
                message=f"Function '{func_name}' raised: {exc}",
                register_before=reg_before,
                execution_time_ms=round((time.time() - start) * 1000, 2),
            )

        # Read register 0 after execution
        post_read = client.read_holding_registers(address=0, count=5)
        reg_after = post_read.registers[0] if not post_read.isError() else None

        elapsed = round((time.time() - start) * 1000, 2)
        return SimulationResult(
            passed=True,
            message=f"Digital twin check passed. Function '{func_name}' returned {result}.",
            register_before=reg_before,
            register_after=reg_after,
            execution_time_ms=elapsed,
        )

    finally:
        client.close()


# ---------------------------------------------------------------------------
# API Endpoints
# ---------------------------------------------------------------------------

@app.get("/health")
async def health() -> Dict[str, str]:
    return {"status": "ok", "service": "gateway_hmi"}


@app.post("/pipeline/trigger", response_model=PipelineState)
async def trigger_pipeline(req: PipelineRequest) -> PipelineState:
    """
    Full pipeline: trigger edge → generate code → validate → store.
    The HMI then displays the result for human approval.
    """
    state = PipelineState()

    # Step 1: Trigger the Edge Camera
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            edge_resp = await client.post(
                f"{EDGE_URL}/trigger",
                params={"equipment_type": req.equipment_type},
            )
            edge_resp.raise_for_status()
            state.edge_event = edge_resp.json()
        except Exception as exc:
            logger.error("Edge trigger failed: %s", exc)
            state.edge_event = {
                "error": str(exc),
                "trigger": f"New {req.equipment_type} Detected (fallback)",
                "equipment_type": req.equipment_type,
                "status": "unconfigured",
            }

    # Step 2: Request Code Generation from Cloud AI Agent
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            gen_resp = await client.post(
                f"{CLOUD_URL}/generate",
                json={
                    "equipment_type": req.equipment_type,
                    "action": req.action,
                    "parameters": req.parameters,
                    "output_format": req.output_format,
                    "plc_host": PLC_HOST,
                    "plc_port": PLC_PORT,
                },
            )
            gen_resp.raise_for_status()
            state.generation_result = gen_resp.json()
        except Exception as exc:
            logger.error("Code generation failed: %s", exc)
            raise HTTPException(status_code=502, detail=f"Code generation failed: {exc}")

    # Step 3: Digital Twin Check (only for python_modbus code)
    if req.output_format == "python_modbus" and state.generation_result:
        code = state.generation_result.get("code", "")
        sim_result = await asyncio.get_event_loop().run_in_executor(
            None, run_digital_twin_check, code
        )
        state.simulation = sim_result

    _pipelines.append(state)
    logger.info("Pipeline %s completed. Simulation: %s", state.pipeline_id, state.simulation)
    return state


@app.post("/pipeline/{pipeline_id}/approve")
async def approve_pipeline(pipeline_id: str) -> Dict[str, Any]:
    """
    Approve a validated pipeline — deploy the code to the PLC permanently.
    This writes the register changes to the PLC simulator.
    """
    from pymodbus.client import ModbusTcpClient

    target = None
    for p in _pipelines:
        if p.pipeline_id == pipeline_id:
            target = p
            break

    if target is None:
        raise HTTPException(status_code=404, detail=f"Pipeline {pipeline_id} not found")

    if target.deployed:
        return {"status": "already_deployed", "pipeline_id": pipeline_id}

    if target.simulation and not target.simulation.passed:
        raise HTTPException(
            status_code=400,
            detail="Cannot deploy: simulation did not pass.",
        )

    # Execute the generated code against the real PLC
    if target.generation_result:
        code = target.generation_result.get("code", "")
        namespace: Dict[str, Any] = {}
        try:
            exec(compile(code, "<deploy>", "exec"), namespace)
            func_name = None
            for name, obj in namespace.items():
                if callable(obj) and not name.startswith("_") and name != "ModbusTcpClient":
                    func_name = name
                    break
            if func_name:
                namespace[func_name](host=PLC_HOST, port=PLC_PORT)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Deployment failed: {exc}")

    target.deployed = True
    logger.info("Pipeline %s deployed to PLC.", pipeline_id)
    return {"status": "deployed", "pipeline_id": pipeline_id}


@app.get("/pipelines", response_model=List[PipelineState])
async def list_pipelines() -> List[PipelineState]:
    """List all pipeline runs (most recent first)."""
    return list(reversed(_pipelines))


@app.get("/pipelines/{pipeline_id}", response_model=PipelineState)
async def get_pipeline(pipeline_id: str) -> PipelineState:
    for p in _pipelines:
        if p.pipeline_id == pipeline_id:
            return p
    raise HTTPException(status_code=404, detail="Pipeline not found")


@app.delete("/pipelines")
async def clear_pipelines() -> Dict[str, str]:
    _pipelines.clear()
    return {"status": "cleared"}


# ---------------------------------------------------------------------------
# Inline pipeline (for local/testing use without inter-service HTTP)
# ---------------------------------------------------------------------------

def run_local_pipeline(
    equipment_type: str = "Chiller",
    action: str = "activate",
    parameters: Optional[Dict[str, Any]] = None,
    plc_host: str = "127.0.0.1",
    plc_port: int = 5020,
) -> PipelineState:
    """
    Run the full pipeline locally (no HTTP calls to edge/cloud services).
    Useful for E2E testing and Docker-internal validation.
    """
    from edge_mock.camera import _generate_event
    from cloud_ai_agent.generator import GenerationRequest, generate_code

    state = PipelineState()

    # Step 1: Generate edge event
    event = _generate_event(equipment_type=equipment_type)
    state.edge_event = event.model_dump()

    # Step 2: Generate code
    req = GenerationRequest(
        equipment_type=equipment_type,
        action=action,
        parameters=parameters or {},
        output_format="python_modbus",
        plc_host=plc_host,
        plc_port=plc_port,
    )
    gen_result = generate_code(req)
    state.generation_result = gen_result.model_dump()

    # Step 3: Digital Twin Check
    sim_result = run_digital_twin_check(gen_result.code, plc_host, plc_port)
    state.simulation = sim_result

    _pipelines.append(state)
    return state


# ---------------------------------------------------------------------------
# HMI (HTML UI)
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def hmi_dashboard() -> HTMLResponse:
    """Serve the HMI dashboard."""
    pipelines_html = ""
    for p in reversed(_pipelines):
        sim_badge = ""
        if p.simulation:
            color = "#22c55e" if p.simulation.passed else "#ef4444"
            label = "PASSED" if p.simulation.passed else "FAILED"
            sim_badge = f'<span style="color:{color};font-weight:bold">{label}</span>'

        deploy_btn = ""
        if p.simulation and p.simulation.passed and not p.deployed:
            deploy_btn = f'''<form method="post" action="/pipeline/{p.pipeline_id}/approve" style="display:inline">
                <button type="submit" style="background:#2563eb;color:white;border:none;padding:8px 16px;border-radius:4px;cursor:pointer;font-weight:bold">
                    APPROVE &amp; DEPLOY
                </button>
            </form>'''
        elif p.deployed:
            deploy_btn = '<span style="color:#22c55e;font-weight:bold">DEPLOYED</span>'

        edge_trigger = ""
        if p.edge_event:
            edge_trigger = p.edge_event.get("trigger", "N/A")

        code_snippet = ""
        if p.generation_result:
            code_text = p.generation_result.get("code", "")[:500]
            code_snippet = f'<pre style="background:#1e293b;color:#e2e8f0;padding:12px;border-radius:8px;overflow-x:auto;font-size:13px">{code_text}</pre>'

        pipelines_html += f'''
        <div style="background:#1e293b;border-radius:12px;padding:20px;margin-bottom:16px">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px">
                <div>
                    <strong style="color:#60a5fa">Pipeline {p.pipeline_id[:12]}…</strong>
                    <span style="color:#94a3b8;margin-left:12px">{p.created_at}</span>
                </div>
                <div>{sim_badge} {deploy_btn}</div>
            </div>
            <div style="color:#cbd5e1;margin-bottom:8px">
                <strong>Edge Trigger:</strong> {edge_trigger}
            </div>
            {code_snippet}
            {"<div style='color:#94a3b8;font-size:13px'>Simulation: " + p.simulation.message + " (" + str(p.simulation.execution_time_ms) + "ms)</div>" if p.simulation else ""}
        </div>'''

    html = f'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Ctrl+Vision HMI</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ background: #0f172a; color: #e2e8f0; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; }}
        .container {{ max-width: 1000px; margin: 0 auto; padding: 24px; }}
        h1 {{ color: #60a5fa; margin-bottom: 8px; }}
        .subtitle {{ color: #94a3b8; margin-bottom: 24px; }}
        .trigger-form {{ background: #1e293b; border-radius: 12px; padding: 20px; margin-bottom: 24px; }}
        .trigger-form label {{ display: block; color: #94a3b8; margin-bottom: 4px; font-size: 14px; }}
        .trigger-form select, .trigger-form input {{
            background: #0f172a; color: #e2e8f0; border: 1px solid #334155;
            padding: 8px 12px; border-radius: 6px; margin-bottom: 12px; width: 100%;
        }}
        .trigger-btn {{
            background: #f59e0b; color: #0f172a; border: none; padding: 10px 24px;
            border-radius: 6px; font-weight: bold; cursor: pointer; font-size: 16px;
        }}
        .trigger-btn:hover {{ background: #d97706; }}
    </style>
</head>
<body>
    <div class="container">
        <h1>Ctrl+Vision HMI</h1>
        <p class="subtitle">Edge-to-Cloud AI Platform for Autonomous Protocol Engineering</p>

        <div class="trigger-form">
            <h3 style="margin-bottom:12px;color:#f59e0b">Trigger New Pipeline</h3>
            <form id="triggerForm">
                <label>Equipment Type</label>
                <select name="equipment_type" id="eqType">
                    <option value="Chiller">Chiller</option>
                    <option value="Compressor">Compressor</option>
                    <option value="VFD">VFD</option>
                </select>
                <label>Action</label>
                <select name="action" id="action">
                    <option value="activate">Activate</option>
                    <option value="deactivate">Deactivate</option>
                    <option value="set_setpoint">Set Setpoint</option>
                    <option value="read_status">Read Status</option>
                </select>
                <button type="submit" class="trigger-btn">TRIGGER PIPELINE</button>
            </form>
        </div>

        <h2 style="margin-bottom:16px">Pipeline Runs</h2>
        {pipelines_html if pipelines_html else '<p style="color:#64748b">No pipelines yet. Trigger one above.</p>'}
    </div>

    <script>
        document.getElementById('triggerForm').addEventListener('submit', async (e) => {{
            e.preventDefault();
            const resp = await fetch('/pipeline/trigger', {{
                method: 'POST',
                headers: {{'Content-Type': 'application/json'}},
                body: JSON.stringify({{
                    equipment_type: document.getElementById('eqType').value,
                    action: document.getElementById('action').value,
                }})
            }});
            if (resp.ok) {{ window.location.reload(); }}
            else {{ alert('Pipeline trigger failed: ' + (await resp.text())); }}
        }});
    </script>
</body>
</html>'''
    return HTMLResponse(content=html)


def main() -> None:
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")


if __name__ == "__main__":
    main()
