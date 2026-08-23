# Comparative Analysis — Meeting Side (Attempt 3)

Scope: 8 capabilities vs **Zoom, Google Meet, Microsoft Teams, Slack huddles, Granola, Fireflies**.
Harness status drawn from the verified reality map: text-only chat with regex echo-bot AI; no voice, video, screen share, recording, transcripts, calendar, presence, or threads; plan-gate approval strong + event-sourced.

Format: **capability** — best-in-class one-liner | harness status | severity | build size.

---

**Video/audio presence** — Zoom/Meet deliver sub-second multi-party video+audio as the core product | Harness: none — text-only chat; no media stack of any kind | **BLOCKER** | **L**

**Screen share** — Teams/Meet share a window or full desktop with annotation and remote control | Harness: none — no capture or viewer surface (closest analog is the dsh terminal stream, which shows agent output only) | **HIGH** | **L**

**Recording + transcript** — Zoom/Meet record the call and produce speaker-attributed transcripts automatically | Harness: none — no recording pipeline, storage path, or ASR integration | **HIGH** | **L**

**AI notetaking / action items** — Granola and Fireflies generate meeting summaries and extract action items with owners without a bot joining | Harness: none for meetings; the echo-bot's "brief" is template extraction from chat text, not meeting-derived content | **HIGH** | **M** (given any transcript source)

**Calendar integration** — Teams/Granola join scheduled events from Google/Outlook calendars and pre-load context | Harness: none — no calendar connection or scheduling surface | **MEDIUM** | **M**

**Threads / replies** — Slack huddles tie conversation into threaded channels so decisions stay attached to context | Harness: none — flat chat stream only; no reply structure or threading model | **MEDIUM** | **M**

**Presence indicators** — Meet/Teams show live who's in-call, available, away, presenting | Harness: none — no presence state at all | **LOW** (for async-first harness) | **S**

**Persistent rooms** — Slack huddles/Zoom persistent links keep a durable room per team/project with history | Harness: none — sessions are ephemeral chat runs; no durable per-room memory surfaced | **MEDIUM** | **M**

---

### Footnote
The harness's genuine differentiator on this side is governance, not media: strong plan-gate approval with an event-sourced trail has no equivalent among the six incumbents above. That contrast is developed in comp-verdict.md.
