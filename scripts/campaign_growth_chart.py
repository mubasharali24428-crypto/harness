"""Mission 17-10x campaign growth chart: project completion % per mission.

Renders docs/briefs/mission19-growth-chart.png — cumulative completion
toward the Cursor-class commercial end state, with the remaining Horizon
NEXT/LATER/Bet missions projected from the 10x plan's effort estimates.
"""
from __future__ import annotations

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch

# (label, completion % after mission, status)  — status: done|active|planned
MISSIONS: list[tuple[str, float, str]] = [
    ("M11\nW1 security", 38, "done"),
    ("M12\nW2 core UX", 48, "done"),
    ("M13\nW3 hardening", 56, "done"),
    ("M14\nW4 product", 62, "done"),
    ("M15\nW5 roadmap-1", 68, "done"),
    ("M16\nW6 perf/UI", 73, "done"),
    ("M17\nelite audit+fixes", 80, "done"),
    ("M18\nHorizon NOW", 85, "done"),
    ("M19\nBASELINE+spine", 90, "active"),
    ("M20\nNEXT part 2", 93, "planned"),
    ("M21\ncontext pillar", 96, "planned"),
    ("M22\ncommercial skel", 98, "planned"),
    ("M23\nBets + polish", 100, "planned"),
]

DONE = "#7C5A9B"
ACTIVE = "#C0392B"
PLANNED = "#B0B7C3"

fig, ax = plt.subplots(figsize=(13, 6.2), dpi=150)
fig.patch.set_facecolor("#FFFFFF")

xs = list(range(1, len(MISSIONS) + 1))
ys = [m[1] for m in MISSIONS]
colors = [{"done": DONE, "active": ACTIVE, "planned": PLANNED}[m[2]] for m in MISSIONS]

ax.plot(xs, ys, color="#D8DCE3", linewidth=2.0, zorder=1)
ax.scatter(xs, ys, s=110, c=colors, zorder=3, edgecolors="white", linewidths=1.5)

for x, (label, pct, status) in zip(xs, MISSIONS):
    ax.annotate(
        f"{pct}%",
        (x, pct),
        textcoords="offset points",
        xytext=(0, 11),
        ha="center",
        fontsize=9,
        fontweight="bold" if status != "planned" else "normal",
        color="#1F2635",
    )
    ax.annotate(
        label,
        (x, pct),
        textcoords="offset points",
        xytext=(0, -34),
        ha="center",
        fontsize=7.4,
        color="#4A5160",
    )

# Current position marker
ax.axvline(x=9, color=ACTIVE, linestyle=":", linewidth=1.2, alpha=0.55)
ax.annotate(
    "YOU ARE HERE",
    (9, 90),
    textcoords="offset points",
    xytext=(0, 30),
    ha="center",
    fontsize=8.5,
    fontweight="bold",
    color=ACTIVE,
)

# Phase bands
ax.axhspan(85, 100, xmin=0.60, alpha=0.05, color=DONE)
ax.text(11.6, 88.5, "remaining:\nNEXT part 2 · context pillar ·\ncommercial skeleton · bets",
        fontsize=7.6, color="#4A5160", style="italic")

ax.set_ylim(25, 108)
ax.set_xlim(0.4, len(MISSIONS) + 0.6)
ax.set_ylabel("Project completion (%)", fontsize=10, color="#1F2635")
ax.set_xticks([])
ax.set_title(
    "AI Engineering Harness — Campaign Growth Chart\n"
    "Cumulative completion toward Cursor-class commercial product (10x plan, 23 missions)",
    fontsize=12,
    fontweight="bold",
    color="#1F2635",
    loc="left",
    pad=14,
)
for spine in ["top", "right"]:
    ax.spines[spine].set_visible(False)
for spine in ["left", "bottom"]:
    ax.spines[spine].set_color("#D8DCE3")
ax.grid(axis="y", color="#EEF0F3", linewidth=0.8)
ax.set_axisbelow(True)

legend_handles = [
    plt.Line2D([0], [0], marker="o", color="w", markerfacecolor=DONE, markersize=9, label="Closed & verified"),
    plt.Line2D([0], [0], marker="o", color="w", markerfacecolor=ACTIVE, markersize=9, label="In flight"),
    plt.Line2D([0], [0], marker="o", color="w", markerfacecolor=PLANNED, markersize=9, label="Planned (10x plan)"),
]
ax.legend(handles=legend_handles, loc="lower right", frameon=False, fontsize=8.5)

fig.tight_layout()
out = "/Users/user/untitled folder 2/harness/docs/briefs/mission19-growth-chart.png"
fig.savefig(out, facecolor=fig.get_facecolor())
print(f"saved {out}")
