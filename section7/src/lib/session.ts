// M12-E6 — meeting persistence across refresh (raw-03 #15).
//
// The Meeting Area used to hold `meetingId` in React state only: one refresh
// and the pointer was gone while the transcript stayed alive in S6 — the user
// saw an idle room with no way back. These helpers persist the latest meeting
// per team under `harness.meeting.<teamId>` so the page can restore it on
// mount and rehydrate the chat through the existing GET endpoints.
//
// Deliberately NOT wired into auth.ts's KEYS map: those entries are flat string
// values while this record is JSON. Sign-out hygiene still covers it —
// clearSession() sweeps every `harness.*` key by prefix, and these keys start
// with `harness.`.

export interface PersistedMeeting {
  meetingId: string;
  /** Project the meeting belongs to; used to refuse cross-workspace resurrection. */
  projectId: string | null;
  /** ISO timestamp, diagnostics only — never parsed back into behavior. */
  savedAt: string;
}

function meetingKey(teamId: string): string {
  return `harness.meeting.${teamId}`;
}

/** Persist the team's latest meeting. Overwrites any previous record — the
 *  storage holds exactly one meeting per team (the latest wins). */
export function saveMeeting(
  teamId: string,
  meetingId: string,
  projectId: string | null,
): void {
  if (typeof window === "undefined") return;
  const record: PersistedMeeting = {
    meetingId,
    projectId,
    savedAt: new Date().toISOString(),
  };
  try {
    window.localStorage.setItem(meetingKey(teamId), JSON.stringify(record));
  } catch {
    /* private-mode/quota failures must never break the page */
  }
}

/** Load the persisted meeting for a team, or null. Malformed/corrupt payloads
 *  read as null rather than throwing — a broken record must never brick the page. */
export function loadMeeting(teamId: string): PersistedMeeting | null {
  if (typeof window === "undefined") return null;
  let raw: string | null = null;
  try {
    raw = window.localStorage.getItem(meetingKey(teamId));
  } catch {
    return null;
  }
  if (!raw) return null;
  try {
    const parsed: unknown = JSON.parse(raw);
    const candidate = parsed as Partial<PersistedMeeting> | null;
    if (
      candidate &&
      typeof candidate === "object" &&
      typeof candidate.meetingId === "string" &&
      candidate.meetingId.trim() !== ""
    ) {
      return parsed as PersistedMeeting;
    }
    return null;
  } catch {
    return null;
  }
}

/** Decide what (if anything) the Meeting Area should restore on mount.
 *  Refuses to resurrect a meeting recorded against a different project than
 *  the active session's (belt-and-braces — sign-out already sweeps all keys). */
export function resolveRestorableMeeting(
  teamId: string | null,
  activeProjectId: string | null,
): PersistedMeeting | null {
  if (!teamId) return null;
  const saved = loadMeeting(teamId);
  if (!saved) return null;
  if (
    activeProjectId &&
    saved.projectId !== null &&
    saved.projectId !== activeProjectId
  ) {
    return null; // stale pointer from another workspace — ignore it
  }
  return saved;
}

/** Drop the persisted pointer (server confirmed the meeting no longer exists). */
export function clearMeeting(teamId: string): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.removeItem(meetingKey(teamId));
  } catch {
    /* ignore — nothing worse than removal failing */
  }
}

/** Exact lead copy of the visible "meeting restored" banner, exported so the
 *  test suite pins what users actually see on rehydration. */
export const RESTORED_NOTE_LEAD = "Meeting restored";

// ---------------------------------------------------------------------------
// M12-E6 restore orchestration — kept as a pure async function (not buried in
// the page's effect) so the restore path is unit-testable without a DOM,
// exactly like the planGate pattern: interaction logic lives in pure
// functions, the component effect is a thin adapter over this.
// ---------------------------------------------------------------------------

import type { BriefDto, MeetingMsg } from "./api";

/** What the restore-time GET discovered about the saved meeting. */
export type RestoreOutcome =
  | "ok" // transcript retrieved — safe to claim "restored"
  | "missing" // server answered 404 — pointer is stale, sweep it
  | "unreachable"; // S6 down/network blip — keep pointer, claim nothing

export interface RehydrateArgs {
  saved: PersistedMeeting;
  token: string;
  /** Existing S6 GET endpoint — injected so tests need no HTTP layer. */
  getMessages: (meetingId: string, token: string) => Promise<MeetingMsg[]>;
  /** Optional: briefs legitimately 404 before generation; absence is fine. */
  getBrief?: (meetingId: string, token: string) => Promise<BriefDto>;
}

