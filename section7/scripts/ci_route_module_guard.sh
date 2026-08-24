#!/bin/sh
# ci_route_module_guard.sh — Mission 15 G6a static guards (Squad ALPHA).
#
# Enforces two architectural laws that until now lived only in convention:
#
#   routes     ROUTE-MODULE LAW (W4 carryover #4): a Next.js route module
#              (section*/app/**/page.tsx) may contain EXACTLY ONE export —
#              `export default`. Any non-default export (metadata, helpers,
#              consts, types) breaks the App Router's index-signature
#              expectation and is the violation class that bit us in Wave 4.
#
#   isolation  SECTION-ISOLATION LAW (M13 audit; AGENTS.md layout rules):
#              sections depend ONLY on contracts/ (imported as the
#              harness_contracts package or @contracts/* TS mirror). Code in
#              sectionN must never import code from sectionM (N != M) —
#              neither relatively (../sectionM/...) nor package-style.
#              Self-references within a section are fine; contracts/ and
#              integration/ are outside this guard by design (integration
#              checkpoint tests exist precisely to touch several sections).
#
# Usage:      ci_route_module_guard.sh routes|isolation
# Exit codes: 0 = clean, 1 = violations found, 2 = usage/internal error.
#
# Called by:  make guard-routes / make guard-isolation (Makefile is the only
#             entry point CI uses — parity contract, docs/ci-gates.md).
# POSIX sh throughout: must run identically under macOS /bin/sh and the
# ubuntu-latest (dash) runner. Pure text scanning; no Node/Python needed.
# Trip-test lessons baked in: violation lines iterate via `while IFS= read`
# on a temp-file fd (no word-splitting on spaces, no pipe subshell eating
# fail_count); every helper is DEFINED before first use (sh runs top-down).

set -u

usage() {
	echo "usage: $0 routes|isolation" >&2
	exit 2
}

[ $# -eq 1 ] || usage
MODE=$1
case "$MODE" in
routes | isolation) ;;
*) usage ;;
esac

# Repo root = two levels above this script (script lives in section7/scripts/).
ROOT=$(CDPATH='' cd -- "$(dirname -- "$0")/../.." && pwd) || exit 2
cd "$ROOT" || exit 2

fail_count=0
TMP=$(mktemp) || exit 2
trap 'rm -f "$TMP"' EXIT INT TERM

note_fail() { # note_fail <file> <line> <detail>
	echo "GUARD[$MODE] FAIL: $1:$2: $3"
	fail_count=$((fail_count + 1))
}

# Iterate a grep "file:line:content" blob WITHOUT word splitting: exactly one
# row per loop pass, executed in the CURRENT shell (fd redirect, not a pipe)
# so fail_count mutations survive. Callback gets the raw row as $1.
each_line() { # each_line <blob> <callback-fn>
	printf '%s\n' "$1" >"$TMP"
	while IFS= read -r ROW; do
		[ -n "$ROW" ] || continue
		"$2" "$ROW"
	done <"$TMP"
}

split_grep_row() { # split_grep_row <row> -> F, LN, BODY
	F=${1%%:*}
	_rest=${1#*:}
	LN=${_rest%%:*}
	BODY=${_rest#*:}
}

parse_routes_hit() { # <grep row>
	split_grep_row "$1"
	note_fail "$F" "$LN" "non-default export in route module: ${BODY}"
	echo "                route modules (app/**/page.tsx) may only 'export default'"
	echo "                (Next index-signature law, W4 carryover — move helpers/metadata"
	echo "                into a non-route module and import them, default-exporting only)."
}

parse_isolation_hit() { # <grep row>
	split_grep_row "$1"
	own=$(printf '%s' "$F" | cut -d/ -f1)
	ref=$(printf '%s\n' "$BODY" | grep -oE 'section[0-9]+' | head -n 1)
	# Self-references (a section naming its own tree) are legal.
	[ "$ref" = "$own" ] && return 0
	note_fail "$F" "$LN" "cross-section import: ${BODY}"
	echo "                '$own' references '$ref' — sections may depend ONLY on contracts/"
	echo "                (harness_contracts / @contracts/*). M13-audit isolation law:"
	echo "                share schemas via contracts, never import sibling-section code."
}

# ------------------------------------------------------------------ routes ---
if [ "$MODE" = routes ]; then
	pages=''
	for d in section*/app; do
		[ -d "$d" ] || continue
		found=$(find "$d" -type f -name 'page.tsx')
		if [ -n "$found" ]; then
			if [ -n "$pages" ]; then
				pages="$pages
$found"
			else
				pages="$found"
			fi
		fi
	done

	if [ -z "$pages" ]; then
		echo "GUARD[routes] WARN: no section*/app/**/page.tsx found — nothing to check."
		exit 0
	fi

	total=$(printf '%s\n' "$pages" | grep -c '^')
	# Every export statement as "file:line:content" ...
	exports=$(printf '%s\n' "$pages" | xargs grep -nE '^[[:space:]]*export[[:space:]]' || true)
	# ...then drop the one legal form: `export default ...`
	hits=''
	if [ -n "$exports" ]; then
		hits=$(printf '%s\n' "$exports" | grep -vE '(^|[[:space:]:])default([[:space:]:]|$)' || true)
	fi

	if [ -n "$hits" ]; then
		each_line "$hits" parse_routes_hit
		echo ""
		echo "GUARD[routes] RESULT: $fail_count violation(s) across $total route module(s)."
		exit 1
	fi

	echo "GUARD[routes] OK: $total route module(s) checked — every app/**/page.tsx is default-export-only."
	exit 0
fi

# -------------------------------------------------------------- isolation ----
files=''
sections_scanned=0
for sdir in section*/; do
	[ -d "$sdir" ] || continue
	list=$(find "$sdir" \
		\( -name node_modules -o -name .next -o -name .venv -o -name venv \
		-o -name __pycache__ -o -name dist -o -name build -o -name .ruff_cache \) -prune \
		-o -type f \( -name '*.py' -o -name '*.ts' -o -name '*.tsx' \
		-o -name '*.js' -o -name '*.jsx' -o -name '*.mjs' -o -name '*.cjs' \) -print)
	[ -n "$list" ] || continue
	sections_scanned=$((sections_scanned + 1))
	if [ -n "$files" ]; then
		files="$files
$list"
	else
		files="$list"
	fi
done

total=$(printf '%s\n' "$files" | grep -c '^')

# Two forbidden shapes:
#  PY: python import statements naming another section's package
#      (from section3.x import y / import section4 / from ..section2 import z)
#  JS: module specifiers resolving into another section's tree
#      ('...sectionN/', incl. ../sectionN/ and ./sectionN/)
RE_PY='^[[:space:]]*(from|import)[[:space:]]+(\.+)?section[0-9]+([^0-9a-zA-Z_]|$)'
RE_JS="['\"](\.\./)*(\./)*section[0-9]+/"

raw_hits=$(printf '%s\n' "$files" | xargs grep -nE -e "$RE_PY" -e "$RE_JS" || true)

if [ -n "$raw_hits" ]; then
	each_line "$raw_hits" parse_isolation_hit
fi

if [ "$fail_count" -gt 0 ]; then
	echo ""
	echo "GUARD[isolation] RESULT: $fail_count violation(s) across $total source file(s) in $sections_scanned section(s)."
	exit 1
fi

echo "GUARD[isolation] OK: $total source file(s) across $sections_scanned section(s) — no cross-section imports."
exit 0
