"""The three cloud models the official brief names.

    DeepSeek        text / story / QA            (OpenAI-compatible chat)
    Doubao Seedream image generation             (Volcengine Ark, synchronous)
    Seedance        video generation             (Volcengine Ark, async task)

All three are plain HTTP + JSON, so this module uses ``urllib`` only: the demo
box must not gain a dependency just to talk to an API. Keys live in ``.env``
(never in git); see ``docs/API_KEYS.md``.

Two conventions matter for the competition:

* **one ledger for all spend** — every call appends to ``runs/cloud/ledger.jsonl``
  and is charged against ``CLOUD_MAX_SPEND_CNY`` before the request is sent;
* **Ark needs an endpoint id as well as a key** — the organiser may hand over
  ``ep-xxxx`` ids instead of model names, so the model is always configurable
  (``SEEDREAM_MODEL`` / ``SEEDANCE_MODEL``) and surfaced by ``cloud --check``.
"""
from __future__ import annotations

import base64
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / "runs" / "cloud" / "ledger.jsonl"

DEEPSEEK_BASE = "https://api.deepseek.com"
DEEPSEEK_MODEL = "deepseek-chat"
ARK_BASE = "https://ark.cn-beijing.volces.com/api/v3"
SEEDREAM_MODEL = "doubao-seedream-3-0-t2i-250415"
SEEDANCE_MODEL = "doubao-seedance-1-0-lite-t2v-250428"


class CloudError(RuntimeError):
    """A provider call failed; the message always names the provider."""


class BudgetError(CloudError):
    """The next call would exceed ``CLOUD_MAX_SPEND_CNY``."""


# --------------------------------------------------------------------------- env
def load_env(path: str | Path | None = None) -> dict:
    """Read ``.env`` into a dict *and* into ``os.environ`` (without overriding).

    The readers/QA modules look at ``os.environ``; the CLI tools look at the
    dict. Loading once here keeps both honest.
    """
    env: dict[str, str] = {}
    p = Path(path) if path else ROOT / ".env"
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if not s or s.startswith("#") or "=" not in s:
                continue
            k, v = s.split("=", 1)
            env[k.strip()] = v.strip()
    for k, v in env.items():
        os.environ.setdefault(k, v)
    return env


def _cfg(env: dict | None = None) -> dict:
    e = env if env is not None else {**os.environ, **load_env()}
    return {
        "deepseek_key": e.get("DEEPSEEK_API_KEY") or e.get("LLM_API_KEY", ""),
        "deepseek_base": (e.get("DEEPSEEK_BASE_URL") or e.get("LLM_API_BASE_URL")
                          or DEEPSEEK_BASE).rstrip("/"),
        "deepseek_model": e.get("DEEPSEEK_MODEL") or e.get("LLM_API_MODEL") or DEEPSEEK_MODEL,
        "ark_key": e.get("ARK_API_KEY") or e.get("VIDEO_API_KEY", ""),
        "ark_base": (e.get("ARK_BASE_URL") or e.get("VIDEO_API_BASE_URL")
                     or ARK_BASE).rstrip("/"),
        "seedream_model": e.get("SEEDREAM_MODEL") or e.get("IMAGE_API_MODEL") or SEEDREAM_MODEL,
        "seedance_model": e.get("SEEDANCE_MODEL") or e.get("VIDEO_API_MODEL") or SEEDANCE_MODEL,
        "budget": float(e.get("CLOUD_MAX_SPEND_CNY") or 50.0),
        "price_image": float(e.get("SEEDREAM_CNY_PER_IMAGE") or 0.2),
        "price_video_s": float(e.get("SEEDANCE_CNY_PER_SEC") or 1.0),
        "price_1k_tokens": float(e.get("DEEPSEEK_CNY_PER_1K_TOKENS") or 0.002),
    }


def provider_status(env: dict | None = None) -> list[dict]:
    """Who is ready and who still needs a key; used by ``cloud --check``."""
    c = _cfg(env)
    return [
        {"provider": "deepseek", "ready": bool(c["deepseek_key"]),
         "base": c["deepseek_base"], "model": c["deepseek_model"],
         "env": "DEEPSEEK_API_KEY"},
        {"provider": "seedream", "ready": bool(c["ark_key"]),
         "base": c["ark_base"], "model": c["seedream_model"],
         "env": "ARK_API_KEY"},
        {"provider": "seedance", "ready": bool(c["ark_key"]),
         "base": c["ark_base"], "model": c["seedance_model"],
         "env": "ARK_API_KEY"},
    ]


