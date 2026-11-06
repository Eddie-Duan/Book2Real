"""Recraft client for the 纸间生灵 pipeline.

Reads `.env` (recraft/*), never takes a key from anywhere else, and enforces a
per-run spend cap so an accidental loop cannot burn the account. Every call is
appended to `runs/api_log.jsonl` with its credit cost, which is how the real
credit <-> dollar ratio gets calibrated instead of guessed.

Endpoints used (OpenAPI: https://www.recraft.ai/docs/api-reference/openapi.json):
    POST /v1/images/inpaint        image + mask + prompt -> retouched image
    POST /v1/images/eraseRegion    image + mask -> paper (no prompt, cheapest)
    GET  /v1/users/me              balance / auth check
Auth is `Authorization: Bearer <token>`. Bodies are JSON with `image_url` /
`mask_url` as data URLs.
"""
from __future__ import annotations

import base64
import io
import json
import os
import time
from pathlib import Path

DEFAULT_BASE = "https://external.api.recraft.ai"
LOG = Path("runs") / "api_log.jsonl"
# the endpoint is slow from some networks (measured 20 s for users/me)
TIMEOUT = 180


class RecraftError(RuntimeError):
    pass


def _env() -> dict:
    env = {}
    p = Path(".env")
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    for k in list(env):                        # process env wins over .env
        if os.environ.get(k):
            env[k] = os.environ[k]
    return env


def config() -> dict:
    env = _env()
    return {
        "token": env.get("RECRAFT_API_TOKEN", ""),
        "base": env.get("RECRAFT_API_BASE") or DEFAULT_BASE,
        "model": env.get("RECRAFT_MODEL") or "recraftv4_1",
        "fmt": env.get("RECRAFT_OUTPUT_FORMAT") or "png",
        "max_spend": float(env.get("RECRAFT_MAX_SPEND_USD") or 2.0),
    }


def _money() -> float:
    """Dollars spent so far this month, from the call log."""
    if not LOG.exists():
        return 0.0
    total = 0.0
    stamp = time.strftime("%Y-%m")
    for line in LOG.read_text(encoding="utf-8").splitlines():
        try:
            rec = json.loads(line)
        except Exception:
            continue
        if str(rec.get("at", "")).startswith(stamp):
            total += float(rec.get("usd", 0.0))
    return total


def _log(rec: dict) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _hard_mask(mask):
    """Recraft only accepts pure 0/255 masks; a blurred edge is a 400 error."""
    return mask.convert("L").point(lambda v: 255 if v >= 128 else 0)


def _data_url(png: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(png).decode("ascii")


def _png(im) -> bytes:
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


def users_me() -> dict:
    import requests

    cfg = config()
    if not cfg["token"]:
        raise RecraftError("RECRAFT_API_TOKEN is empty in .env")
    r = requests.get(cfg["base"] + "/v1/users/me",
                     headers={"Authorization": "Bearer " + cfg["token"]},
                     timeout=TIMEOUT)
    if r.status_code != 200:
        raise RecraftError(f"users/me {r.status_code}: {r.text[:200]}")
    return r.json()


def _post(path: str, payload: dict, *, usd: float) -> dict:
    import requests

    cfg = config()
    if not cfg["token"]:
        raise RecraftError("RECRAFT_API_TOKEN is empty in .env")
    spent = _money()
    if spent + usd > cfg["max_spend"]:
        raise RecraftError(f"spend cap: ${spent:.2f} spent, this call ${usd:.2f}, "
                           f"cap ${cfg['max_spend']:.2f} (raise RECRAFT_MAX_SPEND_USD)")
    t0 = time.time()
    r = requests.post(cfg["base"] + path, json=payload,
                      headers={"Authorization": "Bearer " + cfg["token"],
                               "Content-Type": "application/json"},
                      timeout=TIMEOUT)
    took = round(time.time() - t0, 1)
    rec = {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "path": path, "status": r.status_code,
           "seconds": took, "usd": usd if r.status_code == 200 else 0.0}
    if r.status_code != 200:
        rec["error"] = r.text[:300]
        _log(rec)
        raise RecraftError(f"{path} {r.status_code}: {r.text[:300]}")
    body = r.json()
    rec["credits"] = body.get("credits")
    _log(rec)
    return body


def _download(item: dict):
    from PIL import Image
    import requests

    if item.get("b64_json"):
        return Image.open(io.BytesIO(base64.b64decode(item["b64_json"]))).convert("RGBA")
    if item.get("url"):
        r = requests.get(item["url"], timeout=TIMEOUT)
        r.raise_for_status()
        return Image.open(io.BytesIO(r.content)).convert("RGBA")
    raise RecraftError("response has neither b64_json nor url")


def inpaint(image, mask, prompt: str, *, n: int = 1, usd_each: float = 0.04) -> tuple[list, dict]:
    """Regenerate the masked region; returns (images, response summary)."""
    cfg = config()
    payload = {
        "image_url": _data_url(_png(image.convert("RGB"))),
        "mask_url": _data_url(_png(_hard_mask(mask))),
        "prompt": prompt,
        "n": n,
        "model": cfg["model"],
        "response_format": "b64_json",
    }
    body = _post("/v1/images/inpaint", payload, usd=usd_each * n)
    return [_download(it) for it in body.get("data", [])], body


def erase(image, mask, *, usd: float = 0.002):
    cfg = config()
    payload = {
        "image_url": _data_url(_png(image.convert("RGB"))),
        "mask_url": _data_url(_png(_hard_mask(mask))),
        "response_format": "b64_json",
    }
    body = _post("/v1/images/eraseRegion", payload, usd=usd)
    return _download(body["image"]), body
