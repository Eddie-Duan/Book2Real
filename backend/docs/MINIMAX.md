# MiniMax TTS / ASR 实测与参数手册

> 2026-11-20 用 Explore key 实测：**TTS 和 ASR 都可用**，host 用 `https://api.minimaxi.com`。
> key 只写在本机 `.env`（已 gitignore），不要提交到仓库或发到公开群。

## 1. 推荐配置

```ini
TTS_PROVIDER=minimax
ASR_PROVIDER=minimax
MINIMAX_HOST=https://api.minimaxi.com
MINIMAX_TTS_MODEL=speech-2.8-turbo
MINIMAX_VOICE_ID=Chinese (Mandarin)_Warm_Girl
MINIMAX_VOICE_ID_EN=Sweet_Girl
MINIMAX_EMOTION=calm
MINIMAX_SPEED=0.95
MINIMAX_VOL=1.0
MINIMAX_PITCH=0
MINIMAX_ASR_MODEL=asr-1.0
MINIMAX_ASR_LANGUAGE=
FILLER_DEADLINE_MS=1700
```

- 想更快/更稳：`ASR_PROVIDER=local`（本地 large-v3 + 本书热词，专有名词更准）。
- 想完全离线：`TTS_PROVIDER=sapi` + `ASR_PROVIDER=local`。
- 想更换音色：见第 3 节。

## 2. TTS：`POST /v1/t2a_v2`

| 参数 | 值/范围 | 说明 |
| --- | --- | --- |
| `model` | `speech-2.8-hd/turbo`、`2.6-hd/turbo`、`02-hd/turbo`、`01-hd/turbo` | 实测 2.6/2.8 的 turbo/hd 都可用；推荐 `2.8-turbo` |
| `text` | ≤约 10,000 字符 | 2.8 支持 `<#x#>` 停顿、inline pronunciation 等 |
| `voice_setting.voice_id` | 系统/克隆音色 ID | 327 个系统音色（见第 3 节） |
| `voice_setting.speed` | 0.5–2.0 | 儿童伴读建议 0.9–1.0 |
| `voice_setting.vol` | (0, 10] | 默认 1.0 |
| `voice_setting.pitch` | -12–12 | 默认 0 |
| `voice_setting.emotion` | `happy/sad/angry/fearful/disgusted/surprised/calm/fluent/whisper` | `fluent/whisper` 仅 2.6；2.8 不支持 whisper |
| `timbre_weights` | 最多 4 个 `voice_id` + `weight(1–100)` | 混合音色；`voice_id` 可为空 |
| `voice_modify` | pitch/intensity/timbre -100–100；sound_effects 四选一 | 实测未启用；流式仅部分格式支持 |
| `language_boost` | `auto/English/Chinese/Chinese,Yue/...` | 中英混说建议 `auto` 或按文本选 |
| `text_normalization_mode` | `basic/quality` | 数字/多音字更自然选 quality |
| `pronunciation_dict.tone` | 自定义多音字 | 儿童绘本里的多音字可用 |
| `audio_setting.format` | `mp3/pcm/flac/wav/opus/...` | 流式用 `pcm` 最好接 |
| `audio_setting.sample_rate` | 8000/16000/22050/24000/32000/44100 | 我们固定 24000 |
| `audio_setting.channel` | 1/2 | 我们用单声道 |
| `stream` | true/false | true 返回 SSE 分块 |
| `stream_options.exclude_aggregated_audio` | true | 避免最后重复返回整段 |
| `subtitle_enable/type` | sentence/word/word_streaming | 做高亮/卡拉OK用 |

### 实测 TTS 首块/总耗时

- 探针（`Warm_Girl`，2.8-turbo，calm，0.95）：首块 **~212–300ms**，4–5s 音频总耗时 0.4–0.7s。
- 音色矩阵（12 组，2.6/2.8、turbo/hd、女声/童声/情绪/语速）：首块 **273–371ms**，总耗时 0.51–0.85s。
- 纯文本问答走 TTS：首音 **310ms**。
- 结论：TTS 不是 2s 瓶颈；主要延迟在 ASR/VAD。

## 3. 音色控制

### 3.1 系统音色（327 个）

