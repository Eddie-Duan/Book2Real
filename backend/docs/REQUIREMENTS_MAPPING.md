# 赛题要求 ↔ 后端实现对照

> 只列与后端（OCR/ASR/TTS/问答/检索/会话/延迟）相关的口径；文生图、文生视频、前端播放与触控界面由队友负责。

| 赛题口径 | 实现 | 状态 |
| --- | --- | --- |
| 输入是绘本 PDF，一册一个 PDF | `POST /api/books/ingest` / `tools/ingest_competition.py` | ✅ |
| 提取逐页文字、页码、图文结构，可与原书核对 | `app/book/ocr.py`：文字层 + RapidOCR + blocks/bbox；`book.json` | ✅ |
| PDF 内已标注可点击元素 | 本批 6 本 PDF 实测 `annots=0/links=0`；用脚本 `[点击]` + OCR/页序定位兜底；`elements[]` 输出 name/page/bbox/info | ✅（依赖脚本） |
| OCR 可用 VLM 替代，不强制独立 OCR | 当前 RapidOCR；Provider 可替换 | ✅ |
| ASR 16k/16bit 单声道 Query 音频 | `/api/asr`、WS 二进制 PCM；本地 large-v3 + 热词 | ✅ |
| 语音首响 ≤2s（说完→TTS 首音） | SAPI 10–80ms / MiniMax 流式；实测 0.45–1.27s | ✅ |
| 首响按达标比例统计，warmup 后统计 | `/api/metrics/latency`：hit_ratio、p50/p90/p95；warmup 排除 | ✅ |
| 回答正确率按 Query 条数 | `/api/ask` 返回 answer/action/evidence；`tools/eval_queries.py` 批量核对 | ✅（依赖 LLM 质量） |
| 视频内检索：跳帧、框选元素、描述帧、查字并框出 | `/api/video/{id}/search`、`frame`、`describe`、`elements`；帧 OCR + 时间戳/bbox | ✅（描述为页文本/元素卡，可接 VLM） |
| 触控：点击→暂停→展示信息；对象级 | `elements[]` + `action.element_card`；前端执行暂停/展示 | ✅（bbox 依赖脚本/ manifest） |
| 多模态对话历史覆盖当前绘本、跨轮上下文 | SQLite `messages`（文本/页/元素/图片引用/动作）；LLM 带最近 8 轮 | ✅ |
| 跨语言问答（中英） | `aliases.py` 检索扩展 + prompt 要求用提问语言回答 | ✅（LLM 下最佳） |
| 隐藏测试集/现场新绘本实时入库 | 上传 PDF + 脚本即可；入库约 25–50s/本（含 OCR） | ✅ |
| 现场服务端与演示同机/局域网 | `--host 0.0.0.0`；页图/WS/HTTP 全部本机 | ✅ |
| 儿童内容安全 | `MODERATION_ENABLED` 基础敏感词兜底；日志默认不存音频 | ✅（需人工复核责任人） |
| 模型/API 披露 | `.env` 配置；MiniMax/DeepSeek 调用范围可写进说明书 | ✅ |
| 文生图/文生视频 | 不在本仓库 | 队友 |
| 前端播放/触控界面 | 不在本仓库；契约见 `docs/INTEGRATION.md` | 队友 |

## 明确未做/边界

1. **视觉场景定位**：英文绘本是扫描页，插图页没有文字；纯文本无法精确判断“电影海报/机器人/游戏机”在第几页。用 `manifest` 覆盖最稳；CLIP 尝试过但本机 HF 网络不可达。
2. **VLM 帧描述**：`describe` 目前返回该帧 OCR 文本 + 对应页故事/元素卡，不是生成式画面描述；如赛题要求自然语言描述，需要接一个 VLM（可复用官方多模态模型或本地 Qwen-VL）。
3. **MiniMax ASR**：公开文档未确认 ASR 接口；当前 ASR 用本地 Whisper，Provider 已留好。
4. **答案质量**：提取式兜底只能摘抄原文；正式演示务必接 DeepSeek/官方 LLM，并按 `docs/LATENCY_2S.md` 实测首 token。
5. **音频留存**：默认不存原始音频；如复盘需要，赛后临时开启并脱敏/删除。
