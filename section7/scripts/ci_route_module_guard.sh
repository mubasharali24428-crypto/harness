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
#              sectionN must never import code from sectionM (N != M).
#              Self-references within a section are fine; contracts/ and
#              integration/ are outside this guard by design (integration
#              checkpoint tests exist precisely to touch several sections).
#
# M17-B3 upgrade — the law is now enforced against the threat model that
# actually exists (P-BETA finding C-1: 17 production files imported sibling
# sections by installed PACKAGE NAME or via runtime sys.path injection and
# passed every gate). The isolation mode detects:
#
#   1. literal tree imports   ../sectionM/... / 'sectionM/...' (py + js)
#   2. package-name imports   from harness_<name>[.sub] import x
#                             where <name> belongs to ANOTHER section
#                             (identity/github/router/orchestrator/sandbox/
#                              meeting/review/onboarding)
#   3. sys.path seams         sys.path.append/insert of a sibling section's
#                             src tree ("../sectionN/src") inside sections/
#   4. re-export chains       a section-local module whose body is just
#                             `from harness_<foreign>... import *`
#
# SCOPE NOTE (deliberate, keep in sync with docs/ci-gates.md): rules 2-4 are
# enforced on section SRC trees only (section*/src). Test files may reach a
# sibling's internals to pin cross-section parity (the G3/G2 parity tests do
# exactly that); production coupling may not. deploy/, scripts/, task0/,
# debug_canon/ and Makefile wiring are outside section isolation by design.
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
each_line() { # each_line <blob> <callback-fn> [extra args passed to callback...]
	_blob=$1
	_cb=$2
	shift 2 || true
	printf '%s\n' "$_blob" >"$TMP"
	while IFS= read -r ROW; do
		[ -n "$ROW" ] || continue
		"$_cb" "$ROW" "$@"
	done <"$TMP"
}

split_grep_row() { # split_grep_row <row> -> F, LN, BODY
	F=${1%%:*}
	_rest=${1#*:}
	LN=${_rest%%:*}
	BODY=${_rest#*:}
}

own_pkg_for() { # own_pkg_for <section-dir> -> prints owning package name
	case "$1" in
	section1) echo harness_identity ;;
	section2) echo harness_github ;;
	section3) echo harness_router ;;
	section4) echo harness_orchestrator ;;
	section5) echo harness_sandbox ;;
	section6) echo harness_meeting ;;
	section8) echo harness_review ;;
	section10) echo harness_onboarding ;;
	*) echo "" ;;
	esac
}

