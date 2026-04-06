"""
RAG Engine — Local JSON-based knowledge retrieval for OT datasheets.

Acts as a minimal Retrieval-Augmented Generation (RAG) store. Contains
mock datasheets for industrial equipment and can be queried by equipment
type or keyword to retrieve relevant configuration instructions.
"""

import json
import logging
import os
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, Query
from pydantic import BaseModel, Field

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [RAG] %(levelname)s %(message)s",
)
logger = logging.getLogger("rag_engine")

# ---------------------------------------------------------------------------
# Knowledge Base (embedded JSON — acts as a vector DB mock)
# ---------------------------------------------------------------------------

KNOWLEDGE_BASE: List[Dict[str, Any]] = [
    {
        "id": "DS-001",
        "equipment_type": "Chiller",
        "manufacturer": "Carrier",
        "model": "30XA",
        "protocol": "Modbus TCP",
        "registers": {
            "command": {"address": 40001, "offset": 0, "type": "INT", "description": "0=OFF, 1=ON, 2=COOL, 3=HEAT"},
            "status":  {"address": 40002, "offset": 1, "type": "INT", "description": "0=IDLE, 1=RUNNING, 2=FAULT"},
            "setpoint": {"address": 40003, "offset": 2, "type": "INT", "description": "Temperature setpoint in °C×10"},
            "actual_temp": {"address": 40004, "offset": 3, "type": "INT", "description": "Actual temperature in °C×10"},
            "alarm": {"address": 40005, "offset": 4, "type": "INT", "description": "Bit-field alarm register"},
        },
        "instructions": [
            "To activate Chiller, write Int 1 to Modbus Register 40001 (offset 0).",
            "To set cooling mode, write Int 2 to Modbus Register 40001.",
            "To set heating mode, write Int 3 to Modbus Register 40001.",
            "To shut down Chiller, write Int 0 to Modbus Register 40001.",
            "Read Register 40002 to check running status (1=RUNNING).",
            "Set temperature setpoint by writing °C×10 to Register 40003 (e.g., 220 = 22.0°C).",
            "Read Register 40004 for actual temperature.",
            "Check Register 40005 for alarm bit-field (bit 0=overtemp, bit 1=low-pressure).",
        ],
        "safety_notes": [
            "Always verify status register (40002) is not FAULT (2) before issuing commands.",
            "Maximum setpoint is 350 (35.0°C). Minimum is 50 (5.0°C).",
        ],
    },
    {
        "id": "DS-002",
        "equipment_type": "Compressor",
        "manufacturer": "Atlas Copco",
        "model": "GA 37",
        "protocol": "Modbus TCP",
        "registers": {
            "command": {"address": 40010, "offset": 9, "type": "INT", "description": "0=STOP, 1=START"},
            "status":  {"address": 40011, "offset": 10, "type": "INT", "description": "0=OFF, 1=ON, 2=FAULT"},
            "pressure": {"address": 40012, "offset": 11, "type": "INT", "description": "Pressure in PSI×10"},
        },
        "instructions": [
            "To start Compressor, write Int 1 to Modbus Register 40010 (offset 9).",
            "To stop Compressor, write Int 0 to Modbus Register 40010.",
            "Read Register 40011 for operational status.",
            "Read Register 40012 for current pressure (PSI×10).",
        ],
        "safety_notes": [
            "Do not start compressor if status register shows FAULT (2).",
        ],
    },
    {
        "id": "DS-003",
        "equipment_type": "VFD",
        "manufacturer": "Siemens",
        "model": "SINAMICS G120",
        "protocol": "Modbus TCP",
        "registers": {
            "command": {"address": 40020, "offset": 19, "type": "INT", "description": "0=STOP, 1=RUN_FWD, 2=RUN_REV"},
            "speed_setpoint": {"address": 40021, "offset": 20, "type": "INT", "description": "Speed in RPM"},
            "actual_speed": {"address": 40022, "offset": 21, "type": "INT", "description": "Actual speed in RPM"},
            "fault_code": {"address": 40023, "offset": 22, "type": "INT", "description": "0=No fault"},
        },
        "instructions": [
            "To run VFD forward, write Int 1 to Register 40020 (offset 19).",
            "To run VFD in reverse, write Int 2 to Register 40020.",
            "To stop VFD, write Int 0 to Register 40020.",
            "Set desired speed by writing RPM value to Register 40021.",
            "Read Register 40022 for actual speed feedback.",
        ],
        "safety_notes": [
            "Check fault_code register (40023) before issuing run commands.",
            "Maximum speed setpoint is 3600 RPM.",
        ],
    },
]


