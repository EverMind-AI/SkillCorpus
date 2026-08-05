"""curate.license — GREEN/RED/YELLOW license policy + normalization."""
from __future__ import annotations

import csv
import re
from pathlib import Path


# ======================== license GREEN gate ========================
"""License filter — enforce GREEN-only (commercially redistributable) active set.

Adds a hard-gate step on top of safety filtering: a skill is admitted
to the released `active` set only if its source repository's
GitHub-API `spdx_id` falls in the GREEN allow-list, OR its per-skill
licence string normalises to a GREEN identifier.

Use:
    from skillcorpus.curate.license import (
        is_green_license,
        normalize_license,
        load_source_license_map,
    )

    src_lic = load_source_license_map("source_license_report.csv")
    # ... during ingest, after LLM judging:
    if not is_green_license(record, src_lic):
        record.active = 0
        record.reason = "non-green license"

The GREEN allow-list mirrors the consumer-side mass-pool policy
(see docs/15_consumer_skillcorpus_status.md) and matches the paper's
released artifact.
"""



# OSI-approved permissive licences that allow commercial
# redistribution without copyleft, share-alike, or non-commercial
# restrictions.  This is the strict GREEN set used by the consumer-side
# mass pool and enforced in the released SkillCorpus active set.
GREEN_LICENSES = frozenset({
    "Apache-2.0",
    "BSD",
    "BSD-2-Clause",
    "BSD-3-Clause",
    "0BSD",
    "CC0-1.0",
    "ISC",
    "MIT",
    "MIT-0",
    "MPL-2.0",
    "Mulan-PSL-2.0",
    "Unlicense",
    "WTFPL",
})

# License strings that signal commercial-incompatible terms; explicit
# REJECT list for clarity (any unknown string also falls to NO_LICENSE).
RED_LICENSES = frozenset({
    "AGPL-1.0", "AGPL-3.0", "AGPL-3.0-only", "AGPL-3.0-or-later",
    "GPL-2.0", "GPL-3.0", "GPL-3.0-only", "GPL-3.0-or-later",
    "CC-BY-NC-4.0", "CC-BY-NC-SA-4.0", "CC-BY-ND-4.0",
    "FSL-1.1", "FSL-1.1-MIT", "FSL-1.1-Apache-2.0",
    "PolyForm-NC-1.0", "PolyForm-Noncommercial-1.0.0",
    "BUSL-1.1",
    "Proprietary",
})

YELLOW_LICENSES = frozenset({
    "LGPL-2.1", "LGPL-3.0", "LGPL-3.0-only",
    "CC-BY-SA-4.0", "CC-BY-4.0",
    "EPL-2.0",
})

_JUNK_LIC_STRINGS = frozenset({
    "", "Unknown", "unknown", "LICENSE", "LICENSE.txt", "LICENSE.md",
    "License", "License.txt", "License.md", "license", "license.txt",
})


def normalize_license(lic: str | None) -> str | None:
    """Normalize a free-form licence string to a canonical SPDX-style
    identifier, returning None for unparseable strings."""
    if not lic:
        return None
    s = lic.strip()
    if not s or s in _JUNK_LIC_STRINGS:
        return None
    sl = s.lower()
    if sl.startswith("complete terms in"):
        return None

    # MIT family
    if "mit-0" in sl:
        return "MIT-0"
    if re.match(r"^mit($|\s|\.|,|;)", sl):
        return "MIT"
    if sl in ("mit", "mit license", "mit licence"):
        return "MIT"

    # Apache
    if sl in (
        "apache-2.0", "apache 2.0", "apache 2", "apache-2", "apache2",
        "apache-2.0 license", "apache 2.0 license", "apache license 2.0",
    ):
        return "Apache-2.0"
    if "apache" in sl and "2" in sl:
        return "Apache-2.0"

    # BSD / ISC / MPL / CC0 / Unlicense / WTFPL / Mulan
    if "bsd-3-clause" in sl or "3-clause bsd" in sl or "3 clause bsd" in sl:
        return "BSD-3-Clause"
    if "bsd-2-clause" in sl or "2-clause bsd" in sl:
        return "BSD-2-Clause"
    if sl == "0bsd":
        return "0BSD"
    if sl == "bsd":
        return "BSD"
    if sl == "isc":
        return "ISC"
    if "mpl-2.0" in sl or sl == "mpl":
        return "MPL-2.0"
    if "cc0" in sl or "cc-0" in sl:
        return "CC0-1.0"
    if "unlicense" in sl:
        return "Unlicense"
    if "wtfpl" in sl:
        return "WTFPL"
    if "mulan" in sl:
        return "Mulan-PSL-2.0"

    # Non-green
    if "agpl" in sl:
        return "AGPL-3.0"
    if "lgpl" in sl:
        return "LGPL-3.0"
    if "gpl" in sl:
        return "GPL-3.0"
    if "cc-by-nc" in sl or "cc by-nc" in sl:
        return "CC-BY-NC-4.0"
    if "cc-by-nd" in sl or "cc by-nd" in sl:
        return "CC-BY-ND-4.0"
    if "cc-by-sa" in sl or "cc by-sa" in sl:
        return "CC-BY-SA-4.0"
    if "cc-by" in sl or "cc by" in sl:
        return "CC-BY-4.0"
    if "proprietary" in sl or "private" in sl:
        return "Proprietary"
    if "fsl" in sl:
        return "FSL-1.1"
    if "polyform" in sl:
        return "PolyForm-NC-1.0"
    if "busl" in sl:
        return "BUSL-1.1"

    # Unknown — return None so caller falls back to source-level
    return None


def load_source_license_map(csv_path: str | Path) -> dict[str, str]:
    """Load source → license_category mapping from the enrichment CSV
    produced by `scripts/enrich_unmapped_licenses.py`."""
    out: dict[str, str] = {}
    with open(csv_path) as f:
        for row in csv.DictReader(f):
            out[row["source"]] = row["license_category"]
    return out


def is_green_license(
    record_license: str | None,
    source: str,
    source_license_map: dict[str, str],
) -> bool:
    """Return True iff the skill is commercially redistributable.

    Resolution order:
      1. Normalise the per-skill licence string.  If it resolves to a
         GREEN identifier, return True.
      2. Otherwise fall back to the source repository's GitHub
         `spdx_id` from `source_license_map`.  If that is GREEN, return
         True.
      3. Otherwise return False (NO_LICENSE / fetch failed / RED /
         YELLOW / Custom / unparseable).

    Note that an explicit NON-GREEN per-skill string overrides any
    source-level GREEN inference, to honour the most restrictive
    declaration.  If the per-skill string is parseable AND non-GREEN,
    return False regardless of source-level licence.
    """
    norm = normalize_license(record_license)
    if norm is not None:
        if norm in GREEN_LICENSES:
            return True
        # Explicit non-green per-skill declaration: reject even if source-green
        return False
    # Per-skill string unparseable → fall back to source-level
    src_lic = source_license_map.get(source)
    return src_lic in GREEN_LICENSES if src_lic else False

# For maintaining the active status of skills by license in bulk, see ``license_audit.py``.
