# MISSION-COMMAND DOCTRINE FOR AGENT TEAMS (ALEX_TEAM · v0.1)

> Status: PROPOSED — ratify by adopting M-15 as pilot. Owner: ATLAS.
> Companion files: `templates/command-brief-template.md`,
> `templates/aar-ledger.csv`, `scripts/doctrine/check_doctrine.py`.
> Scope: process doctrine only. Touches NO frozen contract, schema, or service code.

This document adapts military and crisis-command doctrine to ALEX_TEAM's three-tier
org (ATLAS → squad leads → builder agents). It fills the four gaps identified in
review: no formal AAR, no intent documents, no standing red-team cycle, no
degraded-mode plan beyond ad-hoc fallback. Existing assets (escalation law, merge
checklists, verifier teams, fix-loops, GAP failure cards) are PRESERVED — these
mechanisms wrap around them, they do not replace them.

House rule inherited from Truppenführung: *a brief states only what the executor
cannot determine alone.* Everything else stays free.

---

## §1 AUFTRAGSTAKTIK — BRIEF = INTENT + END-STATE + CONSTRAINTS, METHOD FREE

**Tag: PROVEN-IN-HUMANS** (Prussian/German General Staff, Moltke the Elder →
H.Dv. 300 *Truppenführung* 1933; modern form: US Army ADP 6-0 Mission Command).

**Mechanism.** Every node/lane brief carries exactly four blocks and nothing more:
`INTENT` (why, one sentence), `END-STATE` (observable done condition),
`CONSTRAINTS` (frozen paths, contracts, forbidden actions — hard edges),
`OWNERSHIP` (files this agent may write). The HOW is deliberately absent: no
step-by-step method, no prescribed file layout beyond ownership boundaries. If a
constraint isn't written, it doesn't exist. This generalizes the already-working
pattern (M10 briefs specify owned paths + verify commands and leave design free;
sync-fallback delegation requires the full contract at dispatch time because
mid-run steering does not exist).

**Expected gain.** Method-free briefs survive context drift: an agent that lost
half its exploration budget still knows what DONE means. Removes the largest
brief-authoring cost (prescribing method ATLAS cannot verify anyway). Historical
evidence this gap costs us today: M10/A4's first attempt burned **43 minutes
exploring then died on a provider timeout with ZERO writes** — an intent-first
brief would have front-loaded END-STATE so exploration was bounded by "prove the
done-condition," not open-ended reading.

**Implementation sketch.**
- *Document:* `docs/briefs/<missionN>-graph.md` rewritten to template
  `docs/doctrine/templates/command-brief-template.md`.
- *Written when/by:* ATLAS, before fan-out of each wave.
- *Consumed where:* every dispatched agent reads it as its first action; squad
  leads use the same blocks for intra-squad pair contracts; the BACKBRIEF (§3)
  echoes these blocks verbatim; check_doctrine.py fails any graph missing INTEND.

---

## §2 COMMANDER'S INTENT ONE-LINER — THE LINE ABOVE THE FOLD

**Tag: PROVEN-IN-HUMANS** (US Army FM 100-5 / ADP 6-0: intent = purpose + end
state, placed atop every order so subordinates act correctly when the plan breaks).