parse_routes_hit() { # <grep row>
	split_grep_row "$1"
	note_fail "$F" "$LN" "non-default export in route module: ${BODY}"
	echo "                route modules (app/**/page.tsx) may only 'export default'"
	echo "                (Next index-signature law, W4 carryover — move helpers/metadata"
	echo "                into a non-route module and import them, default-exporting only)."
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

# Scan set: section src trees ONLY for the package/sys.path/re-export rules;
# full section trees still get the literal-tree rule (cheap, zero FPs there).
src_files=''
sections_scanned=0
for sdir in section*/; do
	[ -d "$sdir" ] || continue
	[ -d "${sdir}src" ] || continue
	list=$(find "${sdir}src" \
		\( -name node_modules -o -name .next -o -name .venv -o -name venv \
		-o -name __pycache__ -o -name dist -o -name build -o -name .ruff_cache \
		-o -name '*.egg-info' \) -prune \
		-o -type f \( -name '*.py' -o -name '*.ts' -o -name '*.tsx' \
		-o -name '*.js' -o -name '*.jsx' -o -name '*.mjs' -o -name '*.cjs' \) -print)
	[ -n "$list" ] || continue
	sections_scanned=$((sections_scanned + 1))
	if [ -n "$src_files" ]; then
		src_files="$src_files
$list"
	else
		src_files="$list"
	fi
done

total=$(printf '%s\n' "$src_files" | grep -c '^')

# Rule 1 — literal tree references (production/app files; tests exempt):
#   PY: python import statements naming another section's tree
#       (from section3.x import y / import section4 / from ..section2 import z)
#   JS: module specifiers resolving into another section's tree
#       ('...sectionN/', incl. ../sectionN/ and ./sectionN/)
RE_PY_TREE='^[[:space:]]*(from|import)[[:space:]]+(\.+)?section[0-9]+([^0-9a-zA-Z_]|$)'
RE_JS_TREE="['\"](\.\./)*(\./)*section[0-9]+/"

raw_tree_hits=$(find section*/ -type d \
	\( -name node_modules -o -name .next -o -name .venv -o -name venv \
	-o -name __pycache__ -o -name dist -o -name build -o -name .ruff_cache \
	-o -name '*.egg-info' \) -prune \
	-o -type f \( -name '*.py' -o -name '*.ts' -o -name '*.tsx' \
	-o -name '*.js' -o -name '*.jsx' -o -name '*.mjs' -o -name '*.cjs' \) -print |
	grep -vE '(^|/)tests?/|\.test\.|\.spec\.|(^|/)test_' |
	xargs grep -nE -e "$RE_PY_TREE" -e "$RE_JS_TREE" || true)

# Rule 2 — package-name imports (src trees): any import of a harness_* package
# that does not belong to the importing section. contracts (harness_contracts)
# and self-imports are legal; everything else fails.
RE_PKG_IMPORT='^[[:space:]]*(from|import)[[:space:]]+harness_(identity|github|router|orchestrator|sandbox|meeting|review|onboarding)([^0-9a-zA-Z_]|$)'

pkg_hits=$(printf '%s\n' "$src_files" | xargs grep -nE "$RE_PKG_IMPORT" || true)

# Rule 3 — sys.path seams (src trees): runtime path injection pointing at a
# SIBLING section's src tree. Appending one's OWN section src is legal
# (self-reference); anything naming sectionM/src from sectionN/src is not.
RE_SYSPATH='path\.(append|insert)[^#]*section[0-9]+'

syspath_hits=$(printf '%s\n' "$src_files" | xargs grep -nE "$RE_SYSPATH" || true)

parse_isolation_hit() { # parse_isolation_hit <grep row> <rule-label> <remedy1> [remedy2...]
	ROW=$1
	LABEL=$2
	split_grep_row "$ROW"
	own_dir=$(printf '%s' "$F" | cut -d/ -f1)
	own=$(own_pkg_for "$own_dir")
	# Self-references (a section naming its own package/tree) are legal.
	if [ -n "$own" ]; then
		case "$BODY" in
		*"$own"*) return 0 ;;
		esac
	fi
	note_fail "$F" "$LN" "${LABEL}: ${BODY}"
	# Remaining args = remedy lines (shift past row + label first).
	shift 2 || true
	for _line in "$@"; do
		echo "                $_line"
	done
}

fail_before=0

if [ -n "$raw_tree_hits" ]; then
	each_line "$raw_tree_hits" parse_isolation_hit "cross-section import" \
		"'sections may depend ONLY on contracts/' (harness_contracts / @contracts/*)." \
		"M13-audit isolation law: share schemas via contracts, never import sibling code." ||
		true
fi

if [ -n "$pkg_hits" ]; then
	each_line "$pkg_hits" parse_isolation_hit "package-name import" \
		"this harness_* package belongs to another section — sections depend ONLY on" \
		"harness_contracts. Promote shared code into contracts/ (version bump + TS" \
		"mirror regen) or inline the needed logic locally (M17-B3 / P-BETA C-1)." ||
		true
fi

if [ -n "$syspath_hits" ]; then
	each_line "$syspath_hits" parse_isolation_hit "sys.path seam" \
		"runtime sys.path injection of a sibling section's src tree defeats both the" \
		"isolation law AND the venv dependency contract. Import harness_contracts or" \
		"inline; deploy/runners/services.py is the ONLY sanctioned multi-src assembly." ||
		true
fi

# Rule 4 — re-export chains: `from harness_X... import *` where X is foreign.
# (Star-imports are how a dead fork masquerades as its own module.)
reexport_hits=$(printf '%s\n' "$src_files" |
	xargs grep -nE '^[[:space:]]*from[[:space:]]+harness_(identity|github|router|orchestrator|sandbox|meeting|review|onboarding)[a-zA-Z_.]*[[:space:]]+import[[:space:]]+\*' || true)

if [ -n "$reexport_hits" ]; then
	each_line "$reexport_hits" parse_isolation_hit "re-export chain (star)" \
		"star-re-exporting a sibling section's package hides the dependency while" \
		"keeping it whole. Route through harness_contracts or delete the shim." ||
		true
fi

if [ "$fail_count" -gt 0 ]; then
	echo ""
	echo "GUARD[isolation] RESULT: $fail_count violation(s) across $total src file(s) in $sections_scanned section(s)."
	echo "                 Scope note: package/sys.path rules cover section*/src;"
	echo "                 tests may pin cross-section parity by design (docs/ci-gates.md)."
	exit 1
fi

echo "GUARD[isolation] OK: $total src file(s) across $sections_scanned section(s) —"
echo "                 no cross-section tree/package/sys.path/star-reexport edges."
exit 0
