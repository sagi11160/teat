"""End-to-end: real server, real browser bridge bots, real WebRTC media.

Two MockMeet meetings stand in for two different external platforms. Each has a human
participant; a third human joins the unified room straight from the browser. Every
human's microphone plays a distinct pure tone, so an FFT of what each one receives
shows exactly whose audio reaches whom.
"""

from __future__ import annotations

import json
import secrets
import time

import httpx
import pytest

pytestmark = pytest.mark.e2e

TONE_A, TONE_B, TONE_C = 450, 750, 1150
PRESENT_DB = 20.0
ECHO_MARGIN_DB = 30.0

ANALYSE_JS = """
async ({targets, seconds}) => {
  const raw = window.__mmRemoteStreams || [...(window.__mbRemoteStreams || new Map()).values()];
  const streams = raw.filter((s) => s.getAudioTracks().some((t) => t.readyState === 'live'));
  const ctx = new AudioContext();
  await ctx.resume();
  const analyser = ctx.createAnalyser();
  analyser.fftSize = 16384;
  analyser.smoothingTimeConstant = 0;
  streams.forEach((s) => ctx.createMediaStreamSource(s).connect(analyser));
  const bins = analyser.frequencyBinCount;
  const acc = new Float64Array(bins);
  const buf = new Float32Array(bins);
  let n = 0;
  const end = performance.now() + seconds * 1000;
  while (performance.now() < end) {
    await new Promise((r) => setTimeout(r, 100));
    analyser.getFloatFrequencyData(buf);
    for (let i = 0; i < bins; i++) acc[i] += Math.pow(10, buf[i] / 20);
    n++;
  }
  const hz = ctx.sampleRate / analyser.fftSize;
  const avg = (i) => acc[i] / n;
  const peak = (f) => { const b = Math.round(f / hz); let m = 0; for (let i = b - 4; i <= b + 4; i++) m = Math.max(m, avg(i)); return m; };
  const band = [];
  for (let i = Math.round(200 / hz); i < Math.round(3000 / hz); i++) band.push(avg(i));
  band.sort((x, y) => x - y);
  const floor = Math.max(band[Math.floor(band.length / 2)], 1e-12);
  const levels = {};
  targets.forEach((f) => { levels[f] = 20 * Math.log10(peak(f) / floor); });
  ctx.close();
  return { streams: streams.length, levels };
}
"""

VIDEO_JS = """
() => {
  const raw = window.__mmRemoteStreams || [...(window.__mbRemoteStreams || new Map()).values()];
  const vids = [...document.querySelectorAll('video')].filter((v) => v.srcObject && raw.includes(v.srcObject));
  return vids.map((v) => {
    if (!v.videoWidth) return { w: 0, h: 0, spread: 0 };
    const c = document.createElement('canvas');
    c.width = 160; c.height = 90;
    const g = c.getContext('2d');
    g.drawImage(v, 0, 0, 160, 90);
    const d = g.getImageData(0, 0, 160, 90).data;
    let min = 255, max = 0;
    for (let i = 0; i < d.length; i += 4) { const l = (d[i] + d[i + 1] + d[i + 2]) / 3; min = Math.min(min, l); max = Math.max(max, l); }
    return { w: v.videoWidth, h: v.videoHeight, spread: max - min };
  });
}
"""


def poll(fn, timeout=40.0, interval=0.5, what="condition"):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = fn()
        if last:
            return last
        time.sleep(interval)
    raise AssertionError(f"{what} not reached within {timeout}s (last={last!r})")


def link_of(api: httpx.Client, room_id: str, link_id: str) -> dict:
    return next(link for link in api.get(f"/api/rooms/{room_id}").json()["links"] if link["id"] == link_id)


def wait_link(api, room_id, link_id, state, detail=None, timeout=40.0):
    def check():
        link = link_of(api, room_id, link_id)
        return link if link["state"] == state and (detail is None or link["state_detail"] == detail) else None

    return poll(check, timeout, what=f"link {state}/{detail}")


def analyse(page, seconds=3.0):
    return page.evaluate(ANALYSE_JS, {"targets": [TONE_A, TONE_B, TONE_C], "seconds": seconds})


