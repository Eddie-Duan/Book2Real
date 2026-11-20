# 前后端 / 视频组集成契约

本文是队友接入后端需要的全部信息：WebSocket 语音协议、HTTP 接口、绘本数据结构、视频 manifest、触控元素卡、会话历史。

- 后端默认地址：`http://127.0.0.1:8790`（局域网用本机 IP，例如 `http://192.168.x.x:8790`）
- WebSocket：`ws://127.0.0.1:8790/ws/voice/{session_id}`
- OpenAPI：`/docs`；健康检查：`/health`；预热状态：`/api/ready`
- 若设置了 `API_TOKEN`，HTTP 需带 `X-API-Token` 头或 `?token=`；WebSocket 用 `?token=`。

---

## 1. WebSocket 语音回合（低延迟主通道）

### 1.1 时序

```
前端                                     后端
 │  start {book_id, sample_rate:16000,...} │
 │ ──────────────────────────────────────> │  ready {book_id, warm, vad}
 │  binary: PCM16LE 20ms 帧                │
 │ ──────────────────────────────────────> │  VAD / ASR
 │  vad {event:"speech_end", t:epoch_ms}   │
 │ ──────────────────────────────────────> │  speech_end {t}
 │                                         │  asr_final {text, lang, ms}
 │                                         │  answer_delta {text}...
 │  <──────────────────────────────────────│  answer {text, action, evidence}
 │  <──────────────────────────────────────│  audio_start {seq, sample_rate, format}
 │  <──────────────────────────────────────│  binary: PCM16LE 音频块
 │  <──────────────────────────────────────│  audio_end {seq}
 │  <──────────────────────────────────────│  done {timings}
```

### 1.2 前端 → 后端

| type | 字段 | 说明 |
| --- | --- | --- |
| `start` | `book_id`, `sample_rate`(默认16000), `lang?`, `page?`, `element?`, `warmup?`, `vad?` | 开始一次会话/切换绘本。`vad` 可为 `both`(默认)/`client`/`server` |
| 二进制帧 | PCM16LE 单声道 | 建议 20ms（16k → 640 字节/帧），持续发送 |
| `vad` | `event`:`speech_start`/`speech_end`, `t`:epoch ms | 前端 VAD 可用；`vad:"client"` 时后端不再自行判停 |
| `text` | `text`, `t?` | 文字调试/无麦克风时直接提问 |
| `barge_in` | — | 打断当前回答（前端应停止播放并清空队列） |
| `end` | — | 结束会话 |

`element` 是当前触控选中的元素卡，例如：
```json
{"name":"积木城堡","page":3,"bbox":[0.58,0.72,0.66,0.76],"info":"小满和花花一起搭的城堡。"}
```

### 1.3 后端 → 前端

| type | 字段 | 说明 |
| --- | --- | --- |
| `ready` | `book_id`, `sample_rate`, `warm`, `warmup`, `vad` | 会话就绪 |
| `speech_start` | `t` | 服务端 VAD 检测到说话 |
| `speech_end` | `t`, `bytes` | 判停；`t` 是**真实说完**的时间（已回推尾音） |
| `asr_partial` | `text`, `lang` | 可选，默认关闭（`ASR_PARTIAL_MS=0`） |
| `asr_final` | `text`, `lang`, `ms`, `trim?` | 最终识别 |
| `answer_delta` | `text` | LLM/提取式的增量文本，可直接做字幕 |
| `answer` | `text`, `lang`, `intent`, `action`, `evidence`, `timings`, `metrics` | 完整答案与动作 |
| `audio_start` | `seq`, `sample_rate`, `format:"pcm16"`, `text`, `filler?` | 一段音频开始；**以这里的 sample_rate 为准**（SAPI=22050，CosyVoice/MiniMax=24000） |
| 二进制帧 | PCM16LE 单声道 | 紧跟在 `audio_start` 后；按到达顺序播放 |
| `audio_end` | `seq`, `filler?` | 一段音频结束 |
| `filler_used` | `text` | 播放了 1400ms 兜底垫音 |
| `tts_error` | `seq`, `message` | 该小句 TTS 失败（文本答案仍有效） |
| `error` | `stage`, `message` | ASR/管线错误 |
| `done` | `timings` | 本回合结束，可开始下一轮 |

**播放建议**：维护一个 `AudioContext` 播放队列；收到 `audio_start` 后用该 `sample_rate` 创建/复用 `AudioBufferSourceNode`；PCM16 → Float32（除以 32768）；按 chunk 顺序 append。`filler` 段与真实回答是连续的两段，直接顺序播放即可；若用户 `barge_in`，立即 stop 并丢弃未播数据。

### 1.4 action 类型

`answer.action` 由后端给出，前端执行：

