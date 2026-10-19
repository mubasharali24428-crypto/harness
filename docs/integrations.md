# Integrations (part 49)

The integration surface is versioned under `/api/v1/teams/{team_id}/settings/integrations/v1/webhooks`.
It is team scoped and restricted to team administrators. A team must configure each destination
explicitly; creating a webhook does not enable an integration for other teams.

## Outbound webhooks

An administrator creates a destination with one or more event types from the server allowlist.
Destinations require HTTPS on port 443 and a hostname in the operator configured
`HARNESS_WEBHOOK_ALLOWED_HOSTS` allowlist. The worker checks DNS results before delivery and
rejects non-public addresses. Requests do not follow redirects and do not use proxy environment
settings. The generated signing secret is encrypted at rest and displayed only once; rotation
invalidates the prior secret. Pause, resume, rotate, delete, delivery history and health counts are
available in the Section 7 Integrations settings page.

Each delivery carries a stable `Idempotency-Key` and `X-Harness-Delivery-Id`. Its
`X-Harness-Signature` is `t=<unix timestamp>,v1=<hex HMAC-SHA256(secret, timestamp + "." + raw_body)>`.
Consumers must verify the HMAC over the raw request body, reject timestamps outside their chosen
short tolerance, and deduplicate by delivery id. Delivery is at least once: timeouts and retryable
responses use bounded exponential retries (six attempts, maximum one-hour delay), so consumers
must be idempotent. Response bodies and destination secrets are not stored in delivery history.
The payload is a deliberately small identity-only projection of allowlisted audit events; it does
not forward arbitrary audit detail.

The `webhook-delivery` compose service runs the bounded worker. Operators must configure the host
allowlist and outbound network policy. The DNS check reduces SSRF risk, but DNS can change between
validation and connection; deployments should also enforce network egress restrictions.

## Other providers and extension boundary

GitHub OAuth currently requests `repo read:user`; the repository credential is encrypted and
team scoped, with connection status and revoke support. This is broader than read-only repository
access and does not meet a least-privilege read-only integration target. OIDC requests only
`openid profile email`. Stripe inbound events use raw-body signature verification, timestamp
tolerance and an event-id ledger in one transaction. Those inbound protections do not imply
outbound webhook support.

There is no issue-tracker adapter, chat/notification provider setup, third-party extension runtime,
or sandboxed extension API. No arbitrary plugin code executes. Internal tool and repository
operations continue through their existing permission checks and approval flows; external side
effects need their own explicit team setup and idempotency handling. Compatibility policy: routes
are versioned; additive fields and event types may be added within a version, while incompatible
changes require a new version and a documented deprecation window. This policy does not yet have
an automated compatibility checker.

Live GitHub, Stripe and OIDC provider flows were not exercised for this session. See
`docs/briefs/evidence/s7/part49.md` for the evidence boundary.
