# Shared skills library — host E2E results

```text
Feature:               cross-agent shared skills library
Spec:                  skillsearch-shared-skills-spec.md, acceptance 1–9
SkillCorpus commit:    ca32489 (branch feat/shared-skills)
Test date:             2026-09-09
Tester:                tianyi.sun@evermind.ai
Operating system:      Ubuntu 22.04.4 (Linux 5.4.250)
Node / Python:         Node v22.23.1, Python 3.12.1
Catalogue:             EverMind SkillHub, the real service, no key needed
Model:                 Qwen3.6-27B on an internal OpenAI-compatible gateway,
                       reasoning disabled; endpoint redacted, supplied through
                       SKILLSEARCH_E2E_BASE_URL
```

Reported apart from `0.3.0.md` because this is a feature's acceptance rather
than a release's, and it is numbered the way its own spec numbers things. The
cases are in [`../cases.md`](../cases.md) under **S1–S9**.

## Summary

| # | Acceptance | Result |
| --- | --- | --- |
| S1 | a retrieved skill appears under `<shared root>/skills/` | PASS |
| S2 | next turn finds it locally, exactly once, no restart | PASS |
| S3 | agent B retrieves what agent A installed | PASS |
| S4 | a skill in A's own directory is retrievable in B | PASS |
| S5 | a hand-dropped skill reaches the hosts next turn | PASS |
| S6 | `enabled: false` hides A from B, and survives A restarting | PASS |
| S7 | uninstall → gone, recorded, not retrievable | PASS |
| S8 | a failed update leaves the previous version working | PASS |
| S9 | a corrupt registry costs sharing, not retrieval | PASS |

All nine observed on real hosts against the real catalogue. The spec words
S5 and S9 as "五家"; they were run on the hosts named below rather than on all
five, and the per-host table underneath is what covers every host separately.

WorkBuddy is not in any of this: it has no headless path, and the shared
library has **not** been verified there. That is the one open item.

## Per host: does this host join the library at all?

`scripts/e2e_shared_hosts.py`. One skill in the shared directory, each host's
own directory created and left empty, one question only that skill answers.
Same setup for all of them, so a difference in the result is a difference in
that host's wiring — which matters, because the registration is wired at six
separate call sites.

| Host | Registered as | Engine retrieved it | Model called the tool | Verdict |
| --- | --- | --- | --- | --- |
| OpenClaw 1.x (2026.7.1) | `openclaw` | Yes | Yes | PASS |
| OpenClaw 2.0 (2026.8.1) | `openclaw2` | Yes | Yes | PASS |
| DeepSeek Harness (`47f9438`) | `deepseek-harness` | Yes | — *(no tool array on the wire for this probe)* | PASS |
| Raven (`1cb604a` + local patch) | `raven` | Yes | Yes | PASS |
| Hermes (`77ed972`) | `hermes` | Yes | Yes | PASS |
| WorkBuddy 5.3.13 | — | — | — | **not run** |

**Registration and retrieval are separate verdicts, deliberately.** In
on-demand mode retrieval passes through the model choosing to call
`skill_search`, and a model that answers from memory instead leaves the wiring
untested rather than broken. Across three runs of this table the OpenClaw
engine probe was true every time while the model's choice flipped on *both*
generations — once each. A host that registers but whose model did not reach
for the tool is INCONCLUSIVE; a host that does not register is a failure,
because then the others cannot see it.

## Two hosts, sharing with each other

`scripts/e2e_shared.py` — one Python port (Raven) and one TypeScript port
(OpenClaw 2.0), one `SKILLSEARCH_HOME`, separate processes. Two rather than
five because two is what the two *ports* are: a disagreement between them is
the failure a single-host run cannot see.

| Check | Covers | Result |
| --- | --- | --- |
| raven registers its own skills directory | S4 setup | PASS |
| raven installs a catalogue skill into the shared directory | S1 | PASS |
| openclaw retrieves a skill raven installed from the catalogue | **S3** | PASS |
| openclaw retrieves a skill that lives in raven's directory | S4 | PASS |
| a skill dropped into the shared directory is found without a restart | S5 | PASS |
| uninstalling removes it and records that it was removed | S7 | PASS |
| and the other host stops retrieving it, without a restart | S7 | PASS |
| disabling raven's entry hides its skills from openclaw | S6 | PASS |
| and raven restarting does not switch it back on | S6 | PASS |
| a corrupt registry leaves retrieval working | S9 | PASS |

