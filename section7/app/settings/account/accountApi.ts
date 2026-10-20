// Parts 31/58: client for POST /api/v1/account/erasure (a person erases their own account) and the
// words shown when it refuses. The two 409 refusals already carry plain wording from the server.
import { ApiError, IDENTITY_BASE, apiFetch } from "@/lib/api";
import { clearSession } from "@/lib/auth";

export function eraseAccount(confirmEmail: string, token: string): Promise<{ erased: boolean }> {
  return apiFetch(`${IDENTITY_BASE}/api/v1/account/erasure`, token, {
    method: "POST",
    body: JSON.stringify({ confirmEmail }),
  });
}

/** What to tell the person. A refusal says nothing changed; any other failure does not claim to know. */
export function erasureProblem(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.status === 401) return "Your session has ended. Sign in again, then try once more.";
    if (e.code === "SOLE_ADMIN" || e.code === "ERASURE_ON_HOLD") {
      return `Your account was not erased: ${e.message}. Nothing was changed.`;
    }
    if (e.status === 422) return "That is not the email address of this account. Nothing was changed.";
  }
  if (e instanceof TypeError) {
    return "Could not reach the server. Your account may not have been erased; check your connection and try again.";
  }
  return "Something went wrong and your account may not have been erased. Try again.";
}

/** The page's one action: ask, and end the local session only when the server says it is done. */
export async function runErasure(
  typedEmail: string,
  token: string | undefined,
  deps: { erase: typeof eraseAccount; clear: () => void } = { erase: eraseAccount, clear: clearSession },
): Promise<{ done: boolean; problem: string | null }> {
  if (!token) return { done: false, problem: "You are not signed in. Sign in, then try again." };
  try {
    await deps.erase(typedEmail.trim(), token);
  } catch (e) {
    return { done: false, problem: erasureProblem(e) };
  }
  deps.clear();
  return { done: true, problem: null };
}
