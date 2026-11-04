// W23: the five user states, for every server status × connection status.
import * as React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import {
  type ConnStatus,
  STATUS_TEXT,
  USER_STATUS_TEXT,
  liveStatus,
} from "../../app/projects/[projectId]/runs/[runId]/realtimeCore";
import { RunDetailAggregate } from "../../app/projects/[projectId]/runs/[runId]/runDetailAggregate";
import type { RunAggregateDto } from "../../app/runs/runsApi";

const CONNS = Object.keys(STATUS_TEXT) as ConnStatus[];
const SERVER = ["working", "waiting", "blocked", "finished"];
const FRESH: ConnStatus[] = ["live", "polling", "access_lost"];

describe("liveStatus", () => {
  it("covers all seven connection states", () => {
    expect(CONNS).toHaveLength(7);
  });

  for (const server of SERVER) {
    for (const conn of CONNS) {
      it(`${server} over ${conn}`, () => {
        const expected = server === "finished" || FRESH.includes(conn) ? server : "reconnecting";
        expect(liveStatus(server, conn)).toBe(expected);
      });
    }
  }

  it("is null for a missing or unknown server status, whatever the connection", () => {
    for (const conn of CONNS) {
      for (const bad of [undefined, null, "", "reconnecting", "stopped", "constructor"]) {
        expect(liveStatus(bad, conn), `${bad} ${conn}`).toBeNull();
      }
    }
  });

  it("has text for each of the five states", () => {
    expect(Object.keys(USER_STATUS_TEXT).sort()).toEqual(
      ["blocked", "finished", "reconnecting", "waiting", "working"],
    );
  });
});

const agg = (userStatus?: string): RunAggregateDto => ({
  runId: "r1", state: "executing", planId: null, planApproved: true, isHumanEditing: false,
  executing: true, engineMode: null, costUsd: null, prId: null, url: null, steps: [],
  budget: { degraded: false },
  lifecycle: userStatus === undefined ? undefined
    : { phase: "running", cancelConfirmed: false, mutationPossible: true, userStatus },
});

describe("RunDetailAggregate status line", () => {
  it("shows the server status when fresh", () => {
    const html = renderToStaticMarkup(<RunDetailAggregate agg={agg("working")} now={0} conn="polling" />);
    expect(html).toContain('data-testid="run-user-status"');
    expect(html).toContain(">Working<");
  });

  it("shows Reconnecting after a failed refresh", () => {
    const html = renderToStaticMarkup(<RunDetailAggregate agg={agg("working")} now={0} conn="reconnecting" />);
    expect(html).toContain(">Reconnecting<");
  });

  it("shows nothing when an older server sends no userStatus", () => {
    const html = renderToStaticMarkup(<RunDetailAggregate agg={agg()} now={0} />);
    expect(html).not.toContain("run-user-status");
  });
});