`POST /v1/get_voice`（body `{"voice_type":"all"}`）返回全部系统音色。与儿童绘本相关的推荐：

| 用途 | voice_id | 说明 |
| --- | --- | --- |
| 中文旁白（推荐） | `Chinese (Mandarin)_Warm_Girl` | 温暖少女，适合讲故事 |
| 中文旁白备选 | `female-shaonv` / `female-tianmei` | 少女/甜美女性 |
| 中文童声 | `lovely_girl` / `cute_boy` / `clever_boy` | 萌萌女童/可爱男童/聪明男童 |
| 中文温柔 | `Chinese (Mandarin)_Soft_Girl` / `Chinese (Mandarin)_Warm_Girl` | 软软女孩/温暖少女 |
| 英文旁白（推荐） | `Sweet_Girl` | 英文甜美女声；实测 343ms 首块 |
| 英文旁白备选 | `English_Graceful_Lady` / `English_Trustworthy_Man` | 优雅女声/可信男声 |
| 英文童声 | `Cute_Elf` / `Sweet_Girl` | 可爱精灵/甜美女孩 |

> 完整列表见 `data/minimax_voices.json`；试听 wav 在 `data/minimax_matrix/`。

### 3.2 每语言音色

后端已支持：

```ini
MINIMAX_VOICE_ID=Chinese (Mandarin)_Warm_Girl
MINIMAX_VOICE_ID_EN=Sweet_Girl
```

TTS 会按**回答文本**判断语言：中文回答用中文音色，英文回答用英文音色。这样英文书/英文提问不会被中文音色读成中式英语。

### 3.3 情绪/语速/音高/混音

- 逐请求 `emotion`：`calm` 旁白、`happy` 开心、`sad` 难过、`surprised` 惊讶；`fluent/whisper` 仅 2.6。
- `speed` 0.9–1.0 适合儿童；`pitch` -12–12 可微调年龄感。
- `timbre_weights` 最多混 4 个音色（如 70% Warm_Girl + 30% lovely_girl），适合做“温和的童声旁白”。
- `voice_modify` 可做电话音/机器人/礼堂等特效，儿童绘本一般不用。

### 3.4 音色克隆（当前账号不可用）

官方流程：

1. `POST /v1/files/upload`，multipart `file` + `purpose=voice_clone` → `file_id`
2. `POST /v1/voice_clone`，JSON `file_id` + 自定义 `voice_id` + `model` + 预览 `text`
3. 用返回的 `voice_id` 调 TTS

要求：mp3/m4a/wav，10 秒–5 分钟，≤20MB；建议干净、无背景音乐、单人。

本 key 实测：上传成功（200，file_id），但 `voice_clone` 返回：

```json
{"base_resp":{"status_code":2038,"status_msg":"voice clone user forbidden"}}
```

说明该账号没有音色克隆权限，需要在 MiniMax 控制台完成实名/企业认证或联系商务开通；系统音色、`timbre_weights`、情绪/语速/音高不受影响。不要克隆未授权的声音（尤其是真实儿童）。

## 4. ASR：`POST /v1/speech_to_text`

| 参数 | 值 | 说明 |
| --- | --- | --- |
| `model` | `asr-1.0` | 目前唯一公开模型 |
| `file` | multipart 音频 | wav/aiff/flac/m4a/mp3/aac/opus/ogg |
| `language` | header，BCP-47 | `zh/yue/en/ja/ko/th/vi/id/ms/fil/ar/tr/fr/de/es/it/pt/pl/ru/uk`；留空自动，中英混说可用 |
| `response_format` | `json/verbose_json/srt/vtt` | verbose 带分段/说话人 |
| `timestamp_level` | `sentence/word` | 配合 verbose/srt/vtt |
| `stream` | true/false | SSE 增量，只支持 json |
| 限制 | ≤500s、≤50MB | 不支持裸 PCM；建议 16k 单声道 |

### 实测

