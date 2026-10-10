import json

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from meetbridge.main import create_app

from .conftest import CSRF, FakeRunner, make_settings, register


@pytest.fixture
def room(client):
    register(client)
    room = client.post("/api/rooms", json={"title": "Call"}, headers=CSRF).json()
    room["token"] = room["invite_url"].rsplit("/", 1)[1]
    return room


def test_two_browser_participants_can_signal(client, room):
    with client.websocket_connect(f"/ws/call/{room['token']}?name=Dana") as a:
        welcome_a = a.receive_json()
        assert welcome_a["type"] == "welcome" and welcome_a["peers"] == []
        assert welcome_a["ice_servers"]
        with client.websocket_connect(f"/ws/call/{room['token']}?name=%20Eli%20%20Cohen%20") as b:
            welcome_b = b.receive_json()
            assert [p["name"] for p in welcome_b["peers"]] == ["Dana"]
            joined = a.receive_json()
            assert joined["type"] == "peer-joined"
            assert joined["peer"]["name"] == "Eli Cohen" and joined["peer"]["source"] == "web"

            b.send_json({"type": "signal", "to": welcome_a["peer_id"], "data": {"sdp": {"type": "offer", "sdp": "v=0"}}})
            got = a.receive_json()
            assert got == {"type": "signal", "from": welcome_b["peer_id"], "data": {"sdp": {"type": "offer", "sdp": "v=0"}}}

            a.send_json({"type": "media-state", "audio": False, "video": True})
            assert b.receive_json() == {"type": "media-state", "peer_id": welcome_a["peer_id"], "audio": False, "video": True}

            names = {p["name"] for p in client.get(f"/api/rooms/{room['id']}").json()["participants"]}
            assert names == {"Dana", "Eli Cohen"}
            b.send_json({"type": "leave"})
        left = a.receive_json()
        assert left == {"type": "peer-left", "peer_id": welcome_b["peer_id"]}


def test_signal_cannot_cross_rooms(client, room):
    other = client.post("/api/rooms", json={"title": "Other"}, headers=CSRF).json()
    other_token = other["invite_url"].rsplit("/", 1)[1]
    with client.websocket_connect(f"/ws/call/{other_token}?name=Outsider") as outsider:
        outsider_id = outsider.receive_json()["peer_id"]
        with client.websocket_connect(f"/ws/call/{room['token']}?name=Dana") as a:
            a.receive_json()
            a.send_json({"type": "signal", "to": outsider_id, "data": {"candidate": {}}})
            assert a.receive_json() == {"type": "error", "code": "unknown_peer"}


def test_bad_messages_are_rejected_without_disconnect(client, room):
    with client.websocket_connect(f"/ws/call/{room['token']}?name=Dana") as a:
        a.receive_json()
        a.send_text("not json")
        assert a.receive_json()["code"] == "bad_json"
        a.send_json(["list"])
        assert a.receive_json()["code"] == "bad_message"
        a.send_json({"type": "hack"})
        assert a.receive_json()["code"] == "unknown_type"
        a.send_text(json.dumps({"type": "ping", "pad": "x" * (70 * 1024)}))
        assert a.receive_json()["code"] == "message_too_large"
        a.send_json({"type": "ping"})
        assert a.receive_json() == {"type": "pong"}


def test_invalid_invite_and_missing_name(client, room):
    with client.websocket_connect("/ws/call/not-a-token?name=X") as ws:
        assert ws.receive_json() == {"type": "error", "code": "invite_not_found"}
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_json()
        assert exc.value.code == 4404
    with client.websocket_connect(f"/ws/call/{room['token']}?name=%20%20") as ws:
        assert ws.receive_json() == {"type": "error", "code": "name_required"}


def test_rotated_invite_rejects_new_joins(client, room):
    client.post(f"/api/rooms/{room['id']}/rotate-invite", headers=CSRF)
    with client.websocket_connect(f"/ws/call/{room['token']}?name=Late") as ws:
        assert ws.receive_json()["code"] == "invite_not_found"


def test_room_full(tmp_path):
    app = create_app(make_settings(tmp_path, max_participants_per_room=1), runner_factory=lambda *_: FakeRunner())
    with TestClient(app) as client:
        register(client)
        token = client.post("/api/rooms", json={"title": "Tiny"}, headers=CSRF).json()["invite_url"].rsplit("/", 1)[1]
        with client.websocket_connect(f"/ws/call/{token}?name=One") as one:
            one.receive_json()
            with client.websocket_connect(f"/ws/call/{token}?name=Two") as two:
                assert two.receive_json() == {"type": "error", "code": "room_full"}


def test_deleting_room_closes_call(client, room):
    with client.websocket_connect(f"/ws/call/{room['token']}?name=Dana") as a:
        a.receive_json()
        client.delete(f"/api/rooms/{room['id']}", headers=CSRF)
        assert a.receive_json() == {"type": "room-closed"}


def test_organizer_stream_requires_owner(client, room):
    client.cookies.clear()
    assert client.get(f"/api/rooms/{room['id']}/stream").status_code == 401
