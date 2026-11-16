# 2 秒首音：设计与实测

赛题口径（Q9/Q34）：**话音结束 → 用户听到 TTS 首音 ≤ 2s**，按达标比例统计；**warmup 后**再统计，首轮冷启动不计。

本文只讨论后端能做到的部分：ASR、检索/LLM、TTS 与它们之间的流水线。前端麦克风采集、播放器与网络抖动也会影响端到端，需要一起联调。

---

## 1. 流水线

```
麦克风 PCM16 16k
   │  20ms 帧
   ▼
[客户端 VAD] ── speech_start / speech_end(epoch ms) ──┐
   │                                                   │
   ▼                                                   │
[服务端 VAD] 500ms 静音判停，回推“最后说话帧”          │
   │                                                   │
   ▼                                                   │
[ASR] faster-whisper large-v3 + 本书热词                │ 0.45–0.70s
   │                                                   │
   ▼                                                   │
[意图路由] 播放/找画面/查文字/点击卡/词语/内容问答      │ ~0–5ms
   │                                                   │
   ▼                                                   │
[LLM 流式] 首 token 200–400ms（云端）/ 0ms（提取式）    │
   │                                                   │
   ▼                                                   │
[小句切分] 首块 ≥4 中文字/≥8 英文字母，遇标点立即发     │
   │                                                   │
   ▼                                                   │
[TTS] 首块 SAPI 10–80ms / MiniMax 200–500ms / Cosy 2.5s │
   │                                                   │
   ▼                                                   │
WebSocket 二进制 PCM16 ──> 前端 AudioContext 起播       │
```

关键：**TTS 不等整段回答**。LLM 一吐出第一个可朗读的小句，TTS 立即开始；后续小句在 LLM 继续生成时并行排队。

---

## 2. 延迟预算（本地实测 + 云端预估）

| 阶段 | 本地实测 | 云端预估 | 备注 |
| --- | --- | --- | --- |
| VAD 尾音 | 500ms | 500ms | 不计入外部 2s 时钟；内部指标会回推最后说话帧 |
| ASR | 450–700ms | 300–600ms | large-v3 热词；查询音频 1.9s |
| 意图/检索 | 0–5ms | 0–5ms | 纯内存 BM25；找画面/查字直接出动作 |
| LLM 首 token | 0ms（提取式） | 200–400ms | DeepSeek-Flash / 官方模型 |
| 首句累积 | 10–50ms | 30–100ms | 首块只等第一个标点/最短长度 |
| TTS 首块 | 10–80ms（SAPI） | 200–500ms（MiniMax） | CosyVoice3 本机约 2.5s，仅音质备用 |
| 网络/播放 | 20–80ms | 20–80ms | 同机/局域网 |
| **合计** | **0.6–1.3s** | **1.0–1.7s** | 目标 2s |

实测（`tools/ws_latency_probe.py`，真实说完起算）：

| Query | ASR | 首音 | 达标 |
| --- | --- | --- | --- |
| `高亮“积木城堡”` | 656ms | 1227ms | ✅ |
| `找到花花独处的画面` | 586ms | 1174ms | ✅ |
| `Where is Xiao Man?` | 662ms | 1210ms | ✅ |
| `What is the dog?` | 513ms | 1069ms | ✅ |

批量（`tools/eval_queries.py`，本地 SAPI + Whisper）：**119 回合，2s 达标率 100%，p50 547.5ms，p90 985ms，p95 1150ms，最大 1267ms，填充音 0%**。6 本绘本各抽 5 条 Query 共 30/30 全部达标；中文/英文、找画面/查文字/播放控制/内容问答都覆盖。

> 注：`/api/metrics/latency` 的 `first_audio_ms - speech_end_ms` 已经扣除 VAD 尾音，口径与赛题一致；`warmup=1` 的回合不参与统计。

---

## 3. 保证 2s 的六件事

### 3.1 预热（warmup）

- 服务启动后台 `AppState.warmup()`：加载 Whisper、TTS 预热、预合成中英填充音、LLM 探活。
- `/api/ready` 在预热完成后返回 200；`/health` 可看各 Provider 状态。
- 首轮冷启动按 Q34 不计入成绩；现场演示前先跑一次 `/api/warmup` 或发一条 `warmup=1` 的回合。

### 3.2 首句短

`ClauseSplitter` 的首块规则：

- 中文：≥4 个字且遇到 `，。！？；` 就发；最多 18 个单位硬切；
- 英文：≥8 个字母且遇到 `.!?` 就发；最多 18 个单位硬切；
- 后续块 ≤48 字符，避免 TTS 长时间等待。

回答 prompt 要求“1–2 句、中文 ≤45 字”，从源头限制长句。

### 3.3 填充音兜底（关键保险）

`FILLER_DEADLINE_MS=1400`：从**真实说完**起算，1400ms 内还没有任何真音频，就先播预合成的自然垫音：

- 中文：“嗯，我看看。”
- 英文：“Hmm, let me see.”

填充音在 warmup 时用当前 TTS 预合成，运行期零成本；播放后会继续播真实回答。指标里 `used_filler=true`、`filler_ratio` 可监控使用频率。正式答辩时应控制比例（理想 <10%），频繁触发说明 ASR/LLM/TTS 某一环偏慢。

> 填充音是常见的对话系统 backchannel 手法，不是预置答案；但在项目说明书中应如实说明。

