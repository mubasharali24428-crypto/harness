# Comparative Analysis — Coding Side (Attempt 3)

Scope: 10 capabilities vs **Cursor, Windsurf, Copilot Workspace, Devin, Aider**.
Harness status drawn from the verified reality map: text-only chat with regex echo-bot AI; dsh agent execution in an allow-list sandbox with terminal stream + pause/resume/human-edit; read-only diff panel; no code editor, file tree, indexing, RAG, @refs, or checkpoints.

Format: **capability** — best-in-class one-liner | harness status | severity | build size.

---

**Codebase indexing + @refs** — Cursor embeds the whole repo and resolves `@file`/`@symbol` references with sub-second retrieval in any prompt | Harness: none — no indexing, RAG, or symbol refs; the echo-bot sees only text pasted into chat | **BLOCKER** | **L**

**Inline diff editing** — Windsurf Cascade lands edits in open buffers with per-hunk accept/reject where you're already working | Harness: read-only diff panel — can view generated diffs but not accept/reject per hunk or edit in place (human-edit exists only inside a paused dsh run) | **HIGH** | **M**

**Multi-file composer** — Copilot Workspace proposes a coordinated spec→plan→change-set spanning many files with per-file review | Harness: partial — the dsh agent already makes multi-file edits via sandbox commands, but review is one undifferentiated read-only diff, not a grouped per-file change-set | **MEDIUM** | **S**

**Tab completion** — Cursor Tab predicts multi-line next edits across files; Copilot owns the keystroke habit | Harness: none — no completion path of any kind | **HIGH** | **L**

**Live shell view** — Devin streams every executed command with output in real time | Harness: near parity — dsh terminal stream plus pause/resume/human-edit mid-run is arguably ahead of Devin's passive stream | **LOW** (strength) | **S** (polish only)

**Checkpoints / git snapshots** — Cursor checkpoints restore pre-agent workspace state; Aider auto-commits every step | Harness: none — no snapshotting; a bad agent run is unrecoverable except by hand | **HIGH** | **M**

**Per-step model selection** — Cursor switches models per request with visible pricing tradeoffs | Harness: none — single echo-bot "model"; the cost table exists in UI but is unwired, so spend is invisible | **MEDIUM** | **M**

**Terminal transparency** — Aider prints exactly which shell commands run and their diffs | Harness: parity-plus — streamed terminal + human-edit override beats most incumbents' opacity | **LOW** (strength) | **S**

**Rules files** — `.cursorrules` / Windsurf rules / Aider `CONVENTIONS.md` pin repo conventions so corrections stick | Harness: none — "brief" is static template extraction, not repo-scoped persistent instructions | **HIGH** | **S**

**Repo browser** — Cursor/Windsurf/Devin expose a navigable file tree alongside chat | Harness: none — no file tree or editor surface; orientation happens entirely in prose | **MEDIUM** | **M**

---

### Footnotes (outside the 10)
- **PR flow orphaned**: approved work cannot land as a reviewed pull request — Copilot Workspace, Devin, and Aider all close this loop (**HIGH**, **M**).
- **Cost table unwired**: folded into per-step model selection above; UI ships but reports nothing.
