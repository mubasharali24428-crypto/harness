# M17 Phase A — MAC2 red-team findings (salvage run)

Repo: `untitled folder 2/harness` @ HEAD `3c794b2` · Lens: security red-team · Date: 2026-08-24
Provenance: prior MAC2 died to provider 503 pre-write; this is the compacted re-dispatch.
Scope honored: 3 lenses, evidence-first, NO fixes, NO git writes.

Assumptions: static review only (no live services running); line numbers refer to HEAD 3c794b2.

---

## LENS 1 — Authz matrix holes

### F1 (HIGH, confirmed — verifies P-BETA) · Orchestrator run-guard fails OPEN on unwired DB *and* on missing rows

`section4/src/harness_orchestrator/authz.py`

- **:50-52** — `authorize_run` silently skips the team-ownership check whenever
  `getattr(service, "session_factory", None)` is `None`: `return run_data  # bare unit mode`.
  The comment frames it as test convenience, but nothing restricts it to tests:
  any deploy assembly that forgets to wire a session factory gets a service whose
  every run route believes every caller owns every run. Role checks still fire,
  so this escalates *viewer/guest* restrictions too? No — role is independent;
  the lost check is specifically cross-team isolation (any authenticated team
  may drive any run id).
- **:56-58** — second fail-open: `run_data.get("projectId")` empty ⇒ `return run_data`.
  A run never bound to a project is owned by nobody and therefore (by this code)
  accessible to everyone.
- **:59-66** — third fail-open: the ownership verdict is
  `if row is not None and row.team_id != principal.teamId: raise`. A dangling
  `projects` row (deleted project, run orphaned, or a project_id pointing at a
  foreign schema) yields `row is None` ⇒ **allowed**. Default-deny would be
  three characters away (`if row is None or ...`) and is not taken.

Net shape: one guard, three separate "couldn't verify ⇒ permit" branches. The
guard's own docstring (:8-9) acknowledges the first; the other two are silent.

**Siblings checked (fail-open census, cheap grep level):**
- Contrast, correct pattern: `section4/src/harness_orchestrator/api.py:264-277`
  `get_principal` refuses loudly (503 AUTH_NOT_CONFIGURED) when auth is unwired —
  "No wiring = hard failure, never an open endpoint". The run-ownership half of
  the same D5 feature does the exact opposite.
- `section1/src/harness_identity/ratelimit.py:327-333` fails open on store
  errors — but it is *documented as deliberate* (:12-16) with a bounded blast
  radius ("one degraded window"). Acceptable; cited here as the standard the
  authz guard should have met (explicit + logged, not silent).
- `section2/src/harness_github/app.py:139-147` OAuth start-ticket lookup fails
  closed (None/expired ⇒ 401). Correct.
- Deeper sibling sweep (orchestrator service internals) cut for budget; F2 below
  shows the same "family" extends beyond authz.py anyway.

### F2 (HIGH, new) · plan_gate routes enforce role but NEVER team — cross-team plan approve/reject

`section4/src/harness_orchestrator/plan_gate.py`

- `propose_plan` (:248-271), `approve_plan` (:284-325), `reject_plan` (:338+)
  gate exclusively on `_require_role(principal, …)` (:219-221, sets at :235-236).
  Grep of the whole file for `team|ownersh|project`: exactly one hit, an
  unrelated local var at :487. **No `authorize_run`, no project→team join, ever.**
- Run/plan resolution is global-by-id: `_ensure_run(run_id)` at :258;
  `_locate_plan(service, planId)` at :298 (and :437/:487 paths). Any engineer or
  admin of Team B who learns (or guesses/enumerates) a plan id minted inside
  Team A's run can **approve or reject Team A's plan**, driving the target
  run's state machine (`sm.approve_plan(...)` :315) — a write primitive across
  the tenant boundary.
- The API surface right next door proves the intended invariant:
  `api.py:283-321` wraps every run-control route in `_authz_dep(action)` which
  calls `authorize_run` (ownership) *then* `require_role`. plan_gate imports
  `RequirePrincipal` only and reimplements a thinner guard. Two parallel route
  families in one service, two different enforcement depths — the matrix has a
  seam exactly where the sensitive action (plan decision) lives.
- Oracle inconsistency: plan_gate answers 404 PLAN_NOT_FOUND (:300-303) for a
  foreign plan, while section1's M13-G5a no-oracle law deliberately collapses
  foreign-resource probes into indistinguishable 404s
  (`section1/src/harness_identity/app.py:84-100`; `invites.py:126-140`). Even
  the error-channel discipline disagrees between sections.

### F3 (MEDIUM, new) · Invite redemption binds to the token, not to the invited identity

`section1/src/harness_identity/invites.py`

- `redeem_invite` (:411-519): "Any authed user redeems a token and joins the
  invited team with the invited role." The claim row carries an ``email``
  (minted at :169), but redemption **never compares it to the redeemer's
  account email** — the email is used only cosmetically, to derive a display
  name fallback (:464-467). Control is solely token confidentiality.