class RAGResult(BaseModel):
    """A single retrieval result."""
    datasheet_id: str
    equipment_type: str
    manufacturer: str
    model: str
    relevance_score: float
    instructions: List[str]
    safety_notes: List[str]
    registers: Dict[str, Any]


class RAGResponse(BaseModel):
    """Response containing matched datasheets."""
    query: str
    results: List[RAGResult]
    total_matches: int


def search_knowledge_base(
    query: str,
    equipment_type: Optional[str] = None,
) -> List[RAGResult]:
    """
    Search the knowledge base by keyword and optional equipment type filter.
    Uses simple keyword matching (simulates a vector similarity search).
    """
    query_lower = query.lower()
    results: List[RAGResult] = []

    for ds in KNOWLEDGE_BASE:
        score = 0.0

        # Equipment type exact match is a strong signal
        if equipment_type and ds["equipment_type"].lower() == equipment_type.lower():
            score += 0.5

        # Keyword matching across all text fields
        searchable_text = " ".join([
            ds["equipment_type"],
            ds["manufacturer"],
            ds["model"],
            " ".join(ds["instructions"]),
            " ".join(ds["safety_notes"]),
        ]).lower()

        query_tokens = query_lower.split()
        matched_tokens = sum(1 for t in query_tokens if t in searchable_text)
        if query_tokens:
            score += 0.5 * (matched_tokens / len(query_tokens))

        if score > 0.0:
            results.append(RAGResult(
                datasheet_id=ds["id"],
                equipment_type=ds["equipment_type"],
                manufacturer=ds["manufacturer"],
                model=ds["model"],
                relevance_score=round(min(score, 1.0), 2),
                instructions=ds["instructions"],
                safety_notes=ds["safety_notes"],
                registers=ds["registers"],
            ))

    # Sort by relevance descending
    results.sort(key=lambda r: r.relevance_score, reverse=True)
    return results


# ---------------------------------------------------------------------------
# FastAPI application (can be used standalone or imported)
# ---------------------------------------------------------------------------

app = FastAPI(
    title="RAG Engine",
    description="OT datasheet retrieval engine for code generation.",
    version="1.0.0",
)


@app.get("/health")
async def health() -> Dict[str, str]:
    return {"status": "ok", "service": "rag_engine"}


@app.get("/search", response_model=RAGResponse)
async def search(
    query: str = Query(..., description="Search query"),
    equipment_type: Optional[str] = Query(None, description="Filter by equipment type"),
) -> RAGResponse:
    """Search the OT knowledge base."""
    results = search_knowledge_base(query, equipment_type)
    return RAGResponse(query=query, results=results, total_matches=len(results))


@app.get("/datasheets", response_model=List[Dict[str, Any]])
async def list_datasheets() -> List[Dict[str, Any]]:
    """List all available datasheets (summary view)."""
    return [
        {
            "id": ds["id"],
            "equipment_type": ds["equipment_type"],
            "manufacturer": ds["manufacturer"],
            "model": ds["model"],
        }
        for ds in KNOWLEDGE_BASE
    ]


@app.get("/datasheets/{datasheet_id}")
async def get_datasheet(datasheet_id: str) -> Dict[str, Any]:
    """Get a specific datasheet by ID."""
    for ds in KNOWLEDGE_BASE:
        if ds["id"] == datasheet_id:
            return ds
    return {"error": f"Datasheet {datasheet_id} not found"}


def main() -> None:
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8003, log_level="info")


if __name__ == "__main__":
    main()
