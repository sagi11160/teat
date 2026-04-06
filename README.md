# Ctrl+Vision MVP

**Hybrid Edge-to-Cloud AI Platform for Autonomous Protocol Engineering & PLC Code Generation**

## Architecture

```
┌─────────────────┐     ┌──────────────────┐     ┌──────────────────┐
│   Edge Mock      │     │  Gateway HMI     │     │  Cloud AI Agent  │
│ (Jetson Camera)  │────▶│  (FastAPI + UI)  │◀────│  (RAG + CodeGen) │
│   Port 8001      │     │   Port 8000      │     │   Port 8002      │
└─────────────────┘     └────────┬─────────┘     └──────────────────┘
                                 │
                                 ▼
                        ┌──────────────────┐
                        │  PLC Simulator   │
                        │  (Modbus TCP)    │
                        │   Port 5020      │
                        └──────────────────┘
```

### Services

| Service | Dir | Port | Description |
|---------|-----|------|-------------|
| **PLC Simulator** | `plc_simulator/` | 5020 | Modbus TCP server simulating Siemens S7-1200 |
| **Edge Mock** | `edge_mock/` | 8001 | Simulates Jetson Orin Nano camera triggers |
| **Cloud AI Agent** | `cloud_ai_agent/` | 8002 | RAG engine + Structured Text / Python Modbus code generator |
| **Gateway HMI** | `gateway_hmi/` | 8000 | FastAPI gateway with digital twin validation + HTML HMI |

## Quick Start

### Local Development

```bash
pip install -r requirements.txt

# Run all unit tests (40 tests)
python -m pytest plc_simulator/test_server.py edge_mock/test_camera.py \
    cloud_ai_agent/test_cloud.py gateway_hmi/test_gateway.py -v

# Run E2E integration test
python run_e2e_test.py --mode local
```

### Docker Compose

```bash
docker-compose up --build

# In another terminal, run E2E against Docker:
python run_e2e_test.py --mode docker
```

### Access the HMI

Open [http://localhost:8000](http://localhost:8000) to view the HMI dashboard.

## How It Works

1. **Edge Camera** detects new industrial equipment (Chiller, Compressor, VFD)
2. **Gateway** receives the trigger and requests code from the **Cloud AI Agent**
3. **Cloud AI Agent** queries its RAG knowledge base (OT datasheets) and generates either:
   - Python Modbus scripts (executable against PLCs)
   - IEC 61131-3 Structured Text (ST) code
4. **Gateway** runs a **Digital Twin Check** — executes the generated code against the PLC simulator to verify safety
5. **HMI Dashboard** displays the pipeline: trigger → generated code → simulation result
6. Human clicks **APPROVE & DEPLOY** to permanently apply the register changes to the PLC

## Register Map (Chiller — Siemens S7-1200 Style)

| Address | Offset | Description |
|---------|--------|-------------|
| 40001 | 0 | Command (0=OFF, 1=ON, 2=COOL, 3=HEAT) |
| 40002 | 1 | Status (0=IDLE, 1=RUNNING, 2=FAULT) |
| 40003 | 2 | Setpoint Temperature (°C × 10) |
| 40004 | 3 | Actual Temperature (°C × 10) |
| 40005 | 4 | Alarm Register (bit-field) |

## Testing

- **40 unit/integration tests** across all 4 services
- **E2E test** validates the full pipeline: camera trigger → code gen → digital twin → PLC register change
- Tests use real Modbus TCP connections (pymodbus sync client)
- Mock-first TDD: all logic tested without physical hardware

## Tech Stack

- **Python 3.12**
- **pymodbus 3.12** — Modbus TCP server/client
- **FastAPI** — REST APIs for all services
- **Pydantic v2** — Data validation
- **httpx** — Async HTTP client
- **Docker Compose** — Multi-service orchestration