- Consequence: an intercepted/forwarded invite (mail forwarding rules, shared
  inbox, accidental cc, insider re-share) enrolls *whichever account presents
  the token*, at the minted role — and `INVITABLE_ROLES`/the body pattern admit
  `admin` (:50, :147). Admin-grade membership is one forwarded email wide.
- Mitigations present (honest accounting): tokens are 256-bit urlsafe, stored
  hashed only (:91-93), single-statement atomic claim prevents double-spend
  (:425-441), revoke/resend kill stale tokens transactionally (:391-395). This
  is a design-gap finding (email-binding absent), not an implementation bug.
- Related nit: `alreadyMember` path consumes the token and reports the member's
  *existing* role (:477-494) — a user cannot tell they were invited at a
  different role than they hold. Audit trail keeps the truth (:481-487).

### F4 (LOW-MEDIUM, new) · Rate-limit coverage gap: `/api/v1/invites/redeem` and `/api/v1/ws-ticket` are unthrottled

`section1/src/harness_identity/ratelimit.py:138-147` — `DEFAULT_PROTECTED_ROUTES`
covers OTP edges, invite **mint** template, login, CSP report, and the expensive-op
trio. Not covered: `POST /api/v1/invites/redeem` (unauthenticated-shape oracle:
404 unknown / 409 redeemed / 410 revoked / 410 expired — :442-462 — lets a
holder classify a found/leaked token's state; brute-force itself stays
infeasible against 256-bit tokens) and `POST /api/v1/ws-ticket` (each call does
a cleanup DELETE + INSERT; floodable write load, though auth-gated). Cheap fix
surface, noted not fixed per scope.

---

## LENS 2 — WS ticket lifecycle

Architecture recap: `POST /api/v1/ws-ticket` mints a one-time, 30s, project-bound
ticket (`section1/src/harness_identity/app.py:513-534` → `ephemeral.py:50-86`);
`/ws/{project_id}` redeems it at upgrade (`app.py:537-619`). Binding = sha256
of UA + peer IP, first 16 hex chars (`ephemeral.py:26-35`).

### F5 (MEDIUM-HIGH, new) · Fingerprint binding is UA-string equality — trivially spoofable, and the grace path neuters it entirely

- The "fingerprint" is `sha256(UA ‖ client_ip)[:16]` over two values the client
  already knows and controls (`ephemeral.py:29-35`; the docstring honestly says
  "NOT a cryptographic secret" :22-25). An attacker who steals a ticket in
  transit (URL/query-param bearer material — see F7) can replay it from any
  host by sending the same `User-Agent` header; if the victim is behind NAT or
  a shared egress proxy, the IP half matches without any effort. This raises
  theft cost from zero to one curl flag.
- **Grace path defeats the control**: at upgrade, an *absent*
  `X-WS-Fingerprint` is warn-log + allow (`app.py:584-592`), explicitly for
  browsers. So the entire binding is opt-in-by-header today; a replaying
  attacker simply omits the header (or the `fingerprint` query param,
  :583-585) and the check evaporates while looking like legacy traffic. Only
  the server-side recompute-vs-mint comparison (:602-610) survives, and that
  is exactly the spoofable UA+IP hash above.
