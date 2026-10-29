#!/usr/bin/env bash
# Published TypeScript dist must be rebuildable from the committed sources.
#
# Why this exists: 2026-10-29 found dist/ two generations behind src/ in git —
# the W15 spec-drift commit regenerated the hand-written clients, but nothing
# rebuilt the compiled artifact, so anyone installing this package got typings
# without the routing, attestation or finish surfaces the sources already had
# (289 lines of the published API were missing).
#
# Exit 0: dist matches a fresh build. Exit 1: stale (diff printed).
# Exit 2: cannot check (node deps missing) — loud, never a silent pass.
set -euo pipefail

cd "$(dirname "$0")/../.."

if ! command -v npx >/dev/null 2>&1; then
  echo "npx is not on PATH; install Node.js (CI pins 22) to check the TS dist" >&2
  exit 2
fi
if [ ! -d clients/ts/node_modules/typescript ]; then
  echo "clients/ts dependencies are not installed; run: npm ci -C clients/ts" >&2
  exit 2
fi

fresh="$(mktemp -d)"
trap 'rm -rf "$fresh"' EXIT

(cd clients/ts && npx tsc -p tsconfig.build.json --outDir "$fresh")

# --strip-trailing-cr: tsc emits the platform's EOL, so a checkout with
# core.autocrlf must not read as drift.
if ! diff -r --strip-trailing-cr clients/ts/dist "$fresh"; then
  cat >&2 <<'MSG'
clients/ts/dist is stale: `npm run build -C clients/ts` and commit the result.
MSG
  exit 1
fi
echo "clients/ts dist matches a fresh build ($(git ls-files clients/ts/dist | wc -l | tr -d ' ') tracked files)."