# ------------------------------------------------------------------------- budget
def spent_cny(ledger: Path = LEDGER) -> float:
    total = 0.0
    if ledger.exists():
        for line in ledger.read_text(encoding="utf-8").splitlines():
            try:
                total += float(json.loads(line).get("cny") or 0)
            except Exception:                     # noqa: BLE001 - a torn line is not fatal
                continue
    return round(total, 4)


def _charge(provider: str, model: str, cny: float, units: dict, note: str = "",
            ledger: Path = LEDGER) -> None:
    ledger.parent.mkdir(parents=True, exist_ok=True)
    rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "provider": provider,
           "model": model, "cny": round(cny, 4), "units": units, "note": note}
    with ledger.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _guard(cost: float, env: dict | None = None) -> None:
    c = _cfg(env)
    if cost <= 0:
        return
    used = spent_cny()
    if used + cost > c["budget"] + 1e-9:
        raise BudgetError(
            f"预算不足：已花 元{used:.2f}，本次约 元{cost:.2f}，上限 元{c['budget']:.2f}。"
            f" 调整 CLOUD_MAX_SPEND_CNY 或换更便宜的档位。")
    print(f"[cloud] 预计 元{cost:.3f}（已用 元{used:.2f} / 元{c['budget']:.2f}）", flush=True)


# -------------------------------------------------------------------------- HTTP
def _request(method: str, url: str, payload: dict | None, headers: dict,
             timeout: int, retries: int = 2) -> dict:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    last = ""
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, data=body, method=method,
                                     headers={"Content-Type": "application/json", **headers})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:   # noqa: S310
                raw = resp.read()
                return json.loads(raw.decode("utf-8")) if raw else {}
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:500]
            last = f"HTTP {exc.code}: {detail}"
            if exc.code in (400, 401, 403, 404):
                break                              # retrying a bad request is pointless
        except Exception as exc:                   # noqa: BLE001 - network noise
            last = f"{type(exc).__name__}: {exc}"
        if attempt < retries:
            time.sleep(1.5 * (attempt + 1))
    raise CloudError(f"{url} 调用失败：{last}")


def _download(url: str, out: str | Path, timeout: int = 120) -> Path:
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": "book2real/0.1"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:   # noqa: S310
        data = resp.read()
    if not data:
        raise CloudError(f"下载为空：{url}")
    out.write_bytes(data)
    return out


# ---------------------------------------------------------------------- DeepSeek
def deepseek(messages: list[dict], *, model: str | None = None, temperature: float = 0.4,
             max_tokens: int = 1200, json_mode: bool = False, timeout: int = 90,
             env: dict | None = None, dry_run: bool = False) -> dict:
    """One chat completion. Returns ``{"text", "usage", "model", "cny"}``."""
    c = _cfg(env)
    model = model or c["deepseek_model"]
    url = c["deepseek_base"]
    if not url.endswith("/chat/completions"):
        url += "/v1/chat/completions" if not url.endswith("/v1") else "/chat/completions"
    payload = {"model": model, "messages": messages,
               "temperature": temperature, "max_tokens": max_tokens}
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    est = c["price_1k_tokens"] * 3.0               # a small request, charged after the fact
    _guard(est, env)
    if dry_run:
        return {"text": "", "usage": {}, "model": model, "cny": est, "dry_run": True}
    if not c["deepseek_key"]:
        raise CloudError("缺少 DEEPSEEK_API_KEY（或 LLM_API_KEY），见 docs/API_KEYS.md")
    res = _request("POST", url, payload, {"Authorization": f"Bearer {c['deepseek_key']}"},
                   timeout=timeout)
    usage = res.get("usage") or {}
    total_tokens = int(usage.get("total_tokens") or 0) or 1000
    cny = c["price_1k_tokens"] * total_tokens / 1000.0
    _charge("deepseek", model, cny, {"total_tokens": total_tokens}, note=json_mode and "json" or "")
    text = ""
    try:
        text = res["choices"][0]["message"]["content"] or ""
    except Exception:                              # noqa: BLE001
        raise CloudError(f"DeepSeek 返回不符合预期：{json.dumps(res, ensure_ascii=False)[:300]}")
    return {"text": text, "usage": usage, "model": model, "cny": round(cny, 4)}


