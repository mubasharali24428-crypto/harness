import { ApiError } from "./api";

const ONBOARDING_BASE =
  process.env.NEXT_PUBLIC_ONBOARDING_BASE_URL ?? "http://localhost:8010";

/* ---- types (mirror section10 openapi.json) ---- */

export interface OnboardingService {
  key: string;
  name: string;
  url: string;
  available: boolean;
}

export interface OnboardingStep {
  id: string;
  order: number;
  title: string;
  description: string;
  section: number;
  serviceKey: string | null;
  required: boolean;
  availableInDeployment: boolean;
  available: boolean;
}

export interface OnboardingState {
  services: OnboardingService[];
  steps: OnboardingStep[];
  securityChecklistAcknowledged: boolean;
  telemetry: TelemetrySettings;
}

export interface TelemetrySettings {
  baselineEnabled: boolean;
  errorLogs: boolean;
  usageAnalytics: boolean;
  promptMetadata: boolean;
  codeMetadata: boolean;
  fullPromptText: boolean;
  fullCodeContent: boolean;
}

export interface ChecklistItem {
  id: string;
  category: string;
  statement: string;
  enforcedBy: string;
}

export interface SecurityChecklist {
  title: string;
  intro: string;
  items: ChecklistItem[];
}

export interface DemoStep {
  id: string;
  order: number;
  title: string;
  area: string;
  description: string;
  expectedEvent: string | null;
  section: number;
}

export interface GuidedDemo {
  title: string;
  intro: string;
  repoHint: string;
  steps: DemoStep[];
}

export interface DemoRepoManifest {
  name: string;
  description: string;
  language: string;
  testCommand: string;
  lintCommand: string;
  task: {
    title: string;
    goal: string;
    acceptanceCriteria: string[];
    suggestedForbiddenPaths: string[];
  };
  pushScript: string;
  files: string[];
}

/* ---- fetchers ---- */

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${ONBOARDING_BASE}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    const code = body?.detail?.error?.code ?? "UNKNOWN";
    const msg = body?.detail?.error?.message ?? res.statusText;
    throw new ApiError(msg, code, res.status);
  }
  return res.json() as Promise<T>;
}

export function fetchState(): Promise<OnboardingState> {
  return api("/api/v1/onboarding/state");
}

export function fetchChecklist(): Promise<SecurityChecklist> {
  return api("/api/v1/onboarding/security-checklist");
}

export function acknowledgeChecklist(): Promise<{ securityChecklistAcknowledged: boolean }> {
  return api("/api/v1/onboarding/security-checklist/acknowledge", { method: "POST" });
}

export function fetchTelemetrySettings(): Promise<TelemetrySettings> {
  return api("/api/v1/onboarding/telemetry-settings");
}

export function updateTelemetrySettings(
  settings: Partial<TelemetrySettings> & { optInAcknowledged?: boolean },
): Promise<TelemetrySettings> {
  return api("/api/v1/onboarding/telemetry-settings", {
    method: "PUT",
    body: JSON.stringify(settings),
  });
}

export function fetchGuidedDemo(): Promise<GuidedDemo> {
  return api("/api/v1/onboarding/guided-demo");
}

export function fetchDemoRepoManifest(): Promise<DemoRepoManifest> {
  return api("/api/v1/onboarding/demo-repo/manifest");
}