def assert_hears(page, who, expected, absent, timeout=45.0):
    """Polls until the expected tones are clearly present, then checks the absent one."""
    deadline = time.monotonic() + timeout
    result = None
    while time.monotonic() < deadline:
        result = analyse(page)
        lv = {int(k): v for k, v in result["levels"].items()}
        if all(lv[f] >= PRESENT_DB for f in expected):
            # An echo would arrive at roughly the level of the other voices; codec
            # noise around the participant's own frequency sits far below that.
            weakest = min(lv[f] for f in expected)
            assert weakest - lv[absent] >= ECHO_MARGIN_DB, f"{who} hears its own audio back (echo): {lv}"
            return lv
        time.sleep(1)
    raise AssertionError(f"{who} does not hear {expected}: {result}")


@pytest.fixture(scope="module")
def organizer(live_server):
    api = live_server.client()
    res = api.post("/api/auth/register", json={"email": "host@example.com", "display_name": "Host", "password": "correct horse battery"})
    assert res.status_code == 201, res.text
    yield api
    api.close()


def new_room(api, title):
    room = api.post("/api/rooms", json={"title": title}).json()
    room["token"] = room["invite_url"].rsplit("/", 1)[1]
    return room


def add_mock(api, live_server, room_id, code):
    res = api.post(f"/api/rooms/{room_id}/links", json={"url": f"{live_server.base_url}/mock-meet/{code}"})
    assert res.status_code == 201, res.text
    return res.json()["id"]


def join_mock(page, live_server, code, name):
    page.goto(f"{live_server.base_url}/mock-meet/{code}")
    page.fill("#mm-name", name)
    page.click("#mm-join")
    page.wait_for_function("document.getElementById('status').textContent === 'In call'")


def join_web(page, live_server, token, name):
    page.goto(f"{live_server.base_url}/call/{token}")
    page.fill("#name", name)
    page.click("#join-btn")
    page.wait_for_selector("#incall:not([hidden])")


def test_three_way_media_without_echo(live_server, organizer, human):
    api = organizer
    room = new_room(api, "E2E media")
    code_a, code_b = f"plat-a-{secrets.token_hex(3)}", f"plat-b-{secrets.token_hex(3)}"
    link_a = add_mock(api, live_server, room["id"], code_a)
    link_b = add_mock(api, live_server, room["id"], code_b)
    api.post(f"/api/rooms/{room['id']}/links/{link_a}/start")
    api.post(f"/api/rooms/{room['id']}/links/{link_b}/start")
    wait_link(api, room["id"], link_a, "live")
    wait_link(api, room["id"], link_b, "live")

    alice = human(TONE_A)  # on "platform A"
    bob = human(TONE_B)    # on "platform B"
    carol = human(TONE_C)  # in the browser
    join_mock(alice, live_server, code_a, "Alice")
    join_mock(bob, live_server, code_b, "Bob")
    join_web(carol, live_server, room["token"], "Carol")

    participants = poll(lambda: len(api.get(f"/api/rooms/{room['id']}").json()["participants"]) == 3 or None,
                        what="3 room members")
    assert participants

    lv_a = assert_hears(alice, "Alice (platform A)", [TONE_B, TONE_C], TONE_A)
    lv_b = assert_hears(bob, "Bob (platform B)", [TONE_A, TONE_C], TONE_B)
    lv_c = assert_hears(carol, "Carol (browser)", [TONE_A, TONE_B], TONE_C)
    print("levels dB above floor:", json.dumps({"alice": lv_a, "bob": lv_b, "carol": lv_c}))

    # Video: Carol sees both platforms' composited video; Alice sees the room through the bridge.
    def videos_ok(page, minimum):
        vids = page.evaluate(VIDEO_JS)
        good = [v for v in vids if v["w"] > 0 and v["spread"] > 30]
        return vids if len(good) >= minimum else None

    poll(lambda: videos_ok(carol, 2), what="Carol sees video from both platforms")
    poll(lambda: videos_ok(alice, 1), what="Alice sees the room video")

    # Meeting A ends on the platform side: its bridge stops for good and leaves the room.
    httpx.post(f"{live_server.base_url}/mock-meet/api/{code_a}/end")
    link = wait_link(api, room["id"], link_a, "stopped", "meeting_ended")
    assert link["desired_state"] == "stopped"
    poll(lambda: len(carol.evaluate("window.__mbCall().peers")) == 1 or None, what="Carol sees platform A leave")
    assert link_of(api, room["id"], link_b)["state"] == "live"

    # Organizer stops bridge B.
    api.post(f"/api/rooms/{room['id']}/links/{link_b}/stop")
    wait_link(api, room["id"], link_b, "stopped")
    poll(lambda: bob.evaluate("document.querySelectorAll('.tile').length") == 1 or None, what="bridge B leaves meeting B")

    carol.click("#leave")
    poll(lambda: api.get(f"/api/rooms/{room['id']}").json()["participants"] == [] or None, what="room empty")


