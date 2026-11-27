// Task 13: client for GET/PUT /api/v1/me/rendering (X-D1 self-service, X-07).
// Presentation only — the server validates the value and never changes policy or permissions.
import { IDENTITY_BASE, apiFetch } from "@/lib/api";

export const RENDERINGS = ["technical", "plain", "decision"] as const;
export type Rendering = (typeof RENDERINGS)[number];
export interface RenderingResponse {
  userId: string;
  rendering: Rendering;
}

const url = `${IDENTITY_BASE}/api/v1/me/rendering`;

export const getRendering = (token: string) => apiFetch<RenderingResponse>(url, token);
export const putRendering = (token: string, rendering: Rendering) =>
  apiFetch<RenderingResponse>(url, token, { method: "PUT", body: JSON.stringify({ rendering }) });
