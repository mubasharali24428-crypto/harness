import { appendFileSync, chmodSync, existsSync, mkdirSync, readFileSync, renameSync, rmSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { chromium, type Browser, type BrowserContext, type Page } from "@playwright/test";
import { ApprovalStore } from "./approvals.js";
import { newId } from "./digest.js";
import { parseStateKey, seal, unseal } from "./seal.js";
import { Diagnostics, installEgressGuard } from "./diagnostics.js";
import { DEVICE_PROFILES } from "./profiles.js";
import type { ReplayEntry } from "./replay.js";
import type { Artifact } from "./types.js";
import {
  WorkerError,
  type Observation,
  type SessionIdentity,
  type SessionOptions,
} from "./types.js";

/**
 * Session manager: isolated persistent browser sessions per (team, project,
 * run). Storage state (cookies, localStorage) is written under
 * <root>/<team>/<project>/<run>/state.json with 0700 directories; a session
 * reopened with the same identity resumes its cookies, while different
 * identities never share a context. Nothing here trusts a caller-supplied
 * path: identity components are validated before they touch the filesystem.
 */

const ID_PART_RE = /^[A-Za-z0-9_-]{1,64}$/;

export class WorkerSession {
  readonly sessionId: string;
  readonly identity: SessionIdentity;
  readonly allowedOrigins: string[];
  readonly context: BrowserContext;
  readonly page: Page;
  readonly observations = new Map<string, Observation>();
  readonly approvals: ApprovalStore;
  readonly diagnostics: Diagnostics;
  readonly replayLog: ReplayEntry[] = [];
  readonly artifactIndex = new Map<string, Artifact>();
  readonly dir: string;
  readonly artifactsDir: string;

  constructor(args: {
    sessionId: string;
    identity: SessionIdentity;
    allowedOrigins: string[];
    context: BrowserContext;
    page: Page;
    diagnostics: Diagnostics;
    dir: string;
    now: () => number;
  }) {
    this.sessionId = args.sessionId;
    this.identity = args.identity;
    this.allowedOrigins = args.allowedOrigins;
    this.context = args.context;
    this.page = args.page;
    this.dir = args.dir;
    this.artifactsDir = join(args.dir, "artifacts");
    this.approvals = new ApprovalStore(args.now);
    this.diagnostics = args.diagnostics;
  }

  /** Append to the in-memory log AND the durable JSONL next to the session state. */
  recordReplay(entry: ReplayEntry): void {
    this.replayLog.push(entry);
    appendFileSync(join(this.dir, "replay.jsonl"), `${JSON.stringify(entry)}\n`, { mode: 0o600 });
  }

  loadReplay(): void {
    const path = join(this.dir, "replay.jsonl");
    if (!existsSync(path)) return;
    for (const line of readFileSync(path, "utf8").split("\n")) {
      if (line.trim()) this.replayLog.push(JSON.parse(line) as ReplayEntry);
    }
  }

  rememberObservation(observation: Observation): Observation {
    this.observations.set(observation.observationId, observation);
    return observation;
  }

  storedObservation(observationId: string | undefined): Observation | undefined {
    return observationId ? this.observations.get(observationId) : undefined;
  }
}

type StorageStateObject = Awaited<ReturnType<BrowserContext["storageState"]>>;

export interface SessionManagerOptions {
  rootDir: string;
  headless?: boolean;
  now?: () => number;
  /** 64-hex AES-256 key (HARNESS_BROWSER_STATE_KEY). When set, state is sealed at rest. */
  stateKey?: string;
}

export class SessionManager {
  private readonly rootDir: string;
  private readonly headless: boolean;
  private readonly now: () => number;
  private readonly stateKey: Buffer | undefined;
  private readonly sessions = new Map<string, WorkerSession>();
  private browser: Browser | null = null;

  constructor(opts: SessionManagerOptions) {
    this.rootDir = opts.rootDir;
    this.headless = opts.headless ?? process.env.HARNESS_BROWSER_HEADLESS !== "0";
    this.now = opts.now ?? (() => Date.now());
    this.stateKey = parseStateKey(opts.stateKey ?? process.env.HARNESS_BROWSER_STATE_KEY);
  }

  /** Storage state to hand Playwright, or undefined for a fresh profile. Fail closed on sealed state without a key. */
  private loadState(dir: string): string | StorageStateObject | undefined {
    const sealed = join(dir, "state.enc");
    const plain = join(dir, "state.json");
    if (existsSync(sealed)) {
      if (!this.stateKey) {
        throw new WorkerError("BAD_REQUEST", "stored browser state is encrypted; configure the state key");
      }
      return JSON.parse(unseal(readFileSync(sealed, "utf8"), this.stateKey));
    }
    return existsSync(plain) ? plain : undefined;
  }

  private async writeState(session: WorkerSession): Promise<void> {
    if (!this.stateKey) {
      await session.context.storageState({ path: join(session.dir, "state.json") });
      return;
    }
    const state = await session.context.storageState();
    const tmp = join(session.dir, "state.enc.tmp");
    writeFileSync(tmp, seal(JSON.stringify(state), this.stateKey), { mode: 0o600 });
    chmodSync(tmp, 0o600);
    renameSync(tmp, join(session.dir, "state.enc"));
    rmSync(join(session.dir, "state.json"), { force: true }); // never leave a plaintext copy beside the sealed one
  }

  async open(identity: SessionIdentity, options: SessionOptions): Promise<WorkerSession> {
    for (const part of [identity.teamId, identity.projectId, identity.runId]) {
      if (!ID_PART_RE.test(part)) {
        throw new WorkerError("BAD_REQUEST", `invalid session identity component: '${part}'`);
      }
    }
    if (options.allowedOrigins.length === 0) {
      throw new WorkerError("BAD_REQUEST", "a session needs at least one allowed origin");
    }
    for (const origin of options.allowedOrigins) {
      let parsed: URL;
      try {
        parsed = new URL(origin);
      } catch {
        throw new WorkerError("BAD_REQUEST", `invalid allowed origin: '${origin}'`);
      }
      if (parsed.origin !== origin) {
        throw new WorkerError("BAD_REQUEST", `allowed origin must be a bare origin: '${origin}'`);
      }
    }

    const dir = join(this.rootDir, identity.teamId, identity.projectId, identity.runId);
    mkdirSync(dir, { recursive: true, mode: 0o700 });
    const storageState = this.loadState(dir);

    const browser = await this.ensureBrowser();
    const profile = options.profile ? DEVICE_PROFILES[options.profile] : undefined;
    if (options.profile && !profile) {
      throw new WorkerError("BAD_REQUEST", `unknown device profile '${options.profile}'`);
    }
    const context = await browser.newContext({
      ...(storageState ? { storageState } : {}),
      ...(profile ?? {}),
      viewport: options.viewport ?? profile?.viewport ?? { width: 1280, height: 800 },
      deviceScaleFactor: options.deviceScaleFactor ?? profile?.deviceScaleFactor ?? 1,
    });
    const diagnostics = new Diagnostics(this.now);
    await installEgressGuard(context, options.allowedOrigins, diagnostics);
    const page = await context.newPage();
    diagnostics.attach(page);
    const session = new WorkerSession({
      sessionId: newId("bsess"),
      identity,
      allowedOrigins: [...options.allowedOrigins],
      context,
      page,
      diagnostics,
      dir,
      now: this.now,
    });
    session.loadReplay();
    this.sessions.set(session.sessionId, session);
    return session;
  }

  get(sessionId: string): WorkerSession {
    const session = this.sessions.get(sessionId);
    if (!session) throw new WorkerError("SESSION_NOT_FOUND", `unknown session '${sessionId}'`);
    return session;
  }

  /** Flush storage state to disk (persistence) without ending the session. */
  async persist(session: WorkerSession): Promise<void> {
    await this.writeState(session);
  }

  async close(sessionId: string, opts: { deleteState?: boolean } = {}): Promise<void> {
    const session = this.sessions.get(sessionId);
    if (!session) return;
    this.sessions.delete(sessionId);
    if (opts.deleteState) {
      await session.context.close().catch(() => undefined);
      rmSync(session.dir, { recursive: true, force: true });
      return;
    }
    await this.persist(session);
    await session.context.close();
  }

  async dispose(): Promise<void> {
    const closers: Promise<unknown>[] = [];
    for (const session of this.sessions.values()) {
      closers.push(
        this.writeState(session)
          .catch(() => undefined)
          .then(() => session.context.close().catch(() => undefined)),
      );
    }
    await Promise.all(closers);
    this.sessions.clear();
    if (this.browser) {
      const browser = this.browser;
      this.browser = null;
      await browser.close().catch(() => undefined);
    }
  }

  private async ensureBrowser(): Promise<Browser> {
    if (!this.browser) {
      this.browser = await chromium.launch({ headless: this.headless });
    }
    return this.browser;
  }
}