| 音频 | 语言设置 | 耗时 | 结果 |
| --- | --- | --- | --- |
| cn01/06 `高亮积木城堡` | auto | 629ms | `高亮积木城堡。` |
| cn01/06 | auto（直接探针） | 720ms | 同上 |
| en01/05 `Which film?` | auto | 685ms | `Which film?` |
| en01/05 | `en` | **398ms** | `Which film?` |
| cn01/24 `Where is Xiao Man?` | auto | 544ms | `Where is Yao Man?`（专名误识，脚本页对齐仍能命中 page 7） |
| cn01/06 verbose | auto | 453ms | `text` + 逐词 segments + speaker |
| cn01/06 stream | auto | 552ms first delta | 累积文本正确 |

结论：ASR 0.4–0.7s，足够 2s；**书名/人名等专有名词本地 large-v3 + 热词更稳**，MiniMax ASR 适合英文/多语言/云端备份。后端两种 Provider 都保留：

```ini
ASR_PROVIDER=minimax   # 云端，启动快、无 GPU
ASR_PROVIDER=local     # 本地 large-v3 + 本书热词，专名更准
```

## 5. 端到端实测（MiniMax TTS + ASR）

| Query | 首音 | 结果 |
| --- | --- | --- |
| `高亮“积木城堡”` | 1428ms | 命中 bbox |
| `找到花花独处的画面` | 1286ms | page 7 |
| `Where is Xiao Man?` | 1364ms | page 7（Yao Man 仍被脚本对齐救回） |
| `What is the dog carrying?` | **813ms** | page 10 |
| cn01 前 5 条 | p50 767ms / p95 902ms | 5/5 达标 |
| en01 前 5 条 | p50 864ms / p95 879ms | 5/5 达标 |

填充音 0%，全部 <2s。服务端 VAD 默认 500ms 尾音；前端用 `vad:"client"` 可再省约 500ms。

## 6. 故障码

| 返回 | 含义 | 处理 |
| --- | --- | --- |
| `1004` / 401 | key 未带/无效/区域不对 | 确认 `Authorization: Bearer` 和 host（`.minimaxi.com` vs `.minimax.io`） |
| 402 | 余额/套餐不含音频 | 换 pay-as-you-go / M Plan / 充值 |
| 2038 | `voice clone user forbidden` | 账号未开通克隆权限；用系统音色 |
| 404 | host/path 不对 | TTS/STT 用 `.minimaxi.com` 或 `.minimax.io` |
| 422 | 音频格式/敏感内容 | 用 wav/mp3、检查内容 |

## 7. 安全

- key 只在 `.env`（gitignored）；`.env.example` 只有空值。
- `tools/minimax_probe.py` 只打印打码后的 key，不写入结果文件。
- 如果 key 曾在聊天/群里出现过，赛后可到 MiniMax 控制台轮换。
- 克隆音色需要授权/同意；不要克隆真实儿童或赛事素材中的声音。

## 8. LLM 实时回答（已实测）

同一个 key 也能调文本模型（`/v1/models` 可列当前可用模型）：

| 模型 | 首 token（绘本问题） | 说明 |
| --- | --- | --- |
| `MiniMax-M2.7-highspeed` | **~0.7s** | 推荐；thinking 在 content 里，后端已过滤 |
| `MiniMax-M3.1-Flash` | 1.4–1.7s | 强制 adaptive thinking，不能关；首 token 慢 |
| `MiniMax-M3` | ~0.5s（短问） | 长上下文时 thinking 变长 |

配置：

```ini
LLM_PROVIDER=openai
LLM_BASE_URL=https://api.minimaxi.com/v1
LLM_API_KEY=<same key>
LLM_MODEL=MiniMax-M2.7-highspeed
LLM_MAX_TOKENS=512
```

- 旧模型名（M2/M2.1/abab6.5s/Text-01）已下线，返回 2069。
- `thinking.type=disabled` / `reasoning_effort=none` 在 M3 系列返回 400；M2.7 用 `enable_thinking:false` 可省部分延迟。
- 后端对 content 内的 `...` 块做了流式过滤，只把正式回答送 TTS。
- 闲聊/测试类问题（“我说话有声音吗”“你好”）走 chitchat 路由，由 LLM 实时回答，不硬检索绘本。
- 端到端（WS，含 ASR+LLM+TTS）：导航类 ~0.9–1.4s；内容类 ~1.2–1.7s；慢 LLM 时 1.7s 填充音兜底，全部 <2s。
