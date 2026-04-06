"""
Code Generator — Generates Structured Text (ST) and Python Modbus scripts.

Queries the RAG engine for relevant datasheets and produces executable code
that can be validated against the PLC simulator before deployment.
"""

import logging
import textwrap
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from cloud_ai_agent.rag_engine import RAGResult, search_knowledge_base

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [GEN] %(levelname)s %(message)s",
)
logger = logging.getLogger("generator")

# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class GenerationRequest(BaseModel):
    """Input for the code generation endpoint."""
    equipment_type: str = Field(..., description="Type of equipment (e.g. Chiller)")
    action: str = Field(..., description="Desired action (e.g. activate, set_setpoint)")
    parameters: Dict[str, Any] = Field(default_factory=dict, description="Action parameters")
    output_format: str = Field(
        default="python_modbus",
        description="Output format: 'python_modbus' or 'structured_text'",
    )
    plc_host: str = Field(default="plc_simulator", description="PLC hostname")
    plc_port: int = Field(default=5020, description="PLC Modbus port")


class GeneratedCode(BaseModel):
    """Output from the code generation engine."""
    request_id: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
    )
    equipment_type: str
    action: str
    output_format: str
    code: str
    language: str
    description: str
    safety_warnings: List[str]
    rag_sources: List[str]
    generated_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


# ---------------------------------------------------------------------------
# Code generation logic
# ---------------------------------------------------------------------------

