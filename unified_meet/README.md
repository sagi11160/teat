# MeetBridge

One call room that spans Zoom, Google Meet and Microsoft Teams. Each participant joins
from the platform they prefer, or straight from the browser, and everyone hears and
sees everyone else.

The user interface is in Hebrew (RTL). Code and technical documentation are in English.

## How it works

```
 Zoom meeting ──── Zoom bridge ─┐
                                │
 Meet meeting ──── Meet bridge ─┼── MeetBridge room ──── browser participants
                                │   (WebRTC mesh)
 Teams meeting ── Teams bridge ─┘
```

- An organizer creates a **room**, connects existing meetings to it (pastes their join
  links), and shares a single **invite link**.
- For every connected meeting, a **bridge** joins that platform's web client as a
  participant, using a headless Chromium driven by Playwright.
  - Its *microphone* is a live mix of everyone in the room who is **not** on that
    platform.
  - Its *camera* is a grid of their videos.
  - What the bridge receives from the platform (all remote audio and video) is
    published into the room.
- Each platform therefore hears and sees the other platforms and the browser
  participants, and never gets its own audio back (no echo).
- Guests without any of the apps join the room directly from the invite page: camera
  and microphone, listen-only, or phone browser.
- The organizer's room page shows each bridge's state live:
  - joining
  - waiting in the lobby
  - live
  - retrying
  - failed, with the reason

  It also shows who is in the call and an activity log.

## Requirements

- Linux or macOS, Python 3.11+.
- RAM: about 300–500 MB per active bridge (one browser tab each).
- Outbound HTTPS to the meeting platforms.
- For production: an HTTPS hostname. Browsers only allow camera and microphone on
  secure origins.

## Install, run, test

```bash
make install   # venv, dependencies, Chromium (+ system libraries with sudo), .env, database
make run       # http://localhost:8080
make test      # the whole suite, including end-to-end media tests in real browsers
```

The same without `make`: `./scripts/install.sh`, `./scripts/run.sh`, `./scripts/test.sh`.

Open http://localhost:8080, create an account, and then:

1. **Create a room**, and optionally set the name the bridge shows in the platforms.
2. **Connect meetings** by pasting each meeting's join link:
   - Zoom: `https://zoom.us/j/…`. If the link has no `pwd=`, enter the passcode in the
     passcode field.
   - Google Meet: `https://meet.google.com/abc-defg-hij`.
   - Teams: the "Join the meeting" link (`…/l/meetup-join/…` or `…/meet/…?p=…`).
3. Press **Start bridge** on each. If the meeting has a lobby, admit the bridge from the
   host's side; the badge switches to "connected and bridging".
4. **Share the invite link.** Each person picks Zoom, Meet, Teams, or "from the browser".

## Platform notes

The bridge joins each platform the way a guest would in a browser, so the meeting's own
settings decide whether it gets in:

| Platform | What must be allowed | If it is not |
|---|---|---|
| Zoom | "Join from browser" enabled for the meeting; passcode known; no "authenticated users only" | The bridge fails with "platform blocked the bridge" |
| Google Meet | Host admits the bridge from the lobby ("Ask to join") | Times out in "waiting for admission"; retries |
| Teams | Anonymous join allowed by the organizing tenant | Fails with "platform blocked the bridge" |

Some platforms refuse anonymous automated guests. Google Meet does this from some
cloud IP ranges, showing "You can't join this video call". In that case, sign the
bridge in with a dedicated account. This needs a machine with a display; copy the
resulting file to the server's `MB_DATA_DIR/bot-accounts/` if needed:

```bash
.venv/bin/python -m meetbridge.cli bot-login google_meet   # also: zoom, teams
.venv/bin/python -m meetbridge.cli bot-logout google_meet
```

The saved file contains session cookies; keep it private (it is created with mode 600).

Use the bridge responsibly. Participants on each platform see it as a named
participant, and the room name is shown in its video tile. Check that automated
participants are acceptable under each platform's terms and your organisation's
policy. For a high-volume Zoom deployment, Zoom's Meeting SDK is the officially
supported route for bots.

## Configuration

All settings are environment variables with the `MB_` prefix, or entries in `.env`.
`.env.example` documents every one. The most important:

| Variable | Purpose |
|---|---|
| `MB_PUBLIC_BASE_URL` | URL people use to reach the server (https in production) |
| `MB_SECRET_KEY` | Secret for session token digests (≥ 32 chars in production) |
| `MB_DATABASE_URL` | SQLite file by default; `postgresql+psycopg://…` for servers |
| `MB_ICE_SERVERS` | STUN/TURN servers for browser participants (add TURN for corporate networks) |
| `MB_MAX_ACTIVE_BOTS` | Concurrent bridges (memory bound) |
| `MB_ALLOW_REGISTRATION` | Turn off to create organizer accounts only via the CLI |

