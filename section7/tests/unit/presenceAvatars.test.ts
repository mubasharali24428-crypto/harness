import { describe, expect, it } from "vitest";
import { presentPeople } from "@/components/workspace/PresenceAvatars";
import type { PresenceEntry } from "../../app/projects/[projectId]/runs/[runId]/controlApi";

const p = (userId: string, status: string | null, self = false): PresenceEntry =>
  ({ userId, role: "participant", isLeader: false, self, announcement: "", presence: status ? { status } : null }) as PresenceEntry;

describe("presentPeople", () => {
  it("shows only people who are here, others first, and counts the overflow", () => {
    const out = presentPeople([p("me", "online", true), p("a", "online"), p("b", "disconnected"), p("c", "idle"), p("d", null), p("e", "online"), p("f", "online")]);
    expect(out.shown.map((e) => e.userId)).toEqual(["a", "c", "e", "f"]);
    expect(out.more).toBe(1);
  });
  it("is empty when nobody is here (hidden and unknown are not shown)", () => {
    expect(presentPeople([p("a", "hidden"), p("b", "unknown")])).toEqual({ shown: [], more: 0 });
  });
});
