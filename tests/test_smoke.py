"""Smoke tests that need no ffmpeg and no book on disk.

    python -m pytest tests -q      (or: python tests/test_smoke.py)
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
from PIL import Image, ImageDraw

from book2real import imgops as I
from book2real.camera import pose
from book2real.config import Config, FilmConfig
from book2real.contracts import Camera, ShotSpec
from book2real.layers import decompose
from book2real.qc import _hist_sim, _still_ratio
from book2real.render import prepare
from book2real.storybook import _split_text, pinyin_tokens, segments


def _page() -> np.ndarray:
    im = Image.new("RGB", (600, 800), (40, 70, 110))
    d = ImageDraw.Draw(im)
    d.ellipse((180, 420, 420, 660), fill=(245, 240, 230))
    d.ellipse((250, 300, 350, 400), fill=(245, 240, 230))
    d.rectangle((0, 700, 600, 800), fill=(30, 50, 40))
    return np.asarray(im)


def test_otsu_splits_two_modes():
    a = np.concatenate([np.full(500, 20, np.uint8), np.full(500, 220, np.uint8)])
    t = I.otsu(a.reshape(20, 50))
    # flat modes leave a plateau of valid thresholds; accept any split between them
    assert 20 <= t < 220


def test_decompose_finds_subject():
    pl = decompose(_page(), work=400)
    assert pl.count >= 2
    near = pl.layers[-1]
    assert 0.02 < float((near.alpha > 0.5).mean()) < 0.9
    # the far plate must be complete everywhere (it is the backdrop of the move)
    assert float(pl.layers[0].alpha.mean()) > 0.5


def test_camera_moves():
    s = ShotSpec(shot_id="p1", page=1, camera=Camera(move="dolly_in", amount=0.2))
    assert pose(s, 1.0).scale > pose(s, 0.0).scale
    s2 = ShotSpec(shot_id="p2", page=1, camera=Camera(move="pan_lr", amount=0.2))
    assert pose(s2, 1.0).dx > pose(s2, 0.0).dx


def test_warp_keeps_shape():
    a = np.zeros((100, 100, 3), np.uint8)
    a[30:70, 30:70] = 255
    out = I.warp(a, 1.0, 0.0, 0.0, 100, 100)
    assert out.shape == (100, 100, 3)
    assert abs(int(out[50, 50, 0]) - 255) < 2


def test_config_roundtrip(tmp_path=None):
    cfg = Config.load(None)
    assert cfg.film.width == 1280 and cfg.film.fps == 24
    assert cfg.qc.max_flicker > 0


def test_shot_frames():
    s = ShotSpec(shot_id="p1", page=1, duration=2.0, fps=24)
    assert s.frames == 48


def test_flat_plate_skips_decomposition():
    """Printed pages can opt out of layers; the camera still works."""
    assets = prepare(_page(), FilmConfig(), layers=False)
    assert assets.layers.count == 1
    assert assets.card_w > 0 and assets.card_h > 0
    assert float(assets.padded[0].alpha.min()) == 255


def test_still_detector_ignores_slow_moves():
    # a slow dolly: every pair differs slightly -> not "still"
    deltas = np.full(40, 4e-4, np.float32)
    assert _still_ratio(deltas) == 0.0
    # frozen runs of >= 3 frames count
    frozen = np.concatenate([np.full(10, 1e-7), deltas[:5], np.full(10, 1e-7)])
    assert 0.0 < _still_ratio(frozen) < 1.0


def test_color_similarity_catches_wrong_plate():
    page = _page()
    blank = np.zeros_like(page)
    assert _hist_sim(page, page) > 0.99
    assert _hist_sim(page, blank) < 0.5


def test_segments_keep_text_and_punctuation():
    text = "她是个有趣的小人儿。此时，劳拉正在……“你在做什么呢，劳拉？”"
    parts = segments(text)
    assert "".join(parts) == text
    assert "小人儿。" in parts
    assert not any(p and p[0] in "，。！？" for p in parts)


def test_split_text_does_not_break_a_quote():
    text = "劳拉说：“我已经问过汉森老师，在假期里我可不可以把波特带回家。”" * 3
    chunks = _split_text(text, limit=60)
    assert "".join(chunks) == text
    assert all(not c.startswith("”") for c in chunks)


def test_split_text_keeps_screens_short_and_capped():
    from book2real.storybook import MAX_PARTS

    text = ("“我觉得波特是一只女豚鼠，”劳拉说，“它看起来像个女的，因为它的毛特别漂亮。”"
            "咪咪说：“不，波特是男豚鼠。”“你怎么这么了解豚鼠啊？”露塔说。"
            "“因为我家里也有一只……它叫毛毛，是从秘鲁来的。”“秘鲁？”") * 2
    chunks = _split_text(text, limit=64)
    assert "".join(chunks) == text                     # nothing added or lost
    assert 1 < len(chunks) <= MAX_PARTS
    assert max(len(c) for c in chunks) <= 64 * 1.4
    assert all(c.strip() for c in chunks)


def test_part_boxes_start_whole_then_differ():
    from book2real.storybook import _part_boxes

    page = _page()
    boxes = _part_boxes(page, 3, spots=[{"box": [0.1, 0.6, 0.2, 0.3]}])
    assert len(boxes) == 3
    assert boxes[0] == (0.0, 0.0, 1.0, 1.0)            # first screen is the whole page
    assert len(set(boxes[1:])) == 2                    # later screens differ
    for x, y, w, h in boxes:
        assert -1e-9 <= x and -1e-9 <= y and x + w <= 1 + 1e-9 and y + h <= 1 + 1e-9


def test_part_framing_contains_its_hotspot():
    from book2real.storybook import _part_boxes

    spot = {"box": [0.55, 0.30, 0.14, 0.22]}
    boxes = _part_boxes(_page(), 3, spots=[spot])
    for i, (x, y, w, h) in enumerate(boxes[1:], start=1):
        if i == 2:                                     # the screen framed on the spot
            assert x <= spot["box"][0] and y <= spot["box"][1]
            assert x + w >= spot["box"][0] + spot["box"][2]
            assert y + h >= spot["box"][1] + spot["box"][3]


def test_pinyin_tokens_align_to_characters():
    text = "豚鼠。"
    toks = pinyin_tokens(text)
    assert [t["ch"] for t in toks] == list(text)
    assert toks[0]["py"] == "tún"
    assert toks[-1]["py"] == ""


if __name__ == "__main__":
    ok = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            ok += 1
            print(f"ok  {name}")
    print(f"{ok} tests passed")
