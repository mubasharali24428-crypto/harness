# Client and locale support policy

Status: release policy, not a compatibility certification. The application has no automated
cross-browser matrix or real-device evidence at this time. A release may advertise only the
client/version combinations exercised by that release's browser checks.

## Language and locale

- The supported interface language is English (`<html lang="en">`). No translated interface or
  RTL layout is currently shipped.
- Collaboration dates, times, relative times and numbers use browser `Intl` settings where wired;
  notification quiet hours store an IANA time zone. Other application copy remains English.
- Date/number formatting has helper-level tests; this does not prove every screen or every browser
  locale. Approval and permission wording is English only.
- If a locale is unsupported or malformed, formatting helpers fall back to a readable ISO value
  or em dash. Do not infer translated labels from locale-aware numeric output.

## Browser policy

- Intended QA matrix for a release: current and previous stable desktop Chrome, Edge, Firefox and
  Safari, plus the narrowest supported viewport recorded by the release check.
- This is a test target, not a statement that all four browsers have passed. Record browser name,
  exact version, OS, viewport, build identifier, and any excluded checks in release evidence.
- Internet Explorer and embedded/in-app webviews are unsupported. Mobile browsers are not an
  advertised target until the release has a real-device responsive and keyboard check.
- WebSocket failure should use the existing polling fallback. If the browser lacks WebSocket,
  run collaboration through polling only; if that behavior fails, stop the release and document
  the exact client instead of asking users to weaken security settings.
- The product requires JavaScript. An unsupported client must not bypass authentication, approval,
  or isolation controls. Show the ordinary service error/connection state and direct users to a
  supported browser; do not claim a no-JavaScript workflow.

## Release checks

Before adding a browser or locale to the support claim, run the release's supported-client smoke
journey, keyboard-only approval/redirect/handoff journey, 200% zoom/reflow check, reduced-motion
check, and screen-reader review. Record failures and versions in
`docs/briefs/evidence/s10/readiness-report.md`. The current automated accessibility checks are
listed in [`accessibility.md`](../accessibility.md); they do not replace these manual checks.