With `MB_ENV=production`, the server refuses to start if any of these hold:

- the secret key is weak;
- the test platform is enabled;
- the public URL is not https.

## Production deployment

- Run behind a TLS-terminating reverse proxy. Pass WebSockets (`/ws/`) through and
  disable response buffering for `/api/rooms/*/stream` (Server-Sent Events).
- Set these:
  - `MB_ENV=production`
  - `MB_PUBLIC_BASE_URL=https://…`
  - `MB_SECRET_KEY`
  - `MB_COOKIE_SECURE=true`
- Use PostgreSQL. The schema migrates automatically on start, or explicitly with
  `python -m meetbridge.cli migrate`.
- Add a TURN server to `MB_ICE_SERVERS` so browser guests behind strict NATs can
  connect.
- Run **one** instance per set of rooms. Room membership and signalling live in
  process memory, so several instances need sticky routing per room.

### Docker

```bash
docker build -t meetbridge .
docker run -d --name meetbridge -p 8080:8080 -v meetbridge-data:/data \
  -e MB_PUBLIC_BASE_URL=https://meet.example.com -e MB_SECRET_KEY=... meetbridge
```

The image is based on Microsoft's Playwright image (Chromium and its libraries
included). It runs as a non-root user, keeps data in `/data`, logs JSON, and has a
health check on `/readyz`.

## Operations

```bash
.venv/bin/python -m meetbridge.cli doctor                  # config, DB, schema, browser, warnings
.venv/bin/python -m meetbridge.cli backup [file]           # online SQLite backup -> data/backups/
.venv/bin/python -m meetbridge.cli restore FILE --yes      # validates the file, keeps a pre-restore copy
.venv/bin/python -m meetbridge.cli create-user --email you@example.com --name "You"
.venv/bin/python -m meetbridge.cli migrate
```

- `GET /healthz` reports liveness. `GET /readyz` checks the database and bridge
  supervisor, and returns 503 when not ready.
- Logs go to stdout, as JSON when `MB_LOG_JSON=true`. Every request is logged with a
  request ID, which also appears in error responses. Invite tokens are redacted from
  logs.
- PostgreSQL backups use the database's own tooling (`pg_dump` / `pg_restore`). The
  `backup` command prints the exact commands.
- After a restart, bridges that were running reconnect automatically.

## Tests

`make test` runs everything, about one minute:

- **Unit and integration tests:**
  - URL parsing for each platform.
  - Authentication: hashing, sessions, rate limit, CSRF.
  - A positive and a negative test for every room permission (owner, other user,
    anonymous).
  - Bridge supervisor:
    - retries and terminal errors;
    - capacity limit;
    - resume after restart.
  - WebSocket signalling and migrations.
  - CLI: backup, restore, doctor.
  - Security headers and log redaction.
- **End-to-end tests** (`tests/e2e`) start the real server and real bridge bots:
  - Two local "MockMeet" meetings stand in for two platforms, plus a browser
    participant.
  - Each human's microphone plays a different tone. An FFT of what each one receives
    proves that everyone hears exactly the others, and not themselves.
  - Video, lobby admission and denial, meeting end, the organizer's live stream, data
    persistence across restarts, and the full Hebrew UI are checked in a real browser.
    Any console error or CSP violation fails the test.
- **PostgreSQL:**
  `MB_TEST_DATABASE_URL=postgresql+psycopg://user:pass@host/db make test` runs the
  API, permission, supervisor and migration tests against PostgreSQL. The database's
  `public` schema is wiped.
- **Real platforms (opt-in):** `MB_LIVE_PLATFORM_TESTS=1 .venv/bin/python -m pytest tests/live`
  checks Zoom, Meet and Teams against their live sites. Set `MB_LIVE_ZOOM_URL`,
  `MB_LIVE_MEET_URL` or `MB_LIVE_TEAMS_URL` to also test a real join.

## Limitations

- A bridge is a browser automating each platform's web client. When a platform changes
  its join screens, the selectors and phrases in `meetbridge/providers/*.py` may need
  updating. The live-platform tests detect this.
- Media between the room and browser participants is a full WebRTC mesh. It is sized
  for a handful of browser guests per room (`MB_MAX_PARTICIPANTS_PER_ROOM`, default
  12). Each platform counts as one member, however many people are on it.
- Each platform sees the rest of the room as a single composite participant (one mixed
  audio stream, one video grid). The individual people on other platforms are not
  separate tiles there.
