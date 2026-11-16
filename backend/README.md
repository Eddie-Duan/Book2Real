# 纸书智伴 · 语音后端

面向「纸书智伴」赛题的**后端**实现：绘本 PDF 内容理解（OCR/结构化）、本地/云端 ASR、流式 TTS、语音问答、视频内文字检索、触控元素卡、多模态会话与 2 秒首音指标。

> 本仓库只负责后端。文生图、文生视频、前端播放/触控界面由队友负责；接口契约见 [docs/INTEGRATION.md](docs/INTEGRATION.md)。

---

## 1. 能力清单

| 模块 | 状态 | 说明 |
| --- | --- | --- |
| 绘本入库 | ✅ | PDF 文字层 + RapidOCR 扫描页兜底；过滤赛事水印/教学栏；输出页图 + 页文本 + bbox |
| 脚本文本 | ✅ | 解析 `中文绘本01.txt` 等脚本：必选动作、点击目标、找画面/查文字/播放控制 |
| 页面定位 | ✅ | 单调 DP 把 50 条脚本动作对齐到页；支持中英别名扩展；可被 manifest 覆盖 |
| ASR | ✅ | 本地 faster-whisper large-v3（热词偏置）；也支持已有 9881 HTTP ASR；Provider 可换云端 |
| TTS | ✅ | Windows SAPI（10–80ms，本地联调首选）；CosyVoice3 流式服务（9890）；MiniMax SSE 流式客户端（填 key 即用） |
| 语音回合 | ✅ | VAD → ASR → 意图路由/检索 → LLM 流 → 小句切分 → TTS 流；首音兜底填充 |
| 问答 | ✅ | BM25 检索 + 意图路由（找画面/查文字/播放/触控/词语解释/内容问答）；LLM 可换 DeepSeek/OpenAI 兼容 |
| 视频内检索 | ✅ | 帧采样 + 帧 OCR；查字返回时间戳/bbox；场景/元素按 manifest 提供 |
| 会话历史 | ✅ | SQLite：文本 + 页/时间/元素/图片引用 + 动作，支持多轮上下文 |
| 延迟指标 | ✅ | 每回合 speech_end→首音、ASR/LLM/TTS 分段耗时；p50/p90/p95、达标率、填充音比例 |
| 预热 | ✅ | 模型/音色/填充音预热；`/api/warmup`；warmup 回合不计入指标 |

---

## 2. 快速开始（Windows，本地联调）

前提：Python 3.10（已装 `fastapi/uvicorn/httpx/faster-whisper/rapidocr_onnxruntime/opencv/PyMuPDF` 等，见 `requirements.txt`）。

```bash
cd paperbook-backend
python -m pip install -r requirements.txt

# 1) 首次：把 6 本绘本入库（约 4 分钟，含英文扫描页 OCR）
#    赛事素材不随仓库提供，请向组委会获取后填本地路径
python tools/ingest_competition.py --zip "<赛事素材zip路径>/纸书智伴企业预选赛绘本素材.zip"

# 2) 配置（.env 已带本地默认值；也可以复制 .env.example）
#    TTS_PROVIDER=sapi  ASR_PROVIDER=local  LLM_PROVIDER=extractive

# 3) 启动
python -m uvicorn app.main:app --host 0.0.0.0 --port 8790
```

> 赛事 PDF/音频有版权与保密限制：`data/`、`.env`、`pip-cache/` 已加入 `.gitignore`，**不要**把原始绘本、Query 音频或 API key 提交到仓库。

打开 <http://127.0.0.1:8790/docs> 查看交互式 API；<http://127.0.0.1:8790/health> 查看预热/Provider 状态。

### 本地 TTS 选择

| Provider | 首块 | 音质 | 用途 |
| --- | --- | --- | --- |
| `sapi`（默认） | **10–80ms** | Windows 系统音色（中/英） | 本地联调、断网兜底、2s 首音压测 |
| `local` | ~2.5s（CosyVoice3） | 克隆音色，明显更好 | 需要音质时；先启动 `scripts/start_local_tts.py` |
| `minimax` | 待实测（预期 0.2–0.5s） | 官方云端音色 | 11.30 拿到官方 API 后切换 |

CosyVoice3 流式服务（可选）：

```bat
set COSYVOICE_HOME=<CosyVoice 仓库目录>
set COSYVOICE_PY=<你的 cosyvoice 环境>\python.exe
"%COSYVOICE_PY%" scripts\start_local_tts.py --engine cosyvoice --port 9890
```

> IndexTTS-2.5 在本机 8GB 显存上实测 RTF≈36（s2mel 极慢），**不建议**用于实时；已保留脚本但默认不启用。

### MiniMax 云端切换（拿到 API 后）

```ini
TTS_PROVIDER=minimax
MINIMAX_API_KEY=<your-key>
MINIMAX_GROUP_ID=<your-group-id>
MINIMAX_VOICE_ID=<your-voice-id>
MINIMAX_TTS_MODEL=speech-2.6-turbo
MINIMAX_HOST=https://api.minimaxi.com
```

TTS 客户端按 SSE 解析 `data.audio`（hex PCM），支持 `stream_options.exclude_aggregated_audio`，首块即转发。ASR 仍是本地 Whisper；MiniMax 若提供 ASR 接口，按 `app/providers/base.py` 的 `ASRProvider` 新增实现即可。

---

## 3. 实测首音（本地，2026-11-16）

