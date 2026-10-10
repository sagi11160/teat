import pytest

from meetbridge.models import Provider
from meetbridge.providers import InvalidMeetingUrl, ProviderRegistry

from .conftest import make_settings


@pytest.fixture
def registry(tmp_path):
    return ProviderRegistry(make_settings(tmp_path, enable_mock_provider=False))


@pytest.mark.parametrize(
    "url, meeting_id, canonical",
    [
        ("https://zoom.us/j/123456789", "123456789", "https://zoom.us/j/123456789"),
        ("zoom.us/j/12345678901?pwd=AbC.1", "12345678901", "https://zoom.us/j/12345678901?pwd=AbC.1"),
        ("https://us02web.zoom.us/j/9876543210?pwd=x&from=addon", "9876543210", "https://zoom.us/j/9876543210?pwd=x"),
        ("https://app.zoom.us/wc/123456789/join", "123456789", "https://zoom.us/j/123456789"),
        ("https://zoom.us/wc/join/123456789", "123456789", "https://zoom.us/j/123456789"),
        ("https://company.zoomgov.com/j/123456789", "123456789", "https://zoomgov.com/j/123456789"),
    ],
)
def test_zoom_links(registry, url, meeting_id, canonical):
    m = registry.parse(url)
    assert m.provider == Provider.ZOOM
    assert m.meeting_id == meeting_id
    assert m.url == canonical
    assert "/wc/" in m.join_url and meeting_id in m.join_url


def test_zoom_typed_passcode_is_kept_separately(registry):
    m = registry.parse("https://zoom.us/j/123456789", passcode=" 4242 ")
    assert m.passcode == "4242"
    assert "4242" not in m.url


@pytest.mark.parametrize(
    "url, code",
    [
        ("https://zoom.us/my/someone", "zoom_personal_link"),
        ("https://zoom.us/j/12", "zoom_no_meeting_id"),
        ("https://zoom.us/", "zoom_no_meeting_id"),
    ],
)
def test_zoom_rejections(registry, url, code):
    with pytest.raises(InvalidMeetingUrl) as exc:
        registry.parse(url)
    assert exc.value.code == code


@pytest.mark.parametrize(
    "url",
    [
        "https://meet.google.com/abc-defg-hij",
        "meet.google.com/abc-defg-hij?authuser=1",
        "https://meet.google.com/ABC-DEFG-HIJ",
    ],
)
def test_google_meet_links(registry, url):
    m = registry.parse(url)
    assert m.provider == Provider.GOOGLE_MEET
    assert m.url == "https://meet.google.com/abc-defg-hij"
    assert m.join_url.startswith("https://meet.google.com/abc-defg-hij")


def test_google_meet_rejects_landing_page(registry):
    with pytest.raises(InvalidMeetingUrl) as exc:
        registry.parse("https://meet.google.com/landing")
    assert exc.value.code == "meet_no_code"


def test_teams_meetup_join_link(registry):
    url = (
        "https://teams.microsoft.com/l/meetup-join/19%3ameeting_ABC%40thread.v2/0"
        "?context=%7b%22Tid%22%3a%22t%22%2c%22Oid%22%3a%22o%22%7d"
    )
    m = registry.parse(url)
    assert m.provider == Provider.TEAMS
    assert "context=" in m.url
    assert "anon=true" in m.join_url


def test_teams_short_link_needs_passcode(registry):
    with pytest.raises(InvalidMeetingUrl) as exc:
        registry.parse("https://teams.microsoft.com/meet/12345678901")
    assert exc.value.code == "teams_passcode_required"
    m = registry.parse("https://teams.microsoft.com/meet/12345678901", passcode="Xy7")
    assert m.url == "https://teams.microsoft.com/meet/12345678901?p=Xy7"
    assert registry.parse("https://teams.live.com/meet/12345678901?p=Xy7").provider == Provider.TEAMS


def test_teams_rejects_non_meeting_links(registry):
    with pytest.raises(InvalidMeetingUrl) as exc:
        registry.parse("https://teams.microsoft.com/l/chat/0/0")
    assert exc.value.code == "teams_no_meeting"


@pytest.mark.parametrize(
    "url, code",
    [
        ("", "empty_url"),
        ("   ", "empty_url"),
        ("https://example.com/j/123456789", "unsupported_provider"),
        ("https://zoom.us.evil.com/j/123456789", "unsupported_provider"),
        ("javascript:alert(1)", "unsupported_provider"),
        ("https://zoom.us/j/" + "1" * 1200, "url_too_long"),
    ],
)
def test_generic_rejections(registry, url, code):
    with pytest.raises(InvalidMeetingUrl) as exc:
        registry.parse(url)
    assert exc.value.code == code


def test_mock_provider_only_when_enabled(tmp_path):
    off = ProviderRegistry(make_settings(tmp_path, enable_mock_provider=False))
    with pytest.raises(InvalidMeetingUrl):
        off.parse("http://testserver/mock-meet/room1")
    on = ProviderRegistry(make_settings(tmp_path, enable_mock_provider=True))
    assert on.parse("http://testserver/mock-meet/room1").provider == Provider.MOCK
