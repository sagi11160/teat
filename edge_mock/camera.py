"""
Edge Mock — Simulates a Jetson Orin Nano with a camera trigger endpoint.

Provides a FastAPI service that emits mock visual-detection events as if an
edge AI camera spotted new industrial equipment on the factory floor.
"""

import logging
import random
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional

from fastapi import FastAPI, Query
from pydantic import BaseModel, Field

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [EDGE-CAM] %(levelname)s %(message)s",
)
logger = logging.getLogger("edge_mock")

app = FastAPI(
    title="Edge Camera Simulator",
    description="Simulates Jetson Orin Nano visual-detection events.",
    version="1.0.0",
)

# ---------------------------------------------------------------------------
# Domain models
# ---------------------------------------------------------------------------

class DetectionEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    trigger: str
    equipment_type: str
    mac_address: str
    ip_address: str
    status: str
    confidence: float
    metadata: Dict[str, str] = Field(default_factory=dict)


class EventHistory(BaseModel):
    total: int
    events: List[DetectionEvent]


# ---------------------------------------------------------------------------
# In-memory event store
# ---------------------------------------------------------------------------
_event_store: List[DetectionEvent] = []

# Realistic mock data pools
_EQUIPMENT_TYPES = ["Chiller", "Compressor", "VFD", "HMI Panel", "RTU"]
_TRIGGERS = [
    "New {eq} Detected",
    "{eq} Configuration Drift",
    "{eq} Firmware Mismatch",
    "{eq} Unregistered on Network",
]
_STATUSES = ["unconfigured", "misconfigured", "firmware_outdated", "unknown"]


def _random_mac() -> str:
    return ":".join(f"{random.randint(0, 255):02X}" for _ in range(6))


def _random_ip() -> str:
    return f"192.168.1.{random.randint(10, 250)}"


def _generate_event(
    equipment_type: Optional[str] = None,
    trigger: Optional[str] = None,
) -> DetectionEvent:
    eq = equipment_type or random.choice(_EQUIPMENT_TYPES)
    trig = trigger or random.choice(_TRIGGERS).format(eq=eq)
    return DetectionEvent(
        trigger=trig,
        equipment_type=eq,
        mac_address=_random_mac(),
        ip_address=_random_ip(),
        status=random.choice(_STATUSES),
        confidence=round(random.uniform(0.80, 0.99), 2),
        metadata={
            "camera_id": f"CAM-{random.randint(1, 4):02d}",
            "zone": f"Zone-{random.choice(['A', 'B', 'C'])}",
        },
    )


# ---------------------------------------------------------------------------
# API endpoints
# ---------------------------------------------------------------------------

@app.get("/health")
async def health() -> Dict[str, str]:
    return {"status": "ok", "service": "edge_mock"}


@app.post("/trigger", response_model=DetectionEvent)
async def trigger_detection(
    equipment_type: Optional[str] = Query(None, description="Override equipment type"),
    trigger_text: Optional[str] = Query(None, description="Override trigger text"),
) -> DetectionEvent:
    """Simulate a camera detection event and store it."""
    event = _generate_event(equipment_type=equipment_type, trigger=trigger_text)
    _event_store.append(event)
    logger.info(
        "Detection triggered: %s | %s | %s",
        event.trigger,
        event.equipment_type,
        event.mac_address,
    )
    return event


@app.get("/events", response_model=EventHistory)
async def list_events(
    limit: int = Query(50, ge=1, le=500),
) -> EventHistory:
    """Return the most recent detection events."""
    recent = list(reversed(_event_store[-limit:]))
    return EventHistory(total=len(_event_store), events=recent)


@app.get("/events/latest", response_model=Optional[DetectionEvent])
async def latest_event() -> Optional[DetectionEvent]:
    """Return the single most recent detection event, or null."""
    if _event_store:
        return _event_store[-1]
    return None


@app.delete("/events")
async def clear_events() -> Dict[str, str]:
    """Clear the in-memory event store."""
    _event_store.clear()
    return {"status": "cleared"}


def main() -> None:
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001, log_level="info")


if __name__ == "__main__":
    main()
