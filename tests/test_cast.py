"""Cast agent tests: no ffmpeg, no models, no book needed.

    python -m pytest tests -q      (or: python tests/test_cast.py)
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
from PIL import Image, ImageDraw

from book2real.cast import castclerk, critic, motion, pagescout, pipeline, rigger
from book2real.cast import imagekit as K
from book2real.cast.contracts import MotionPlan, Rig

BG = (255, 255, 255)
SKIN = (247, 226, 199)
DRESS = (214, 96, 74)
HAIR = (236, 203, 122)


def _figure_page() -> np.ndarray:
    """A flat page with one paper-doll figure and two thin raised arms."""
    im = Image.new("RGB", (400, 560), BG)
    d = ImageDraw.Draw(im)
    d.ellipse((150, 30, 250, 130), fill=SKIN, outline=(60, 40, 30))       # head
    d.polygon([(150, 40), (250, 40), (210, 10), (190, 10)], fill=HAIR)    # hair
    d.ellipse((176, 66, 190, 82), fill=(250, 250, 250), outline=(40, 30, 20))  # eye L
    d.ellipse((210, 66, 224, 82), fill=(250, 250, 250), outline=(40, 30, 20))  # eye R
    d.line((188, 104, 214, 104), fill=(90, 50, 40), width=4)              # mouth
    d.rectangle((165, 130, 245, 360), fill=DRESS)                          # torso
    d.line((167, 170, 122, 232), fill=SKIN, width=6)                       # forearm L
    d.line((243, 170, 288, 232), fill=SKIN, width=6)                       # forearm R
    d.ellipse((112, 222, 130, 250), fill=SKIN)                             # hand L
    d.ellipse((280, 222, 298, 250), fill=SKIN)                             # hand R
    d.rectangle((170, 360, 200, 480), fill=(70, 80, 120))                  # leg L
    d.rectangle((210, 360, 240, 480), fill=(70, 80, 120))                  # leg R
    return np.asarray(im)


def _write_book(tmp: Path) -> Path:
    book_dir = tmp / "book"
    art = book_dir / "reader" / "art"
    art.mkdir(parents=True, exist_ok=True)
    Image.fromarray(_figure_page()).save(art / "p01.jpg", quality=95)
    book = {
        "book_id": "demo",
        "pages": [{"id": "p01", "kind": "page", "art": "art/p01.jpg",
                   "text": "劳拉挥挥手，说：“你好呀！”", "art_w": 400, "art_h": 560}],
        "characters": [],
    }
    (book_dir / "reader" / "book.json").write_text(
        json.dumps(book, ensure_ascii=False), encoding="utf-8")
    return book_dir


def test_pagescout_finds_the_figure():
    with tempfile.TemporaryDirectory() as td:
        book_dir = _write_book(Path(td))
        book = json.loads((book_dir / "reader" / "book.json").read_text(encoding="utf-8"))
        scan = pagescout.scan(book, book_dir, {})
        page = scan["pages"]["p01"]
        assert not page["degraded"]
        ent = page["entities"][0]
        x, y, w, h = ent["box"]
        assert 0.3 < x + w / 2 < 0.75 and 0.1 < y + h / 2 < 0.9
        assert ent["area"] > 0.05


def test_pagescout_names_only_when_unambiguous():
    with tempfile.TemporaryDirectory() as td:
        book_dir = _write_book(Path(td))
        book = json.loads((book_dir / "reader" / "book.json").read_text(encoding="utf-8"))
        known = {"劳拉": "lola"}
        found = pagescout.scan_page(book_dir / "reader" / "art" / "p01.jpg", ["p01"],
                                    "劳拉挥挥手。", known=known, roles={"劳拉": "妹妹"})
        assert found["entities"][0]["name"] == "劳拉"
        assert found["entities"][0]["name_source"] == "text"

        # a guinea pig name must not stick to a tall figure
        found = pagescout.scan_page(book_dir / "reader" / "art" / "p01.jpg", ["p01"],
                                    "波特不见了。", known={"波特": "port"},
                                    roles={"波特": "豚鼠"})
        assert found["entities"][0]["name"] == ""


def test_castclerk_keeps_curation_text():
    with tempfile.TemporaryDirectory() as td:
        book_dir = _write_book(Path(td))
        reader = book_dir / "reader"
        book = json.loads((reader / "book.json").read_text(encoding="utf-8"))
        curation = {"characters": [{"id": "lola", "name": "劳拉", "role": "妹妹",
                                    "desc": "人工写的描述。"}]}
        scan = pagescout.scan(book, book_dir, curation)
        out = castclerk.build(scan, book, curation, book_dir, reader / "characters")
        card = next(c for c in out["characters"] if c["id"] == "lola")
        assert card["desc"] == "人工写的描述。"
        assert card["role"] == "妹妹"
        assert card["portrait"].startswith("characters/portrait_lola")
        assert "p01" in card["pages"]


def test_rigger_cuts_two_arms():
    with tempfile.TemporaryDirectory() as td:
        book_dir = _write_book(Path(td))
        reader = book_dir / "reader"
        out = reader / "characters"
        out.mkdir(parents=True, exist_ok=True)
        rig = rigger.build(reader, out, "lola", "art/p01.jpg", (0.3, 0.01, 0.42, 0.9))
        assert rig.kind == "paper_puppet", rig.notes
        arms = [p for p in rig.parts if p.name.startswith("arm_")]
        assert len(arms) == 2
        for p in arms:
            assert 0.0 <= p.pivot[0] <= 1.0 and 0.0 <= p.pivot[1] <= 1.0
            assert 8.0 <= p.amp <= 25.0
            assert (reader / p.image).exists()
        assert (reader / rig.art).exists()


def test_critic_rejects_a_blank_face():
    blank = np.full((120, 90, 3), 255, np.uint8)
    score, detail = critic.face_completeness(blank)
    assert score < 0.3 and detail["eyes"] == 0.0


def test_critic_accepts_the_synthetic_rig():
    with tempfile.TemporaryDirectory() as td:
        book_dir = _write_book(Path(td))
        reader = book_dir / "reader"
        out = reader / "characters"
        out.mkdir(parents=True, exist_ok=True)
        rig = rigger.build(reader, out, "lola", "art/p01.jpg", (0.3, 0.01, 0.42, 0.9))
        art = np.asarray(Image.open(reader / rig.art).convert("RGB"))
        key, _ = K.border_key(art)
        for p in rig.parts:
            if not p.name.startswith("arm_"):
                continue
            part = np.asarray(Image.open(reader / p.image).convert("RGBA"))
            assert critic.edge_background(part[..., :3], part[..., 3], key) < critic.BG_MAX
            seam = critic.seam_metrics(art, part, p.pivot, key)
            assert seam["hole"] <= critic.HOLE_MAX
            assert seam["seam"] <= critic.SEAM_MAX


def test_motion_plan_stays_in_the_comfort_range():
    from book2real.cast.contracts import RigPart

    rig = Rig(kind="paper_puppet",
              parts=[RigPart("arm_r", "characters/x_arm_r.png", (0.7, 0.7), 18.0)])
    plan = motion.plan("p01", "劳拉挥挥手，说：“你好呀！”", "lola", rig)
    kinds = [b.action for b in plan.beats]
    assert "idle" in kinds and "wave" in kinds
    wave = next(b for b in plan.beats if b.action == "wave")
    assert 8.0 <= wave.amp <= 25.0 and 1.0 <= wave.cycles <= 3.0
    score, bad = critic.motion_naturalness(plan.to_dict())
    assert score == 1.0, bad


def test_motion_roundtrip():
    from book2real.cast.contracts import RigPart

    rig = Rig(kind="paper_puppet",
              parts=[RigPart("arm_l", "characters/y_arm_l.png", (0.3, 0.6), 14.0)])
    plan = motion.plan("p02", "查理说：“走吧！”", "charlie", rig)
    again = MotionPlan.from_dict(json.loads(json.dumps(plan.to_dict())))
    assert again.to_dict() == plan.to_dict()


def test_text_coverage_flags_a_missing_character():
    book = {"pages": [{"text": "劳拉和露塔都笑了。" * 3}]}
    cards = [{"id": "lola", "name": "劳拉", "pages": ["p01"]},
             {"id": "lotta", "name": "露塔", "pages": []}]
    cov, missing = critic.text_coverage(book, cards)
    assert cov == 0.5 and missing == ["露塔"]


def test_prune_removes_only_previously_managed_files():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        char_dir = root / "reader" / "characters"
        cast_dir = root / "reader" / "cast"
        char_dir.mkdir(parents=True)
        cast_dir.mkdir(parents=True)
        for name in ("stale_body.jpg", "kept_body.jpg", "portrait_mimi.jpg", "manual.svg",
                     "orphan_this_run.jpg"):
            (char_dir / name).write_bytes(b"asset")
        manifest = cast_dir / "managed_character_files.json"
        manifest.write_text(json.dumps({"files": ["stale_body.jpg", "kept_body.jpg"]}),
                            encoding="utf-8")

        removed = pipeline._prune(char_dir, manifest, {"kept_body.jpg", "new_body.jpg"},
                                  {"orphan_this_run.jpg", "kept_body.jpg"})

        assert removed == 2
        assert not (char_dir / "stale_body.jpg").exists()
        assert not (char_dir / "orphan_this_run.jpg").exists()
        assert (char_dir / "kept_body.jpg").exists()
        assert (char_dir / "portrait_mimi.jpg").exists()   # never recorded as generated
        assert (char_dir / "manual.svg").exists()          # hand-made file is never touched
        assert json.loads(manifest.read_text(encoding="utf-8")) == {
            "files": ["kept_body.jpg", "new_body.jpg"]}


if __name__ == "__main__":
    ok = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            ok += 1
            print(f"ok  {name}")
    print(f"{ok} tests passed")
