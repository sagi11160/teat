# E2E_STATE — MeetBridge (unified Zoom / Google Meet / Teams room)
> Updated: 2026-10-10 | Round: 1

## Goal
One shared call room: every participant joins from the platform they prefer (Zoom, Google Meet, Microsoft Teams, or a plain browser), and everyone hears and sees everyone else, because the system bridges audio and video between all the linked meetings in real time.

## Requirement sources
- User message (2026-10-10): "Build a platform that unifies Zoom, Google Meet and Teams. Everyone joins from their preferred platform and everyone is in the same call room."
- Operating protocol: `Coding-End-to-End-5_32e1.pdf` (Part 0 + Part A + Part B), uploaded by the user.

## Requirement matrix
| ID | Requirement | Source | Status | Evidence | Notes |
|----|-------------|--------|--------|----------|-------|
| R-001 | Organizer account: register, log in, log out, hashed passwords, sessions that expire | §0.9 (identity) | open | | |
| R-002 | Unified room: create, list, rename, delete; each room owned by one organizer | user | open | | |
| R-003 | Link external meetings to a room (Zoom / Meet / Teams URL); detect provider, validate and normalize URL; Zoom passcode | user | open | | |
| R-004 | Bridge connector per link: start/stop, persisted desired state, lifecycle (starting → joining → waiting_admission → live → stopped / failed) | user | open | | |
| R-005 | Media bridge: audio and video from each platform reach all other platforms and web participants, with no echo (a platform never receives its own audio back) | user | open | | Core requirement |
| R-006 | Browser participants: join the same room from a browser without any of the three apps (mic/camera, mute, camera off, leave) | user ("preferred platform") | open | | |
| R-007 | Unified invite page: one link listing every way to join (each linked platform + browser) | user | open | | |
| R-008 | Real-time status for the organizer: connector state, participants, event log | §0.6, cmd 203/204 | open | | |
| R-009 | Permissions: only the owner manages a room / its links / connectors; guests only via invite token; negative tests | §0.10.6, cmd 071/072/275 | open | | |
| R-010 | Failure handling and recovery: retries with backoff, terminal errors (denied, meeting ended, invalid link), resume connectors after a server restart | cmd 058/107 | open | | |
| R-011 | Provider adapters for Zoom web client, Google Meet, Teams web (join flow, lobby detection, end detection) behind one interface | user, cmd 210 | open | | Live verification needs real meetings |
| R-012 | Persistence with managed migrations (SQLite local, PostgreSQL via DATABASE_URL); data survives restart | §0.9 | open | | |
| R-013 | Hebrew RTL UI with loading / empty / error states, keyboard access, small screens | §0.9, cmd 092/094/095 | open | | |
| R-014 | Configuration via env + documented `.env.example`; startup validation; no secrets in code | §0.9, cmd 145 | open | | |
| R-015 | Operations: structured logs, /healthz + /readyz, `doctor` command, backup + restore | §0.10.7, cmd 148/170 | open | | |
| R-016 | Tests: one command runs everything; success + failure path per flow; automated E2E of the cross-platform media path | §0.10.4 | open | | |
| R-017 | Install / run docs (README), one command each for install, run, test; container image | §0.9 | open | | |

## Assumptions and decisions
- (pending)

## Blockers
- (none yet)

## Round log
- Round 1: read protocol in full, surveyed environment, created matrix.

## Next step
Scaffold `unified_meet/` (config, DB, models, migrations) and implement R-001.