- Mint-time vs upgrade-time inputs are symmetric (`_client_ip`
  `app.py:57-70`, `websocket.client.host` :581), so no proxy asymmetry hole;
  behind a reverse proxy every client shares the proxy IP and the fingerprint
  degrades to UA-equality only (acknowledged in `_client_ip`'s docstring).

### F6 (LOW, confirmed-safe with one nit) · TTL handling

- TTL is fixed at 30s (`WS_TICKET_TTL_SECONDS`, `ephemeral.py:19`) and enforced
  inside the atomic single-statement claim (`DELETE … WHERE expires_at > now
  RETURNING`, :104-115) — expiry and single-use cannot race apart. Expired
  ticket rows are swept opportunistically at each mint (:64-67). Solid.
- Nit: redemption compares ISO **strings** against `_now_iso()` output
  (:112). The module's own docstring (:1-9) documents why mixed formats break
  string comparison; all current writers normalize through `_iso()`/`_now_iso()`,
  so this holds today, but any future raw-datetime writer reintroduces the bug
  class silently. Fragile invariant, not an exploit.
- Replay-window shape: 30s one-time tokens are fine per-ticket; the real
  window risk is F8 below (mint is unlimited), not TTL length.

### F7 (MEDIUM, new) · Ticket travels as a URL query parameter on the upgrade

- `project_ws(websocket, project_id, token="", afterSeq=0, ticket="",
  fingerprint="")` (`app.py:537-541`) takes the bearer-grade ticket from the
  query string. Query strings land in access logs, proxy logs, and browser
  history — the exact exposure H8's docstring says the ticket exists to avoid
  for long-lived tokens ("the WS upgrade never needs a long-lived bearer token
  in a URL", :523-524). The 30s TTL caps the damage, but combined with F5 the
  effective protection of "ticket in URL" is thin.
- Auth-fallback ladder widens surface: if no ticket, a long-lived session
  token is accepted via `?token=` (:552-553) or the `Authorization` header
  (:555-562). The `?token=` variant puts a LONG-LIVED credential in the URL —
  precisely what H8 forbids — with no TTL cap. That is the strongest finding
  in this lens even though it is a legacy-compat channel.

## LENS 3 — Rate-limit evasions

Design: Postgres fixed-window counters keyed `<client_ip>|<route_template>`
(`section1/src/harness_identity/ratelimit.py:192-195`), atomic upsert
(:171-181), shipped route table :138-147, budgets :118-131. DB failure fails
open deliberately (:12-16, :327-333 — documented, accepted).

### F8 (HIGH, structural) · Pure peer-IP keying ⇒ IPv6 rotation divides every budget by /64… or multiplies it by 2⁶⁴

- `bucket_key(client_ip, …)` uses `scope["client"][0]` verbatim (:286, :313).
  A client holding an IPv6 block (/64 is the minimum routable allocation) owns
  2⁶⁴ distinct bucket keys and therefore 2⁶⁴ × budget requests per window:
  OTP spray at "10/min/IP" becomes ~10/min × 18 quintillion in theory,
  practically limited only by rotation speed. No prefix normalization (e.g.
  key on /64), no subnet awareness anywhere in the file (grep: `bucket_key`
  is the sole keying site).
- Same mechanism defeats per-IP lockout on `/auth/login`'s second layer:
  `_login_rate_limited` keys the same raw IP (`app.py:73-88`).
- Header tricks are NOT needed and do NOT work here (worth stating for the
  matrix): `X-Forwarded-For` is deliberately ignored (`_client_ip`,
  app.py:57-70) — so no XFF-spoof budget-splitting, but also **no trusted-proxy
  story**: behind any LB, ALL users share ONE bucket (proxy IP), meaning the
  limiter converts a deployed proxy into a global DoS fuse (one busy office
  exhausts everyone's auth-edge budget) while attackers rotate IPv6 freely.
  The design is simultaneously too coarse (shared egress) and too fine (v6
  rotation) — the classic fixed-window/IP-keying pincer.
- Fixed-window burst bonus: windows are aligned to wall-clock epochs
  (`current_window`, :184-189), so up to 2× budget is available across a
  window boundary (budget at :59 then budget at :60). Minor next to F8 but
  standard sliding-window would remove it.

### F9 (MEDIUM, new) · Bucket table grows forever; fail-open amplifies under load

- No code path ever deletes from `rate_buckets`: migration 0011 creates the
  table + a `window_start` index (`migrations/0011_rate_buckets.sql:7-12`) but
  the module contains no cleanup (grep `DELETE` — only the ws_tickets sweep in
  ephemeral.py). Every unique `(ip|template)` pair is a permanent PK row.
  Combined with F8, an attacker rotating IPv6 addresses performs unbounded
  table growth → disk/ vacuum pressure — a storage-exhaustion DoS whose side
  effect is the limiter itself.
- When the resulting store pressure causes errors/timeouts, `_check` fails
  OPEN (:327-333): the attack that filled the store now disables limiting
  entirely, including on the credential edges. The documented "attacker gains
  at most one degraded window" assumption (:15) doesn't model an attacker who
  is *inducing* the degradation continuously. Cheap partial fix (not applied):
  reuse the ws_tickets sweep pattern — opportunistic `DELETE … WHERE
  window_start < now - N windows` inside `_check`.

---

## Verdict summary

| # | Finding | Sev | File:line |
|---|---|---|---|
| F1 | Run-guard triple fail-open (unwired factory / no projectId / dangling project row) | HIGH | section4/authz.py:50-66 |
| F2 | plan_gate approve/reject: role-only, zero team scoping — cross-team write | HIGH | section4/plan_gate.py:248-338 |
| F3 | Invite redeem ignores invited email; admin role joinable via forwarded token | MED | section1/invites.py:411-519 |
| F4 | `/invites/redeem` + `/ws-ticket` absent from protected-route table | LOW-MED | section1/ratelimit.py:138-147 |
| F5 | WS fingerprint = spoofable UA hash; absent-header grace path bypasses check | MED-HIGH | section1/ephemeral.py:29-35, app.py:579-610 |
| F6 | TTL/single-use atomicity sound; ISO string compare is a fragile invariant | LOW | section1/ephemeral.py:19,104-115 |
| F7 | Ticket (and worse, long-lived session token) accepted as URL query param | MED | section1/app.py:537-562 |
| F8 | Raw-IP keying: IPv6 rotation evades budgets; proxy deployments get one shared bucket | HIGH | section1/ratelimit.py:192-195 |
| F9 | rate_buckets never garbage-collected; attacker-induced fail-open cascade | MED | migrations/0011_rate_buckets.sql, ratelimit.py:327-333 |

Method note: static review at HEAD 3c794b2 within a hard tool-call budget
(salvage law); no live probes were run — all findings cite read source. F1
verifies P-BETA's prior report (authz.py:50-58) and extends it with two more
fail-open branches (:56-58, :65) plus the plan_gate sibling family (F2).
No fixes applied; no git writes made.
