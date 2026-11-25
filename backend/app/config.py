"""配置：全部来自环境变量 / .env，不依赖 pydantic-settings。"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = ROOT.parent  # Book2Real/ — the repo this backend was merged into


def _load_dotenv(path: Path) -> None:
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


# 融合仓库：单一 .env 放在仓库根目录；backend/.env 只补缺（setdefault 语义）
_load_dotenv(REPO_ROOT / ".env")
_load_dotenv(ROOT / ".env")


def _s(key: str, default: str = "") -> str:
    return os.environ.get(key, default).strip()


def _i(key: str, default: int) -> int:
    try:
        return int(_s(key, str(default)))
    except ValueError:
        return default


def _f(key: str, default: float) -> float:
    try:
        return float(_s(key, str(default)))
    except ValueError:
        return default


def _b(key: str, default: bool) -> bool:
    return _s(key, "1" if default else "0").lower() in {"1", "true", "yes", "on"}


def _expand(value: str) -> str:
    return os.path.expandvars(os.path.expanduser(value))


def _list(key: str, default: str) -> list[str]:
    return [x.strip() for x in _s(key, default).split(",") if x.strip()]


def _first(*names: str) -> str:
    """返回第一个非空的同名环境变量（融合仓库沿用 Book2Real 的 *-API-* 命名）。"""
    for name in names:
        value = _s(name)
        if value:
            return value
    return ""


@dataclass
class Settings:
    # 服务
    host: str = field(default_factory=lambda: _s("APP_HOST", "0.0.0.0"))
    port: int = field(default_factory=lambda: _i("APP_PORT", 8790))
    data_dir: Path = field(default_factory=lambda: Path(_expand(_s("DATA_DIR", str(REPO_ROOT / "data")))).resolve())
    log_level: str = field(default_factory=lambda: _s("LOG_LEVEL", "INFO").upper())
    api_token: str = field(default_factory=lambda: _s("API_TOKEN"))
    cors_origins: list[str] = field(default_factory=lambda: _list("CORS_ORIGINS", "*"))

    # ASR
    asr_provider: str = field(default_factory=lambda: _s("ASR_PROVIDER", "local").lower())
    asr_model: str = field(default_factory=lambda: _s("ASR_MODEL", "large-v3"))
    asr_device: str = field(default_factory=lambda: _s("ASR_DEVICE", "cuda"))
    asr_compute: str = field(default_factory=lambda: _s("ASR_COMPUTE", "float16"))
    asr_language: str = field(default_factory=lambda: _s("ASR_LANGUAGE"))
    asr_http_url: str = field(default_factory=lambda: _s("ASR_HTTP_URL", "http://127.0.0.1:9881/asr"))
    asr_preload: bool = field(default_factory=lambda: _b("ASR_PRELOAD", True))
    asr_partial_ms: int = field(default_factory=lambda: _i("ASR_PARTIAL_MS", 0))
    asr_partial_window_s: float = field(default_factory=lambda: _f("ASR_PARTIAL_WINDOW_S", 4.0))

    # TTS
    tts_provider: str = field(default_factory=lambda: _s("TTS_PROVIDER", "auto").lower())
    tts_local_url: str = field(default_factory=lambda: _s("TTS_LOCAL_URL", "http://127.0.0.1:9890").rstrip("/"))
    tts_local_batch_url: str = field(default_factory=lambda: _s("TTS_LOCAL_BATCH_URL", "http://127.0.0.1:9886/tts"))
    tts_local_ref_audio: str = field(default_factory=lambda: _expand(_s("TTS_LOCAL_REF_AUDIO", "")))
    tts_local_prompt_text: str = field(default_factory=lambda: _s("TTS_LOCAL_PROMPT_TEXT", ""))
    tts_local_speed: float = field(default_factory=lambda: _f("TTS_LOCAL_SPEED", 1.0))
    tts_sample_rate: int = field(default_factory=lambda: _i("TTS_SAMPLE_RATE", 24000))
    tts_timeout_s: float = field(default_factory=lambda: _f("TTS_TIMEOUT_S", 90.0))
    tts_max_clause_chars: int = field(default_factory=lambda: _i("TTS_MAX_CLAUSE_CHARS", 48))
    sapi_voice_zh: str = field(default_factory=lambda: _s("SAPI_VOICE_ZH", "Huihui"))
    sapi_voice_en: str = field(default_factory=lambda: _s("SAPI_VOICE_EN", "Zira"))
    sapi_rate: int = field(default_factory=lambda: _i("SAPI_RATE", 0))

    # MiniMax
    minimax_host: str = field(default_factory=lambda: _s("MINIMAX_HOST", "https://api.minimaxi.com").rstrip("/"))
    minimax_api_key: str = field(default_factory=lambda: _s("MINIMAX_API_KEY"))
    minimax_group_id: str = field(default_factory=lambda: _s("MINIMAX_GROUP_ID"))
    minimax_tts_model: str = field(default_factory=lambda: _s("MINIMAX_TTS_MODEL", "speech-2.6-turbo"))
    minimax_voice_id: str = field(default_factory=lambda: _s("MINIMAX_VOICE_ID"))
    minimax_voice_id_en: str = field(default_factory=lambda: _s("MINIMAX_VOICE_ID_EN"))
    minimax_sample_rate: int = field(default_factory=lambda: _i("MINIMAX_SAMPLE_RATE", 24000))
    minimax_language_boost: str = field(default_factory=lambda: _s("MINIMAX_LANGUAGE_BOOST", "auto"))
    minimax_speed: float = field(default_factory=lambda: _f("MINIMAX_SPEED", 1.0))
    minimax_vol: float = field(default_factory=lambda: _f("MINIMAX_VOL", 1.0))
    minimax_pitch: int = field(default_factory=lambda: _i("MINIMAX_PITCH", 0))
    minimax_emotion: str = field(default_factory=lambda: _s("MINIMAX_EMOTION"))
    minimax_asr_model: str = field(default_factory=lambda: _s("MINIMAX_ASR_MODEL", "asr-1.0"))
    minimax_asr_language: str = field(default_factory=lambda: _s("MINIMAX_ASR_LANGUAGE"))

    # LLM
    llm_provider: str = field(default_factory=lambda: (_s("LLM_PROVIDER") or ("openai" if _first("LLM_API_KEY", "DEEPSEEK_API_KEY") else "extractive")).lower())
    llm_base_url: str = field(default_factory=lambda: (_first("LLM_BASE_URL", "LLM_API_BASE_URL") or "https://api.deepseek.com/v1").rstrip("/"))
    llm_api_key: str = field(default_factory=lambda: _first("LLM_API_KEY", "DEEPSEEK_API_KEY"))
    llm_model: str = field(default_factory=lambda: _first("LLM_MODEL", "LLM_API_MODEL") or "deepseek-chat")
    llm_timeout_s: float = field(default_factory=lambda: _f("LLM_TIMEOUT_S", 20.0))
    llm_max_tokens: int = field(default_factory=lambda: _i("LLM_MAX_TOKENS", 120))
    llm_temperature: float = field(default_factory=lambda: _f("LLM_TEMPERATURE", 0.3))
    llm_translate_timeout_s: float = field(default_factory=lambda: _f("LLM_TRANSLATE_TIMEOUT_S", 2.0))

    # VAD / 延迟
    vad_frame_ms: int = field(default_factory=lambda: _i("VAD_FRAME_MS", 20))
    vad_silence_ms: int = field(default_factory=lambda: _i("VAD_SILENCE_MS", 500))
    vad_min_speech_ms: int = field(default_factory=lambda: _i("VAD_MIN_SPEECH_MS", 100))
    vad_energy_ratio: float = field(default_factory=lambda: _f("VAD_ENERGY_RATIO", 3.2))
    vad_min_rms: float = field(default_factory=lambda: _f("VAD_MIN_RMS", 0.006))
    filler_enabled: bool = field(default_factory=lambda: _b("FILLER_ENABLED", True))
    filler_deadline_ms: int = field(default_factory=lambda: _i("FILLER_DEADLINE_MS", 1500))
    filler_zh: str = field(default_factory=lambda: _s("FILLER_ZH", "嗯，我看看。"))
    filler_en: str = field(default_factory=lambda: _s("FILLER_EN", "Hmm, let me see."))
    # 每语言预合成句数；用掉一句后台补一句，避免 warmup 一次打 33 句触发 TTS 限流
    ack_prewarm_lines: int = field(default_factory=lambda: _i("ACK_PREWARM_LINES", 5))
    ack_refill: bool = field(default_factory=lambda: _b("ACK_REFILL", True))
    # 真答案的最早播放起点（相对"话音结束"）。只推迟播放，不推迟计算：
    # 缓存命中时答案 1ms 就绪，直接播会显得没有思考过程；统一压到 0.9s 再开口。
    answer_min_start_ms: int = field(default_factory=lambda: _i("ANSWER_MIN_START_MS", 900))
    latency_budget_ms: int = field(default_factory=lambda: _i("LATENCY_BUDGET_MS", 2000))
    warmup_on_start: bool = field(default_factory=lambda: _b("WARMUP_ON_START", True))

    # OCR / 视频
    ocr_dpi: int = field(default_factory=lambda: _i("OCR_DPI", 150))
    ocr_min_text_chars: int = field(default_factory=lambda: _i("OCR_MIN_TEXT_CHARS", 40))
    ocr_use_rapidocr: bool = field(default_factory=lambda: _b("OCR_USE_RAPIDOCR", True))
    ocr_supplement: bool = field(default_factory=lambda: _b("OCR_SUPPLEMENT", True))
    ocr_langs: list[str] = field(default_factory=lambda: _list("OCR_LANGS", "ch,en"))
    video_sample_fps: float = field(default_factory=lambda: _f("VIDEO_SAMPLE_FPS", 1.0))
    video_max_frames: int = field(default_factory=lambda: _i("VIDEO_MAX_FRAMES", 900))

    # 数据
    store_audio: bool = field(default_factory=lambda: _b("STORE_AUDIO", False))
    session_history_turns: int = field(default_factory=lambda: _i("SESSION_HISTORY_TURNS", 8))
    moderation_enabled: bool = field(default_factory=lambda: _b("MODERATION_ENABLED", True))
    fast_answer: bool = field(default_factory=lambda: _b("FAST_ANSWER", False))
    # 人物/关系类问题：先垫话，再校验答案；校验不过就拖/澄清，绝不播错答案。
    verified_answers: bool = field(default_factory=lambda: _b("VERIFIED_ANSWERS", True))
    answer_verify_timeout_s: float = field(default_factory=lambda: _f("ANSWER_VERIFY_TIMEOUT_S", 6.0))
    chitchat_cache: bool = field(default_factory=lambda: _b("CHITCHAT_CACHE", True))

    # Skill 4：点击回答默认复用预存卡片（零延迟）；打开后走同一套文风 prompt
    style_touch_llm: bool = field(default_factory=lambda: _b("STYLE_TOUCH_LLM", False))

    @property
    def books_dir(self) -> Path:
        return self.data_dir / "books"

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache"

    @property
    def source_dir(self) -> Path:
        return self.data_dir / "source"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "paperbook.sqlite3"

    def ensure_dirs(self) -> None:
        for path in (self.data_dir, self.books_dir, self.cache_dir, self.source_dir):
            path.mkdir(parents=True, exist_ok=True)


settings = Settings()
