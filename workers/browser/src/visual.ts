import { createHash } from "node:crypto";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { PNG } from "pngjs";
import pixelmatch from "pixelmatch";
import { WorkerError } from "./types.js";

const NAME_RE = /^[A-Za-z0-9._-]{1,80}$/;

export interface VisualResult {
  status: "match" | "diff" | "new" | "size-mismatch";
  diffRatio: number;
  diffPixels: number;
  baselineSha256?: string;
  actualSha256: string;
  diffPng?: Buffer;
}

/**
 * Visual regression. A missing baseline is "new" - never a pass - and a
 * baseline only changes through approveBaseline() with a named approver.
 */
export class VisualStore {
  constructor(private readonly dir: string) {}

  private paths(name: string) {
    if (!NAME_RE.test(name)) throw new WorkerError("BAD_REQUEST", `invalid baseline name '${name}'`);
    return { png: join(this.dir, `${name}.png`), meta: join(this.dir, `${name}.json`) };
  }

  compare(name: string, actual: Buffer, opts: { maxDiffRatio?: number; threshold?: number } = {}): VisualResult {
    const { png } = this.paths(name);
    const actualSha256 = createHash("sha256").update(actual).digest("hex");
    if (!existsSync(png)) return { status: "new", diffRatio: 1, diffPixels: -1, actualSha256 };
    const base = readFileSync(png);
    const baselineSha256 = createHash("sha256").update(base).digest("hex");
    const a = PNG.sync.read(base);
    const b = PNG.sync.read(actual);
    if (a.width !== b.width || a.height !== b.height) {
      return { status: "size-mismatch", diffRatio: 1, diffPixels: -1, baselineSha256, actualSha256 };
    }
    const diff = new PNG({ width: a.width, height: a.height });
    const diffPixels = pixelmatch(a.data, b.data, diff.data, a.width, a.height, { threshold: opts.threshold ?? 0.1 });
    const diffRatio = diffPixels / (a.width * a.height);
    const ok = diffRatio <= (opts.maxDiffRatio ?? 0);
    return {
      status: ok ? "match" : "diff", diffRatio, diffPixels, baselineSha256, actualSha256,
      ...(ok ? {} : { diffPng: PNG.sync.write(diff) }),
    };
  }

  approveBaseline(name: string, actual: Buffer, approver: string): { sha256: string } {
    if (!approver) throw new WorkerError("BAD_REQUEST", "approver required");
    const { png, meta } = this.paths(name);
    mkdirSync(this.dir, { recursive: true, mode: 0o700 });
    PNG.sync.read(actual); // must be a valid PNG
    writeFileSync(png, actual);
    const sha256 = createHash("sha256").update(actual).digest("hex");
    writeFileSync(meta, JSON.stringify({ sha256, approvedBy: approver, approvedAtMs: Date.now() }));
    return { sha256 };
  }
}
