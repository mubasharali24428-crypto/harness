#!/bin/bash
# Q0 pinned baseline: every suite against ONE revision, in a detached worktree ($Q/wt),
# so concurrent commits on the main checkout cannot mix revisions into the counts.
Q="$(cd "$(dirname "$0")" && pwd)"; R="$Q/wt"; OUT="$Q/pinned"
mkdir -p "$OUT/junit" "$OUT/logs"; : > "$OUT/summary.tsv"
rec() { echo -e "$1\t$2\t$(date -u +%FT%TZ)" >> "$OUT/summary.tsv"; }
cd "$R"; echo "PINNED $(git rev-parse HEAD)" > "$OUT/revision.txt"
PP="$R/contracts/src:$R/section1/src:$R/section4/src"

# make gate's non-test half, verbatim targets
for t in static-gates lint lint-mcp lint-language; do make $t > "$OUT/logs/$t.log" 2>&1; rec $t "exit=$?"; done

py() { # name dir python
  ( cd "$R/$2" && PYTHONPATH="$R/$2/src:$PP" PYTHONDONTWRITEBYTECODE=1 "$3" -m pytest tests -q -rs \
      --junitxml="$OUT/junit/$1.xml" ) > "$OUT/logs/$1.log" 2>&1; rec "$1" "exit=$?"
}
for s in section1 section2 section3 section4 section5 section6 section8 section10 section11; do py $s $s ./.venv/bin/python; done
py mcp mcp ./.venv/bin/python
py language packages/language ./.venv/bin/python

# A5 packages (venvs from the first run; add the local siblings their tests need)
V="$Q/venvs"
"$V/packages-extensions/bin/pip" -q install -e "$R/integrations/connectors/wiki[dev]" > "$OUT/logs/fix-ext.log" 2>&1; rec fix-ext-wiki-dep "exit=$?"
"$V/packages-ops/bin/pip" -q install -e "$R/packages/telemetry[dev]" -e "$R/packages/ops[dev]" > "$OUT/logs/fix-ops.log" 2>&1; rec fix-ops-telemetry-dep "exit=$?"
"$V/integrations-connectors-wiki/bin/pip" -q install starlette > "$OUT/logs/fix-wiki.log" 2>&1; rec fix-wiki-starlette "exit=$?"
for p in packages/extensions packages/ops packages/telemetry integrations/connectors/wiki integrations/connectors/workflows; do
  n=$(echo "$p" | tr '/' '-')
  ( cd "$R/$p" && PYTHONPATH="src:$R/contracts/src" PYTHONDONTWRITEBYTECODE=1 "$V/$n/bin/python" -m pytest tests -q -rs \
      --junitxml="$OUT/junit/$n.xml" ) > "$OUT/logs/$n.log" 2>&1; rec "$n" "exit=$?"
done

( PYTHONPATH="$PP" PYTHONDONTWRITEBYTECODE=1 section4/.venv/bin/python -m pytest integration -q -rs \
    --junitxml="$OUT/junit/integration.xml" ) > "$OUT/logs/integration.log" 2>&1; rec integration "exit=$?"
for d in scripts/eval scripts/ops scripts; do
  n="scripts-$(basename $d)"; [ "$d" = scripts ] && n=scripts-root
  ( PYTHONPATH="$PP" PYTHONDONTWRITEBYTECODE=1 section4/.venv/bin/python -m pytest $d/test_*.py -q -rs \
      --junitxml="$OUT/junit/$n.xml" ) > "$OUT/logs/$n.log" 2>&1; rec "$n" "exit=$?"
done
( cd clients/python && PYTHONPATH=".:$R/contracts/src" "$V/clients-python/bin/python" -m pytest tests -q -rs \
    --junitxml="$OUT/junit/clients-python.xml" ) > "$OUT/logs/clients-python.log" 2>&1; rec clients-python "exit=$?"
for c in ts vscode; do ( cd clients/$c && npm test --silent ) > "$OUT/logs/clients-$c.log" 2>&1; rec "clients-$c" "exit=$?"; done

( cd section7 && npx tsc --noEmit ) > "$OUT/logs/fe-tsc.log" 2>&1; rec fe-tsc "exit=$?"
( cd section7 && npx vitest run --reporter=default --reporter=junit --outputFile.junit="$OUT/junit/section7-vitest.xml" ) > "$OUT/logs/fe-vitest.log" 2>&1; rec fe-vitest "exit=$?"
( cd section7 && NEXT_TELEMETRY_DISABLED=1 npx next build ) > "$OUT/logs/fe-build.log" 2>&1; rec fe-build "exit=$?"
make check-ts-dist > "$OUT/logs/check-ts-dist.log" 2>&1; rec check-ts-dist "exit=$?"
( PYTHONPATH="$PP" section4/.venv/bin/python -m pytest section4/tests/unit/test_program_docs.py -q ) > "$OUT/logs/program-docs.log" 2>&1; rec test_program_docs "exit=$?"
echo DONE >> "$OUT/summary.tsv"