def _generate_python_modbus(
    rag_result: RAGResult,
    action: str,
    parameters: Dict[str, Any],
    plc_host: str,
    plc_port: int,
) -> str:
    """Generate a Python script that performs the requested Modbus operation."""
    regs = rag_result.registers
    eq = rag_result.equipment_type

    if action == "activate":
        cmd_reg = regs.get("command", {})
        offset = cmd_reg.get("offset", 0)
        return textwrap.dedent(f"""\
            #!/usr/bin/env python3
            \"\"\"Auto-generated Modbus script — Activate {eq}.\"\"\"
            from pymodbus.client import ModbusTcpClient

            def activate_{eq.lower()}(host: str = "{plc_host}", port: int = {plc_port}) -> bool:
                client = ModbusTcpClient(host, port=port)
                if not client.connect():
                    raise ConnectionError(f"Cannot connect to PLC at {{host}}:{{port}}")
                try:
                    # Read status register first (safety check)
                    status_reg = {regs.get("status", {}).get("offset", 1)}
                    status = client.read_holding_registers(address=status_reg, count=1)
                    if not status.isError() and status.registers[0] == 2:
                        raise RuntimeError("{eq} is in FAULT state — cannot activate.")
                    # Write command = 1 (ON) to register offset {offset}
                    result = client.write_register(address={offset}, value=1)
                    if result.isError():
                        raise RuntimeError(f"Write failed: {{result}}")
                    # Verify write
                    verify = client.read_holding_registers(address={offset}, count=1)
                    if verify.isError() or verify.registers[0] != 1:
                        raise RuntimeError("Verification failed: register not updated.")
                    return True
                finally:
                    client.close()

            if __name__ == "__main__":
                success = activate_{eq.lower()}()
                print(f"{eq} activation: {{'OK' if success else 'FAILED'}}")
        """)

    elif action == "deactivate":
        cmd_reg = regs.get("command", {})
        offset = cmd_reg.get("offset", 0)
        return textwrap.dedent(f"""\
            #!/usr/bin/env python3
            \"\"\"Auto-generated Modbus script — Deactivate {eq}.\"\"\"
            from pymodbus.client import ModbusTcpClient

            def deactivate_{eq.lower()}(host: str = "{plc_host}", port: int = {plc_port}) -> bool:
                client = ModbusTcpClient(host, port=port)
                if not client.connect():
                    raise ConnectionError(f"Cannot connect to PLC at {{host}}:{{port}}")
                try:
                    result = client.write_register(address={offset}, value=0)
                    if result.isError():
                        raise RuntimeError(f"Write failed: {{result}}")
                    verify = client.read_holding_registers(address={offset}, count=1)
                    if verify.isError() or verify.registers[0] != 0:
                        raise RuntimeError("Verification failed: register not updated.")
                    return True
                finally:
                    client.close()

            if __name__ == "__main__":
                success = deactivate_{eq.lower()}()
                print(f"{eq} deactivation: {{'OK' if success else 'FAILED'}}")
        """)

    elif action == "set_setpoint":
        sp_reg = regs.get("setpoint", regs.get("speed_setpoint", {}))
        offset = sp_reg.get("offset", 2)
        value = parameters.get("value", 220)
        return textwrap.dedent(f"""\
            #!/usr/bin/env python3
            \"\"\"Auto-generated Modbus script — Set {eq} setpoint.\"\"\"
            from pymodbus.client import ModbusTcpClient

            def set_{eq.lower()}_setpoint(value: int = {value}, host: str = "{plc_host}", port: int = {plc_port}) -> bool:
                client = ModbusTcpClient(host, port=port)
                if not client.connect():
                    raise ConnectionError(f"Cannot connect to PLC at {{host}}:{{port}}")
                try:
                    result = client.write_register(address={offset}, value=value)
                    if result.isError():
                        raise RuntimeError(f"Write failed: {{result}}")
                    verify = client.read_holding_registers(address={offset}, count=1)
                    if verify.isError() or verify.registers[0] != value:
                        raise RuntimeError("Verification failed: setpoint not applied.")
                    return True
                finally:
                    client.close()

            if __name__ == "__main__":
                success = set_{eq.lower()}_setpoint()
                print(f"{eq} setpoint update: {{'OK' if success else 'FAILED'}}")
        """)

    elif action == "read_status":
        status_reg = regs.get("status", {})
        offset = status_reg.get("offset", 1)
        return textwrap.dedent(f"""\
            #!/usr/bin/env python3
            \"\"\"Auto-generated Modbus script — Read {eq} status.\"\"\"
            from pymodbus.client import ModbusTcpClient

            def read_{eq.lower()}_status(host: str = "{plc_host}", port: int = {plc_port}) -> int:
                client = ModbusTcpClient(host, port=port)
                if not client.connect():
                    raise ConnectionError(f"Cannot connect to PLC at {{host}}:{{port}}")
                try:
                    result = client.read_holding_registers(address={offset}, count=1)
                    if result.isError():
                        raise RuntimeError(f"Read failed: {{result}}")
                    return result.registers[0]
                finally:
                    client.close()

            if __name__ == "__main__":
                status = read_{eq.lower()}_status()
                status_map = {{0: "IDLE", 1: "RUNNING", 2: "FAULT"}}
                print(f"{eq} status: {{status_map.get(status, 'UNKNOWN')}} ({{status}})")
        """)

    else:
        raise ValueError(f"Unsupported action: {action}")


