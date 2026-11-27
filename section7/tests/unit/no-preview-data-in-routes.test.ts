/**
 * E0 guard (ui-ux-enhancement-master-prompt-2026-11-21 §5/§7 E0d): nothing in a
 * shipped route may import preview data.
 *
 * The R1-era `src/components/workspace/previewData.ts` held pure helpers, not
 * sample data, so "no shipped route imports previewData" was vacuous while it
 * existed and false once shipped pages imported it for real helpers. E0 moved
 * those helpers to runRows.ts / diffParse.ts and deleted the module. This test
 * pins the law for any future preview/sample module:
 *
 *   1. `previewData.*` must not exist as a source module again. If a future
 *      phase reintroduces one, it must live under a path matching
 *      `**\/previewData.(ts|tsx)` AND be imported only by tests/stories.
 *   2. No file under `app/**` (shipped routes) and no non-test file under
 *      `src/**` may import a module named `previewData` (any case, any depth).
 *      Note this is a name-level law, not the word "preview": real product
 *      components like app/meeting/ContextPreviewPanel (a live meeting-context
 *      UI) legitimately ship and are not sample data.
 *
 * A failure here means sample data is about to ship in a real route — build
 * the state from the API/event stream instead, or report the backend gap.
 */
import { describe, expect, it } from "vitest";
import { readFileSync, readdirSync, statSync, existsSync } from "node:fs";
import { join, relative } from "node:path";

const ROOT = join(__dirname, "..", "..");

function walk(dir: string, out: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    if (name === "node_modules" || name === ".next" || name.startsWith(".")) continue;
    const p = join(dir, name);
    const st = statSync(p);
    if (st.isDirectory()) walk(p, out);
    else if (/\.(ts|tsx|js|jsx|mjs)$/.test(name)) out.push(p);
  }
  return out;
}

const srcRoot = join(ROOT, "src");
const appRoot = join(ROOT, "app");

/** The banned sample-data module name. Name-level, not /preview/ — see above. */
const BANNED = /(^|\/)previewData(\.(ts|tsx|js|jsx|mjs))?$/i;

function importsOf(file: string): string[] {
  const text = readFileSync(file, "utf8");
  const specs: string[] = [];
  const re = /(?:import|export)\s+(?:[\s\S]*?from\s+)?["']([^"']+)["']/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(text)) !== null) specs.push(m[1]);
  return specs;
}

function resolvesToLocal(spec: string, importer: string): string | null {
  if (!spec.startsWith(".")) return null;
  const base = spec.endsWith(".js") ? spec.slice(0, -3) : spec;
  for (const ext of ["", ".ts", ".tsx", ".js", ".jsx", ".mjs"]) {
    const cand = join(importer, "..", base + ext);
    if (existsSync(cand) && statSync(cand).isFile()) return cand;
  }
  return null;
}

describe("no preview/sample data in shipped routes (E0 guard)", () => {
  const appFiles = existsSync(appRoot) ? walk(appRoot) : [];
  const srcFiles = existsSync(srcRoot) ? walk(srcRoot) : [];

  it("still finds the shipped trees to audit (sanity)", () => {
    expect(appFiles.length).toBeGreaterThan(0);
    expect(srcFiles.length).toBeGreaterThan(0);
  });

  it("no app/ route imports any preview-named module", () => {
    const offenders: string[] = [];
    for (const f of appFiles) {
      for (const spec of importsOf(f)) {
        const local = resolvesToLocal(spec, f);
        const target = local ?? spec;
        if (BANNED.test(target)) offenders.push(`${relative(ROOT, f)} -> ${spec}`);
      }
    }
    expect(offenders).toEqual([]);
  });

  it("no non-test src/ module imports the previewData module", () => {
    const offenders: string[] = [];
    for (const f of srcFiles) {
      if (/\.(test|spec)\./.test(f)) continue;
      for (const spec of importsOf(f)) {
        const local = resolvesToLocal(spec, f);
        const target = local ?? spec;
        if (BANNED.test(target)) offenders.push(`${relative(ROOT, f)} -> ${spec}`);
      }
    }
    expect(offenders).toEqual([]);
  });

  it("the R1 previewData module stays deleted (no source file resurrects it)", () => {
    const resurrected = [...walk(ROOT)].filter((f) => /previewData\.(ts|tsx)$/.test(f));
    expect(resurrected).toEqual([]);
  });
});