export interface RestoredPayload {
  outcome: RestoreOutcome;
  meetingId: string;
  messages: MeetingMsg[];
  /** Present only when a brief already existed for the restored meeting. */
  brief: BriefDto | null;
}

function isNotFound(err: unknown): boolean {
  return (
    err instanceof Error &&
    "status" in err &&
    typeof (err as { status?: unknown }).status === "number" &&
    (err as { status: number }).status === 404
  );
}

export async function rehydrateMeeting(args: RehydrateArgs): Promise<RestoredPayload> {
  const { saved, token, getMessages, getBrief } = args;
  const empty: RestoredPayload = {
    outcome: "unreachable",
    meetingId: saved.meetingId,
    messages: [],
    brief: null,
  };
  let messages;
  try {
    messages = await getMessages(saved.meetingId, token);
  } catch (err) {
    return { ...empty, outcome: isNotFound(err) ? "missing" : "unreachable" };
  }
  let brief: BriefDto | null = null;
  if (getBrief) {
    try {
      brief = await getBrief(saved.meetingId, token);
    } catch {
      /* no brief yet — 404 expected pre-generation */
    }
  }
  return { outcome: "ok", meetingId: saved.meetingId, messages, brief };
}

/** Full mount-time restore for the Meeting Area: look up the team's persisted
 *  meeting and rehydrate it through the injected S6 GET endpoints. Null means
 *  "nothing to restore" (no team, no record, or a cross-workspace pointer). */
export async function restoreLatestMeeting(
  teamId: string | null,
  activeProjectId: string | null,
  token: string,
  getMessages: RehydrateArgs["getMessages"],
  getBrief?: RehydrateArgs["getBrief"],
): Promise<RestoredPayload | null> {
  const saved = resolveRestorableMeeting(teamId, activeProjectId);
  if (!saved) return null;
  return rehydrateMeeting({ saved, token, getMessages, getBrief });
}

// ---------------------------------------------------------------------------
// M12-E6: mount-effect orchestration for the Meeting Area page. Kept here
// (not inline in the page component) so the disposition logic is unit-testable
// without a DOM — the planGate pattern: logic in plain functions, the effect
// is just its caller. Next pages may only default-export the component, which
// is why this lives in lib.
// ---------------------------------------------------------------------------

export interface MeetingStateDeps {
  teamId: string | null;
  token: string | null;
  getProjectId: () => string | null;
  getMessages: RehydrateArgs["getMessages"];
  getBrief: RehydrateArgs["getBrief"];
  setMeetingId: (id: string | null) => void;
  setMessages: (msgs: MeetingMsg[]) => void;
  setLive: (v: boolean) => void;
  setBrief: (b: BriefDto | null) => void;
  setRestoredFromStorage: (v: boolean) => void;
  setError: (msg: string | null) => void;
}

/** Runs once per page mount: restores the team's persisted meeting (if any)
 *  through the existing S6 GET endpoints and lands the result in page state.
 *  Dispositions: "ok" → transcript + restored banner; "missing" → sweep stale
 *  pointer, clean idle UI; "unreachable" → keep pointer, honest error, no
 *  false "restored" claim. Never creates a meeting. */
export function runMeetingRestore(deps: MeetingStateDeps): void {
  const { teamId, token } = deps;
  if (!teamId || !token) return;
  void restoreLatestMeeting(
    teamId,
    deps.getProjectId(),
    token,
    deps.getMessages,
    deps.getBrief,
  ).then(
    (result) => {
      if (!result) return; // nothing persisted — stay idle, create nothing
      deps.setMeetingId(result.meetingId);
      if (result.outcome === "ok") {
        deps.setMessages(result.messages);
        if (result.messages.length) deps.setLive(true);
        if (result.brief) deps.setBrief(result.brief);
        deps.setRestoredFromStorage(true);
      } else if (result.outcome === "missing") {
        // Server confirmed the meeting no longer exists (e.g. S6 was
        // reset): sweep the stale pointer, fall back to clean idle UI.
        clearMeeting(teamId);
        deps.setMeetingId(null);
        deps.setError("Your previous meeting no longer exists — start a new one.");
      } else {
        // Pointer stays (the meeting likely still lives server-side), but we
        // won't claim "restored" until the transcript actually loads.
        deps.setError("Saved meeting found, but its transcript couldn't be loaded (is S6 up?).");
      }
    },
    () => {}, /* orchestration never rejects — belt and braces */
  );
}
