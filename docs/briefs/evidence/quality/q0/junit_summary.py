"""Summarise q0 junit files: counts per suite + every skip/xfail grouped by (suite, file, reason)."""
import collections
import glob
import os
import sys
import xml.etree.ElementTree as ET

d = sys.argv[1]
counts, groups = {}, collections.defaultdict(list)
for path in sorted(glob.glob(os.path.join(d, "junit", "*.xml"))):
    suite = os.path.basename(path)[:-4]
    c = collections.Counter()
    for tc in ET.parse(path).getroot().iter("testcase"):
        kids = {k.tag: k for k in tc}
        if "failure" in kids or "error" in kids:
            c["failed"] += 1
            groups[(suite, "FAILED", tc.get("classname", ""), (kids["failure"] if "failure" in kids else kids["error"]).get("message", "")[:160])].append(tc.get("name"))
        elif "skipped" in kids:
            msg = kids["skipped"].get("message", "") or ""
            kind = "xfail" if kids["skipped"].get("type") == "pytest.xfail" or msg.startswith("xfail") else "skipped"
            c[kind] += 1
            groups[(suite, kind, tc.get("file") or tc.get("classname", ""), msg[:200])].append(tc.get("name"))
        else:
            c["passed"] += 1
    counts[suite] = c
for s, c in counts.items():
    print(f"{s}\tpassed={c['passed']}\tfailed={c['failed']}\tskipped={c['skipped']}\txfail={c['xfail']}")
print("---")
for (s, kind, where, msg), names in sorted(groups.items()):
    print(f"{s}\t{kind}\t{len(names)}\t{where}\t{msg}")