### 3.4 热词与 VAD

- 每本书的 `BookIndex.asr_initial_prompt()` 把书名、点击对象、脚本引文拼成 220 字以内的 `initial_prompt`，显著改善专有名词（实测“积木崇宝”→“积木城堡”）。
- VAD `VAD_SILENCE_MS=500`：自然语句中的 200ms 停顿不会被切断；判停后回推 500ms，指标仍按真实说完计时。
- 前端若有更好的 VAD，可在 `start` 里传 `"vad":"client"`，由前端发 `speech_end` 与时间戳，后端只负责识别/回答。
- 滚动部分识别 `ASR_PARTIAL_MS` 默认关闭：本地 large-v3 上部分识别会与最终识别争 GPU，反而拖慢 final；需要 UI 字幕时再开（建议 350ms）。

### 3.5 连接复用与模型选择

- `httpx.AsyncClient` 复用 TLS/连接池；MiniMax/LLM 均为长连接。
- 本地 TTS 首选 SAPI（CPU，10–80ms），把 GPU 全留给 ASR；正式切 MiniMax 后 GPU 只跑 ASR。
- ASR 用 `large-v3`（CUDA fp16，热启动 0.45–0.7s）；若显存紧张可换 `large-v3-turbo`/`medium`，用 `ASR_MODEL` 配置。

### 3.6 意图直达

“找画面/查文字/播放控制/点击卡”不走 LLM，规则路由直接出答案与动作（0–5ms）；只有内容问答才走 LLM。赛题 50 条 Query 中相当一部分是导航类，这条能显著降低尾延迟。

---

## 4. 云端切换（MiniMax）

TTS 客户端已按官方流式协议实现：

```
POST https://api.minimaxi.com/v1/t2a_v2?GroupId=...
Authorization: Bearer <key>
{
  "model": "speech-2.6-turbo",
  "text": "...",
  "stream": true,
  "stream_options": {"exclude_aggregated_audio": true},
  "voice_setting": {"voice_id": "...", "speed": 1, "vol": 1, "pitch": 0},
  "audio_setting": {"sample_rate": 24000, "format": "pcm", "channel": 1},
  "language_boost": "Chinese"
}
```

响应逐行 `data: {"data":{"audio":"<hex pcm>","status":1}}`，客户端 hex 解码后立即通过 WebSocket 转发；`status=2` 结束。首块通常 200–500ms（以实际账号/地域为准）。

ASR：MiniMax 公开文档以 TTS 为主，未确认 ASR 接口；当前用本地 Whisper。若官方提供 ASR，按 `ASRProvider.transcribe()` 新增一个 provider 即可，管线不动。

LLM：`LLM_PROVIDER=openai` + `LLM_BASE_URL/LLM_API_KEY/LLM_MODEL`，兼容 DeepSeek/官方 OpenAI 协议；流式首 token 200–400ms。

---

## 5. 怎么测

### 单条 WebSocket 首音

```bash
python tools/ws_latency_probe.py --book cn01 --wav "<Query wav路径>/06.wav" --page 3
```

输出：`speech_end`、`asr_final`、`FIRST_AUDIO ... after_speech_end`、`answer`、`metrics`。

### 指标聚合

```bash
curl http://127.0.0.1:8790/api/metrics/latency
```

返回 `count / hit_ratio / p50 / p90 / p95 / filler_ratio / avg_asr_ms / avg_llm_ms / avg_tts_ms`。`warmup=1` 回合被排除。

### 批量 Query

```bash
python tools/eval_queries.py --book cn01 --base http://127.0.0.1:8790 --out data/eval_cn01.json
```

逐条调用 `/api/ask`（音频），记录 ASR 文本、回答、动作、首音耗时，便于人工核对正确率与挑坏例。

---

## 6. 调参表

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `VAD_SILENCE_MS` | 500 | 判停静音；太小会切断自然停顿，太大会增加端点等待 |
| `VAD_MIN_SPEECH_MS` | 100 | 最短语音，防咳嗽/噪声 |
| `FILLER_DEADLINE_MS` | 1400 | 首音保险触发点；建议 1200–1500 |
| `FILLER_ENABLED` | true | 关闭后只剩真音频，抖动时可能超 2s |
| `ASR_MODEL` | large-v3 | `large-v3-turbo`/`medium` 更快 |
| `ASR_PARTIAL_MS` | 0 | >0 开启滚动部分识别（UI 字幕用） |
| `TTS_PROVIDER` | sapi | `sapi`/`local`/`minimax` |
| `TTS_MAX_CLAUSE_CHARS` | 48 | 后续小句最大长度 |
| `LATENCY_BUDGET_MS` | 2000 | 指标达标线 |

---

## 7. 已知风险

1. **GPU 争用**：ASR + CosyVoice 同时跑会互相拖慢；本地演示优先 SAPI/CPU TTS，或把 ASR 换 turbo。
2. **VAD 误判**：儿童语速/停顿差异大；前端有 VAD 时用 `vad:"client"`，后端 500ms 仅兜底。
3. **网络抖动**：MiniMax/LLM 首包可能偶发 >1s；填充音是最后保险。
4. **英文扫描页**：ASR/检索不受影响，但“找画面”依赖页序/关键词；建议视频组提供 manifest 精确场景。
5. **首轮冷启动**：Whisper 加载 9–30s、CosyVoice 30s+；务必提前预热并按 Q34 的口径统计。