| type | 字段 | 用途 |
| --- | --- | --- |
| `play` / `pause` | — | 播放/暂停 |
| `seek_page` | `page`, `query` | 跳到绘本页对应的视频场景（若视频索引可用，会带 `t`） |
| `seek` | `t`, `end`, `page` | 直接跳到视频时间 |
| `highlight_text` | `text`, `page`, `bbox`, `hits`, `frame_t?`, `video_hits?` | 查字/高亮；`bbox` 为页内归一化 `[x0,y0,x1,y1]`，视频命中时带帧时间 |
| `element_card` | `element` | 展示触控元素卡 |

### 1.5 前端 VAD 建议

- 浏览器可用 `MediaStream + AudioWorklet` 计算 RMS，20ms 帧，静音 500ms 判停；发送 `vad start/end` 时带上 `t=Date.now()`。
- 若前端 VAD 更准，`start` 传 `"vad":"client"`，后端不再按 500ms 自行判停，只做兜底。
- 未做前端 VAD 时，直接持续推流，后端会在 500ms 静音后判停（`vad:"both"`）。
- 打断：用户再次开口时发 `barge_in`，然后正常推流；后端会取消当前回合。

---

## 2. HTTP 接口

### 2.1 问答

`POST /api/ask`（JSON 或 multipart）

JSON：
```json
{"book_id":"cn01","text":"他们在搭什么？","session_id":"demo","page":2}
```
音频：
```json
{"book_id":"cn01","audio_b64":"<wav/mp3 base64>","session_id":"demo","page":2}
```
multipart：`book_id`, `audio`(file), `session_id`, `page`, `element`(json string)。

返回：
```json
{
  "answer":"绘本里说：我们一起搭城堡，搭高高，还不倒。",
  "lang":"zh",
  "asr_text":"他们在搭什么？",
  "asr_lang":"zh",
  "action":null,
  "evidence":[{"page":2,"text":"...","score":12.3}],
  "timings":{"asr_final":554.7,"first_audio":631.2},
  "metrics":{"first_audio_after_speech_ms":631.2,"hit":true,"budget_ms":2000,"used_filler":false},
  "audio_format":"pcm16",
  "sample_rate":22050,
  "audio_b64":"...",
  "audio_wav_b64":"..."
}
```

`POST /api/ask/stream`：同上 JSON，返回 `application/x-ndjson`，每行一个事件；`audio` 事件的 PCM 以 `audio_b64` 字段返回。

### 2.2 ASR / TTS

- `POST /api/asr`：multipart `audio` + `language?` → `{text, lang, duration_ms}`
- `POST /api/tts`：JSON `{text, lang?}` → `application/x-pcm` 流；响应头 `X-Sample-Rate`、`X-Audio-Format: pcm16le`

MiniMax 云端（已实测）：

```ini
TTS_PROVIDER=minimax
ASR_PROVIDER=minimax
MINIMAX_TTS_MODEL=speech-2.8-turbo
MINIMAX_VOICE_ID=Chinese (Mandarin)_Warm_Girl
MINIMAX_VOICE_ID_EN=Sweet_Girl
MINIMAX_EMOTION=calm
MINIMAX_ASR_MODEL=asr-1.0
```

- TTS 按回答文本自动选中/英音色；`audio_start.sample_rate` 为 24000。
- ASR 为 `POST /v1/speech_to_text`，multipart `file` + `model=asr-1.0`；细节与音色列表见 `docs/MINIMAX.md`。
- 音色克隆本账号返回 `2038`，需要 MiniMax 账号开通；系统音色/情绪/语速/音高可用。

### 2.3 绘本

| 接口 | 说明 |
| --- | --- |
| `GET /api/books` | 列表（id/title/lang/page_count/summary） |
| `GET /api/books/{id}` | 完整 book.json（pages/blocks/script/elements） |
| `GET /api/books/{id}/pages/{no}` | 页图 PNG（1 起） |
| `GET /api/books/{id}/queries/{name}` | Query 音频 wav（如 `06.wav`） |
| `GET /api/books/{id}/elements?page=` | 元素卡 |
| `POST /api/books/ingest` | multipart PDF + 可选 txt 脚本 |
| `POST /api/books/ingest-materials` | form `zip_path`, `force` |

### 2.4 视频

| 接口 | 说明 |
| --- | --- |
| `POST /api/video/{id}/index` | multipart `video`(mp4) + 可选 `manifest`(json) |
| `POST /api/video/{id}/index-path` | JSON `{video_path, manifest_path}`（本地文件，免上传） |
| `GET /api/video/{id}/status` | 是否已建索引/时长/帧数 |
| `GET /api/video/{id}/search?q=` | 帧文字检索：`[{t, frame, text, bbox, score}]` |
| `GET /api/video/{id}/frame?t=` | 最近采样帧及该帧 OCR 文本 |
| `GET /api/video/{id}/describe?t=&box=x0,y0,x1,y1` | 帧/框选描述（页文本/元素卡；VLM 可后接） |
| `GET /api/video/{id}/elements?t=` | 当前场景可点击元素 |
| `GET /api/video/{id}/frames/{name}` | 采样帧 JPEG |

