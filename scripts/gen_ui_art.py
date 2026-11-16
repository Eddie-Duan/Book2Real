"""生成 UI 插图草稿（Recraft 文生图）。产物先放 runs/ui_drafts/，确认后再进 web/img/。

    py scripts/gen_ui_art.py            # 两张草稿
    py scripts/gen_ui_art.py --out book2real/web/img

每次调用都过 RECRAFT_MAX_SPEND_USD 预算护栏，记录在 runs/api_log.jsonl。
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
    "qa_kid_dog": (
        "A friendly little girl with two buns reading a picture book, a small brown dog "
        "with a red collar sitting beside her, cozy warm cream background, soft gouache "
        "children's picture book illustration, rounded shapes, gentle warm light, "
        "no text, no watermark"
    ),
    "film_cinema": (
        "A cozy little home cinema with red velvet curtains and a warm spotlight, an open "
        "picture book on a soft cushion in the middle, warm cream background, soft gouache "
        "children's picture book illustration, no people, no text, no watermark"
    ),
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(ROOT / "runs" / "ui_drafts"))
    parser.add_argument("--only", action="append", default=[])
    parser.add_argument("--size", default="1024x1024")
    parser.add_argument("--style", default="digital_illustration")
    args = parser.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    names = args.only or list(PROMPTS)
    for name in names:
        prompt = PROMPTS[name]
        images, body = recraft.generate(prompt, size=args.size, style=args.style)
        for i, im in enumerate(images):
            path = out / f"{name}_{i}.png"
            im.convert("RGB").save(path)
            print(f"saved {path}  credits={body.get('credits')}")
    print(f"spent this month: ${recraft._money():.2f} / cap ${recraft.config()['max_spend']:.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
