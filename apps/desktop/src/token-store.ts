import { existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import path from "node:path";

export interface Cipher {
  isEncryptionAvailable(): boolean;
  encryptString(plain: string): Buffer;
  decryptString(data: Buffer): string;
}

/**
 * Session-token persistence. Fails closed: if OS-backed encryption is unavailable the token is
 * kept in memory only — it is never written in plaintext.
 */
export class TokenVault {
  private memory: string | null = null;
  constructor(
    private readonly dir: string,
    private readonly cipher: Cipher,
  ) {}

  private get file(): string {
    return path.join(this.dir, "session.bin");
  }

  get persistent(): boolean {
    return this.cipher.isEncryptionAvailable();
  }

  save(token: string): { persisted: boolean } {
    this.memory = token;
    if (!this.persistent) return { persisted: false };
    mkdirSync(this.dir, { recursive: true, mode: 0o700 });
    writeFileSync(this.file, this.cipher.encryptString(token), { mode: 0o600 });
    return { persisted: true };
  }

  load(): string | null {
    if (this.memory) return this.memory;
    if (!this.persistent || !existsSync(this.file)) return null;
    try {
      this.memory = this.cipher.decryptString(readFileSync(this.file));
      return this.memory;
    } catch {
      rmSync(this.file, { force: true }); // corrupt or from another machine/user: discard
      return null;
    }
  }

  clear(): void {
    this.memory = null;
    rmSync(this.file, { force: true });
  }
}