Evidence worth naming: OpenClaw's reply carried `Wombat-Ledger-7`, a token
present in nothing but the `SKILL.md` under **Raven's** directory, which Raven
registered and OpenClaw read out of `registry.json`.

## Installing, against the live catalogue

`scripts/e2e_install.py`. Against the real hub rather than a fixture, because
the failure this guards is a real catalogue's shape — and one such bug was
found this way, below.

| Check | Covers | Result |
| --- | --- | --- |
| a retrieved skill is kept under the shared skills directory | S1 | PASS |
| and it carries a readable provenance marker | S1 | PASS |
| the next turn finds it exactly once, not twice | **S2** | PASS |
| a newly built engine sees it too, and still once | S2 | PASS |
| a failed update leaves the previous version installed and working | **S8** | PASS |
| and no half-written directory is left behind | S8 | PASS |
| uninstalling reports that it removed what was there | S7 | PASS |
| and nothing is left in the ledger or on disk | S7 | PASS |

Installed in the run above: `hub/mzlzyCA_html-markdown_extract-tables-from-pdf`
and `hub/openclaw_skills_table-extractor`. S8's numbers: the download failed as
expected, the directory was unchanged, and the previous body was byte-identical
at 2546 characters.

## What running this found

Six defects, all in code that the package suites reported green.

| Found by | Defect |
| --- | --- |
| the live catalogue | `list_installed` looked only at the top level, so a bundle that wraps the skill in a directory was invisible. Dedup worked while every management call was blind — `listInstalled` reported an empty library holding two skills |
| the same, one port later | the identical bug, unfixed, in the TypeScript port |
| the differential | the two ports computed **different install directories** for a non-ASCII slug, so one skill occupied two of them; every all-CJK slug collapsed to the same name and overwrote itself |
| the differential | a whitespace-only `SKILLSEARCH_SKILLS_DIRS` read as an override on one side and as unset on the other, silently emptying the host's skills directory and with it the shared library |
| the DSH host build | `installRootFor(cfg.shareSkills)` did not compile: the field is optional on the exported `Config`, and the zod default only applies to config the harness parsed. No suite here compiles that file against that interface |
| the spec, re-read verbatim | acceptance 7 asks for a record to be kept on uninstall. There was none — the directory was deleted and nothing was written |

Three of the six are the same shape: a fix applied to one port and not the
other. Every package suite compares a port with itself, which is why
[`../../parity/`](../../parity) exists.

## Not executed

| What | Why |
| --- | --- |
| the whole library on WorkBuddy | No headless path — it is a desktop application installed from a marketplace. Nothing here has verified that its `UserPromptSubmit` hook and its MCP server both join the shared library |
| S5 and S9 on all five hosts | The spec says "五家". They ran on the hosts named in each table above; the per-host table covers registration and retrieval for every host separately, which is the part that is per-host code |
| concurrent writes from several hosts at once | The registry write is atomic and the tests assert that, but no test runs two hosts registering simultaneously |

## Reproducing

```bash
export SKILLSEARCH_E2E_BASE_URL=https://your-openai-compatible-endpoint/v1
export SKILLSEARCH_E2E_MODEL=your-model-id

cd skillcorpus_plugin/tests/host-e2e

# every host, one at a time: does it join at all
python scripts/e2e_shared_hosts.py \
  --openclaw1 /path/to/1.x/openclaw --openclaw2 /path/to/2.0/openclaw \
  --dsh /path/to/deepseek-harness \
  --raven /path/to/raven --raven-site /path/to/site-packages \
  --hermes /path/to/hermes-agent

# two hosts, sharing with each other
python scripts/e2e_shared.py --openclaw /path/to/2.0/openclaw \
  --raven /path/to/raven --raven-site /path/to/site-packages

# installing, against the live catalogue
python scripts/e2e_install.py
```

Every script writes its full record with `--dump FILE`, including what went
over the wire.