# ---------------------------------------------------------------------- Seedream
def seedream(prompt: str, out: str | Path, *, model: str | None = None,
             size: str = "1024x1024", watermark: bool = False, timeout: int = 180,
             seed: int | None = None, env: dict | None = None,
             dry_run: bool = False) -> dict:
    """Generate one image into ``out``. Ark returns a 24 h URL or base64."""
    c = _cfg(env)
    model = model or c["seedream_model"]
    url = c["ark_base"] + "/images/generations"
    payload = {"model": model, "prompt": prompt, "size": size,
               "response_format": "url", "watermark": bool(watermark)}
    if seed is not None:
        payload["seed"] = int(seed)
    _guard(c["price_image"], env)
    if dry_run:
        return {"path": "", "model": model, "cny": c["price_image"], "dry_run": True}
    if not c["ark_key"]:
        raise CloudError("缺少 ARK_API_KEY，见 docs/API_KEYS.md")
    res = _request("POST", url, payload, {"Authorization": f"Bearer {c['ark_key']}"},
                   timeout=timeout)
    item = (res.get("data") or [{}])[0]
    if item.get("b64_json"):
        out = Path(out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(base64.b64decode(item["b64_json"]))
        return {"path": str(out), "model": model, "cny": c["price_image"], "url": ""}
    link = item.get("url") or ""
    if not link:
        raise CloudError(f"Seedream 未返回图片：{json.dumps(res, ensure_ascii=False)[:300]}")
    path = _download(link, out)
    _charge("seedream", model, c["price_image"], {"images": 1}, note=str(prompt)[:60])
    return {"path": str(path), "model": model, "cny": c["price_image"], "url": link}


# ---------------------------------------------------------------------- Seedance
def seedance(prompt: str, out: str | Path, *, image: str | Path | None = None,
             model: str | None = None, seconds: int = 5, resolution: str = "720p",
             ratio: str = "16:9", watermark: bool = False, poll_s: float = 5.0,
             timeout: int = 900, env: dict | None = None, dry_run: bool = False) -> dict:
    """Text-to-video (or image-to-video when ``image`` is given).

    Ark's video API is asynchronous: create a task, poll it, then download the
    result. ``seconds`` is the *model* duration; the price is per second.
    """
    c = _cfg(env)
    model = model or c["seedance_model"]
    base = c["ark_base"] + "/contents/generations/tasks"
    content = [{"type": "text", "text": prompt}]
    if image:
        img = Path(image)
        if not img.exists():
            raise FileNotFoundError(img)
        # Ark accepts a public URL; for local files use the data-URL form.
        mime = "image/png" if img.suffix.lower() == ".png" else "image/jpeg"
        content.append({"type": "image_url", "image_url": {
            "url": f"data:{mime};base64,{base64.b64encode(img.read_bytes()).decode()}"}})
    payload = {"model": model, "content": content, "duration": int(seconds),
               "resolution": resolution, "ratio": ratio, "watermark": bool(watermark)}
    cost = c["price_video_s"] * int(seconds)
    _guard(cost, env)
    if dry_run:
        return {"path": "", "model": model, "cny": cost, "dry_run": True}
    if not c["ark_key"]:
        raise CloudError("缺少 ARK_API_KEY，见 docs/API_KEYS.md")
    res = _request("POST", base, payload, {"Authorization": f"Bearer {c['ark_key']}"},
                   timeout=60)
    task_id = res.get("id") or (res.get("data") or {}).get("id")
    if not task_id:
        raise CloudError(f"Seedance 未返回任务 id：{json.dumps(res, ensure_ascii=False)[:300]}")
    deadline = time.time() + timeout
    state, link = "queued", ""
    while time.time() < deadline:
        time.sleep(poll_s)
        info = _request("GET", f"{base}/{task_id}", None,
                        {"Authorization": f"Bearer {c['ark_key']}"}, timeout=60)
        state = str(info.get("status") or "").lower()
        if state in ("succeeded", "success"):
            content_out = (info.get("content") or {})
            link = content_out.get("video_url") or (info.get("data") or {}).get("video_url") or ""
            if not link:
                raise CloudError(f"Seedance 成功但没有视频地址：{json.dumps(info, ensure_ascii=False)[:300]}")
            break
        if state in ("failed", "error", "cancelled"):
            raise CloudError(f"Seedance 任务失败：{json.dumps(info, ensure_ascii=False)[:400]}")
    if not link:
        raise CloudError(f"Seedance 任务超时（{timeout}s，最后状态 {state}）")
    path = _download(link, out, timeout=300)
    _charge("seedance", model, cost, {"seconds": int(seconds), "task": task_id},
            note=str(prompt)[:60])
    return {"path": str(path), "model": model, "cny": cost, "task": task_id, "url": link}
