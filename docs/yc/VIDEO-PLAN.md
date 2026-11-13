# YC Winter 2027: founder video and demo plan (Harness)

Prepared 2026-11-01 (PKT). Companion to [APPLICATION-PLAN.md](APPLICATION-PLAN.md). The
detailed demo shot list stays in [docs/demo/yc-demo-runbook.md](../demo/yc-demo-runbook.md);
this file is the plan around it.

Assumptions: the founder video shows every founder on camera (you, plus anyone named in
APPLICATION-PLAN §7); the demo is a separate file from the founder video; the YC length
guidance (about 1 minute, under 1 minute) is second-hand, so confirm it on the form.

## Part A: Founder video (about 60 seconds)

YC's guidance, as reported: about one minute, every founder on camera, nothing but the
founders talking, no demo footage, and no reading from a script. Speak from bullet points.

| Time | Who | Say (in your words) |
|---|---|---|
| 0:00–0:10 | Each founder, in turn | Name, role. "I'm [name], I [built / run / handle] [part]." |
| 0:10–0:25 | Founder 1 | What: "Harness lets a team approve every AI coding agent's plan before it runs, watch the run, and accept or reject each file it changes." |
| 0:25–0:40 | Founder 1 | Proof: "I've built it since August. It runs end to end in a sandbox on a real model, with file-level review and an audit log. No users yet." |
| 0:40–0:55 | Founder 1 or 2 | Why us: `[your specific edge, one sentence]`. Next: "In three months, three teams running it on real work." |
| 0:55–1:00 | Everyone | Close: names. |

Rules:
- **State the company in the first five seconds.**
- Do not claim users, revenue, or production use (see APPLICATION-PLAN §4).
- Do not say "should work", "almost", or "in beta". Say what runs today.
- Every founder on camera for their part, looking at the lens.
- Two takes at most. Pick the one that sounds like you.

Set-up: phone or laptop camera, landscape, at eye level; a window light or a lamp in front of
you (not behind); a plain wall; quiet room; no music; Do Not Disturb on. If a founder is
remote, screen-record your video call with everyone visible.

## Part B: Demo video (target 75–90 seconds)

YC's demo is optional and separate from the founder video. Its guidance, as reported: show the
real product right away, no logos or title cards at the start, one complete task, trim
delays and errors, and explain steps with a voice-over or captions. The full shot list
(about 2 minutes) is in the runbook. For the application, cut it to the storyboard below and
keep the long version as a backup.

| Time | Screen | Caption (on screen) |
|---|---|---|
| 0:00 | Web Command Center: type the goal, Propose plan, Approve plan | "Nothing runs until I approve the plan." |
| 0:12 | Start run; steps stream in on the Overview tab | "Runs in a sandbox. Every tool call and cost is recorded." |
| 0:25 | Pause. The status changes to Paused and the steps stop | "Pause stops the agent's process." |
| 0:35 | Team tab, Direction, Send direction | "I can redirect while it is paused." |
| 0:45 | Resume; new steps appear | "It resumes with my direction." |
| 0:55 | VS Code: Harness: Run on selection, plan dialog, Approve | "The same gate in the editor." |
| 1:05 | "Record this result" dialog: files, tests, cost, time | "Built from what happened, not from what the agent says." |
| 1:15 | calc.py: Alt+Y. shout.py: Alt+N. Final notice: 1 applied, 1 refused | "Each file is my decision. Only what I approve is written." |

Pick a goal that runs long enough to pause. Short goals finish in 13–26 seconds, before you can
click Pause. The `demo-repo` workspace starts empty, so the web goal must create files (the
runbook has one that does).

### Pre-flight (checked today, 2026-11-01)

- [x] Stack up: the compose services are running. Web answers on `http://localhost:3010` (200).
      Orchestrator `/health` returns `{"ok": true}`.
- [x] Automated rehearsal of the VS Code path passed today: run `run-032b992d17cb`,
      evidence `docs/briefs/evidence/w24/w24-window-c0b8125fa4b4.json` (verdict PASS, automated).
- [ ] Right before recording: run `scripts/demo/yc_demo_vscode.sh`, then sign in with
      `Harness: Sign in` before you start recording.
- [ ] Hide the terminal and any `.env` file. The OTP and provider keys must not be on screen.
- [ ] Use a test email, or hide the one you type.
- [ ] Do Not Disturb on, other windows closed, screen recording set to the Harness window.
- [ ] Open the web app at `http://localhost:3010` (not `127.0.0.1`; sign-in fails there).

### How to record

1. macOS: Cmd+Shift+5, "Record Selected Portion" or "Record Entire Screen", with the mic on.
2. Take the web part and the VS Code part as separate clips. Each one is a separate run.
3. Two takes at most. A failed run means start that take again; do not stitch around it.
4. Edit in iMovie or CapCut: trim the waits, add the captions from the table, export 1080p MP4.
5. Check the file size and format limit on the YC form before you export.

### Be honest in the video

- Say "the plan gate" for approval. The plan text is a fixed template right now, so do not read
  it aloud as the agent's reasoning (runbook note).
- Do not claim one review covers both runs. The web run and the VS Code run are separate runs.
- Pause kills the agent's process between steps. Say "a redirect is delivered when it resumes,"
  never "I changed it mid-step."

### If the live run fails on the day

Use the recorded evidence and label it. The W24 evidence has screenshots of each VS Code step:
`docs/briefs/evidence/w24/w24-window-*-01…07.png`. Put "recorded run, 2026-11-01" in the
caption. Do not present a recording as a live run.

## Part C: Interview demo (only if invited)

Run the same storyboard live, in this order, with the same pre-flight. Keep the web run to
about 40 seconds and show the review screen. If the interview is shorter than planned, skip the
VS Code part and show the review summary card instead.
