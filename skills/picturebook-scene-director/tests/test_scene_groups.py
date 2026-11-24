"""S0 -> S2 分场契约测试。"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import plan_scenes  # noqa: E402


def test_scene_groups_map_book_ids(tmp_path):
    data = {"segments": [
        {"segment_id": "sc_01", "pages": ["p01", "page_002"]},
        {"segment_id": "sc_02", "pages": ["p03"]},
    ]}
    f = tmp_path / "groups.json"
    f.write_text(json.dumps(data), encoding="utf-8")
    assert plan_scenes.load_scene_groups(f) == {"1": "sc_01", "2": "sc_01", "3": "sc_02"}
