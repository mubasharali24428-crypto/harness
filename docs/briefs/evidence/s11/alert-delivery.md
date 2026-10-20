# Parts 28/55: alerts are delivered by email (2026-10-20)

Before: `alerts.yml` loaded into Prometheus and fired only on its `/alerts` page; nothing routed to a person.

## What changed

- `deploy/observability/alertmanager.yml`: one `email` receiver; every severity routes to it (`page`: first mail after 10 s,
  repeated hourly; `warning`: repeated every 12 h); resolved mail on. A template: the compose `alertmanager` entrypoint fills it
  from the stack's existing `HARNESS_SMTP_*` settings plus `HARNESS_ALERT_EMAIL_TO`, and writes the SMTP password to a 0600 file
  (`smtp_auth_password_file`) so it never appears in the rendered config. It refuses to start without `HARNESS_ALERT_EMAIL_TO`.
- `deploy/docker-compose.yml`: `alertmanager` service (`prom/alertmanager:v0.28.1`, profile `observability`, job-class limits).
- `deploy/observability/prometheus.yml`: `alerting.alertmanagers` → `alertmanager:9093`.
- Test: `section1/tests/test_deploy_observability.py::test_alerts_are_routed_to_alertmanager_and_delivered_by_email`
  (red before the change: `KeyError: 'alerting'`; fails if any severity used in `alerts.yml` is not routed to the email receiver).

## Live run (Docker Desktop, compose stack on this commit)

SMTP pointed at a throwaway Mailpit catcher on the compose network (`HARNESS_SMTP_HOST=mailpit`, port 1025, `STARTTLS=false`).

| Check | Result |
|---|---|
| `amtool check-config /tmp/alertmanager.yml` (rendered) | SUCCESS; the password is not in the file |
| Prometheus `/api/v1/alertmanagers` | active: `http://alertmanager:9093/api/v2/alerts` |
| Alerts posted to Alertmanager, one `page`, one `warning` | 2 mails, `alerts@harness.test` → `oncall@harness.test` |
| Real rule: `meeting` stopped 14:01:29Z | `[FIRING:1] HarnessServiceDown meeting:8006 (... page)` mailed 14:03:55Z (2 m `for:` + scrape + 10 s group wait) |

## Not covered

- A real SMTP provider (only the local catcher was used); deliverability, SPF/DKIM.
- No second channel (pager/chat): one email address is the whole on-call path.
- Backup/WAL rules stay inert (no emitter), so they can never mail.
- No Alertmanager high availability (one instance; if it is down, alerts are lost until it returns).