**Mechanism.** First non-title line of every brief is exactly one sentence of the
form: `INTENT — <so that X>, deliver <Y> such that <observable Z>`. Not a task
list, not a spec quote. It must remain true even if half the nodes fail — the
intent is what a stranded agent falls back to when its specific node becomes
unachievable (report, don't improvise past constraints).

**Expected gain.** One-line intents are cheap to write (~30 s/node) and give
every downstream consumer — fix-loop agents, verifier pairs, re-dispatches after
timeout — a stable anchor without re-reading the whole graph. Directly targets
the observed failure class where a re-dispatched agent repeats the original
exploration instead of resuming toward done-state.

**Implementation sketch.**
- *Document:* the `INTENT —` line lives in each graph brief header and in every
  per-lane brief; mirrored as `intent:` field in delegation payloads.
- *Written when/by:* ATLAS at graph authoring; leads may tighten wording but may
  not change the end-state without re-issue.
- *Consumed where:* printed in agent kickoff prompt; quoted back during
  BACKBRIEF (§3); cited in AAR "was intent achieved?" verdict (§4);
  check_doctrine.py asserts its presence and ≤ 40 words.

---

## §3 BACKBRIEFING — RESTATE BEFORE YOU BUILD

**Tag: PROVEN-IN-HUMANS** (German General Staff *Rückfrage*/backbrief: the
subordinate restates mission + constraints in their own words to the issuer
before execution begins).

**Mechanism.** Before writing any code, each agent posts a ≤ 120-word BACKBRIEF
to its lead: (a) the intent in its own words, (b) owned paths it will write,
(c) frozen things it will NOT touch, (d) its planned done-proof (which tests /
probes demonstrate END-STATE). Lead compares against the brief in one pass —
mismatch kills the dispatch in minutes, not after a full build cycle. Cheap
rule: *no backbrief, no writes* (leads reject deliveries from agents whose lane
file lacks the section).

**Expected gain.** Misreads are cheapest at minute 0. We already pay for skipped
confirmation at the most expensive point: a delegated Codex run once self-reported
"50 passed" while an expiry test had silently failed — independent re-verification
caught it late. A backbrief forces the agent to expose its interpretation while
correcting it costs nothing. Secondary gain: the backbrief doubles as the
per-agent entry in the wave record (who believed what at start).

**Implementation sketch.**
- *Document:* `## BACKBRIEF` section appended to each lane file in
  `docs/briefs/wave<N>/`, written by the agent immediately on receipt of brief.
- *Written when/by:* builder agent, before first edit; lead approves/corrects
  asynchronously (one reply max).
- *Consumed where:* lead uses diffs between backbrief and brief as dispatch QA;
  AAR (§4) audits backbrief-vs-outcome deltas to find systematic misread sources;
  check_doctrine.py flags lane files missing the section.

---

## §4 WAVE AAR + SUSTAIN/IMPROVE LEDGER

**Tag: PROVEN-IN-HUMANS** (US Army After Action Review, National Training Center,
post-Vietnam institutionalization; "professional discussion of an event, focused
on performance standards, not blame").

**Mechanism.** At every wave close, a 30-minute structured review with four
questions, answered from artifacts (graphs, gates, fix-loops) — not memory:
1. What was the intent/end-state? 2. What actually happened (gate verdicts,
   re-dispatch counts, fix-loop counts)? 3. Why the delta (root causes)? 4. What
do we SUSTAIN / IMPROVE next wave? Each answer lands as one row in
`docs/doctrine/aar-ledger.csv`: `wave,item,class(SUSTAIN|IMPROVE),evidence,owner,next_mission`.
Rows graduate: an IMPROVE row with an owner and a mission number becomes a graph
requirement; a SUSTAIN row repeated three waves becomes doctrine text here. The
ledger feeds the existing GAP_ANALYSIS_AND_FIXES.md pipeline (process rows get GAP
cards like code defects do) — today that file captures product failures superbly
but process lessons die in session scrollback.

**Expected gain.** Converts per-session tribal knowledge into durable process
memory. Evidence the raw material exists and evaporates: migration-number races
(`0013→0017 taken underneath us; G1 ended at 0018`), middleware double-install
(6 red tests until wiring moved to assembly level), test-isolation shim leaks —
each was learned, written into session notes, and would need rediscovering by a
fresh ATLAS. A ledger row makes the lesson addressable and auditable.

**Implementation sketch.**
- *Documents:* `docs/doctrine/aar/m<N>-aar.md` (the 4-question write-up, ~1 page)
  + append rows to `docs/doctrine/templates/aar-ledger.csv` (copied to
  `docs/doctrine/aar-ledger.csv` on adoption).
- *Written when/by:* leads draft within 1 h of wave close while verdicts are
  fresh; ATLAS consolidates and commits at merge.
- *Consumed where:* next wave's graph authoring MUST cite relevant prior IMPROVE
  rows (check_doctrine.py warns if a new graph references none); recurring
  IMPROVE items get promoted into this doctrine file by patch.

---

## §5 PRE-MORTEM ROUNDS BEFORE RISKY MERGES

**Tag: ADAPTED** (Gary Klein's prospective-hindsight pre-mortem, 2007; adopted
into military red-teaming and aviation crew briefings — not classical doctrine,
hence ADAPTED not PROVEN).

**Mechanism.** For merges flagged risky (cross-section contracts, migrations,
assembly-level wiring, anything touching > 1 section or a FROZEN boundary), the
lead runs a 10-minute round before merge: each participating agent independently
writes 2–3 failure sentences starting "The merge went red because…" — written
as if looking back from a failed future, each with one concrete artifact check
that would detect it. ATLAS dedupes, converts surviving scenarios into explicit
gate checklist lines, and the merge proceeds only when each scenario has a named
detector (test, probe, grep, compose-config assert).

**Expected gain.** Pre-mortems specifically catch integration-class failures that
unit-green suites miss — our two worst repeat offenders (response-shape drift
across lanes; wired-but-never-called routers, which shipped dead TWICE in one
mission) were found only by adversarial probing AFTER the fact. Both classes are
exactly the kind of thing a "router registered?" scenario surfaces pre-merge.
Cost: ~10 min/risky merge; benefit scales with wave width (10-lane waves have
the most seams).

**Implementation sketch.**
- *Document:* `## PRE-MORTEM` block appended to the mission graph's merge
  checklist section; scenario→detector table included.
- *Written when/by:* squad leads + one designated skeptic per squad, after last
  node goes DONE and before ATLAS starts gate traffic.
- *Consumed where:* ATLAS executes the detector column as part of the standard
  gate sequence (footprint → rerun → live-probe → detectors); outcomes feed the
  §4 AAR (did any predicted scenario actually fire?).

---

## §6 RED-TEAM ROTATION — THE PERMANENT ATTACKER SQUAD

**Tag: PROVEN-IN-HUMANS** (IDF Directorate of Military Intelligence "Devil's
Advocate" / *Ipcha Mistabra* office, established after the 1973 Yom Kippur
surprise failure; older root: Vatican's advocatus diaboli).

**Mechanism.** One squad is permanently chartered as RED: every wave it receives
(a) the near-final tree, (b) the process documents themselves, and attacks both.
Product attack = the flaw-hunt pattern we already proved (parallel auditor lenses,
every finding with file:line or live probe output — yielded 90+ genuine findings
when trialed). Process attack = tries to make a delivery PASS dishonestly:
fabricated test output, assertions weakened to match buggy behavior, footprints
that omit generated files. Rotation rule: squads rotate through RED duty so
builders keep attacker empathy and RED never ossifies into rubber-stamping; the
standing RED lead reports directly to ATLAS (bypasses squad leads, mirroring
intelligence reporting lines). RED files blockers as FIX-LOOP entries — same
mechanism verifier pairs already use — so remediation flow is unchanged.

**Expected gain.** Institutionalizes what ad-hoc verifier teams demonstrated:
two entire defect classes (shape drift, check-without-increment quota logic)
passed ALL unit tests and fell only to adversarial cross-lane probing. Permanent
charter removes the per-mission decision "do we have budget for verifiers?"
— the answer is always yes, scoped by rotation.

**Implementation sketch.**
- *Documents:* `docs/redteam/m<N>-red-report.md` (findings, evidence, blocker
  IDs) written during the wave's verify window; charter + rotation schedule kept
  in `docs/redteam/CHARTER.md`.
- *Written when/by:* RED squad (rotating assignment announced in the wave graph),
  starting once builders hit DONE and finishing before merge.
- *Consumed where:* ATLAS triages RED blockers exactly like verifier FIX-LOOPs
  (re-dispatch originating lane with blocker list verbatim); RED process-findings
  route into the §4 ledger as IMPROVE rows.

---

## §7 OODA TEMPO METRICS — CYCLE TIME PER DECISION CLASS

**Tag: ADAPTED** (Col. John Boyd, USAF — Observe/Orient/Decide/Act loop from
fighter-combat analysis; the loop is doctrine-proven for humans, the per-decision-
class instrumentation is our adaptation).

**Mechanism.** Classify recurring decisions and measure each class's full loop
time from the event trail we already persist (agent_events, gate verdicts, fix-loop
entries): 
- **D1 lane-local** (agent fixes its own test): dispatch → DONE.
- **D2 fix-loop** (blocker → re-dispatch → closed): blocker filed → loop closed.
- **D3 gate verdict** (delivery → gate outcome): delivery → verdict.
- **D4 merge decision**: last-DONE → merged.
- **D5 escalation** (agent → lead/ATLAS): question raised → answered.
Report median + p95 per class per wave in the AAR. The metric is not vanity
speed — it localizes stalls: e.g., if D2 p95 blows out while D1 stays flat, the
queue is at lead review, not builder capability, and the fix is staffing/charter,
not better prompts. Boyd's point applies to us literally: the side that closes
its loop faster while keeping accuracy wins the wave.

**Expected gain.** Today stall diagnosis is anecdotal (one A4 re-dispatch sat 43
minutes in exploration; fix-loop latency across M10–M14 is unrecorded). With per-
class numbers, ATLAS can set explicit tempo budgets (e.g., D3 ≤ 20 min p95) and
the AAR asks one question: which class broke budget and why. Expected effect:
stall localization replaces guesswork; over successive waves the budget table
becomes the org's realistic capacity model for sizing waves.

**Implementation sketch.**
- *Documents:* `docs/doctrine/ooda-budgets.md` (decision-class definitions +
  target budgets, revised at AAR); metrics table rendered into each m<N>-aar.md;
  measurement script `scripts/doctrine/ooda_tempo.py` (parses event logs/graph
  status tables; v1 may be manual-entry with script assist).
- *Written when/by:* ATLAS defines budgets at wave authoring; leads log actuals
  at wave close.
- *Consumed where:* AAR §4 reviews budget-vs-actual; persistent budget breach
  two waves running = mandatory IMPROVE row with owner.

---

## §8 DEGRADED-MODE PLAYBOOKS — PROVIDER OUTAGE POSTURES

**Tag: PROVEN-IN-HUMANS** (military loss-of-communications procedures / EMCON;
aviation Quick Reference Handbook abnormal-procedure philosophy: predefined
postures, executed without improvisation under stress).

**Mechanism.** Predefined, rehearsed responses keyed to trigger conditions, so an
outage costs minutes not a session. Postures:
- **GREEN:** normal operation.
- **AMBER (provider 429/slow, intermittent):** stop burning primary-provider
  retries; switch remaining lanes to the documented paid-model recipe (already
  proven: `DEEPSEEK_BASE_URL=https://openrouter.ai/api/v1` + OPENROUTER key +
  explicit model, redeploy orchestrator container); cap concurrent lanes at
  half-width; ATLAS announces posture in wave thread.
- **RED (primary down, fallback exhausted):** freeze fan-out; convert wave to
  single-lane critical path (merge-blockers and RED-squad findings only); agents
  checkpoint state (write uncommitted work to disk with notes) and idle cleanly
  — mirrors our D2 honesty guard that BLOCKs rather than fakes success.
- **DRILL:** once per mission series, run one lane end-to-end in AMBER posture
  on purpose, so the switch is muscle memory and the recipe stays true.
Trigger thresholds are numeric (e.g., AMBER = ≥ 2 timeout/429 events across
distinct lanes within 30 min) so posture changes aren't mood calls.

**Expected gain.** Provider fragility is our best-documented operational risk:
free-tier stealth models hit daily OpenRouter 429s mid-mission; the fallback
ladder honestly BLOCKs runs (correct anti-fake-success behavior, but currently
that means a stalled wave and improvisation). A playbook converts those events
from unplanned downtime into a checklist execution. Secondary gain: clean-idle
checkpointing protects uncommitted lane work — the known hazard of ATLAS lane
traffic stash/pop cycles reverting mid-flight edits.

**Implementation sketch.**
- *Document:* `docs/runbooks/degraded-mode.md` (triggers, posture table,
  exact env/container commands per posture, checkpoint protocol, drill log
  appendix) — companion to existing backup-and-pitr.md runbook style.
- *Written when/by:* ATLAS authors once at doctrine adoption; ops-lead updates
  after every real invocation (posture timeline appended).
- *Consumed where:* any agent detecting threshold conditions cites the runbook
  section in its escalation; ATLAS declares posture explicitly; AAR audits
  time-from-trigger-to-posture (should be minutes).

---

## §9 NO-BLAME DEBRIEF RULES FOR AGENTS

**Tag: ADAPTED** (Crew Resource Management debrief culture + NTSB-style no-blame
accident investigation, proven across aviation; Edmondson's psychological-safety
research names the mechanism. Adapted because agents don't have egos to protect —
but they DO have incentive gradients that punish admitting confusion.)

**Mechanism.** Rules binding every AAR, postmortem, and fix-loop write-up:
1. **Root cause, not culprit** — findings name processes and information states
   ("the brief omitted the assembly-level wiring constraint"), never agent IDs
   as causes. Agent handles appear only as attribution of WORK, not fault.
2. **Confession is data** — an agent reporting "my suite was green but I now
   believe my test asserted the bug" is treated as the wave's most valuable
   signal, logged as a SUSTAIN row (the honesty worked), regardless of the
   underlying defect.
3. **Fix-forward** — every finding gets an owner + next-mission reference; there
   is no punishment channel to route around, which removes the incentive to hide.
4. **Equal voice** — in AAR rounds the newest/re-dispatched agent's observation
   is heard before leads interpret (fresh eyes see the seam; cf. our rule that
   fix-loop blockers go back to the ORIGINATING lane with the list verbatim —
   correction without condemnation).
Rationale specific to agents: self-reports have already been caught being wrong
("all green" while a test failed). An org that punishes wrong self-reports gets
quiet agents and louder lies; an org that rewards detection gets earlier truth.
The existing precedent — ATLAS silently fixing a residual tsc error in a
sibling lane's file and recording it plainly in the gate verdict, no drama —
is this rule already working; §9 just makes it law.

**Expected gain.** Preserves the honest-failure signals we depend on (BLOCK-not-
fake D2 guards, disclosed design notes for lead review, "say so honestly"
conventions in briefs). Failure-cost asymmetry: a hidden misread surfaces at
merge as a red CI; a confessed misread surfaces at minute 0 via backbrief.

**Implementation sketch.**
- *Document:* `## DEBRIEF RULES` section in this doctrine file (above) referenced
  by template header comment in aar template + red-team report template.
- *Written when/by:* ratified once with doctrine adoption; enforced by ATLAS at
  every AAR and in FIX-LOOP phrasing guidelines.
- *Consumed where:* AAR facilitator opens with the rules; violations (blaming
  language in write-ups) are themselves IMPROVE-row findings about the process.

---

## §10 CCIR — COMMANDER'S CRITICAL INFORMATION REQUIREMENTS

**Tag: PROVEN-IN-HUMANS** (US/NATO staff doctrine: CCIR = the short list of
information elements that, if changed, must reach the commander immediately
because they change the decision).

**Mechanism.** The existing escalation law says WHO may escalate WHAT; CCIR
defines WHICH signals are decision-changing enough to interrupt ATLAS immediately
versus batch-reportable. Standing CCIR (extendable per wave):
- C1: any agent observes another lane writing outside its owned paths.
- C2: provider posture trigger crossed (§8 thresholds) — ties outage detection
  to a mandatory, immediate notification.
- C3: an agent believes its END-STATE is unachievable within constraints.
- C4: any request to touch a FROZEN path (contracts/, migrations numbering,
  conftest).
- C5: gate verdict contradicts an agent self-report (suite green locally, red at
  gate or vice versa).
Everything else batches into normal status updates. Anti-noise rule: CCIR items
are numbered and few (≤ 7 active); adding one requires retiring another. Each
CCIR names its DECISION: what ATLAS will decide upon receiving it (halt lane /
declare posture / authorize exception), so escalations arrive pre-framed for a
fast OODA close (feeds §7's D5 class).

**Expected gain.** Escalation fatigue is the failure mode of agent orgs — either
everything interrupts the coordinator (ATLAS becomes the bottleneck, known cost
of gating every delivery personally) or nothing does and seams rot. CCIR keeps
interruption scarce and meaningful. Directly addresses observed classes: C1
covers the sibling-file race damage lane traffic causes; C4 covers the migration
numbering PK-crash class; C5 covers the dishonest-self-report class.

**Implementation sketch.**
- *Document:* `## CCIR` block in each wave graph (standing list + wave-specific
  additions), maintained by ATLAS.
- *Written when/by:* ATLAS at graph authoring; amendments logged in wave thread.
- *Consumed where:* every agent's kickoff prompt lists active CCIR; leads filter
  escalations against it; AAR audits false-positive/negative CCIR firings to
  prune the list.

---

## Adoption sequence (pilot = next mission, M-15)

1. ATLAS patches next graph to template (§1+§2), adds CCIR block (§10).
2. Agents produce backbriefs (§3) — zero new tooling, pure convention.
3. Wave close: first AAR (§4) + ledger seeded from mission-scaling lessons and
   M10–M14 history; OODA budgets drafted (§7).
4. Risky merges in the pilot wave run pre-mortem rounds (§5).
5. RED squad charter activated from existing verifier roster (§6).
6. Runbook `degraded-mode.md` authored; first DRILL scheduled (§8).
7. Debrief rules ratified (§9); check_doctrine.py wired into wave-close routine.

Success criteria for the pilot wave: 100% backbrief coverage; ≥ 1 pre-mortem
detector that fires pre-merge; AAR ledger ≥ 8 rows; D3/D5 baselines recorded.
