#!/usr/bin/env bash
# Compare the two ports on identical inputs. See README.md.
#
# Needs python3 and npx in one place, which is why this is a hand-run tool
# rather than a CI job: the pipeline runs the two languages in separate images.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

python3 "$here/_py_side.py" > "$work/py.json"
# Run in place: the TypeScript side imports the engine by relative path, so
# copying it to a scratch directory would break every one of those.
(cd "$here" && npx --yes tsx _ts_side.ts "$work/py.json") > "$work/ts.json"
python3 "$here/_compare.py" "$work/py.json" "$work/ts.json"
