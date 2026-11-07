"""Author the p06 screen plan the way the page actually reads.

Page 6 holds three beats but only one drawing:

    beat 1  “我觉得波特是一只女豚鼠，”劳拉说，“它看起来像个女的，因为它的毛特别漂亮。”
            -> needs its own picture: the page has no view of 波特 alone
    beat 2  咪咪说：“不，波特是男豚鼠。”…“因为我家里也有一只……它叫毛毛，是从秘鲁来的。”
            -> the page's collage of guinea pigs fits this beat
    beat 3  “秘鲁？”劳拉和露塔一起说，“秘鲁在哪儿啊？”
            -> too short to stand alone; carry it onto page 7

    python scripts/plan_p06.py            # write the plan only
    python scripts/plan_p06.py --regen    # also generate screen 1 via Recraft
"""
import argparse
import json
import sys

sys.path.insert(0, '.')
from pathlib import Path

from PIL import Image

BOOK = Path("books/charlie_lola")
CUR = BOOK / "curation.json"
ART = BOOK / "reader" / "art"

PROMPT = ("flat children's picture-book illustration, clean black ink outline, "
          "a small fluffy brown guinea pig named 波特 sitting on white paper, "
          "pale warm background, no text, no letters")


def beats(text: str) -> tuple[str, str, str]:
    """Cut at the two dialogue turns, not at the first quote."""
    key2 = "咪咪说："
    key3 = "“秘鲁？”"
    i2 = text.index(key2)
    i3 = text.index(key3)
    return text[:i2], text[i2:i3], text[i3:]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--regen", action="store_true")
    ap.add_argument("--strength", type=float, default=0.55)
    args = ap.parse_args()

    cur = json.loads(CUR.read_text(encoding="utf-8"))
    p06 = cur["pages"]["p06"]
    b1, b2, b3 = beats(p06["text"])
    p06["split_plan"] = {
        "screens": [
            {"text": b1, "visual": {"type": "regenerate", "prompt": PROMPT,
                                    "file": "art/p06-1-gen.jpg"}},
            {"text": b2, "visual": {"type": "original"}},
        ],
        "carry": b3,
    }
    CUR.write_text(json.dumps(cur, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("p06 plan:")
    print("  screen1 [regenerate]", b1)
    print("  screen2 [original]  ", b2[:36], "...")
    print("  carry -> p07        ", b3)

    if args.regen:
        from book2real.vision import recraft

        src = Image.open(ART / "p06.jpg").convert("RGB")
        imgs, body = recraft.image_to_image(src, PROMPT, strength=args.strength)
        out = ART / "p06-1-gen.jpg"
        imgs[0].convert("RGB").save(out, quality=93)
        print(f"generated {out}  credits={body.get('credits')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
