from fastapi.testclient import TestClient

from meetbridge.main import create_app
from meetbridge.providers import JoinError

from .conftest import CSRF, FakeRunner, make_settings, register, wait_for

MEET = "https://meet.google.com/abc-defg-hij"
ZOOM = "https://zoom.us/j/123456789"


def setup_link(client, url=MEET):
    register(client)
    room = client.post("/api/rooms", json={"title": "Room"}, headers=CSRF).json()
    link = client.post(f"/api/rooms/{room['id']}/links", json={"url": url}, headers=CSRF).json()
    return room["id"], link["id"]


def link_state(client, rid, lid):
    links = client.get(f"/api/rooms/{rid}").json()["links"]
    return next(link for link in links if link["id"] == lid)


def state_is(client, rid, lid, state, detail=None):
    def check():
        link = link_state(client, rid, lid)
        ok = link["state"] == state and (detail is None or link["state_detail"] == detail)
        return link if ok else None

    return check


def test_start_goes_live_and_stop_disconnects(client, runner):
    rid, lid = setup_link(client)
    res = client.post(f"/api/rooms/{rid}/links/{lid}/start", headers=CSRF)
    assert res.json()["desired_state"] == "running"
    wait_for(state_is(client, rid, lid, "live"))
    job = runner.jobs[-1]
    assert job.display_name == "MeetBridge · Room"
    assert job.provider == "google_meet"

    client.post(f"/api/rooms/{rid}/links/{lid}/stop", headers=CSRF)
    wait_for(state_is(client, rid, lid, "stopped"))
    events = client.get(f"/api/rooms/{rid}/events").json()
    states = [e["detail"].get("state") for e in events if e["kind"] == "link_state"]
    assert {"joining", "live", "stopped"} <= set(states)
    kinds = {e["kind"] for e in events}
    assert {"bridge_start_requested", "bridge_stop_requested"} <= kinds


def test_state_events_are_published_only_after_commit(client, runner):
    from meetbridge.db import session_scope
    from meetbridge.models import MeetingLink

    rid, lid = setup_link(client)
    bus = client.app.state.services.bus
    original = bus.publish
    seen: list[tuple[str, str]] = []

    def checking_publish(room_id, event):
        if event.get("type") == "link":
            with session_scope() as db:
                stored = db.get(MeetingLink, event["link"]["id"]).state
            seen.append((event["link"]["state"], stored))
        original(room_id, event)

    bus.publish = checking_publish
    client.post(f"/api/rooms/{rid}/links/{lid}/start", headers=CSRF)
    wait_for(state_is(client, rid, lid, "live"))
    wait_for(lambda: any(s == "live" for s, _ in seen))
    assert seen and all(published == stored for published, stored in seen), seen


def test_start_is_idempotent(client, runner):
    rid, lid = setup_link(client)
    client.post(f"/api/rooms/{rid}/links/{lid}/start", headers=CSRF)
    client.post(f"/api/rooms/{rid}/links/{lid}/start", headers=CSRF)
    wait_for(state_is(client, rid, lid, "live"))
    assert len(runner.jobs) == 1


def test_waiting_room_state_is_reported(client, runner):
    runner.script(MEET, "wait")
    rid, lid = setup_link(client)
    client.post(f"/api/rooms/{rid}/links/{lid}/start", headers=CSRF)
    wait_for(state_is(client, rid, lid, "waiting_admission"))
    client.post(f"/api/rooms/{rid}/links/{lid}/stop", headers=CSRF)
    wait_for(state_is(client, rid, lid, "stopped"))


def test_meeting_end_stops_bridge_without_retry(client, runner):
    runner.script(MEET, "ended")
    rid, lid = setup_link(client)
    client.post(f"/api/rooms/{rid}/links/{lid}/start", headers=CSRF)
    link = wait_for(state_is(client, rid, lid, "stopped", "meeting_ended"))
    assert link["desired_state"] == "stopped"
    assert len(runner.jobs) == 1


def test_terminal_error_fails_without_retry(client, runner):
    runner.script(MEET, JoinError("admission_denied", terminal=True))
    rid, lid = setup_link(client)
    client.post(f"/api/rooms/{rid}/links/{lid}/start", headers=CSRF)
    link = wait_for(state_is(client, rid, lid, "failed", "admission_denied"))
    assert link["desired_state"] == "stopped"
    assert len(runner.jobs) == 1


