// R4 pure rules for reviewing in the pane: which paths are risky, when Approve
// is allowed, and how a diff comment reads as direction. No fetches, no DOM.

import type { ReviewDto } from "@/lib/api";
import { roleCanAct } from "../../../app/dashboard/planGateApi";

// Same list as the standalone review page (app/review/page.tsx).
const RISKY_FILES = ["auth/", "payment/", "migrations", ".env", "secrets"];

/** One line per risky thing in the pull request: recorded flags first, then sensitive paths. */
export function reviewRisks(review: ReviewDto | null): string[] {
  if (!review) return [];
  const flags = review.risk_flags ?? review.riskFlags ?? [];
  if (flags.length > 0) return flags.map((f) => `${f.path}: ${f.severity}, ${f.reason}`);
  const files = review.changed_files ?? review.changedFiles ?? [];
  return files
    .filter((f) => RISKY_FILES.some((r) => f.path.toLowerCase().includes(r)))
    .map((f) => `${f.path}: sensitive area`);
}

/** Why Approve is not available, or null when it is. Risky changes need an explicit tick first. */
export function approveBlock(o: { role?: string | null; risks: string[]; acknowledged: boolean }): string | null {
  if (!roleCanAct(o.role)) return "Your role can read this review but not decide it.";
  if (o.risks.length > 0 && !o.acknowledged) return "Tick the box to confirm you read the risk flags, then approve.";
  return null;
}

export interface DiffComment {
  path: string;
  line: number | null;
  body: string;
}

/** A diff comment as the agent reads it. */
export function commentAsDirection(c: DiffComment): string {
  return `${c.path}${c.line ? `:${c.line}` : ""}: ${c.body}`;
}
