"""CastClerk: turn per-page blobs into one cast of characters.

Identity is the hard part — the same child is drawn from a different angle on
every spread — and colour histograms are not reliable enough on painted
picture-book art. So CastClerk never guesses: it accepts a name when PageScout
had hard evidence (a curated spot, or narration that names exactly one
character whose shape fits), and otherwise keeps the figure anonymous and
reports it as a rig candidate for a human or a later model to promote.

Curation always wins: a human-written `role`/`desc`/portrait is never replaced
by auto text.
"""
from __future__ import annotations

import re
from pathlib import Path

from PIL import Image

from ..util import imread
from . import imagekit as K
from .contracts import CharacterCard, Rig

ROLE_WORDS = ("妈妈", "爸爸", "爷爷", "奶奶", "外婆", "外公", "哥哥", "姐姐", "弟弟",
              "妹妹", "老师", "同学", "朋友", "豚鼠")


def _slug(name: str, fallback: str) -> str:
    try:
        from pypinyin import lazy_pinyin

        slug = re.sub(r"[^a-z0-9]+", "_", "_".join(lazy_pinyin(name)).lower()).strip("_")
        if slug:
            return slug
    except Exception:
        pass
    return fallback


def _role_from_text(name: str, pages: list[str], book: dict) -> str:
    for pid in pages:
        page = next((p for p in book.get("pages", []) if p["id"] == pid), None)
        if not page:
            continue
        text = page.get("text", "")
        at = text.find(name)
        while at >= 0:
            tail = text[at + len(name):at + len(name) + 4]
            for w in ROLE_WORDS:
                if w in tail:
                    return w
            at = text.find(name, at + 1)
    return ""


def _portrait(reader_dir: Path, out_dir: Path, cid: str, art: str, box,
              pad: float = 0.08) -> str:
    """Crop a 4:5 portrait from the cleaned art."""
    rgb = imread(reader_dir / art)
    crop, _ = K.crop_box(rgb, tuple(box), pad=pad)
    if crop.size == 0:
        return ""
    im = K.pad_to_ratio(Image.fromarray(crop), 4 / 5)
    if max(im.size) > 720:
        s = 720 / max(im.size)
        im = im.resize((max(1, int(im.width * s)), max(1, int(im.height * s))), Image.LANCZOS)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"portrait_{cid}.jpg"
    im.save(path, quality=94)
    return f"characters/{path.name}"


def build(scan_result: dict, book: dict, curation: dict, book_dir: Path,
          out_dir: Path) -> dict:
    book_dir, out_dir = Path(book_dir), Path(out_dir)
    reader_dir = book_dir / "reader"
    page_text = {p["id"]: p.get("text", "") for p in book.get("pages", [])}
    page_art = {p["id"]: p.get("art_page") or p.get("art", "") for p in book.get("pages", [])}

    cards: dict[str, CharacterCard] = {}
    by_name: dict[str, CharacterCard] = {}
    for ch in curation.get("characters", []):
        cid = ch.get("id") or _slug(ch.get("name", ""), f"c{len(cards) + 1}")
        card = CharacterCard(id=cid, name=ch.get("name", cid), role=ch.get("role", ""),
                             desc=ch.get("desc", ""), aliases=list(ch.get("aliases", [])),
                             confidence=0.6, evidence="curation")
        cards[cid] = card
        by_name[card.name] = card

    assigned: dict[str, list[dict]] = {cid: [] for cid in cards}
    anonymous: list[dict] = []
    for pid, page in scan_result.get("pages", {}).items():
        for ent in page.get("entities", []):
            name = ent.get("name") or ""
            if not name:
                anonymous.append(ent)
                continue
            card = by_name.get(name)
            if card is None:
                cid = _slug(name, f"c{len(cards) + 1}")
                card = CharacterCard(id=cid, name=name, confidence=0.4,
                                     evidence=ent.get("name_source", "none"))
                cards[cid] = card
                by_name[name] = card
                assigned[cid] = []
            assigned[card.id].append(ent)

    portraits: dict[str, str] = {}
    generated_files: list[str] = []
    sources: dict[str, dict] = {}
    assignments: dict[str, list[dict]] = {}
    for cid, card in cards.items():
        mine = assigned.get(cid, [])
        assignments[cid] = [{"page": e["page"], "box": list(e["box"]),
                             "area": e["area"], "confidence": e["confidence"],
                             "name_source": e.get("name_source", "")} for e in mine]
        if mine:
            best = max(mine, key=lambda e: e["area"] * e["confidence"])
            sources[cid] = {"page": best["page"], "box": list(best["box"]),
                            "area": best["area"], "confidence": best["confidence"]}
            card.confidence = min(0.95, 0.4 + 0.4 * best["confidence"]
                                  + 0.2 * min(1.0, best["area"] * 6))
            card.evidence = best.get("name_source", "none")
            card.pages = sorted({e["page"] for e in mine}, key=lambda p: (len(p), p))
            curated = reader_dir / "art" / f"char_{cid}.jpg"
            if curated.exists():
                card.portrait = f"art/char_{cid}.jpg"
            else:
                portrait = _portrait(reader_dir, out_dir, cid, page_art[best["page"]],
                                     best["box"])
                if portrait:
                    card.portrait = portrait
                    generated_files.append(Path(portrait).name)
        else:
            # a human already described this character; keep it on the wall even
            # if no page gave us a clean figure
            card.pages = sorted({pid for pid, t in page_text.items() if card.name in t},
                                key=lambda p: (len(p), p))
            fallback = next((c.get("image") for c in book.get("characters", [])
                             if c.get("id") == cid and c.get("image")), "")
            if fallback:
                card.portrait = fallback
            card.evidence = card.evidence or "curation"
        if not card.desc:
            card.desc = (f"出现在第 {card.pages[0]}–{card.pages[-1]} 页。"
                         if card.pages else "")
        if not card.role:
            card.role = _role_from_text(card.name, card.pages, book)
        if card.portrait:
            portraits[cid] = card.portrait
        card.rig = Rig()

    # figures nobody could name: offer them as rig candidates for the next step
    candidates = []
    for ent in sorted(anonymous, key=lambda e: -e["area"]):
        if ent["area"] < 0.04:
            continue
        candidates.append({"page": ent["page"], "box": list(ent["box"]),
                           "area": ent["area"], "confidence": ent["confidence"],
                           "hint": "anonymous figure"})

    ordered = sorted(cards.values(), key=lambda c: (-len(c.pages), c.id))
    return {
        "agent": "CastClerk",
        "characters": [c.to_dict() for c in ordered],
        "portraits": portraits,
        "generated_files": generated_files,
        "sources": sources,
        "assignments": assignments,
        "candidates": candidates,
        "stats": {
            "characters": len(ordered),
            "entities": len(anonymous) + sum(len(v) for v in assigned.values()),
            "named_entities": sum(len(v) for v in assigned.values()),
            "anonymous_entities": len(anonymous),
            "characters_without_figure": sum(1 for c in ordered if c.id not in sources),
            "pages_scanned": len(scan_result.get("pages", {})),
        },
        "notes": [
            "identity: curated spots > narration with exactly one named character > anonymous",
            "curation role/desc/portrait always win over auto text",
        ],
    }
