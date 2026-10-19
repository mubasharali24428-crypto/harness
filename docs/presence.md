# Presence and participation (part 41)

Code: `section4/src/harness_orchestrator/presence.py` and `participation.py`; tables from
`section1/migrations/0042_presence.sql`. Scope is a run (`scope_kind='run'`); the schema also accepts
`'session'` but no route serves it yet.

## What presence is, and is not
Presence is an approximate hint, never a promise of availability. Every presence response carries
`approximate: true` and a `notice`. The API never returns exact timestamps, device ids or device counts,
only a status and a coarse last-seen bucket.

| status | meaning |
|---|---|
| `online` | at least one device has a live record and reported online |
| `idle` | live records exist, none reported online (mode `full` only) |
| `disconnected` | records exist but all expired; `lastSeen` is `under_5_min`, `under_1_hour` or `under_1_day` (mode `full` only) |
| `unknown` | never seen, last seen over 24 hours ago, or mode `limited` and not live |
| `hidden` | the user's `sharePresence` hides them from you |

## Expiry and multi-device
- A heartbeat upserts one row per (run, user, device) and sets `expires_at = now + stale_seconds`
  (team setting, default 90, range 15..3600). A crashed client simply stops heartbeating; no cleanup call is needed.
- Reads merge a user's devices: online beats idle beats disconnected. One expired device never changes the merged state while another is live.
- Expired rows are kept 24 hours only to compute the bucket, and are purged by the next heartbeat on that run. Runs nobody heartbeats again
  keep their rows until the retention job in the migration header is run.
- Max 10 device rows per user per run (429 `TOO_MANY_DEVICES`); the user's own expired rows are reclaimed first.
- Clients should heartbeat every `heartbeatEverySeconds` (stale/3, at least 5) from the response.

## Privacy and visibility
- Every route sits behind the run ownership guard (`authorize_run`): a team that does not own the run gets the same 404 `NOT_FOUND`
  as an unknown run, and the denial is audited. No presence is readable without it.
- Heartbeats are accepted only from run participants (409 `NOT_PARTICIPANT`).
- Per user and run, `sharePresence`: `nobody` (only you see yourself), `team` (default, visible to run participants), `everyone`
  (visible to anyone who can see the run). Hidden users still appear in the roster with their role, with `presence.status = "hidden"`.
- Team setting `mode` (admins only, `PUT /presence-settings`):
  `full` as above; `limited` stores and reports only live/not-live (idle is stored as online, no last-seen);
  `off` refuses heartbeats (409 `PRESENCE_DISABLED`), reports `presence: null`, and the team's stored rows are deleted when it is switched on.
  The setting is per team. There is no org-wide setting; an org admin applies it to each team.

## Participation
| route (prefix `/api/v1/orchestrator`) | behaviour |
|---|---|
| `POST runs/{id}/participation/observe` | participant -> observer. Leader: 409 `LEADER_MUST_HAND_OFF` |
| `.../participate` | observer -> participant. Viewer/guest: 403 `ROLE_DENIED` |
| `.../leave` | deletes your `run_participants` row and presence rows, cancels a pending handoff addressed to you. Leader: 409 `LEADER_MUST_HAND_OFF` |
| `.../rejoin` | inserts you (observer for viewer/guest, else participant). Idempotent; subject to the cap |
| `.../mute`, `.../unmute`, `GET|PUT .../prefs` | per-run `muted`, `notify`, `sharePresence`; `notificationsEnabled = notify and not muted` |
| `GET runs/{id}/presence` | roster payload (below) |
| `POST runs/{id}/presence/heartbeat`, `.../leave` | `leave` expires one device (`deviceId`) or all yours; roster membership untouched |
| `GET|PUT presence-settings` | team settings; GET any member, PUT admin |

All actions are idempotent (a repeat returns `changed: false`, writes no event or audit). Non-members get 409 `NOT_PARTICIPANT`.
Prefs persist across leave and rejoin. Mute is private: it is audited but never broadcast, and only your own roster entry shows `preferences`.

### Capacity
`team_presence_settings.max_participants` (default 20, range 1..200) counts every roster row (leader, participants, observers).
A full run refuses new members with 409 `SESSION_FULL` and writes `participant.capacity_refused`. Existing members, role switches and
retries are never refused. Lowering the cap never evicts. `participation.capacity_ok(db, run_id, team_id, user_id) -> bool`
is what the collab join path calls: True for an existing member or when below the cap; on PostgreSQL it takes a per-run advisory
transaction lock so concurrent joins cannot overshoot. A store without the 0042 table falls back to the default cap.

### Roster payload (`GET runs/{id}/presence`)
`mode`, `approximate`, `notice`, `capacity {max,current}` and `participants[]` with `userId`, `role`, `isLeader`, `self`,
`presence` (see table), `announcement`, and `preferences` on your own entry. Control: `isLeader` is the only control status here;
the lease holder is served by the control routes (lane C).

## Accessibility announcements
Every participation change returns an `announcement` sentence ("Name is now observing", "Name left the run") and writes the same text in the
`announcement` field of the `run_collab_events` payload (kinds `participant.observing`, `participant.participating`, `participant.left`,
`participant.rejoined`), so live clients can feed an `aria-live` region. Each roster entry also carries the current-status sentence
("Name is idle", "Name disconnected"); presence changes by expiry produce no event, so clients derive them by polling `GET presence`.
Control-change announcements are the control lane's events and are not produced here.

## Audit
`participant.observing`, `participant.left`, `participant.rejoined`, `participant.capacity_refused`, `participant.muted`, `participant.unmuted`,
`presence.settings_changed` (team settings and non-mute pref changes, with `before`/`after`). Observer -> participant has no reserved action
name, so it is logged as `participant.joined` with before/after roles. Heartbeats are not audited.

## Limits
Heartbeat rate is not throttled beyond the device cap. Scope `session` has no routes. Tested on sqlite only (see the evidence file).
