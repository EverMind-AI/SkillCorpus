"""License filter 一致性测试 — JSON whitelist 的 green_categories 必须与代码
GREEN_LICENSES 完全一致 (防 iter3 的 0BSD 缺失类 drift)."""

from __future__ import annotations

import json
from pathlib import Path

from skill_library.rules import GREEN_LICENSES


def test_json_green_categories_matches_code():
    json_path = Path(__file__).resolve().parents[1] / "license_safe_sources.json"
    data = json.loads(json_path.read_text(encoding="utf-8"))
    assert set(data["green_categories"]) == set(GREEN_LICENSES), (
        f"green_categories drift: json={sorted(data['green_categories'])} "
        f"code={sorted(GREEN_LICENSES)}; 跑 `python -m skill_library.license_audit build` 重生成"
    )


if __name__ == "__main__":
    test_json_green_categories_matches_code()
    print("LICENSE FILTER TEST PASSED")
