import { createHash, randomUUID } from "node:crypto";
import { mkdtempSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import {
  DesktopError, type DesktopAdapter, type DesktopGrant, type DesktopOp, type Display,
  type Region, type ScreenObservation,
} from "./types.js";

const MAX_AGE_MS = 5_000;
const MAX_TYPE = 2_000;
const KEY_RE = /^(?:(?:cmd|ctrl|alt|shift)\+)*[A-Za-z0-9]+$|^(?:enter|tab|escape|space|backspace|up|down|left|right)$/i;

const inside = (r: Region, x: number, y: number) =>
  x >= r.x && y >= r.y && x < r.x + r.width && y < r.y + r.height;

/**
 * Enforces grant scope, region bounds, observation freshness, the image->point
 * transform and human takeover. All input flows through here; the adapter is
 * never reachable directly by the agent.
 */
export class DesktopController {
  private display: Display | null = null;
  private last: ScreenObservation | null = null;
  private humanHasControl = false;
  private readonly dir = mkdtempSync(join(tmpdir(), "a3-desktop-"));

  constructor(
    private readonly adapter: DesktopAdapter,
    private readonly grant: DesktopGrant | null,
    private readonly now: () => number = () => Date.now(),
  ) {}

  private requireGrant(cap: "capture" | "input", team: string, run: string): DesktopGrant {
    const g = this.grant;
    if (!g) throw new DesktopError("NO_GRANT", "no desktop grant: full-host control is never implicit");
    if (g.teamId !== team || g.runId !== run) throw new DesktopError("GRANT_SCOPE", "grant belongs to another run");
    if (this.now() >= g.expiresAtMs) throw new DesktopError("GRANT_EXPIRED", "desktop grant expired");
    if (!g.capabilities.includes(cap)) throw new DesktopError("GRANT_SCOPE", `grant lacks '${cap}'`);
    return g;
  }

  private async ensureDisplay(): Promise<Display> {
    if (this.display) return this.display;
    const probe = await this.adapter.probe();
    if (!probe.ok || !probe.display) {
      throw new DesktopError("UNSUPPORTED", `desktop adapter '${this.adapter.name}' unavailable: ${probe.reasons.join("; ")}`);
    }
    return (this.display = probe.display);
  }

  async observe(team: string, run: string): Promise<ScreenObservation> {
    const g = this.requireGrant("capture", team, run);
    const display = await this.ensureDisplay();
    const out = join(this.dir, `${randomUUID()}.png`);
    await this.adapter.capture(g.region, out);
    const bytes = readFileSync(out);
    const obs: ScreenObservation = {
      observationId: `dobs_${randomUUID()}`,
      display, region: g.region,
      imageSize: { width: Math.round(g.region.width * display.scale), height: Math.round(g.region.height * display.scale) },
      sha256: createHash("sha256").update(bytes).digest("hex"),
      capturedAtMs: this.now(), imagePath: out,
    };
    return (this.last = obs);
  }

  /** Image pixel (relative to the captured region) -> absolute logical point. */
  toPoint(obs: ScreenObservation, px: number, py: number): { x: number; y: number } {
    return { x: obs.region.x + px / obs.display.scale, y: obs.region.y + py / obs.display.scale };
  }

  takeOver(): void { this.humanHasControl = true; }
  /** Resuming invalidates the last observation: the human changed the screen. */
  handBack(): void { this.humanHasControl = false; this.last = null; }

  async act(team: string, run: string, observationId: string, op: DesktopOp): Promise<void> {
    const g = this.requireGrant("input", team, run);
    if (this.humanHasControl) throw new DesktopError("HUMAN_CONTROL", "human has taken over; agent input is paused");
    const obs = this.last;
    if (!obs) throw new DesktopError("NEEDS_OBSERVATION", "observe the screen before acting");
    if (obs.observationId !== observationId) throw new DesktopError("STALE_OBSERVATION", "observation superseded");
    if (this.now() - obs.capturedAtMs > MAX_AGE_MS) throw new DesktopError("STALE_OBSERVATION", "observation too old");

    if (op.kind === "type") {
      if (op.text.length === 0 || op.text.length > MAX_TYPE) throw new DesktopError("BAD_REQUEST", "text length out of bounds");
      return this.adapter.perform({ kind: "type", text: op.text });
    }
    if (op.kind === "key") {
      if (!KEY_RE.test(op.combo)) throw new DesktopError("BAD_REQUEST", `unsupported key combo '${op.combo}'`);
      return this.adapter.perform({ kind: "key", combo: op.combo });
    }
    const point = op.space === "image" ? this.toPoint(obs, op.x, op.y) : { x: op.x, y: op.y };
    if (!Number.isFinite(point.x) || !Number.isFinite(point.y) || !inside(g.region, point.x, point.y)) {
      throw new DesktopError("OUT_OF_REGION", "target lies outside the granted region");
    }
    return this.adapter.perform({ ...op, x: point.x, y: point.y, space: "point" });
  }
}
