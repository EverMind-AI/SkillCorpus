# Differential checks between the two ports

The engine exists twice — `engine-python/` and `engine-typescript/` — and the
two are independent ports. Each package's own suite compares a port with
itself, which is exactly the blind spot that let three bugs ship into this
branch:

1. a fix applied to Python and not to TypeScript (`listInstalled` walking past
   a bundle's wrapper directory);
2. the two ports computing **different directory names** for one skill, because
   Python's `str.isalnum` is Unicode-aware and a JS character class is ASCII —
   so a CJK slug installed by Raven and by OpenClaw occupied two directories;
3. the two ports disagreeing on whether a whitespace-only environment variable
   is an override, where TypeScript's answer silently emptied the host's skills
   directory.

None of those are visible from inside one port. All three crossed the boundary
that matters: **both ports read and write the same files, in one shared
directory, on one machine.**

## What is pinned where

| Contract | Fixture | Read by |
| --- | --- | --- |
| registry parsing | `engine-typescript/tests/fixtures-registry.json` | both suites |
| install directory naming | `engine-typescript/tests/fixtures-slugdir.json` | both suites |
| everything else below | `check.sh`, run by hand | — |

The fixtures are the durable half: they run in CI, on every push, in both
languages. `check.sh` is the exploratory half — it needs both toolchains in one
place, which no CI job here has, so it is a tool for whoever is changing these
modules rather than a gate.

## Running it

```bash
cd skillcorpus_plugin/tests/parity && ./check.sh
```

It compares, on identical inputs:

- `identity`, `bodyDigest`, `optedIn`, and the shape of `now()`;
- **the bytes**: Python writes a registry and a marker, TypeScript reads both,
  rewrites the registry, and the file must come out byte-identical. This is the
  strongest check here, and the one a shape-only comparison would miss;
- environment-variable precedence, including the whitespace case above.

Not compared, deliberately: the directory fingerprint in `watch.py` /
`watch.ts`. It never leaves the process that computed it, so the two ports have
no contract there — only the *behaviour* has to match, and each suite asserts
that itself.
