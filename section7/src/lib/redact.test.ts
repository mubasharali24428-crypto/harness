import { describe, expect, it } from "vitest";
import { scrubSecrets } from "@/lib/redact";

// Token-shaped fixtures are assembled at runtime so secret scanners do not flag this file.
const GHP = ["ghp", "_", "a".repeat(36)].join("");
const SKP = ["sk", "-proj-", "SECRETabc12345"].join("");

describe("scrubSecrets (port of section4 redact.py)", () => {
  it.each([
    ['curl -H "Authorization: Bearer abc123SECRET" https://x', "abc123SECRET"],
    [`git push https://user:${GHP}@github.com/o/r`, GHP],
    ["deploy --token=xyzSECRET", "xyzSECRET"],
    [`401: invalid key ${SKP}`, SKP],
    ["export OPENAI_API_KEY=sk-SECRET123 && npm test", "sk-SECRET123"],
    ["password: 'two words'", "two words"],
    ["X-Api-Key: abcdef123456", "abcdef123456"],
    [`token ${GHP}`, GHP],
    ["-----BEGIN RSA PRIVATE KEY-----\nMIIE\n-----END RSA PRIVATE KEY-----", "MIIE"],
  ])("masks %s", (input, secret) => {
    const out = scrubSecrets(input);
    expect(out).not.toContain(secret);
    expect(out).toContain("[redacted");
  });

  it("leaves ordinary text and identifiers alone", () => {
    for (const plain of ["npm test -- --runInBand", "tokenizer=bpe token_count=12", "path: src/auth/session.ts"]) {
      expect(scrubSecrets(plain)).toBe(plain);
    }
  });
});
