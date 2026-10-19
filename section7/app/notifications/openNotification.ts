// Part 47: how a notification is opened. Always through GET /link/{id} (the server re-checks access on every
// call); the path it returns is used only if it is a same-site absolute path to a page this app has.
import { ApiError } from "@/lib/api";
import { getLink, notificationError } from "./notificationsApi";
import { LINK_GONE, LINK_NO_PAGE, hasRoute, safeInternalPath } from "./notificationsModel";

export type OpenResult =
  | { kind: "go"; path: string }
  | { kind: "message"; tone: "info" | "danger"; text: string };

export async function resolveLink(token: string, id: string): Promise<OpenResult> {
  try {
    const { path } = await getLink(token, id);
    const safe = safeInternalPath(path);
    if (!safe) return { kind: "message", tone: "danger", text: LINK_NO_PAGE };
    if (!hasRoute(safe)) return { kind: "message", tone: "info", text: LINK_NO_PAGE };
    return { kind: "go", path: safe };
  } catch (e) {
    // 404 covers "not yours", "gone" and "you lost access" alike: the server tells us nothing more, and neither do we.
    return { kind: "message", tone: "danger", text: e instanceof ApiError && e.status === 404 ? LINK_GONE : notificationError(e) };
  }
}
