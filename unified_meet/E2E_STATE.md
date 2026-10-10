# E2E_STATE — MeetBridge (unified Zoom / Google Meet / Teams room)
> Updated: 2026-10-10 | Round: 6 (delivery)

## Goal
One shared call room: every participant joins from the platform they prefer (Zoom, Google Meet, Microsoft Teams, or a plain browser), and everyone hears and sees everyone else, because the system bridges audio and video between all the linked meetings in real time.

## Requirement sources
- User message (2026-10-10): "Build a platform that unifies Zoom, Google Meet and Teams. Everyone joins from their preferred platform and everyone is in the same call room."
- Operating protocol: `Coding-End-to-End-5_32e1.pdf` (Part 0 + Part A + Part B), uploaded by the user.

## Requirement matrix
Test paths are relative to `unified_meet/tests/`.

| ID | Requirement | Source | Status | Evidence | Notes |
|----|-------------|--------|--------|----------|-------|
| R-001 | Organizer account: register, log in, log out, hashed passwords, sessions that expire | §0.9 | verified | `test_auth.py` (flow, Argon2 hash + token digest only, cookie flags, expiry, server-side logout, wrong password, rate limit, validation, CSRF, closed registration); `cli create-user` in `test_cli.py` | No hardcoded admin; first user via signup or `create-user` |
| R-002 | Unified room: create, list, rename, delete; owned by one organizer | user | verified | `test_rooms.py::test_room_crud`, `test_room_validation`, `test_room_list_is_per_user`; `e2e/test_ui.py::test_organizer_flow` | |
| R-003 | Link Zoom / Meet / Teams URLs; detect provider, validate, normalize; passcodes | user | verified | `test_providers.py` (10 tests), `test_rooms.py::test_links_for_all_three_platforms`, `test_link_errors`, `test_link_passcode_never_returned` | Passcodes are write-only through the API |
| R-004 | Bridge per link: start/stop, persisted desired state, lifecycle | user | verified | `test_supervisor.py::test_start_goes_live_and_stop_disconnects`, `test_start_is_idempotent`, `test_waiting_room_state_is_reported`; `e2e/test_media_bridge.py::test_three_way_media_without_echo` (stop path) | |
| R-005 | Media bridge: audio + video from each platform reaches all others, no echo | user | verified (local platform) | `e2e/test_media_bridge.py::test_three_way_media_without_echo`: three participants on two bridged meetings + browser, each plays a distinct tone; FFT shows every participant hears both other tones 54–66 dB over noise, own tone at codec-noise level (≥30 dB below); video frames from every source present | Real Zoom/Meet/Teams media: see R-011 |
| R-006 | Browser participants: mic/camera, mute, camera off, leave; fallback with no devices | user | verified | `e2e/test_ui.py::test_browser_call_page` (camera + mic toggles, leave), `test_live.py` (signaling, room full, rotated invite, room closed) | Falls back to audio-only, then listen-only |
| R-007 | Unified invite page listing every way to join | user | verified | `test_rooms.py::test_invite_page_data_is_public_but_minimal`, `e2e/test_ui.py::test_organizer_flow`, `test_invalid_invite_and_call_links` | Invite token can be rotated |
| R-008 | Real-time organizer status: bridge state, participants, event log | §0.6 | verified | `e2e/test_media_bridge.py::test_organizer_live_stream` (SSE), `test_supervisor.py::test_state_events_are_published_only_after_commit`, `test_rooms.py::test_events_are_recorded`, `test_live.py::test_organizer_stream_requires_owner` | |
| R-009 | Permissions; guests only via invite token; negative tests | §0.10.6 | verified | `test_rooms.py::test_owner_can_access` / `test_other_user_gets_not_found` / `test_anonymous_gets_unauthorized` (per endpoint), `test_link_of_other_room_is_not_reachable_through_own_room`, `test_live.py::test_signal_cannot_cross_rooms` | |
| R-010 | Retries with backoff, terminal errors, resume after restart | cmd 058/107 | verified | `test_supervisor.py` (transient retry, give-up, terminal error, meeting end, capacity queue, resume after restart); `e2e/test_media_bridge.py::test_lobby_admission_and_denial`, `test_already_ended_meeting_is_not_retried`; Docker `restart` resumed a live bridge | |
| R-011 | Zoom / Meet / Teams web adapters (join, lobby, end detection) behind one interface | user | implemented — live join blocked | `test_detection.py` (page-state detection in a real browser); `live/test_real_platforms.py::test_nonexistent_meeting_is_reported` run against the real sites: Zoom and Teams → `meeting_not_found`, Meet → `meeting_not_found` / `platform_blocked_bot` | Blocked: joining a real live meeting needs a real meeting URL (see Blockers) |
| R-012 | Persistence with migrations (SQLite / PostgreSQL); survives restart | §0.9 | verified | `test_migrations.py` (from zero, on existing DB keeping data, downgrade/upgrade, PostgreSQL 16), `e2e/test_media_bridge.py::test_data_persists_across_restart`, `test_writes_are_visible_to_the_next_request`; full suite on PostgreSQL: 100 passed | |
| R-013 | Hebrew RTL UI; loading/empty/error states; keyboard; mobile | §0.9 | verified | `e2e/test_ui.py::test_keyboard_only_signup_and_room`, `test_invalid_invite_and_call_links`, console-error check in all UI tests; desktop + mobile screenshots reviewed | |
| R-014 | Env config + `.env.example`; startup validation; no secrets in code | §0.9 | verified | `test_cli.py::test_production_refuses_unsafe_config`, `test_doctor_healthy_and_failing`; `.env.example` documents every setting; session secret generated into the data dir | |
| R-015 | Structured logs, /healthz + /readyz, doctor, backup + restore | §0.10.7 | verified | `test_cli.py` (doctor, backup/restore round trip, invalid restore rejected, migrate), `test_security.py::test_invite_tokens_are_not_logged`, `test_request_id_is_sanitised`; container healthcheck on /readyz | |
| R-016 | One test command; success + failure per flow; automated cross-platform media E2E | §0.10.4 | verified | `make test`: 147 passed, 7 skipped (opt-in live-platform / PostgreSQL), stable over repeated runs | |
| R-017 | README; one command each to install, run, test; container image | §0.9 | verified | Clean clone: `make install`, `make test`, `make run` + `/readyz` OK; Docker image built and run with a volume | |