def test_lobby_admission_and_denial(live_server, organizer):
    api = organizer
    room = new_room(api, "E2E lobby")
    code = f"lobby-{secrets.token_hex(3)}"
    httpx.post(f"{live_server.base_url}/mock-meet/api/{code}/config", json={"lobby": True})
    link = add_mock(api, live_server, room["id"], code)
    api.post(f"/api/rooms/{room['id']}/links/{link}/start")
    wait_link(api, room["id"], link, "waiting_admission")
    poll(lambda: httpx.post(f"{live_server.base_url}/mock-meet/api/{code}/admit-all").json()["admitted"] or None,
         what="bridge waiting in lobby")
    wait_link(api, room["id"], link, "live")
    api.post(f"/api/rooms/{room['id']}/links/{link}/stop")
    wait_link(api, room["id"], link, "stopped")

    code2 = f"deny-{secrets.token_hex(3)}"
    httpx.post(f"{live_server.base_url}/mock-meet/api/{code2}/config", json={"lobby": True})
    link2 = add_mock(api, live_server, room["id"], code2)
    api.post(f"/api/rooms/{room['id']}/links/{link2}/start")
    wait_link(api, room["id"], link2, "waiting_admission")
    poll(lambda: httpx.post(f"{live_server.base_url}/mock-meet/api/{code2}/deny-all").json()["denied"] or None,
         what="bridge waiting in lobby")
    failed = wait_link(api, room["id"], link2, "failed", "admission_denied")
    assert failed["desired_state"] == "stopped"


def test_already_ended_meeting_is_not_retried(live_server, organizer):
    api = organizer
    room = new_room(api, "E2E ended")
    code = f"ended-{secrets.token_hex(3)}"
    httpx.post(f"{live_server.base_url}/mock-meet/api/{code}/end")
    link = add_mock(api, live_server, room["id"], code)
    api.post(f"/api/rooms/{room['id']}/links/{link}/start")
    result = poll(lambda: (lambda l: l if l["state"] in ("failed", "stopped") else None)(link_of(api, room["id"], link)),
                  what="ended meeting detected")
    assert result["state_detail"] == "meeting_ended"
    assert result["attempts"] == 0


def test_organizer_live_stream(live_server, organizer, human):
    api = organizer
    room = new_room(api, "E2E stream")
    cookies = dict(api.cookies)
    events = []
    with httpx.stream("GET", f"{live_server.base_url}/api/rooms/{room['id']}/stream", cookies=cookies, timeout=20) as res:
        assert res.status_code == 200
        lines = res.iter_lines()
        first = next(line for line in lines if line.startswith("data:"))
        events.append(json.loads(first[5:]))
        guest = human(TONE_A)
        join_web(guest, live_server, room["token"], "Guest")
        for line in lines:
            if line.startswith("data:"):
                events.append(json.loads(line[5:]))
                break
        guest.click("#leave")
    assert events[0] == {"type": "participants", "participants": []}
    assert events[1]["type"] == "participants"
    assert [p["name"] for p in events[1]["participants"]] == ["Guest"]


def test_writes_are_visible_to_the_next_request(live_server, organizer):
    for i in range(40):
        room = new_room(organizer, f"rw-{i}")
        assert organizer.get(f"/api/rooms/{room['id']}").status_code == 200
        organizer.delete(f"/api/rooms/{room['id']}")


def test_data_persists_across_restart(live_server, organizer):
    api = organizer
    room = new_room(api, "Survives restart")
    live_server.stop()
    live_server.start()
    fresh = live_server.client()
    res = fresh.post("/api/auth/login", json={"email": "host@example.com", "password": "correct horse battery"})
    assert res.status_code == 200
    titles = [r["title"] for r in fresh.get("/api/rooms").json()]
    assert "Survives restart" in titles and "E2E media" in titles
    assert fresh.get(f"/api/invite/{room['token']}").status_code == 200
    # Sessions are stored server-side, so the organizer stays signed in across restarts.
    assert api.get("/api/auth/me").status_code == 200
    fresh.close()
