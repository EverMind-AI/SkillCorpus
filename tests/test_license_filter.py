"""License filter consistency test — the JSON whitelist's green_categories must exactly
match the code's GREEN_LICENSES (guards against a past drift where the 0BSD category went missing)."""

from __future__ import annotations

import json
from pathlib import Path

from skill_library.rules import GREEN_LICENSES


def test_json_green_categories_matches_code():
    json_path = Path(__file__).resolve().parents[1] / "license_safe_sources.json"
    data = json.loads(json_path.read_text(encoding="utf-8"))
    assert set(data["green_categories"]) == set(GREEN_LICENSES), (
        f"green_categories drift: json={sorted(data['green_categories'])} "
        f"code={sorted(GREEN_LICENSES)}; run `python -m skill_library.license_audit build` to regenerate"
    )


if __name__ == "__main__":
    test_json_green_categories_matches_code()
    print("LICENSE FILTER TEST PASSED")
