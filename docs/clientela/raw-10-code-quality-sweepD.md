# Mechanical Sweep D — attempt 3 (rerun of dead agent's exact greps)

Scope: `/Users/user/untitled folder 2/harness` — `section*/src` + `deploy/`, read-only.
97 Python/TS files scanned in section*/src (tests excluded). Zero repo modifications.

## Lens totals

| # | Lens | Hits | Notes |
|---|------|------|-------|
| 1 | Broad `except Exception` | **44** | 0 bare `except:`; 38 carry noqa+rationale; **6 unexplained** |
| 2 | TODO/FIXME/HACK/XXX | **0** | clean across src + deploy |
| 3 | `print(` outside cli/__main__ | **3** | all OTP dev-notify on stderr |
| 4 | Hardcoded localhost/127.0.0.1//Users | **16 src** (+19 deploy) | mostly env-default fallbacks |
| 5 | Mutable default args (`=[]`/`={}`) | **0** | clean |
| 6 | TS `: any` / @ts-ignore / eslint-disable | **2** | both eslint-disable exhaustive-deps |
| 7 | sync-in-async (time.sleep/requests./subprocess.run) | **0** | clean |
| 8a | `import *` | **0** | clean |
| 8b | Naming drift section6 | **snake 65 vs camel 5** | camel is real drift, not aliases |
| + | Magic numbers | **7** | timeouts & max_tokens inline |
| + | Cross-file duplicate lines | **38 dup lines / 20 clusters** | biggest: auth shim x5 |

## Findings — one line each

### L1 broad excepts (44; listing the 6 unexplained + pattern note)
- [section1/src/harness_identity/eventbus.py:140] [except] broad except w/o noqa or rationale → add noqa comment or narrow to psycopg/asyncpg errors
- [section3/src/harness_router/ledger.py:146] [except] broad except swallows ledger read failure silently → log at warning + narrow exception type
- [section3/src/harness_router/api.py:205] [except] `except Exception as e` no noqa/comment → annotate intent or narrow
- [section5/src/harness_sandbox/api.py:108] [except] `except Exception as e` no noqa/comment → annotate intent or narrow
- [section8/src/harness_review/api.py:129] [except] `except Exception as exc` no rationale → align with codebase noqa convention
- [section8/src/harness_review/api.py:203] [except] `except Exception as exc` no rationale → align with codebase noqa convention
- Pattern note: remaining 38 are deliberately annotated (`# noqa: BLE001 — <reason>`), consistent style; no action beyond the 6 above.

### L3 print() (3)
- [section1/src/harness_identity/notifier.py:51] [print] OTP printed to stderr → OK for dev notifier; gate behind settings.env != prod
- [section1/src/harness_identity/app.py:169] [print] duplicate of notifier.py:51 OTP printer → extract shared helper (see dup D17)
- [section1/src/harness_identity/notifier.py:315] [print] multi-line print in delivery path → route through logger for prod observability

### L4 hardcoded hosts (src, 16)
- [section1/src/harness_identity/config.py:8] [hardcoded] cors_origins default localhost:3000 → env-driven only in prod; acceptable default, document
- [section2/src/harness_github/config.py:9] [hardcoded] same cors default copy-pasted → shared contracts constant candidate
- [section8/src/harness_review/config.py:9] [hardcoded] same cors default third copy → shared contracts constant candidate
- [section10/src/harness_onboarding/api.py:33] [hardcoded] fourth copy of cors default → shared contracts constant candidate
- [section10/src/harness_onboarding/api.py:35] [hardcoded] identity_url localhost:8001 → fine as Settings default (env-overridable)
- [section10/src/harness_onboarding/api.py:36] [hardcoded] github_url localhost:8100 → fine as Settings default
- [section10/src/harness_onboarding/api.py:38] [hardcoded] orchestrator_url localhost:8004 → fine as Settings default
- [section2/src/harness_github/config.py:12] [hardcoded] github_redirect_uri localhost:8002 → must be env-set in any non-local deploy; flag in deploy README
- [section2/src/harness_github/config.py:14] [hardcoded] frontend_base localhost:3000 → env-overridable; OK
- [section7/src/lib/api.ts:7] [hardcoded] NEXT_PUBLIC_IDENTITY_BASE_URL fallback :8001 → standard Next.js pattern; OK
- [section7/src/lib/api.ts:9] [hardcoded] orchestrator fallback :8004 → OK
- [section7/src/lib/api.ts:11] [hardcoded] meeting fallback :8006 → OK
- [section7/src/lib/api.ts:13] [hardcoded] review fallback :8008 → OK
- [section7/src/lib/api.ts:15] [hardcoded] identity WS ws://localhost:8001 → OK; ensure wss in prod docs
- [section7/src/lib/api.ts:396] [hardcoded] github fallback :8002 → OK
- [section7/src/lib/onboarding.ts:4] [hardcoded] onboarding fallback :8010 → OK
- deploy/ spot-check (no action, defaults are env-parameterized): docker-compose.yml:12,24,25,54,83–209 healthchecks hardcode 127.0.0.1:800x by design; runners/services.py:29,74 mirror CORS/redirect defaults; walg/restore-walg.sh:207 doc text.

### L6 TS suppressions (2)
- [section7/src/components/WorkingArea.tsx:52] [eslint-disable] exhaustive-deps suppressed → verify effect deps or restructure; add tracking comment
- [section7/src/lib/codemirror.ts:95] [eslint-disable] exhaustive-deps suppressed → same; second suppression in file set

