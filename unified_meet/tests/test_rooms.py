import pytest

from .conftest import CSRF, register, switch_user

ZOOM = "https://zoom.us/j/123456789?pwd=abc"
MEET = "https://meet.google.com/abc-defg-hij"
TEAMS = "https://teams.microsoft.com/meet/12345678901?p=xyz"


def create_room(client, title="Weekly sync"):
    res = client.post("/api/rooms", json={"title": title}, headers=CSRF)
    assert res.status_code == 201, res.text
    return res.json()


def add_link(client, room_id, url, **extra):
    return client.post(f"/api/rooms/{room_id}/links", json={"url": url, **extra}, headers=CSRF)


def test_room_crud(client):
    register(client)
    room = create_room(client, "  Weekly   sync ")
    assert room["title"] == "Weekly sync"
    assert room["bot_display_name"] == "MeetBridge · Weekly sync"
    assert room["invite_url"].startswith("http://testserver/j/")

    assert [r["id"] for r in client.get("/api/rooms").json()] == [room["id"]]
    res = client.patch(f"/api/rooms/{room['id']}", json={"title": "Renamed", "bot_display_name": "Bridge"}, headers=CSRF)
    assert res.json()["title"] == "Renamed"
    assert res.json()["bot_display_name"] == "Bridge"

    rotated = client.post(f"/api/rooms/{room['id']}/rotate-invite", headers=CSRF).json()
    assert rotated["invite_url"] != room["invite_url"]
    old_token = room["invite_url"].rsplit("/", 1)[1]
    assert client.get(f"/api/invite/{old_token}").status_code == 404

    assert client.delete(f"/api/rooms/{room['id']}", headers=CSRF).status_code == 204
    assert client.get(f"/api/rooms/{room['id']}").status_code == 404


def test_room_validation(client):
    register(client)
    assert client.post("/api/rooms", json={"title": ""}, headers=CSRF).status_code == 422
    res = client.post("/api/rooms", json={"title": "   "}, headers=CSRF)
    assert res.status_code == 422 and res.json()["error"]["code"] == "title_required"
    room = create_room(client)
    res = client.patch(f"/api/rooms/{room['id']}", json={"bot_display_name": "  "}, headers=CSRF)
    assert res.json()["error"]["code"] == "bot_name_required"


def test_links_for_all_three_platforms(client):
    register(client)
    room = create_room(client)
    for url, provider in ((ZOOM, "zoom"), (MEET, "google_meet"), (TEAMS, "teams")):
        res = add_link(client, room["id"], url)
        assert res.status_code == 201, res.text
        assert res.json()["provider"] == provider
        assert res.json()["state"] == "idle"
    links = client.get(f"/api/rooms/{room['id']}").json()["links"]
    assert {link["provider"] for link in links} == {"zoom", "google_meet", "teams"}


def test_link_errors(client, settings):
    register(client)
    room = create_room(client)
    res = add_link(client, room["id"], "https://example.com/meeting")
    assert res.status_code == 422 and res.json()["error"]["code"] == "unsupported_provider"
    assert add_link(client, room["id"], ZOOM).status_code == 201
    dup = add_link(client, room["id"], "zoom.us/j/123456789?pwd=abc")
    assert dup.status_code == 409 and dup.json()["error"]["code"] == "link_exists"
    for i in range(settings.max_links_per_room - 1):
        assert add_link(client, room["id"], f"https://zoom.us/j/{100000000 + i}").status_code == 201
    over = add_link(client, room["id"], MEET)
    assert over.status_code == 409 and over.json()["error"]["code"] == "too_many_links"


def test_link_passcode_never_returned(client):
    register(client)
    room = create_room(client)
    link = add_link(client, room["id"], "https://zoom.us/j/123456789", passcode="9999").json()
    assert link["has_passcode"] is True
    assert "9999" not in str(link)
    assert "9999" not in client.get(f"/api/rooms/{room['id']}").text


