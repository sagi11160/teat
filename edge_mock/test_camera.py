"""Tests for the Edge Mock Camera API."""

import pytest
from fastapi.testclient import TestClient

from edge_mock.camera import app, _event_store


@pytest.fixture(autouse=True)
def clear_store():
    """Clear event store before each test."""
    _event_store.clear()
    yield
    _event_store.clear()


client = TestClient(app)


def test_health():
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["service"] == "edge_mock"


def test_trigger_creates_event():
    resp = client.post("/trigger")
    assert resp.status_code == 200
    data = resp.json()
    assert "event_id" in data
    assert "trigger" in data
    assert "mac_address" in data
    assert data["status"] in ["unconfigured", "misconfigured", "firmware_outdated", "unknown"]
    assert 0.80 <= data["confidence"] <= 0.99


def test_trigger_with_overrides():
    resp = client.post("/trigger?equipment_type=Chiller&trigger_text=New+Chiller+Detected")
    assert resp.status_code == 200
    data = resp.json()
    assert data["equipment_type"] == "Chiller"
    assert data["trigger"] == "New Chiller Detected"


def test_events_list():
    # Create 3 events
    for _ in range(3):
        client.post("/trigger")
    resp = client.get("/events")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 3
    assert len(data["events"]) == 3


def test_latest_event_empty():
    resp = client.get("/events/latest")
    assert resp.status_code == 200
    # Should be null when empty
    assert resp.json() is None


def test_latest_event_returns_last():
    client.post("/trigger?equipment_type=Compressor")
    client.post("/trigger?equipment_type=Chiller")
    resp = client.get("/events/latest")
    assert resp.status_code == 200
    assert resp.json()["equipment_type"] == "Chiller"


def test_clear_events():
    client.post("/trigger")
    client.post("/trigger")
    resp = client.delete("/events")
    assert resp.status_code == 200
    assert resp.json()["status"] == "cleared"
    # Verify cleared
    resp = client.get("/events")
    assert resp.json()["total"] == 0