### L8b naming drift (section6)
- [section6/src/harness_meeting/prompt_store.py:13] [naming] dataclass field `meetingId: str` camelCase amid 65 snake_case uses → rename to meeting_id
- [section6/src/harness_meeting/prompt_store.py:39] [naming] constructor kwarg meetingId= → rename
- [section6/src/harness_meeting/brief_generator.py:35] [naming] kwarg meetingId=meeting_id → rename
- Not an alias-config case: no populate_by_name/alias_generator anywhere in section6 — genuine drift.

### Magic numbers (7)
- [section4/src/harness_orchestrator/engine.py:123] [magic] max_tokens=4096 inline → named constant MAX_TOKENS
- [section2/src/harness_github/client.py:49] [magic] timeout=30 inline httpx client → module constant GITHUB_TIMEOUT_S
- [section2/src/harness_github/client.py:136] [magic] timeout=30 repeated → same constant (dup within file)
- [section2/src/harness_github/app.py:149] [magic] timeout=30 third occurrence → hoist constant
- [section1/src/harness_identity/notifier.py:80] [magic] SMTP timeout=10 → constant SMTP_TIMEOUT_S
- [section1/src/harness_identity/notifier.py:301] [magic] SMTP timeout=10 repeat → same constant
- [section1/src/harness_identity/invites.py:74] [magic] token length bounds 16/256 → named constants MIN/MAX_TOKEN_LEN (borderline; pydantic Field)

### Duplicate blocks (cross-file, 20 clusters from 38 dup lines)
- [section1/auth.py:83 · s4/api.py:237 · s5/api.py:26 · s6/api.py:15 · s8/api.py:32] [dup x5] identical get_principal auth shim (≈15 lines incl. getattr/AUTH_NOT_CONFIGURED/RequirePrincipal) → move to harness_contracts or shared auth util package
- [s4/api.py:244 · s5/api.py:28 · s6/api.py:17 · s8/api.py:34] [dup x4] `dep = getattr(request.app.state, "get_principal", None)` → part of shim extraction
- [s4/api.py:248 · s5/api.py:32 · s6/api.py:21 · s8/api.py:38] [dup x4] AUTH_NOT_CONFIGURED error body → part of shim extraction
- [s4/api.py:253 · s6/api.py:26 · s8/api.py:43] [dup x3] RequirePrincipal Annotated alias → part of shim extraction
- [section2/app.py:254 · section8/api.py:128,145,161,183] [dup x6] `raise HTTPException(404, detail=str(exc)) from exc` → local helper not_found(exc) per section or shared
- [s1/config.py:8 · s10/api.py:33 · s2/config.py:9 · s8/config.py:9] [dup x4] cors_origins=["http://localhost:3000"] → shared default in contracts
- [s1/config.py:5 · s2/config.py:5 · s8/config.py:5] [dup x3] SettingsConfigDict(env_prefix="HARNESS_", env_file=".env", extra="ignore") → shared Settings base class
- [s1/config.py:7 · s2/config.py:7 · s8/config.py:7] [dup x3] database_url postgresql+asyncpg://localhost/harness_dev → shared default
- [s1/config.py:9 · s2/config.py:8 · s8/config.py:8] [dup x3] master_key=b"dev-master-key-change-me-in-prod!" → shared default + startup warn if unset in prod
- [s1/app.py:61 · s2/app.py:26 · s8/api.py:81] [dup x3] def make_app(session_factory, settings) signature → convention already; consider contract-typed factory
- [s1/ephemeral.py:27 · s2/state_store.py:22] [dup x2] _iso tz-normalize helper → promote to harness_contracts.util
- [s1/ephemeral.py:58 · s2/state_store.py:42] [dup x2] envelope {"e": _iso(now+ttl)...} → same promotion
- [s1/models.py:102,111,121,136… · billing.py:71] [dup x18] id=Mapped[str]=mapped_column(String(36), primary_key=True, default=uid) → declarative mixin BaseIdMixin
- [s1/api_keys.py:87,104,117,144 · app.py:179,192 …] [dup x18] async with request.app.state.session_factory() as db → FastAPI dependency get_db()
- [s1/api_keys.py:72,104,136 · billing.py:213,229 …] [dup x10] principal: Annotated[Principal, Depends(get_principal)] param → RequirePrincipal alias already exists in other sections; adopt in s1
- [s1/models.py:169,171… · notifier.py:243,245…] [dup x7] DateTime(timezone=True), server_default=func.now(), nullable=False → TimestampMixin
- [s1/eventbus.py:36 · notifier.py:254,352,415] [dup x4] session_factory: async_sessionmaker[AsyncSession] ctor param → TypeAlias SessionFactory
- [s6/prompt_store.py:17 · service.py:22 · summary_service.py:15] [dup x3] createdAt=default_factory=lambda: datetime.now(UTC).isoformat() → shared timestamp field helper
- [s4/api.py:353,390 · s6/api.py:162] [dup x3] INVALID_STATE error body → error_body-style helper
- [s1/app.py:169 · s1/notifier.py:51] [dup x2] HARNESS-OTP stderr print line → single source of truth
- Also noted: s1/ratelimit.py:120 · security_headers.py:58 identical ASGI __call__ signature comment pattern (docs duplication, low priority).

## Clean lenses (verified zero)
TODO/FIXME/HACK/XXX: 0 · mutable defaults: 0 · sync-in-async: 0 · `import *`: 0 · bare `except:`: 0

## Top actions if triaged
1. Extract the 5-way duplicated get_principal shim into a shared package (~60 lines removed).
2. Fix the 6 unannotated broad excepts (eventbus.py:140, router ledger/api x2, sandbox api, review api x2).
3. Rename section6 camelCase `meetingId` fields/kwargs (5 sites).
4. Hoist timeout/max_tokens magic numbers into constants (7 sites).
