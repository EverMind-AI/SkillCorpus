# Licence and governance

SkillCorpus redistributes agent skills authored by thousands of people across
public repositories. Doing that responsibly requires a **source-licence audit** —
which prior skill releases do not report — and a way for authors to opt out. This
document is that record.

## The GREEN / RED / YELLOW policy

Each source's licence is mapped to one of three tiers. **Only GREEN is included
in the corpus.**

| Tier | Meaning | Action | Licences |
|---|---|---|---|
| 🟢 **GREEN** | Permissive; redistribution + derivative use allowed with attribution | **Included** | `MIT`, `MIT-0`, `Apache-2.0`, `BSD`, `BSD-2-Clause`, `BSD-3-Clause`, `0BSD`, `ISC`, `CC0-1.0`, `Unlicense`, `WTFPL`, `MPL-2.0`, `Mulan-PSL-2.0` |
| 🔴 **RED** | Copyleft or non-commercial; incompatible with open redistribution | **Excluded** | `GPL-*`, `AGPL-*`, `CC-BY-NC-*`, `CC-BY-ND-*`, `BUSL-1.1`, `FSL-1.1*`, `PolyForm-NC-*` |
| 🟡 **YELLOW** | Weak-copyleft / share-alike / attribution-heavy; needs case-by-case review | **Excluded (default)** | `LGPL-*`, `EPL-2.0`, `CC-BY-4.0`, `CC-BY-SA-4.0` |

The authoritative lists live in `skillcorpus/curate/license.py`
(`GREEN_LICENSES` / `RED_LICENSES` / `YELLOW_LICENSES`). Unparseable, missing, or
custom licences are treated as **not GREEN** and excluded (safe by default).

## How a skill's licence gates its inclusion

```
GitHub API (repo spdx_id)
  └─► audit/source_license_report.csv        # per-source SPDX id (reviewable)
        └─► audit/license_safe_sources.json   # the GREEN whitelist (sources ∈ GREEN)
              └─► curate.license_audit         # sets skills.active = 1 iff source ∈ whitelist
                    └─► export.corpus          # writes only rows with active = 1
```

`active` starts at `0` for every ingested skill; `curate.license_audit`
(`activate`) is the **only** step that flips it, per the whitelist. The final
corpus export (`WHERE deleted = 0 AND active = 1`) therefore contains GREEN
sources only. The demo ships `audit/license_safe_sources.json` covering the four
public demo sources; the production whitelist is generated from the private
source-licence CSV and is not shipped.

## Basis for redistribution

- **Only permissively-licensed skills are included** — no skill is relicensed.
- Every corpus row carries its `source`, `source_url`, and `license`, so
  downstream users can honour each skill's original terms and attribution.
- `SKILL.md` bodies are redistributed under their upstream permissive licence;
  the corpus adds derived metadata (category, quality, tags), not new rights over
  the content.

## Opt-out

If you are an author or rights-holder and want a skill or an entire source
removed from the corpus, open an issue on the repository (or email the address in
the dataset card). Removal requests are honoured on the next corpus release: the
source is dropped from the registry and the whitelist, and the affected rows are
excluded from every subsequent build.
