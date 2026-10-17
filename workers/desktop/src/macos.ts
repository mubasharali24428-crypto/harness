import { execFile } from "node:child_process";
import { promisify } from "node:util";
import type { DesktopAdapter, Display, Region } from "./types.js";
import { DesktopError } from "./types.js";

const run = promisify(execFile);
type Exec = (cmd: string, args: string[]) => Promise<{ stdout: string }>;

/**
 * macOS adapter. Opt-in only (HARNESS_DESKTOP_ADAPTER=macos at the call site).
 * Capture: /usr/sbin/screencapture. Input: CoreGraphics via a swift snippet and
 * System Events for keys; both need the user to grant Screen Recording /
 * Accessibility to the host process. Missing prerequisites => probe() reports
 * not-ok and every operation fails closed with UNSUPPORTED.
 */
export class MacOSAdapter implements DesktopAdapter {
  readonly name = "macos";
  constructor(private readonly exec: Exec = (c, a) => run(c, a, { timeout: 15_000 })) {}

  async probe() {
    const reasons: string[] = [];
    if (process.platform !== "darwin" && this.exec === undefined) reasons.push("not macOS");
    try {
      const out = await this.exec("/usr/bin/osascript", ["-e", 'tell application "Finder" to get bounds of window of desktop']);
      const [x1, y1, x2, y2] = out.stdout.trim().split(/,\s*/).map(Number);
      if ([x1, y1, x2, y2].some((n) => !Number.isFinite(n))) throw new Error("bad bounds");
      const scaleOut = await this.exec("/usr/bin/osascript", ["-l", "JavaScript", "-e",
        'ObjC.import("AppKit"); $.NSScreen.mainScreen.backingScaleFactor']);
      const display: Display = { id: "main", width: x2! - x1!, height: y2! - y1!, scale: Number(scaleOut.stdout.trim()) || 1 };
      return reasons.length ? { ok: false, reasons } : { ok: true, reasons, display };
    } catch (e) {
      return { ok: false, reasons: [...reasons, `display probe failed: ${(e as Error).message}`] };
    }
  }

  async capture(r: Region, outPath: string) {
    await this.exec("/usr/sbin/screencapture", ["-x", "-t", "png", "-R", `${r.x},${r.y},${r.width},${r.height}`, outPath]);
  }

  async perform(op: { kind: string; [k: string]: unknown }) {
    const num = (v: unknown) => { if (typeof v !== "number" || !Number.isFinite(v)) throw new DesktopError("BAD_REQUEST", "bad coordinate"); return v; };
    if (op.kind === "click" || op.kind === "move") {
      const x = num(op.x), y = num(op.y);
      const ev = op.kind === "move" ? "mouseMoved" : op.button === "right" ? "rightMouseDown" : "leftMouseDown";
      const up = op.button === "right" ? "rightMouseUp" : "leftMouseUp";
      const btn = op.button === "right" ? ".right" : ".left";
      const clicks = op.double ? 2 : 1;
      const src = `import CoreGraphics\nlet p = CGPoint(x: ${x}, y: ${y})\n` + (op.kind === "move"
        ? `CGEvent(mouseEventSource: nil, mouseType: .mouseMoved, mouseCursorPosition: p, mouseButton: .left)!.post(tap: .cghidEventTap)\n`
        : `for i in 1...${clicks} { for t in [CGEventType.${ev}, CGEventType.${up}] { let e = CGEvent(mouseEventSource: nil, mouseType: t, mouseCursorPosition: p, mouseButton: ${btn})!; e.setIntegerValueField(.mouseEventClickState, value: Int64(i)); e.post(tap: .cghidEventTap) } }\n`);
      await this.exec("/usr/bin/swift", ["-e", src]);
    } else if (op.kind === "type") {
      // Text is passed as an argv item to a fixed script - never interpolated into code.
      await this.exec("/usr/bin/osascript", ["-e", "on run argv\ntell application \"System Events\" to keystroke (item 1 of argv)\nend run", String(op.text)]);
    } else if (op.kind === "key") {
      const parts = String(op.combo).toLowerCase().split("+");
      const key = parts.pop()!;
      const mods = parts.map((m) => ({ cmd: "command", ctrl: "control", alt: "option", shift: "shift" } as Record<string, string>)[m] + " down").join(", ");
      const named: Record<string, number> = { enter: 36, tab: 48, escape: 53, space: 49, backspace: 51, left: 123, right: 124, down: 125, up: 126 };
      const target = key in named ? `key code ${named[key]}` : `keystroke "${key.replace(/[^a-z0-9]/g, "")}"`;
      await this.exec("/usr/bin/osascript", ["-e", `tell application "System Events" to ${target}${mods ? ` using {${mods}}` : ""}`]);
    } else {
      throw new DesktopError("UNSUPPORTED", `unknown op '${op.kind}'`);
    }
  }
}