def test_transient_errors_retry_then_recover(client, runner):
    runner.script(MEET, JoinError("join_timeout", terminal=False), "crash", "live")
    rid, lid = setup_link(client)
    client.post(f"/api/rooms/{rid}/links/{lid}/start", headers=CSRF)
    link = wait_for(state_is(client, rid, lid, "live"), timeout=8)
    assert link["attempts"] == 0
    assert len(runner.jobs) == 3
    details = [e["detail"].get("detail") for e in client.get(f"/api/rooms/{rid}/events").json() if e["kind"] == "link_state"]
    assert "join_timeout" in details and "bot_crashed" in details


def test_gives_up_after_max_retries(client, runner, settings):
    runner.script(MEET, JoinError("join_timeout", terminal=False))
    rid, lid = setup_link(client)
    client.post(f"/api/rooms/{rid}/links/{lid}/start", headers=CSRF)
    link = wait_for(state_is(client, rid, lid, "failed", "join_timeout"), timeout=10)
    assert link["desired_state"] == "stopped"
    assert len(runner.jobs) == settings.bot_max_retries + 1


def test_restart_after_failure_resets_attempts(client, runner):
    runner.script(MEET, JoinError("meeting_not_found", terminal=True), "live")
    rid, lid = setup_link(client)
    client.post(f"/api/rooms/{rid}/links/{lid}/start", headers=CSRF)
    wait_for(state_is(client, rid, lid, "failed"))
    client.post(f"/api/rooms/{rid}/links/{lid}/start", headers=CSRF)
    wait_for(state_is(client, rid, lid, "live"))


def test_deleting_live_link_stops_bot(client, runner):
    rid, lid = setup_link(client)
    client.post(f"/api/rooms/{rid}/links/{lid}/start", headers=CSRF)
    wait_for(state_is(client, rid, lid, "live"))
    services = client.app.state.services
    assert lid in services.supervisor.running_link_ids()
    client.delete(f"/api/rooms/{rid}/links/{lid}", headers=CSRF)
    wait_for(lambda: lid not in services.supervisor.running_link_ids())


def test_capacity_limit_queues_extra_bridges(tmp_path):
    runner = FakeRunner()
    app = create_app(make_settings(tmp_path, max_active_bots=1), runner_factory=lambda *_: runner)
    with TestClient(app) as client:
        rid, first = setup_link(client, MEET)
        second = client.post(f"/api/rooms/{rid}/links", json={"url": ZOOM}, headers=CSRF).json()["id"]
        client.post(f"/api/rooms/{rid}/links/{first}/start", headers=CSRF)
        wait_for(state_is(client, rid, first, "live"))
        client.post(f"/api/rooms/{rid}/links/{second}/start", headers=CSRF)
        wait_for(state_is(client, rid, second, "starting", "waiting_for_capacity"))
        client.post(f"/api/rooms/{rid}/links/{first}/stop", headers=CSRF)
        wait_for(state_is(client, rid, second, "live"))


def test_bridges_resume_after_server_restart(tmp_path):
    settings = make_settings(tmp_path)
    runner = FakeRunner()
    app = create_app(settings, runner_factory=lambda *_: runner)
    with TestClient(app) as client:
        rid, lid = setup_link(client)
        other = client.post(f"/api/rooms/{rid}/links", json={"url": ZOOM}, headers=CSRF).json()["id"]
        client.post(f"/api/rooms/{rid}/links/{lid}/start", headers=CSRF)
        wait_for(state_is(client, rid, lid, "live"))
        cookies = dict(client.cookies)
    assert runner.closed

    runner2 = FakeRunner()
    app2 = create_app(settings, runner_factory=lambda *_: runner2)
    with TestClient(app2) as client:
        client.cookies.update(cookies)
        wait_for(state_is(client, rid, lid, "live"))
        assert runner2.jobs and runner2.jobs[0].link_id == lid
        assert link_state(client, rid, other)["state"] == "idle"
        details = [e["detail"].get("detail") for e in client.get(f"/api/rooms/{rid}/events").json() if e["kind"] == "link_state"]
        assert "resumed_after_restart" in details
