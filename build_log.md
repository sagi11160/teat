# Ctrl+Vision MVP — Build Log

## Project Structure
```
/ctrl_vision_mvp
  /edge_mock          # Simulates Jetson Orin Nano & Camera triggers
  /gateway_hmi        # The DMZ server (FastAPI) and HMI (HTML)
  /plc_simulator      # Modbus TCP/Snap7 Server simulating Siemens S7-1200
  /cloud_ai_agent     # Mock Devin/LLM API for RAG & Code Gen
  docker-compose.yml
  build_log.md
  run_e2e_test.py
  requirements.txt
  README.md
```

## Phase 1: OT Hardware Simulators
- [x] `plc_simulator/server.py` — Async Modbus TCP server on port 5020
  - Uses pymodbus 3.12 `ModbusDeviceContext` (renamed from `ModbusSlaveContext`)
  - Compensates for pymodbus 3.12 internal +1 address offset in `build_context()`
  - Holding registers: Chiller Command, Status, Setpoint, Actual Temp, Alarm
- [x] `edge_mock/camera.py` — FastAPI service generating mock detection events
  - Supports equipment types: Chiller, Compressor, VFD, HMI Panel, RTU
  - Event store with trigger, list, latest, and clear endpoints
- [x] 3 PLC tests + 7 Edge tests = **10 tests passing**

### Pivot Log
- **pymodbus 3.12 API changes:** `ModbusSlaveContext` → `ModbusDeviceContext`, `slaves=` → `devices=`, `slave=0` parameter removed from client methods (now `device_id`). Fixed all references.
- **Address offset:** pymodbus 3.12 applies +1 internal offset (no `zero_mode`). Fixed by padding values array in `build_context()`.

## Phase 2: Cloud AI Agent
- [x] `cloud_ai_agent/rag_engine.py` — JSON-based knowledge base with 3 datasheets
  - Chiller (Carrier 30XA), Compressor (Atlas Copco GA 37), VFD (Siemens SINAMICS G120)
  - Keyword-based search with relevance scoring
- [x] `cloud_ai_agent/generator.py` — Code generation engine
  - Python Modbus scripts: activate, deactivate, set_setpoint, read_status
  - IEC 61131-3 Structured Text: activate, deactivate, set_setpoint
  - All generated code uses pymodbus 3.12 API (no `slave=` parameter)
- [x] 21 tests passing (RAG + Generator)

## Phase 3: Gateway & HMI
- [x] `gateway_hmi/api.py` — FastAPI gateway with:
  - Pipeline trigger (edge → generate → validate)
  - Digital Twin Check: executes generated Modbus code against PLC simulator
  - Approval/Deploy: clicking approve permanently alters PLC registers
  - HTML HMI dashboard with pipeline visualization
  - Local pipeline mode for testing without inter-service HTTP
- [x] 9 tests passing (health, HMI, digital twin, pipeline, deploy)

## Phase 4: Containerization & E2E
- [x] 4 Dockerfiles (one per microservice)
- [x] `docker-compose.yml` with:
  - 3 isolated networks (ot_net, dmz_net, cloud_net)
  - Health checks for all services
  - Service dependency ordering
- [x] `run_e2e_test.py` — Dual-mode E2E test (local + docker)
  - Tests: PLC init → Edge trigger → Code gen → Digital twin → Pipeline → Register verification
  - Bonus: Deactivation cycle + Structured Text generation

## Final Results
- **40/40 unit & integration tests passing**
- **E2E integration test passing** (local mode verified)
- **0 placeholders** — all functions fully implemented
- **0 pivots needed** beyond pymodbus API adaptation