### 2.5 会话 / 指标

- `GET /api/sessions/{id}/history?limit=50` → `{messages:[...]}`；每条含 `role/text/lang/page/scene_t/element/image/action`
- `POST /api/sessions/{id}/events`：记录触控/点击事件（JSON：`role,text,page,t,element,image,action`）
- `GET /api/metrics/latency?session_id=&book_id=` → p50/p90/p95、2s 达标率、填充音比例、平均 ASR/LLM/TTS
- `GET /api/metrics/turns?limit=20` → 最近回合明细
- `POST /api/warmup` → 重新预热
- `/health`、`/api/ready`

---

## 3. book.json 结构

```json
{
  "id": "cn01",
  "title": "中文绘本01",
  "lang": "zh",
  "page_count": 12,
  "pages": [
    {
      "no": 2,
      "image": "/api/books/cn01/pages/2",
      "width": 2716, "height": 1358,
      "story_text": "下雨了，小满、花花只好在家里玩。……",
      "teaching_text": "汉字源流……阅读指导……",
      "blocks": [
        {"text":"积木城堡没有倒。","bbox":[0.54,0.78,0.69,0.82],"kind":"story","score":1.0}
      ],
      "ocr": false
    }
  ],
  "script": {
    "items": [
      {"no":5,"text":"他们在搭什么？","type":"question","page":3,"required":true,
       "click":{"target":"积木城堡","action":"暂停并展示城堡信息","required":true},
       "audio":"/api/books/cn01/queries/05.wav"}
    ]
  },
  "elements": [
    {"name":"积木城堡","page":3,"bbox":[0.58,0.72,0.66,0.76],
     "info":"三块、四块、五块，积木城堡没有倒。","action":"暂停并展示城堡信息","required":true}
  ]
}
```

- `bbox` 统一为页内归一化 `[x0,y0,x1,y1]`（0–1），前端按页图实际尺寸换算像素。
- `blocks[].kind`：`story`（正文）/`teaching`（阅读指导、汉字源流等）/`page_no`。
- 英文本是扫描页，`story_text` 来自 OCR；插图页可能为空，属于正常。
- `script.items[].page` 是后端用单调 DP 对齐的页码，可能因语义近似有 1–2 页误差；视频组可用 manifest 覆盖。

---

## 4. 视频 manifest（建议视频组产出）

```json
{
  "video": "cn01_final.mp4",
  "width": 1280,
  "height": 720,
  "scenes": [
    {
      "page": 2,
      "start": 0.0,
      "end": 7.5,
      "elements": [
        {"name":"积木城堡","bbox":[0.42,0.30,0.71,0.82],
         "info":"小满和花花一起搭的积木城堡。","page":2}
      ]
    }
  ]
}
```

- `start/end` 单位秒；`page` 对应 book.json 的页号。
- `elements[].bbox` 为该场景内归一化框；如果元素会移动，可按关键帧给多个 box（后续可扩展）。
- 没有 manifest 时，后端按页数均分视频时长生成场景；查字仍可用帧 OCR。
- 触控验收要求“同一目标所有可见且可识别的帧都能点”；建议视频组在 manifest 里给出每个场景/关键帧的元素框，前端按时间切换。

---

## 5. 会话历史与多模态

- 后端自动记录每轮 user/assistant 文本、页码、元素名、图片引用（前端通过 `/api/sessions/{id}/events` 补充触控/截图）。
- LLM 上下文默认带最近 `SESSION_HISTORY_TURNS=8` 轮；跨轮指代（“它”“那个人”）可用。
- 默认不落原始音频（`STORE_AUDIO=false`）；如需复盘，赛后可临时开启并注意脱敏/删除。

---

## 6. 联调检查清单

- [ ] `/api/ready` 返回 200，`/health` 里 `warm=true`、`asr.loaded=true`、`filler.prepared=true`
- [ ] `GET /api/books` 能看到 6 本；页图能打开
- [ ] WS `start` 收到 `ready`；持续推 16k PCM 能收到 `speech_start`
- [ ] 说完一句后收到 `asr_final`、`answer`、`audio_start` + 二进制音频
- [ ] 前端按 `audio_start.sample_rate` 播放，无杂音/变速
- [ ] 触控点击后发 `element`，问“这是什么”能出元素卡
- [ ] 视频索引建好后，`/api/video/{id}/search?q=...` 能返回帧时间与 bbox
- [ ] 现场前跑一次 `/api/warmup`，并用 `tools/ws_latency_probe.py` 测 3 条 Query
