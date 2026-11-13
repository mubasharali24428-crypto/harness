"use client";

// U2 run workspace — interactive timeline (docs/plans/ui-overhaul-plan-2026-11-13.md §5).
// One block per recorded step, oldest first; a scrubber selects a step, and the
// panel below states the run state recorded at that point. Follow keeps the newest
// block selected while the run is live and yields the moment a person scrubs.
// The pure grouping/state/follow rules live in src/lib/runTimeline.ts.
import * as React from "react";
import { useEffect, useMemo, useState } from "react";
import { formatTimestamp } from "@/lib/format";
import {
  groupEvents,
  reduceFollow,
  scrubIndex,
  stateAtStep,
  type TimelineEventInput,
} from "@/lib/runTimeline";
import { stateLabel } from "../../../../runs/runsApi";
import { cssVar, fontSize, radius, space } from "@/components/tokens";

const SCRUB_KEYS = new Set(["j", "k", "ArrowDown", "ArrowUp", "Home", "End"]);

const STATUS_PILL: Record<string, string> = {
  running: "pill yellow",
  ok: "pill green",
  failed: "pill red",
  neutral: "",
};

const muted = { color: cssVar("muted") } as const;

export function RunTimeline({
  events,
  live,
}: {
  events: TimelineEventInput[];
  /** True while the run can still produce events; Follow is live-only. */
  live: boolean;
}) {
  const steps = useMemo(() => groupEvents(events), [events]);
  const [selected, setSelected] = useState(-1);
  const [follow, setFollow] = useState(live);

  // Follow pins the newest block; without it the selection stays where the
  // person left it (clamped when the list changes).
  useEffect(() => {
    if (steps.length === 0) {
      setSelected(-1);
      return;
    }
    setSelected((prev) => {
      if (follow) return steps.length - 1;
      return prev < 0 ? steps.length - 1 : Math.min(prev, steps.length - 1);
    });
  }, [steps, follow]);

  function moveTo(index: number) {
    setFollow((f) => reduceFollow(f, "manual-move"));
    setSelected(index);
  }

  function onScrubKey(e: React.KeyboardEvent<HTMLInputElement>) {
    if (!SCRUB_KEYS.has(e.key)) return;
    e.preventDefault();
    moveTo(scrubIndex(selected, steps.length, e.key));
  }

  const current = selected >= 0 && selected < steps.length ? steps[selected] : null;
  const at = stateAtStep(steps, selected);

  return (
    <section aria-label="Step timeline" data-testid="run-timeline" style={{ marginTop: space.section }}>
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: space.md,
          flexWrap: "wrap",
          marginBottom: space.md,
        }}
      >
        <h2 style={{ margin: 0, fontSize: fontSize.md }}>
          Steps{" "}
          <span style={muted}>
            ({steps.length} recorded, oldest first)
          </span>
        </h2>
        <label
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: space.sm,
            color: live ? cssVar("text2") : cssVar("muted"),
            fontSize: fontSize.sm,
          }}
          title={live ? "Keep the newest step selected" : "Follow applies while the run is live"}
        >
          <input
            type="checkbox"
            checked={follow && live}
            disabled={!live}
            onChange={() => setFollow((f) => reduceFollow(f, "toggle"))}
            data-testid="timeline-follow"
          />
          Follow newest step
        </label>
      </div>

      {steps.length === 0 ? (
        <p style={{ ...muted, fontSize: fontSize.sm }}>No events recorded for this run yet.</p>
      ) : (
        <>
          <label htmlFor="run-timeline-scrubber" style={{ ...muted, fontSize: fontSize.sm }}>
            Scrub the run — j / k or arrow keys, Home / End
          </label>
          <input
            id="run-timeline-scrubber"
            data-testid="timeline-scrubber"
            type="range"
            min={1}
            max={steps.length}
            value={selected >= 0 ? selected + 1 : 1}
            onChange={(e) => moveTo(Number(e.target.value) - 1)}
            onKeyDown={onScrubKey}
            aria-label="Select a run step"
            aria-valuetext={
              current ? `Step ${selected + 1} of ${steps.length}: ${current.label}` : undefined
            }
            style={{ width: "100%", margin: `${space.sm}px 0 ${space.md}px` }}
          />

          {current && (
            <div
              data-testid="timeline-selection"
              aria-live="polite"
              style={{
                border: `1px solid ${cssVar("border")}`,
                borderRadius: radius.md,
                background: cssVar("panel2"),
                padding: `${space.md}px ${space.lg}px`,
                marginBottom: space.md,
                minWidth: 0,
              }}
            >
              <div style={{ display: "flex", gap: space.sm, flexWrap: "wrap", alignItems: "center" }}>
                <strong style={{ fontSize: fontSize.md }}>
                  Step {selected + 1} of {steps.length}
                </strong>
                <span className={STATUS_PILL[current.status]}>{current.status}</span>
                <span style={{ ...muted, fontSize: fontSize.sm }}>
                  {formatTimestamp(current.ts)}
                </span>
              </div>
              <div style={{ marginTop: space.xs, fontSize: fontSize.md }}>{current.label}</div>
              {current.receipt && (
                <div
                  style={{
                    ...muted,
                    marginTop: space.xs,
                    fontFamily: "var(--font-mono)",
                    fontSize: fontSize.sm,
                    overflowWrap: "anywhere",
                  }}
                >
                  {current.receipt}
                </div>
              )}
              <div style={{ marginTop: space.sm, fontSize: fontSize.sm, color: cssVar("text2") }}>
                State at this step:{" "}
                {at.state ? (
                  <>
                    {stateLabel(at.state)}
                    {at.label && (
                      <span style={muted}>
                        {" "}
                        (recorded by {at.label}, seq {at.seq})
                      </span>
                    )}
                  </>
                ) : (
                  <span style={muted}>not recorded</span>
                )}
              </div>
            </div>
          )}

          <ol
            style={{
              listStyle: "none",
              margin: 0,
              padding: 0,
              display: "grid",
              gap: space.xs,
              maxHeight: 420,
              overflowY: "auto",
            }}
          >
            {steps.map((step, i) => (
              <li key={step.id} style={{ minWidth: 0 }}>
                <button
                  type="button"
                  onClick={() => moveTo(i)}
                  aria-current={i === selected ? "step" : undefined}
                  style={{
                    display: "grid",
                    gridTemplateColumns: "minmax(0, 1fr) auto",
                    gap: `${space.xs}px ${space.md}px`,
                    width: "100%",
                    textAlign: "left",
                    padding: `${space.sm}px ${space.md}px`,
                    borderRadius: radius.md,
                    border: `1px solid ${i === selected ? cssVar("accent") : cssVar("borderSoft")}`,
                    background: i === selected ? cssVar("panel2") : "transparent",
                    color: cssVar("text"),
                    font: "inherit",
                    fontSize: fontSize.sm,
                    cursor: "pointer",
                    minWidth: 0,
                  }}
                >
                  <span style={{ overflowWrap: "anywhere" }}>
                    {step.label}
                    {step.receipt && (
                      <span
                        style={{
                          display: "block",
                          color: cssVar("muted"),
                          fontFamily: "var(--font-mono)",
                          overflowWrap: "anywhere",
                        }}
                      >
                        {step.receipt}
                      </span>
                    )}
                  </span>
                  <span style={{ ...muted, whiteSpace: "nowrap" }}>
                    {formatTimestamp(step.ts)}
                  </span>
                </button>
              </li>
            ))}
          </ol>
        </>
      )}
    </section>
  );
}
