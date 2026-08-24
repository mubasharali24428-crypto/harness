# M12-W2.1 lane E1 — ATLAS merge wiring for H-002 (S6 → S3 with team BYO key)

`deploy/runners/services.py::app_meeting` — replace the body between
`app.state.get_principal = ...` and `install_security_headers(app)` additions
with the block below. Everything else in the factory stays as-is.

```python
def app_meeting():
    from fastapi import FastAPI
    from fastapi.middleware.cors import CORSMiddleware
    from harness_contracts import KeyVault, make_bearer_dep   # vault import added
    from harness_contracts.fastapi_auth import make_bearer_dep as _bearer  # (existing import line stays)
    from harness_meeting.api import router

    factory = get_factory()
    app = FastAPI(title="harness-meeting")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health")
    async def health():
        return {"ok": True}

    # --- M12-E1 (H-002): @ai real-LLM wiring via team BYO keys -------------
    # S6 resolves the caller's team key from S1's frozen api_keys store,
    # decrypts IN-PROCESS, and POSTs it to S3 /api/v1/models/call server-side.
    # The raw key never leaves this process except inside that request body.
    app.state.session_factory = factory            # key lookups (frozen table name)
    app.state.vault = KeyVault(MASTER_KEY)         # decrypt in-process ONLY
    # -----------------------------------------------------------------------

    app.state.get_principal = make_bearer_dep(factory)
    # Wave9-E (append): security headers on the meeting app (S6).
    from harness_identity.security_headers import install_security_headers

    install_security_headers(app)
    app.include_router(router)
    return app
```

Notes:
- No compose/env change required: `models_base_url` defaults to
  `http://models:8003` (compose service DNS) and can be overridden with
  `HARNESS_MODELS_URL`.
- `MASTER_KEY` is already module-level in services.py (same value S8 uses for
  its vault), so keys minted via `POST /api/v1/keys` decrypt correctly.
- api.py route signatures are unchanged — no contract surface moves.
