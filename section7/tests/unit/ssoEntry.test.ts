// F-gap closure — pure helpers behind the SSO login half (lib/sso.ts).
// The redirect_uri must be echoed byte-for-byte at complete (Section 1 binds
// it per attempt and rejects mismatches), so the stash round-trip is the
// contract under test, together with the IdP's two callback shapes.
import { describe, expect, it } from "vitest";
import {
  SSO_PENDING_KEY,
  clearPendingSso,
  readPendingSso,
  readSsoCallbackParams,
  savePendingSso,
  ssoRedirectUri,
} from "../../lib/sso";

function fakeStorage(init: Record<string, string> = {}): Storage {
  const map = new Map<string, string>(Object.entries(init));
  return {
    get length() {
      return map.size;
    },
    clear: () => map.clear(),
    getItem: (k: string) => map.get(k) ?? null,
    key: (i: number) => Array.from(map.keys())[i] ?? null,
    removeItem: (k: string) => {
      map.delete(k);
    },
    setItem: (k: string, v: string) => {
      map.set(k, v);
    },
  };
}

describe("ssoRedirectUri", () => {
  it("binds the SPA callback route on the given origin", () => {
    expect(ssoRedirectUri("http://localhost:3000")).toBe(
      "http://localhost:3000/auth/sso/callback",
    );
  });
});

describe("pending SSO attempt stash", () => {
  it("round-trips the team id and the EXACT redirect uri bound at start", () => {
    const storage = fakeStorage();
    const pending = {
      teamId: "team-1",
      redirectUri: "http://localhost:3000/auth/sso/callback",
    };
    savePendingSso(storage, pending);
    expect(readPendingSso(storage)).toEqual(pending);
    expect(storage.getItem(SSO_PENDING_KEY)).toBe(JSON.stringify(pending));
  });

  it("returns null for garbage instead of throwing", () => {
    expect(readPendingSso(fakeStorage({ [SSO_PENDING_KEY]: "not json" }))).toBeNull();
    expect(readPendingSso(fakeStorage({ [SSO_PENDING_KEY]: '{"teamId":1}' }))).toBeNull();
  });

  it("clears the stash", () => {
    const storage = fakeStorage();
    savePendingSso(storage, { teamId: "t", redirectUri: "r" });
    clearPendingSso(storage);
    expect(readPendingSso(storage)).toBeNull();
  });
});

describe("sso callback params", () => {
  it("reads the success shape (code + state)", () => {
    expect(readSsoCallbackParams("?code=abc&state=xyz")).toEqual({
      code: "abc",
      state: "xyz",
      error: null,
    });
  });

  it("surfaces the IdP's error redirect", () => {
    expect(
      readSsoCallbackParams("?error=access_denied&error_description=nope").error,
    ).toBe("access_denied");
  });
});
