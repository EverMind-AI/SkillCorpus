"""Compare the two ports' output. Exits non-zero on any disagreement."""

import json
import sys

py, ts = (json.load(open(p, encoding="utf-8")) for p in sys.argv[1:3])

CHECKS = [
    ("identity", py.get("identity"), ts.get("identity")),
    ("bodyDigest", py.get("body_digest"), ts.get("body_digest")),
    ("optedIn", py.get("opted_in"), ts.get("opted_in")),
    ("now() shape", list(py.get("now_shape") or []), ts.get("now_shape")),
    ("env precedence", py.get("env_precedence"), ts.get("env_precedence")),
    # The strongest one: TypeScript rewrites what Python wrote, and the file
    # must come out byte for byte the same. A shape-only comparison passes
    # while the two quietly write different JSON.
    ("registry bytes", py.get("registry_written"), ts.get("registry_after_ts_rewrite")),
    ("marker bytes", py.get("marker_written"), ts.get("marker_written")),
    (
        "TypeScript reads Python's registry",
        ["raven", "openclaw2"],
        [e["id"] for e in ts.get("reads_python_registry") or []],
    ),
    (
        "TypeScript reads Python's marker",
        {"origin": "hub/s", "source": "hub", "slug": "s", "version": "1.0"},
        {k: (ts.get("reads_python_marker") or {}).get(k) for k in ("origin", "source", "slug", "version")},
    ),
]

bad = 0
for name, a, b in CHECKS:
    if a == b:
        print(f"  ok        {name}")
        continue
    bad += 1
    print(f"  MISMATCH  {name}\n      python: {a!r}\n      node:   {b!r}")

print("\nthe two ports agree" if not bad else f"\n{bad} disagreement(s)")
sys.exit(1 if bad else 0)
