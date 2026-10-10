"""UI flows in a real browser against the live server: every page, every main button.

Any console error or CSP violation fails the test, so dead wiring shows up here.
"""

from __future__ import annotations

import re
import secrets

import httpx
import pytest
from playwright.sync_api import expect

pytestmark = pytest.mark.e2e


@pytest.fixture
def browser_page(playwright_instance):
    browser = playwright_instance.chromium.launch(
        args=["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream", "--autoplay-policy=no-user-gesture-required"]
    )
    context = browser.new_context(permissions=["microphone", "camera", "clipboard-read", "clipboard-write"], locale="he-IL")
    page = context.new_page()
    errors: list[str] = []
    page.on("console", lambda m: m.type == "error" and errors.append(m.text))
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.errors = errors
    yield page
    browser.close()


def no_errors(page):
    # Chrome logs every 4xx response as "Failed to load resource"; the flows trigger
    # those on purpose (validation, invalid invites). Script errors and CSP
    # violations still fail the test.
    relevant = [e for e in page.errors if "favicon" not in e and not e.startswith("Failed to load resource")]
    assert not relevant, relevant


def test_organizer_flow(live_server, browser_page):
    page = browser_page
    base = live_server.base_url
    email = f"ui-{secrets.token_hex(3)}@example.com"
    page.on("dialog", lambda d: d.accept())

    page.goto(base + "/")
    expect(page.locator("html")).to_have_attribute("dir", "rtl")
    expect(page.locator("#auth")).to_be_visible()

    # Registration: validation errors first, then success.
    page.click("#tab-register")
    page.fill("#reg-name", "מארגנת")
    page.fill("#reg-email", "not-an-email")
    page.fill("#reg-password", "short")
    page.click("#register-form button[type=submit]")
    expect(page.locator("#auth-alert")).to_contain_text("חלק מהשדות אינם תקינים")
    expect(page.locator("#reg-email")).to_have_attribute("aria-invalid", "true")
    page.fill("#reg-email", email)
    page.fill("#reg-password", "correct horse battery")
    page.click("#register-form button[type=submit]")
    expect(page.locator("#dashboard")).to_be_visible()
    expect(page.locator("#rooms-empty")).to_be_visible()
    expect(page.locator("#who")).to_contain_text("מארגנת")

    # Create a room -> lands on its management page.
    page.fill("#room-title", "ישיבת צוות")
    page.click("#create-form button[type=submit]")
    page.wait_for_url(re.compile(r"/rooms/[0-9a-f]+$"))
    expect(page.locator("#room-title")).to_have_text("ישיבת צוות")
    expect(page.locator("#links-empty")).to_be_visible()
    expect(page.locator("#live-status")).to_contain_text("עדכונים חיים פעילים")
    invite = page.input_value("#invite-url")
    assert "/j/" in invite

    # Invalid link -> Hebrew error; valid links for each platform.
    page.fill("#link-url", "https://example.com/meeting")
    page.click("#link-form button[type=submit]")
    expect(page.locator("#link-alert")).to_contain_text("Zoom, Google Meet או Microsoft Teams")
    page.fill("#link-url", "https://zoom.us/my/someone")
    page.click("#link-form button[type=submit]")
    expect(page.locator("#link-alert")).to_contain_text("קישור אישי של Zoom")
    page.fill("#link-url", "https://meet.google.com/abc-defg-hij")
    page.fill("#link-label", "המשרד בתל אביב")
    page.click("#link-form button[type=submit]")
    expect(page.locator("#link-alert")).to_contain_text("חוברה")
    expect(page.locator(".link-item")).to_have_count(1)
    expect(page.locator(".link-item .chip.google_meet")).to_be_visible()

    code = f"ui-{secrets.token_hex(3)}"
    page.fill("#link-url", f"{base}/mock-meet/{code}")
    page.click("#link-form button[type=submit]")
    expect(page.locator(".link-item")).to_have_count(2)

    # Start the mock bridge; the badge follows the live state over SSE.
    mock_item = page.locator(".link-item", has=page.locator(".chip.mock"))
    mock_item.locator("button[data-action=start]").click()
    expect(mock_item.locator(".state")).to_have_text("מחובר ומגשר", timeout=40_000)
    expect(page.locator("#participants li")).to_have_count(1, timeout=10_000)
    expect(page.locator("#events")).to_contain_text("מחובר ומגשר")

    # Settings, invite rotation.
    page.fill("#set-bot", "גשר הצוות")
    page.click("#settings-form button[type=submit]")
    expect(page.locator("#settings-alert")).to_have_text("נשמר.")
    page.click("#rotate-invite")
    expect(page.locator("#copy-status")).to_contain_text("נוצר קישור הזמנה חדש")
    new_invite = page.input_value("#invite-url")
    assert new_invite != invite

    # Invite page as a guest.
    guest = page.context.browser.new_context(locale="he-IL").new_page()
    guest.goto(new_invite)
    expect(guest.locator("#title")).to_have_text("ישיבת צוות")
    expect(guest.locator(".join-option")).to_have_count(3)
    expect(guest.locator(".join-option.google_meet")).to_have_attribute("href", re.compile("meet.google.com/abc-defg-hij"))
    expect(guest.locator(".join-option.web")).to_have_attribute("href", re.compile("/call/"))
    expect(guest.locator("#no-bridge")).to_be_visible()  # Meet link is not bridged yet
    guest.goto(invite)
    expect(guest.locator("#page-alert")).to_contain_text("קישור ההזמנה אינו תקף")

    # Stop the bridge, remove the Meet link, delete the room.
    mock_item.locator("button[data-action=stop]").click()
    expect(mock_item.locator(".state")).to_have_text("מנותק", timeout=30_000)
    meet_item = page.locator(".link-item", has=page.locator(".chip.google_meet"))
    meet_item.locator("button.ghost").click()
    expect(page.locator(".link-item")).to_have_count(1)
    page.click("#delete-room")
    page.wait_for_url(base + "/")
    expect(page.locator("#rooms-empty")).to_be_visible()

    page.click("#logout")
    expect(page.locator("#auth")).to_be_visible()
    no_errors(page)


