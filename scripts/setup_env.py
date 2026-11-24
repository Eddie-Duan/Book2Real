# -*- coding: utf-8 -*-
"""Create a local .env from .env.example and fill secrets interactively.

Secrets never leave this machine: .env is gitignored and excluded from the
submission zip. Team members run this once after cloning.
"""
from __future__ import annotations

import getpass
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV = ROOT / ".env"
EXAMPLE = ROOT / ".env.example"
KEYS = [
    ("DEEPSEEK_API_KEY", "DeepSeek key (https://platform.deepseek.com/api_keys)"),
    ("ARK_API_KEY", "Volcengine Ark key (image + video, https://console.volcengine.com/ark)"),
    ("MINIMAX_API_KEY", "MiniMax key (optional, ASR/TTS; leave empty to skip)"),
]


def main() -> int:
    if not ENV.exists():
        shutil.copyfile(EXAMPLE, ENV)
        print(f"created {ENV} from .env.example")
    text = ENV.read_text(encoding="utf-8")
    lines = text.splitlines()
    index = {ln.split("=", 1)[0].strip(): i for i, ln in enumerate(lines)
             if "=" in ln and not ln.strip().startswith("#")}
    for key, label in KEYS:
        cur = ""
        if key in index:
            cur = lines[index[key]].split("=", 1)[1].strip()
        prompt = f"{label}\n  {key}" + (" [already set, Enter to keep]" if cur else "") + ": "
        val = getpass.getpass(prompt).strip()
        if not val:
            continue
        if key in index:
            lines[index[key]] = f"{key}={val}"
        else:
            lines.append(f"{key}={val}")
    ENV.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("saved. next: python -m book2real cloud --check")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
