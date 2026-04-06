"""Tests for the Cloud AI Agent — RAG Engine + Code Generator."""

import pytest
from fastapi.testclient import TestClient

from cloud_ai_agent.rag_engine import app as rag_app, search_knowledge_base
from cloud_ai_agent.generator import (
    GenerationRequest,
    app as gen_app,
    generate_code,
)


# ---------------------------------------------------------------------------
# RAG Engine tests
# ---------------------------------------------------------------------------

rag_client = TestClient(rag_app)


def test_rag_health():
    resp = rag_client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["service"] == "rag_engine"


def test_rag_search_chiller():
    resp = rag_client.get("/search", params={"query": "activate Chiller"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_matches"] >= 1
    assert data["results"][0]["equipment_type"] == "Chiller"


def test_rag_search_by_equipment_type():
    resp = rag_client.get(
        "/search",
        params={"query": "start", "equipment_type": "Compressor"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert any(r["equipment_type"] == "Compressor" for r in data["results"])


def test_rag_list_datasheets():
    resp = rag_client.get("/datasheets")
    assert resp.status_code == 200
    sheets = resp.json()
    assert len(sheets) >= 3
    assert all("id" in s and "equipment_type" in s for s in sheets)


def test_rag_get_datasheet_by_id():
    resp = rag_client.get("/datasheets/DS-001")
    assert resp.status_code == 200
    ds = resp.json()
    assert ds["equipment_type"] == "Chiller"
    assert "registers" in ds


def test_rag_get_datasheet_not_found():
    resp = rag_client.get("/datasheets/DS-999")
    assert resp.status_code == 200
    assert "error" in resp.json()


def test_search_knowledge_base_function():
    results = search_knowledge_base("activate", equipment_type="Chiller")
    assert len(results) >= 1
    assert results[0].equipment_type == "Chiller"
    assert results[0].relevance_score > 0


# ---------------------------------------------------------------------------
# Code Generator tests
# ---------------------------------------------------------------------------

gen_client = TestClient(gen_app)


def test_gen_health():
    resp = gen_client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["service"] == "code_generator"


def test_generate_python_activate_chiller():
    req = GenerationRequest(
        equipment_type="Chiller",
        action="activate",
        plc_host="localhost",
        plc_port=5020,
    )
    result = generate_code(req)
    assert result.language == "python"
    assert "activate_chiller" in result.code
    assert "ModbusTcpClient" in result.code
    assert "write_register" in result.code
    assert len(result.safety_warnings) > 0
    assert result.rag_sources == ["DS-001"]


def test_generate_python_deactivate():
    req = GenerationRequest(
        equipment_type="Chiller",
        action="deactivate",
        plc_host="localhost",
        plc_port=5020,
    )
    result = generate_code(req)
    assert "deactivate_chiller" in result.code
    assert "value=0" in result.code or "value= 0" in result.code


def test_generate_python_set_setpoint():
    req = GenerationRequest(
        equipment_type="Chiller",
        action="set_setpoint",
        parameters={"value": 250},
        plc_host="localhost",
        plc_port=5020,
    )
    result = generate_code(req)
    assert "set_chiller_setpoint" in result.code
    assert "250" in result.code


def test_generate_python_read_status():
    req = GenerationRequest(
        equipment_type="Chiller",
        action="read_status",
        plc_host="localhost",
        plc_port=5020,
    )
    result = generate_code(req)
    assert "read_chiller_status" in result.code
    assert "read_holding_registers" in result.code


def test_generate_structured_text_activate():
    req = GenerationRequest(
        equipment_type="Chiller",
        action="activate",
        output_format="structured_text",
    )
    result = generate_code(req)
    assert result.language == "iec-61131-st"
    assert "PROGRAM Activate_Chiller" in result.code
    assert "END_PROGRAM" in result.code
    assert "fault_detected" in result.code


def test_generate_structured_text_deactivate():
    req = GenerationRequest(
        equipment_type="VFD",
        action="deactivate",
        output_format="structured_text",
    )
    result = generate_code(req)
    assert "PROGRAM Deactivate_VFD" in result.code
    assert "MW" in result.code


def test_generate_structured_text_set_setpoint():
    req = GenerationRequest(
        equipment_type="Chiller",
        action="set_setpoint",
        parameters={"value": 180},
        output_format="structured_text",
    )
    result = generate_code(req)
    assert "Set_Chiller_Setpoint" in result.code
    assert "180" in result.code


def test_generate_unknown_equipment():
    """Equipment with zero RAG matches should raise ValueError."""
    req = GenerationRequest(
        equipment_type="NonExistent",
        action="xyzzy_noop",  # nonsense action that won't match any keywords
    )
    with pytest.raises(ValueError, match="No datasheet found"):
        generate_code(req)


def test_generate_unsupported_action():
    req = GenerationRequest(
        equipment_type="Chiller",
        action="explode",
    )
    with pytest.raises(ValueError, match="Unsupported action"):
        generate_code(req)


def test_generate_api_endpoint():
    resp = gen_client.post("/generate", json={
        "equipment_type": "Compressor",
        "action": "activate",
        "plc_host": "localhost",
        "plc_port": 5020,
    })
    assert resp.status_code == 200
    data = resp.json()
    assert "activate_compressor" in data["code"]


def test_generate_api_not_found():
    resp = gen_client.post("/generate", json={
        "equipment_type": "Toaster",
        "action": "xyzzy_noop",
    })
    assert resp.status_code == 404


def test_supported_actions():
    resp = gen_client.get("/supported-actions")
    assert resp.status_code == 200
    data = resp.json()
    assert "activate" in data["actions"]
    assert "python_modbus" in data["output_formats"]


def test_no_slave_parameter_in_generated_code():
    """Ensure generated Python code uses pymodbus 3.12 API (no slave param)."""
    req = GenerationRequest(
        equipment_type="Chiller",
        action="activate",
        plc_host="localhost",
        plc_port=5020,
    )
    result = generate_code(req)
    assert "slave=" not in result.code
