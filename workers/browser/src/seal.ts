import { createCipheriv, createDecipheriv, randomBytes } from "node:crypto";
import { WorkerError } from "./types.js";

/** AES-256-GCM envelope for browser storage state (cookies are credentials). */
export function parseStateKey(hex: string | undefined): Buffer | undefined {
  if (!hex) return undefined;
  if (!/^[0-9a-fA-F]{64}$/.test(hex)) {
    throw new WorkerError("BAD_REQUEST", "state key must be 64 hex chars (32 bytes)");
  }
  return Buffer.from(hex, "hex");
}

export function seal(plain: string, key: Buffer): string {
  const iv = randomBytes(12);
  const c = createCipheriv("aes-256-gcm", key, iv);
  const ct = Buffer.concat([c.update(plain, "utf8"), c.final()]);
  return JSON.stringify({ v: 1, iv: iv.toString("base64"), tag: c.getAuthTag().toString("base64"), ct: ct.toString("base64") });
}

export function unseal(blob: string, key: Buffer): string {
  try {
    const { v, iv, tag, ct } = JSON.parse(blob) as { v: number; iv: string; tag: string; ct: string };
    if (v !== 1) throw new Error("version");
    const d = createDecipheriv("aes-256-gcm", key, Buffer.from(iv, "base64"));
    d.setAuthTag(Buffer.from(tag, "base64"));
    return Buffer.concat([d.update(Buffer.from(ct, "base64")), d.final()]).toString("utf8");
  } catch {
    throw new WorkerError("BAD_REQUEST", "stored browser state failed authentication (wrong key or tampered)");
  }
}
