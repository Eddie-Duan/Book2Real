"""生成 UI 插图：问答入口卡（3:2，左侧留白给标题）。

    py scripts/gen_ui_art.py --only ask_entry_a --size 1536x1024
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "vendor"))

from book2real.vision import recraft  # noqa: E402

PROMPTS = {
    "ask_entry_a": (
        "A curious little girl with two buns leaning on a big open blank picture book, "
        "a small brown dog with a red collar sitting beside her, both looking up with wonder, "
        "warm cream paper background, soft gouache children's picture book illustration, "
        "rounded shapes, gentle warm light, wide composition with wide empty space on the left, "
        "blank pages, no letters, no text, no watermark, no logo"
    ),
    "ask_entry_b": (
        "A little girl and a small brown dog looking at an open blank picture book together, "
        "close-up, warm cream paper background, soft gouache children's picture book illustration, "
        "quiet and friendly, blank pages, no letters, no text, no watermark, no logo"
    ),
    "film_cinema": (
        "A cozy little home cinema with red velvet curtains and a warm spotlight, an open blank "
        "picture book on a soft cushion in the middle, warm cream background, soft gouache "
        "children's picture book illustration, no people, blank pages, no letters, no text, "
        "no watermark"
    ),
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(ROOT / "runs" / "ui_drafts"))
    parser.add_argument("--only", action="append", default=[])
    parser.add_argument("--size", default="1536x1024")
    parser.add_argument("--style", default="digital_illustration")
    args = parser.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for name in args.only or list(PROMPTS):
        images, body = recraft.generate(PROMPTS[name], size=args.size, style=args.style)
        for i, im in enumerate(images):
            path = out / f"{name}_{i}.png"
            im.convert("RGB").save(path)
            print(f"saved {path}  credits={body.get('credits')}")
    print(f"spent this month: ${recraft._money():.2f} / cap ${recraft.config()['max_spend']:.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