## Assumptions and decisions
- Bridging is done by headless Chromium "bridge bots" that join each external meeting as a guest through the platform's web client, and relay media through a WebRTC full-mesh room hub that browser participants join directly. Official SDKs were not used: each requires per-platform app registration, and none can relay media between vendors.
- Each meeting gets the mix of everyone except itself, which is how echo is avoided.
- MockMeet, a local meeting platform with lobby, admit, deny and end, is enabled only in dev/test. It makes the full media path testable without real accounts and without sending anything real.
- Google Meet often blocks anonymous automated guests. `cli bot-login google_meet` saves a signed-in session that the bot reuses. Credentials stay on the server (chmod 600), never in the DB, logs or this file.
- SQLite by default; PostgreSQL via `MB_DATABASE_URL`. Alembic migrations run on startup.
- Mesh topology suits small rooms (default cap 8 members). Larger rooms would need an SFU.

## Blockers
- Joining a live Zoom / Meet / Teams meeting end to end (R-011). Requires a real meeting URL that admits guests, or a host to admit the bot. To resolve: set `MB_LIVE_PLATFORM_TESTS=1` plus `MB_LIVE_ZOOM_URL` / `MB_LIVE_MEET_URL` / `MB_LIVE_TEAMS_URL`, then run `pytest tests/live`.
- Google Meet from cloud IPs. Anonymous bots get "You can't join this video call". To resolve: run `python -m meetbridge.cli bot-login google_meet` once on a machine with a display.
- Production exposure (HTTPS domain and a TURN server for participants behind strict NAT). This is deployment configuration outside standing permission. To resolve: set `MB_PUBLIC_BASE_URL` and `MB_ICE_SERVERS` as described in the README.

## Round log
- Round 1: read the protocol in full, surveyed the environment, created the matrix.
- Round 2: auth, rooms, links, provider parsing, migrations, permission matrix.
- Round 3: bridge runner, inject script, room hub, supervisor, MockMeet; three-way media E2E with FFT.
- Round 4: Hebrew UI (dashboard, room, invite, call), SSE, keyboard and mobile; fixed the read-your-writes race.
- Round 5: probed the real platforms (fixed the Teams detection issues, added bot-login for Meet), CLI ops, Docker, PostgreSQL run, security hardening.
- Round 6: verified from a clean clone, fixed the event-publish-before-commit bug, filled evidence gaps (session expiry, camera toggle), cleaned up.

## Next step
Delivery gate passed except for the blockers above, each of which needs an external input (a real meeting URL, a signed-in bot account, or a deployment target).