def test_link_update_and_delete(client):
    register(client)
    room = create_room(client)
    link = add_link(client, room["id"], MEET).json()
    res = client.patch(f"/api/rooms/{room['id']}/links/{link['id']}", json={"label": "  London  "}, headers=CSRF)
    assert res.json()["label"] == "London"
    assert client.delete(f"/api/rooms/{room['id']}/links/{link['id']}", headers=CSRF).status_code == 204
    assert client.delete(f"/api/rooms/{room['id']}/links/{link['id']}", headers=CSRF).status_code == 404


def test_events_are_recorded(client):
    register(client)
    room = create_room(client)
    add_link(client, room["id"], MEET)
    kinds = [e["kind"] for e in client.get(f"/api/rooms/{room['id']}/events").json()]
    assert kinds[:2] == ["link_added", "room_created"]


def test_invite_page_data_is_public_but_minimal(client):
    register(client)
    room = create_room(client)
    add_link(client, room["id"], "https://zoom.us/j/123456789", passcode="4242")
    add_link(client, room["id"], MEET)
    token = room["invite_url"].rsplit("/", 1)[1]
    client.cookies.clear()
    info = client.get(f"/api/invite/{token}").json()
    assert info["title"] == "Weekly sync"
    assert [o["provider"] for o in info["options"]] == ["zoom", "google_meet"]
    assert info["options"][0]["passcode"] == "4242"
    assert info["options"][1]["passcode"] is None
    assert "id" not in info and "links" not in info
    assert client.get("/api/invite/nope").status_code == 404


# ------------------------------------------------------------ permissions
ROOM_ROUTES = [
    ("GET", "/api/rooms/{rid}", None),
    ("PATCH", "/api/rooms/{rid}", {"title": "x"}),
    ("POST", "/api/rooms/{rid}/rotate-invite", None),
    ("DELETE", "/api/rooms/{rid}", None),
    ("POST", "/api/rooms/{rid}/links", {"url": MEET}),
    ("PATCH", "/api/rooms/{rid}/links/{lid}", {"label": "x"}),
    ("DELETE", "/api/rooms/{rid}/links/{lid}", None),
    ("POST", "/api/rooms/{rid}/links/{lid}/start", None),
    ("POST", "/api/rooms/{rid}/links/{lid}/stop", None),
    ("GET", "/api/rooms/{rid}/events", None),
    ("GET", "/api/rooms/{rid}/stream", None),
]


@pytest.fixture
def owned(client):
    register(client)
    room = create_room(client)
    link = add_link(client, room["id"], ZOOM).json()
    return room["id"], link["id"]


@pytest.mark.parametrize("method, path, body", ROOM_ROUTES)
def test_owner_can_access(client, owned, method, path, body):
    rid, lid = owned
    if path.endswith("/stream"):
        pytest.skip("streaming endpoint is covered in test_live")
    url = path.format(rid=rid, lid=lid)
    if method == "POST" and url.endswith("/links"):
        body = {"url": TEAMS}
    res = client.request(method, url, json=body, headers=CSRF)
    assert res.status_code in (200, 201, 204), res.text


@pytest.mark.parametrize("method, path, body", ROOM_ROUTES)
def test_other_user_gets_not_found(client, owned, method, path, body):
    rid, lid = owned
    switch_user(client, "intruder@example.com", "Intruder")
    res = client.request(method, path.format(rid=rid, lid=lid), json=body, headers=CSRF)
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "room_not_found"


@pytest.mark.parametrize("method, path, body", ROOM_ROUTES)
def test_anonymous_gets_unauthorized(client, owned, method, path, body):
    rid, lid = owned
    client.cookies.clear()
    res = client.request(method, path.format(rid=rid, lid=lid), json=body, headers=CSRF)
    assert res.status_code == 401
    assert res.json()["error"]["code"] == "not_authenticated"


def test_room_list_is_per_user(client):
    register(client)
    create_room(client, "Mine")
    switch_user(client, "other@example.com")
    assert client.get("/api/rooms").json() == []


def test_link_of_other_room_is_not_reachable_through_own_room(client):
    register(client)
    victim_room = create_room(client, "Victim")
    victim_link = add_link(client, victim_room["id"], ZOOM).json()
    switch_user(client, "attacker@example.com")
    own = create_room(client, "Attacker")
    res = client.post(f"/api/rooms/{own['id']}/links/{victim_link['id']}/start", headers=CSRF)
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "link_not_found"