测试方式：`tools/ws_latency_probe.py` 把 Query wav 按 20ms 分帧推给 WebSocket，服务端 VAD 判停后统计 **真实说完 → 第一块音频**（已扣除 VAD 尾音）。

| Query | ASR | 首音（真实说完起算） | 结果 |
| --- | --- | --- | --- |
| `高亮“积木城堡”` | 656ms | **1227ms** | 命中，返回页内 bbox |
| `找到花花独处的画面` | 586ms | **1174ms** | 命中 page 7 |
| `Where is Xiao Man?` | 662ms | **1210ms** | 命中 page 7（别名 小满） |
| `What is the dog?` | 513ms | **1069ms** | 检索到 page 10；复杂回答建议接 LLM |

批量实测（`tools/eval_queries.py`，本地 SAPI + Whisper）：**119 回合，2s 达标率 100%，p50 547.5ms，p90 985ms，p95 1150ms，最大 1267ms，填充音 0%**；6 本绘本各抽 5 条 Query 共 30/30 全部达标。

- 目标 2000ms，当前本地余量约 700–1000ms；
- 填充音在 **1400ms** 仍未出真音频时兜底，确保任何抖动下首音 <2s；
- 详细预算与调参见 [docs/LATENCY_2S.md](docs/LATENCY_2S.md)。

---

## 4. 目录

```
app/
  main.py                 FastAPI 路由 + WebSocket
  config.py               环境变量配置
  providers/              ASR / TTS / LLM 适配（local/http/minimax/sapi/mock）
  pipeline/               VAD、切句、回合编排、填充音、时间线
  book/                   PDF 入库、OCR、BM25 索引、意图路由、视频索引、SQLite
scripts/
  start_local_tts.py      CosyVoice3 / IndexTTS 本地流式 TTS 服务
  *.cmd                   Windows 一键启动
tools/
  ingest_competition.py   6 本绘本入库
  ws_latency_probe.py     WebSocket 首音探针
  eval_queries.py         50 条 Query 批量跑分（ASR/回答/延迟）
tests/                    单元测试（pytest）
docs/                     延迟、集成、OCR、API 文档
data/                     运行时生成（页图/索引/DB/缓存），已 gitignore
```

---

## 5. 常用接口

| 接口 | 用途 |
| --- | --- |
| `POST /api/ask` | 文本或音频问答，返回 answer/action/evidence/audio_b64/timings |
| `POST /api/ask/stream` | NDJSON 流式事件（含 base64 音频块） |
| `WS /ws/voice/{session}` | 麦克风 PCM 流式回合，低延迟主通道 |
| `POST /api/asr` | 音频文件转写 |
| `POST /api/tts` | 文本 → PCM16 流 |
| `GET /api/books` / `/api/books/{id}` | 绘本列表/详情 |
| `POST /api/books/ingest` | 上传 PDF + 脚本入库 |
| `POST /api/books/ingest-materials` | 指定 zip 批量入库 |
| `GET /api/video/{id}/search?q=` | 视频帧文字检索（查字并框出） |
| `GET /api/video/{id}/describe?t=&box=` | 帧/框选描述（基于页文本/元素卡） |
| `GET /api/sessions/{id}/history` | 多模态会话历史 |
| `GET /api/metrics/latency` | 首音 p50/p90/p95、2s 达标率、填充音比例 |
| `POST /api/warmup` | 重新预热（TTS/ASR/填充音） |

详细字段见 [docs/INTEGRATION.md](docs/INTEGRATION.md) 与 `/docs`。

---

## 6. 测试

```bash
python -m pytest -q
# 18 passed（切句/VAD/检索/意图/回合/填充音/脚本解析）
```

批量跑 Query（需先启动服务与入库）：

```bash
python tools/eval_queries.py --book cn01 --base http://127.0.0.1:8790 --limit 50 --out data/eval_cn01.json
```

输出每条 Query 的脚本原文、ASR 文本、回答、动作、页码、首音耗时，便于人工核对正确率。

---

## 7. 已知边界

- **英文绘本是扫描页**：文字层只有赛事水印，入库靠 RapidOCR；插图页没有文字，脚本里的“找到某个画面”只能靠页序/关键词近似，建议视频组用 `manifest` 精确覆盖场景与元素 bbox。
- **本地 SAPI 音色**是系统音色，适合联调/兜底；正式演示切 MiniMax 或 CosyVoice。
- **提取式 LLM 兜底**只能摘抄原文，复杂“为什么/跨语言”问题需要接 DeepSeek/官方模型；接口已就绪，填 key 即切换。
- **MiniMax ASR** 未验证到公开接口；当前 ASR 用本地 large-v3（热词偏置），延迟 0.45–0.7s。若官方提供 ASR，按 Provider 接口接入。
- **CLIP 视觉场景匹配**尝试过，但本机 HF 网络不可达，未启用；后续可离线装 CLIP 提升英文绘本的场景定位。
- 赛事素材有版权限制：`data/` 与原始 PDF/音频不要提交到公开仓库；`.gitignore` 已排除。

---

## 8. 赛题合规提示

- 不预置/硬编码测试 Query 的答案；`eval_queries.py` 只做本地评测与人工核对。
- 交互日志默认只存文本/页/元素引用，不存原始音频（`STORE_AUDIO=false`）。
- 儿童内容安全：`MODERATION_ENABLED=true` 时有基础敏感词兜底；正式提交需按要求落实人工复核责任人与年龄适配标注。
- 第三方服务（MiniMax/DeepSeek/模型）需在项目说明书中披露调用范围与授权。
