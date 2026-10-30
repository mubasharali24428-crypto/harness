# Migrating from Claude Code or Codex

You already have conventions written down: `CLAUDE.md`, `AGENTS.md`, Cursor rules
under `.cursor/rules/`, maybe `.codex/rules/`. Harness runs a governed agent on
the same repos, and the project **rules document** is where those conventions
belong — it is loaded for every run on that project and reviewed like any other
change.

This guide moves them across without copying a single secret.

## What maps to what

| You have | Harness equivalent | How it moves |
|---|---|---|
| `CLAUDE.md`, `AGENTS.md` | project rules document | `scripts/migrate_agent_config.py` |
| `.cursor/rules/*.mdc`, `.codex/rules/*.md` | same document (front-matter stripped) | the same tool |
| `~/.claude/settings.json` / Codex `config.toml` (model, approval mode) | deployment settings, not project rules | **not imported** — see "What does not move" |
| MCP servers (`claude_desktop_config.json`, `[mcp_servers]`) | `docs/integrations.md` + `docs/extensions.md` (deployment-level MCP config) | configured by the operator |

The rules document is one text blob capped at **64KB UTF-8** (`PUT
/api/v1/projects/{id}/rules`). That is why the importer merges rather than
creating one rule per file.

## 1. See what it would do

```sh
python3 scripts/migrate_agent_config.py --project-id <project-id>
```

This reads the current directory, prints the merged document and a JSON summary,
and sends **nothing**. Read the summary:

| Field | Means |
|---|---|
| `sources` | the files it found, in the order they are merged |
| `droppedSecretLines` | lines dropped because they looked like credentials |
| `truncatedAt` | a file that did not fit under 64KB — dropped whole, named here |
| `bytes` | the document size the API will see |

If `droppedSecretLines` is not 0, open those files and remove the secrets there
too: the importer protects the rules document, not your repository.

## 2. Apply it

```sh
HARNESS_TOKEN=<session token> \
  python3 scripts/migrate_agent_config.py --project-id <project-id> --apply
```

The token needs write access to the project (admin or engineer in the owning
team). `--token-env` names a different variable if `HARNESS_TOKEN` is taken in
your shell. `--base-url` defaults to `http://127.0.0.1:8001`.

What the tool does, in order:

1. `GET /api/v1/projects/{id}/rules` to read what is there now;
2. merge the sources into a fenced block:

   ```markdown
   <!-- harness:migrated-agent-rules -->
   ## From AGENTS.md

   …your text…

   ## From CLAUDE.md

   …your text…
   <!-- /harness:migrated-agent-rules -->
   ```

3. `PUT` the result.

Everything **outside** the block is your own text and is preserved. Running the
tool again replaces the block in place — it never duplicates it — so it is safe
to re-run after editing `AGENTS.md`.

## What is deliberately dropped

* Lines that look like credentials: `sk-…`, `ghp_…`, `github_pat_…`, `xox?-…`,
  `AKIA…`/`ASIA…`, `hrn_…`/`hsc_…` (Harness tokens), `-----BEGIN … PRIVATE
  KEY-----` blocks, and `key = value` / `token: …` shapes.
* Cursor front-matter (`description:`, `globs:`) — editor metadata, not
  instructions.
* Empty files, and anything beyond the 64KB cap (whole files, never mid-file).

## What does not move

* **Model and provider choice.** Harness pins the model configured for the
  deployment; there is no per-run downgrade or silent fallback (`AGENTS.md`
  no-downgrade rule). Set it in the deployment's environment, not in rules.
* **Approval modes.** Harness always requires a human-approved plan before a run;
  an "auto-accept edits" setting has no equivalent by design.
* **Hooks and shell wrappers** from Claude Code settings. The sandbox, egress
  allow-list and policy gate replace them; see `docs/agents.md` and
  `docs/audit-and-events.md`.
* **Skills / slash commands.** Author them as team skills (reviewed, versioned)
  instead of copying prompt files.

## After the import

1. Review the merged document in the project's settings page — it is text a human
   should read once, not a black box.
2. Run one real task and check the plan the agent proposes reflects the imported
   conventions.
3. Keep `AGENTS.md` as the source of truth in the repo and re-run the importer
   when it changes; the block is deterministic, so the diff is the change.
