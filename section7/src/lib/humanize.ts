// Browser fetch failures surface as "Failed to fetch" / "Load failed" — raw
// plumbing text that reads like a crash. This maps the transport-level
// failures to a sentence a first-time reviewer understands; everything else
// passes through unchanged, because server error envelopes are already
// written for humans.

const TRANSPORT = /failed to fetch|networkerror|load failed|network request failed|fetch failed/i;

export function loadErrorMessage(error: unknown): string {
  const raw = error instanceof Error ? error.message : String(error ?? "");
  if (TRANSPORT.test(raw)) {
    return "We couldn’t reach the server. Check your connection, then try again.";
  }
  return raw.trim() || "Something went wrong while loading this page.";
}
