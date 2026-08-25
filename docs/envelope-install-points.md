# M17-B3 / C-2 — error-envelope install points (one canonical middleware)

The frozen wire contract (api-conventions.md:14) is: every error response
answers `{"error": {code, message}}` (+ legacy `detail` mirror). The
middleware that enforces it — `install_error_envelope` /
`IdempotencyMiddleware` — exists EXACTLY ONCE:

    section1/src/harness_identity/envelope_middleware.py

The three byte-identical forks (`harness_router`, `harness_orchestrator`,
`harness_meeting`) were deleted in M17-B3; their only production consumers
were their own tests.

## Install matrix

| Service | Installed at | Since |
|---|---|---|
| S1 identity | `section1/src/harness_identity/app.py` `make_app()` | M12-W2.1 |
| S2 github   | `simple_app()` via deploy assembly (below) + `make_app` local try/except bus | M17-B3 |
| S3 models   | `deploy/runners/services.py::simple_app()` | M17-B3 (was NOT installed) |
| S4 orchestrator | `deploy/runners/services.py::simple_app()` | M17-B3 (was NOT installed) |
| S5 sandbox  | `deploy/runners/services.py::simple_app()` | M17-B3 (was NOT installed) |
| S6 meeting  | `deploy/runners/services.py::app_meeting()` | M17-B3 (was NOT installed) |
| S8 review   | `deploy/runners/services.py::app_review()` | M17-B3 (was NOT installed) |

## Why deploy-level wiring for S2/S3/S4/S5/S6/S8

Section venvs do not carry each other's packages (isolation law, now also
guard-enforced). The section app factories stay middleware-free where the
middleware is an operational/deployment concern; the deploy assembler
(`deploy/runners/services.py`) imports every section's src tree by design
and installs the envelope next to CORS + security headers.

Consequences pinned by tests:

* S7's `src/lib/api.ts` three-shape parser keeps working unchanged — the
  contract shape was already first in its list.
* S8's blanket-except routes raising `HTTPException(500, str(exc))`
  (findings M-3) are normalized to coded envelopes by the same install.
