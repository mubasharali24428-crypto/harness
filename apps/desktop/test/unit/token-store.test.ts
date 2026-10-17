import { describe, expect, it } from "vitest";
import { mkdtempSync, readFileSync, existsSync, statSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { TokenVault, type Cipher } from "../../src/token-store";

const xor: Cipher = {
  isEncryptionAvailable: () => true,
  encryptString: (s) => Buffer.from(Buffer.from(s).map((b) => b ^ 0x5a)),
  decryptString: (d) => Buffer.from(d.map((b) => b ^ 0x5a)).toString(),
};
const none: Cipher = { ...xor, isEncryptionAvailable: () => false };
const dir = () => path.join(mkdtempSync(path.join(tmpdir(), "vault-")), "v");

describe("TokenVault", () => {
  it("persists encrypted, never plaintext, with owner-only perms; survives restart", () => {
    const d = dir();
    const v = new TokenVault(d, xor);
    expect(v.save("tok-SECRET-123456")).toEqual({ persisted: true });
    const raw = readFileSync(path.join(d, "session.bin"));
    expect(raw.toString()).not.toContain("tok-SECRET");
    expect(statSync(path.join(d, "session.bin")).mode & 0o777).toBe(0o600);
    expect(new TokenVault(d, xor).load()).toBe("tok-SECRET-123456");
  });
  it("fails closed without OS encryption: memory only, nothing on disk", () => {
    const d = dir();
    const v = new TokenVault(d, none);
    expect(v.save("tok-SECRET-123456")).toEqual({ persisted: false });
    expect(existsSync(path.join(d, "session.bin"))).toBe(false);
    expect(v.load()).toBe("tok-SECRET-123456");
    expect(new TokenVault(d, none).load()).toBeNull();
  });
  it("discards a corrupt/foreign blob and clear() removes everything", () => {
    const d = dir();
    const bad: Cipher = { ...xor, decryptString: () => { throw new Error("bad key"); } };
    new TokenVault(d, xor).save("tok-SECRET-123456");
    expect(new TokenVault(d, bad).load()).toBeNull();
    expect(existsSync(path.join(d, "session.bin"))).toBe(false);
    const v = new TokenVault(d, xor); v.save("x1234567"); v.clear();
    expect(v.load()).toBeNull();
  });
});