def _generate_structured_text(
    rag_result: RAGResult,
    action: str,
    parameters: Dict[str, Any],
) -> str:
    """Generate IEC 61131-3 Structured Text (ST) code."""
    eq = rag_result.equipment_type
    regs = rag_result.registers

    if action == "activate":
        cmd_addr = regs.get("command", {}).get("address", 40001)
        status_addr = regs.get("status", {}).get("address", 40002)
        return textwrap.dedent(f"""\
            // ============================================================
            // Auto-Generated Structured Text — Activate {eq}
            // Equipment: {rag_result.manufacturer} {rag_result.model}
            // Generated: {datetime.now(timezone.utc).isoformat()}
            // ============================================================
            PROGRAM Activate_{eq}
            VAR
                cmd_register  : INT := 0;    // MW{cmd_addr - 40001}
                status_register : INT := 0;  // MW{status_addr - 40001}
                activation_ok : BOOL := FALSE;
                fault_detected : BOOL := FALSE;
            END_VAR

            // Step 1: Read current status
            status_register := MW{status_addr - 40001};

            // Step 2: Safety check — do not activate if FAULT
            IF status_register = 2 THEN
                fault_detected := TRUE;
                activation_ok := FALSE;
            ELSE
                // Step 3: Write activation command
                MW{cmd_addr - 40001} := 1;  // 1 = ON
                activation_ok := TRUE;
                fault_detected := FALSE;
            END_IF;

            END_PROGRAM
        """)

    elif action == "deactivate":
        cmd_addr = regs.get("command", {}).get("address", 40001)
        return textwrap.dedent(f"""\
            // ============================================================
            // Auto-Generated Structured Text — Deactivate {eq}
            // Equipment: {rag_result.manufacturer} {rag_result.model}
            // Generated: {datetime.now(timezone.utc).isoformat()}
            // ============================================================
            PROGRAM Deactivate_{eq}
            VAR
                cmd_register : INT := 0;  // MW{cmd_addr - 40001}
            END_VAR

            // Write OFF command
            MW{cmd_addr - 40001} := 0;  // 0 = OFF

            END_PROGRAM
        """)

    elif action == "set_setpoint":
        sp_reg = regs.get("setpoint", regs.get("speed_setpoint", {}))
        sp_addr = sp_reg.get("address", 40003)
        value = parameters.get("value", 220)
        return textwrap.dedent(f"""\
            // ============================================================
            // Auto-Generated Structured Text — Set {eq} Setpoint
            // Equipment: {rag_result.manufacturer} {rag_result.model}
            // Generated: {datetime.now(timezone.utc).isoformat()}
            // ============================================================
            PROGRAM Set_{eq}_Setpoint
            VAR
                setpoint_value : INT := {value};  // MW{sp_addr - 40001}
            END_VAR

            MW{sp_addr - 40001} := setpoint_value;

            END_PROGRAM
        """)

    else:
        raise ValueError(f"Unsupported action for ST generation: {action}")


def generate_code(request: GenerationRequest) -> GeneratedCode:
    """
    Main generation entry-point. Queries RAG, picks the best match,
    and generates code in the requested format.
    """
    # Query RAG
    results = search_knowledge_base(
        query=f"{request.action} {request.equipment_type}",
        equipment_type=request.equipment_type,
    )

    if not results:
        raise ValueError(
            f"No datasheet found for equipment type '{request.equipment_type}'"
        )

    best = results[0]
    logger.info(
        "RAG match: %s %s (score %.2f)",
        best.manufacturer,
        best.model,
        best.relevance_score,
    )

    if request.output_format == "structured_text":
        code = _generate_structured_text(best, request.action, request.parameters)
        language = "iec-61131-st"
    else:
        code = _generate_python_modbus(
            best, request.action, request.parameters,
            request.plc_host, request.plc_port,
        )
        language = "python"

    return GeneratedCode(
        equipment_type=request.equipment_type,
        action=request.action,
        output_format=request.output_format,
        code=code,
        language=language,
        description=f"Generated {request.output_format} code to {request.action} {request.equipment_type}",
        safety_warnings=best.safety_notes,
        rag_sources=[best.datasheet_id],
    )


# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Code Generator",
    description="Generates ST and Python Modbus scripts from RAG datasheets.",
    version="1.0.0",
)


@app.get("/health")
async def health() -> Dict[str, str]:
    return {"status": "ok", "service": "code_generator"}


@app.post("/generate", response_model=GeneratedCode)
async def generate(request: GenerationRequest) -> GeneratedCode:
    """Generate code for the given equipment action."""
    try:
        result = generate_code(request)
        logger.info(
            "Generated %s code for %s/%s",
            result.output_format,
            result.equipment_type,
            result.action,
        )
        return result
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@app.get("/supported-actions")
async def supported_actions() -> Dict[str, List[str]]:
    return {
        "actions": ["activate", "deactivate", "set_setpoint", "read_status"],
        "output_formats": ["python_modbus", "structured_text"],
    }


def main() -> None:
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8002, log_level="info")


if __name__ == "__main__":
    main()