def test_browser_call_page(live_server, browser_page):
    page = browser_page
    api = live_server.client()
    api.post("/api/auth/register", json={"email": f"c-{secrets.token_hex(3)}@example.com", "display_name": "C",
                                         "password": "correct horse battery"})
    room = api.post("/api/rooms", json={"title": "שיחת דפדפן"}).json()
    token = room["invite_url"].rsplit("/", 1)[1]

    page.goto(f"{live_server.base_url}/call/{token}")
    expect(page.locator("#room-title")).to_have_text("שיחת דפדפן")
    page.fill("#name", "  ")
    page.click("#join-btn")
    expect(page.locator("#prejoin-alert")).to_contain_text("יש להזין שם")
    page.fill("#name", "דנה")
    page.click("#join-btn")
    expect(page.locator("#incall")).to_be_visible()
    expect(page.locator("#call-status")).to_contain_text("אתם הראשונים בשיחה")
    expect(page.locator("#count")).to_have_text("1 בשיחה")

    other = page.context.new_page()
    other.goto(f"{live_server.base_url}/call/{token}")
    other.fill("#name", "יוסי")
    other.click("#join-btn")
    expect(page.locator("#count")).to_have_text("2 בשיחה", timeout=15_000)
    expect(page.locator("#stage .tile")).to_have_count(2)
    # wait_for_function evaluates strings in-page, which the app's CSP forbids; poll instead.
    for _ in range(80):
        if page.evaluate("() => window.__mbCall().peers.some(p => p.state === 'connected')"):
            break
        page.wait_for_timeout(250)
    else:
        raise AssertionError("browser participants never connected")

    other.click("#toggle-cam")
    expect(other.locator("#toggle-cam")).to_have_attribute("aria-pressed", "true")
    expect(other.locator("#tile-self .avatar")).to_be_visible()
    other.click("#toggle-cam")
    expect(other.locator("#tile-self video")).to_be_visible()
    other.click("#toggle-mic")
    expect(other.locator("#toggle-mic")).to_have_attribute("aria-pressed", "true")
    expect(page.locator("#stage .tile:not(#tile-self) .hint")).to_be_visible()  # "(מושתק)"

    other.click("#leave")
    expect(other.locator("#left")).to_be_visible()
    expect(page.locator("#count")).to_have_text("1 בשיחה", timeout=15_000)

    # Room deleted by the organizer while in the call.
    api.delete(f"/api/rooms/{room['id']}")
    expect(page.locator("#left-text")).to_contain_text("המארגן סגר את החדר", timeout=10_000)
    no_errors(page)
    api.close()


def test_keyboard_only_signup_and_room(live_server, browser_page):
    page = browser_page
    page.goto(live_server.base_url + "/")
    page.wait_for_selector("#auth:not([hidden])")
    page.keyboard.press("Tab")
    expect(page.locator(".skip-link")).to_be_focused()
    page.focus("#tab-login")
    page.keyboard.press("ArrowLeft")
    expect(page.locator("#tab-register")).to_be_focused()
    expect(page.locator("#register-form")).to_be_visible()
    page.keyboard.press("Tab")
    expect(page.locator("#reg-name")).to_be_focused()
    page.keyboard.type("Keyboard User")
    page.keyboard.press("Tab")
    page.keyboard.type(f"kb-{secrets.token_hex(3)}@example.com")
    page.keyboard.press("Tab")
    page.keyboard.type("correct horse battery")
    page.keyboard.press("Enter")
    expect(page.locator("#dashboard")).to_be_visible()
    page.focus("#room-title")
    page.keyboard.type("Keyboard room")
    page.keyboard.press("Enter")
    page.wait_for_url(re.compile(r"/rooms/"))
    page.focus("#link-url")
    page.keyboard.type("https://meet.google.com/abc-defg-hij")
    page.keyboard.press("Enter")
    expect(page.locator(".link-item")).to_have_count(1)
    no_errors(page)


def test_invalid_invite_and_call_links(live_server, browser_page):
    page = browser_page
    page.goto(f"{live_server.base_url}/j/doesnotexist")
    expect(page.locator("#page-alert")).to_contain_text("קישור ההזמנה אינו תקף")
    page.goto(f"{live_server.base_url}/call/doesnotexist")
    expect(page.locator("#prejoin-alert")).to_contain_text("קישור ההזמנה אינו תקף")
    expect(page.locator("#prejoin-form")).to_be_hidden()
    assert httpx.get(f"{live_server.base_url}/rooms/whatever").status_code == 200
    page.goto(f"{live_server.base_url}/rooms/whatever")
    page.wait_for_url(live_server.base_url + "/")  # not signed in -> home
