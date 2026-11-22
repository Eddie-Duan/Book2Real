# HANDOVER · 语音测试页“说话无应答/ASR空”修复交接

> 写给接手的 AI/开发者。目标：让 `http://127.0.0.1:8790/test` 这个测试页**用真实麦克风说话**能稳定得到语音回答（说完→首音 <2s）。
> 后端管线本身已验证是通的；**剩余问题集中在浏览器麦克风采集这一段**。本文自包含，不需要额外上下文。

---

## 0. 一句话现状

- 后端（ASR→LLM→TTS 流式）用脚本注入音频**全部跑通**，首音 0.9–1.7s，批量 15/15 <2s。
- 测试页**打字提问正常**；**“用测试音频当我说”按钮正常**（绕过麦克风）。
- **真实麦克风说话仍不稳定**：用户最后一次反馈（18:00 左右）仍是 ASR 空。已做两轮修复（见 §4），最新一轮（关浏览器降噪）**用户尚未验证**。
- 你的首要任务：确认/继续修麦克风采集链路，让真人说话可用。

## 1. 系统是什么

「纸书智伴」赛题后端 + 一个极简语音测试页。链路：

```
浏览器麦克风(48k) → 页面降采样16k PCM16 → WS二进制帧
  → 服务端按客户端VAD事件切句 → MiniMax ASR(asr-1.0, HTTP multipart)
  → 意图路由(导航类规则直达/内容类走LLM) → MiniMax LLM(M2.7-highspeed, SSE流式)
  → 小句切分 → MiniMax TTS(speech-2.8-turbo, SSE hex PCM)
  → WS二进制PCM块 → 页面 AudioContext 排队播放
```

- 项目：`E:\projects\paperbook-backend`（公开仓库 github.com/chhaaabbbccc/paperbook-backend，main=54fbc62）
- 服务：FastAPI/uvicorn，端口 **8790**，`python -m uvicorn app.main:app --host 0.0.0.0 --port 8790`
- 配置：`.env`（**含 MiniMax key，已 gitignore，绝不提交/外发**）；当前 `ASR_PROVIDER=minimax`、`TTS_PROVIDER=minimax`、`LLM_PROVIDER=openai`(MiniMax M2.7-highspeed)
- 测试页：`web/index.html`（单文件原生JS，无框架），路由 `GET /test`
- 用户环境：Windows 11 + Chrome，麦克风设备正常（其他软件可用）；机器 RTX 5070 Laptop 8GB

## 2. 关键文件地图

| 文件 | 作用 | 重点位置 |
| --- | --- | --- |
| `web/index.html` (~540行) | 测试页全部逻辑 | `startMic()`≈L255；`onaudioprocess`≈L270（降采样/增益/上传门控）；`clientVad()`≈L225；`handleMessage()`≈L370；`holdDown/holdUp`≈L470 |
| `app/main.py` (~700行) | 路由+WS | `voice_ws()`≈L418：binary 收帧、`vad` 事件、`end_turn()`；`GET /test`≈L150 |
| `app/pipeline/turn.py` | 回合编排 | `run_voice()`≈L108：trim→低音量归一化→ASR→`run_text()`；空ASR提示≈L140 |
| `app/pipeline/vad.py` | 服务端能量VAD | client 模式下**不启用**（`app/main.py` 中 `events = [] if vad_mode=="client" else vad.push(chunk)`） |
| `app/providers/asr_minimax.py` | ASR | multipart file+model=asr-1.0，裸PCM会包WAV |
| `app/providers/tts_minimax.py` | TTS | SSE hex PCM；中英双音色按回答文本选 |
| `app/providers/llm.py` | LLM | OpenAI兼容SSE；过滤``块 |
| `tools/ws_latency_probe.py` | 命令行首音探针 | 把wav按20ms帧推WS（服务端VAD模式） |
| `tools/minimax_probe.py` | MiniMax直连探针 | 列音色/TTS/ASR |
| `data/_server.log` | 服务日志 | **排查主战场**，含 ws binary/vad/end_turn 计数日志 |

## 3. 已验证的事实（不要重复怀疑这些）

用脚本模拟浏览器把 `E:/Downloads/_q06.wav`（内容“高亮积木城堡”，1.92s 16k）按 640B/20ms 推 WS：

| 实验 | 结果 |
| --- | --- |
| 正常音量注入 | ASR=`高亮积木城堡。`，首音 893–1361ms ✅ |
| 衰减到 5% 音量注入 | 服务端归一化兜底后仍识别 ✅（903ms） |
| 模拟页面 48k↔16k 重采样再喂 ASR | 识别正确 ✅ → **页面降采样算法没有 bug** |
| TTS 生成的音频当提问注入 | ASR/LLM/TTS 全通，1716ms ✅ |
| 页面“用测试音频当我说”按钮（fetch wav→WS） | 用户实测**正常出回答说和语音** ✅ |
| 打字提问 | 正常 ✅ |
| MiniMax key | TTS/ASR/LLM 全部可用；voice_clone 返回 2038 无权限（不用管） |

结论：**WS 协议、服务端切句、ASR、LLM、TTS、播放全部正确**。问题只在“真实麦克风→WS 二进制”这一段。

## 4. 麦克风问题的两轮诊断与修复史

### 第一轮（17:37 日志）：barge-in 回声循环（已修，commit d63e9d3）
- 现象：客户端 `speech_start/speech_end` 都发了，服务端全忽略，日志大量 `barge_in ack`。
- 根因：页面收到服务端回声 `speech_start` 时又发一次 `barge_in`，把服务端 `speaking/buffer` 清零 → `speech_end` 到达时 `speaking=False` 被丢弃。
- 修复：页面回声 handler 不再 barge-in；服务端 client 模式下 `speech_end` 只要 buffer≥3200B 就应答；client 模式关闭服务端 VAD。

### 第二轮（17:54 日志）：采集近静音（部分修复，commit 72cf62b）
- 现象：`speech_end bytes=27300~147420` 但 ASR 全空；页面 `mic rms≈0.001–0.006`（正常说话应 0.05+；用户 17:37 那次瞬时 rms 有 0.0885）。
- 验证：把测试音频衰减到 rms 0.0018 喂 ASR 同样返回空 → 确认是**输入音量**问题而非识别问题。
- 修复：页面增益滑杆（默认8x）+ VAD 灵敏度滑杆 + 服务端峰值<6000 归一化放大（≤6x）。

### 第三轮（18:00 日志）：语音被截成碎片（最新修复，commit 54fbc62，**用户未验证**）
- 现象：`speech_start → speech_end` 间隔仅 **80–120ms**，buffer 只有 2730–5460B（0.08–0.17s）。一句话应录 1–2s。
- 推断：浏览器 `noiseSuppression:true` 把语音主体当噪声削掉，只剩起始瞬态；VAD 触发后立刻判静音结束。
- 修复：
  - `getUserMedia` 默认 `echoCancellation:false, noiseSuppression:false, autoGainControl:true`
  - 页面加“开启浏览器降噪/回声消除”checkbox（`#procToggle`），切换会重启麦克风
  - 默认模式改“按住说话”（push-to-talk）
  - 空 ASR 且录音<500ms 时提示“可能被降噪截断”
- **注意**：80–120ms 碎片还有另一种可能解释——见 §5 疑点 A。

## 5. 剩余疑点（按优先级排查）

### A. 上传门控与 VAD 的时序竞态（重点怀疑）
`web/index.html` `onaudioprocess` 中：
```js
const speakingNow = state.mode === 'push' ? state.holdActive : state.vad.started;
if (speakingNow) { state.ws.send(pcm.buffer); ... }
```
问题：
1. **auto 模式**：`vad.started` 要连续 120ms 超阈值才置 true → **语音前 120ms 不上传**；`speech_end` 在静音 500ms 后发，但**静音段不上传** → 服务端 buffer 只有语音中段。若语音本身被降噪削弱、只有零星帧超阈值，`started` 反复置位/复位，每次只上传 1–2 帧 → 正好符合日志中 2730B(=1帧48k→2730B) 碎片！
   - 2730B = 4096样本@48k ÷3 ×2B，**恰好一个 ScriptProcessor buffer**。多次 `end_turn bytes=2730` 强烈暗示：VAD 每次只“抓住”一个 buffer 就结束。
2. **push 模式**：`holdDown()` 发 `speech_start`，但用户按下到 `onaudioprocess` 检查 `holdActive` 之间没问题；松开时 `holdUp` 立即发 `speech_end`，**最后一个未满 buffer 可能没发**（小问题）。
3. **修复建议**：
   - 加**前置环形缓冲**：始终缓存最近 ~500ms PCM，`speech_start` 时先把环形缓冲补发，保证语音开头不丢。
   - `speech_end` 后**继续上传静音帧直到事件发出**（或干脆一直上传，服务端 client 模式反正只按事件切句；之前门控是为省 ASR 静音前缀，但代价是丢语音）。**最简单可靠方案：恢复一直上传**，服务端 `end_turn` 里已有 `trim_speech` 会去静音。
   - VAD 判停后加 200ms “尾音拖尾”再发 `speech_end`。

### B. ScriptProcessor 在后台标签页/省电模式被节流
- `createScriptProcessor(4096)` 已过时，Chrome 可能节流；建议换 **AudioWorklet**（`AudioWorkletNode` + `postMessage` PCM），或至少确认页面在前台。
- 用户日志帧间隔正常（~85ms/buffer），此项优先级低，但换 Worklet 是正规做法。

### C. 增益应用位置
- 增益 8x 在**降采样前**应用于 48k 原始样本（`input[i]=raw[i]*state.gain`），VAD 用的也是放大后的 rms → 阈值 floor=0.012/sens 与增益耦合。确认调参时两者一致（目前一致）。
- 若系统麦克风本身增益极低（Windows 设置里麦克风音量/加强），页面 8x 可能仍不够；提示用户检查 Windows 声音设置→输入→音量/加强，或把滑杆拉到 16–20。

### D. 回声/自激
- 关掉 echoCancellation 后，**TTS 播放会被麦克风录回去** → 回答播放期间 VAD 可能误触发。现有 barge-in 逻辑会把播放当新提问。
- 建议：播放期间（`state.activeSources.length>0` 或 `turnBusy`）**暂停 clientVad**（代码里已有 `state.turnBusy` guard，但播放长于 turnBusy 窗口时仍可能漏）。最稳：播放时 mute 上传 + VAD，播完恢复；或戴耳机测试。

## 6. 复现与验证方法（不需要真人麦克风）

```bash
cd E:\projects\paperbook-backend
# 服务（若没跑）：
python -m uvicorn app.main:app --host 0.0.0.0 --port 8790   # 日志落 data/_server.log

# 1) 命令行探针（服务端VAD路径）：
python tools/ws_latency_probe.py --book cn01 --wav E:/Downloads/_q06.wav --page 3

# 2) 模拟页面 client-VAD 路径（最重要，和页面行为一致）：
#    参考本会话用过的注入脚本：start(vad:client) → vad speech_start → 640B/20ms 帧 → vad speech_end
#    期望：asr_final 非空，answer.metrics.first_audio_after_speech_ms < 2000

# 3) 单测：
python -m pytest -q        # 18 passed

# 4) 批量：
python tools/eval_queries.py --book cn01 --limit 10
```

**模拟“碎片语音”回归**（修复疑点 A 后必须过）：把 06.wav 拆成“只发每句的第 1 个 20ms 帧 + 静音”的畸形流，验证前置缓冲/一直上传方案能恢复完整语音或给出正确提示。

**真人验收清单**（用户回来时）：
1. Ctrl+F5 刷新 /test（HTML 是 no-store，但 WS 逻辑在页面里，必须刷新）
2. 按住说话：按住→说“他们在搭什么”→松开 → 出识别+语音
3. 自动 VAD：直接说话 → 停顿后自动回答
4. 观察 `mic rms`：说话时 ≥0.05；`speech_end bytes` 应 ≥30000（1s+）
5. 回答播放时再说话（barge-in）不应自激循环

## 7. WS 协议速查（页面↔服务端）

上行：`{type:"start",book_id,page,sample_rate:16000,vad:"client"}`；二进制 PCM16LE 16k 单声道；`{type:"vad",event:"speech_start"|"speech_end",t:epoch_ms}`；`{type:"text",text}`；`{type:"barge_in"}`。
下行：`ready` / `speech_end{t,bytes}` / `asr_final{text,ms}` / `answer_delta{text}` / `audio_start{seq,sample_rate,filler?}` + 二进制 PCM + `audio_end` / `answer{text,action,metrics}` / `done` / `error`。
服务端规则（client 模式）：`speech_end` 且 buffer≥3200B 才 `end_turn`；`t` 作为首音计时起点；`turn_active` 期间丢弃音频。

## 8. 纪律（必须遵守）

- `.env` 内有 MiniMax key：**不提交、不打印全文、不写进任何文档/报告**。提交前跑 key 泄漏检查（本仓库历史提交都做过）。
- `data/`（赛事绘本 PDF/音频/页图）有版权限制，已 gitignore，**不上传**。
- 提交作者用 `chhaaabbbccc <chhaaabbbccc@users.noreply.github.com>`，**不要写 Co-Authored-By**（用户明确反感 contributor 污染）。
- 改动跑 `python -m pytest -q`（18 个测试）+ WS 注入冒烟再 push；CI 会自动跑测试。
- 不要动甘雨/QQ/教材等其他项目目录；本任务只在 `paperbook-backend` 内。
- 端口 8790 被占时先 `netstat -ano | grep :8790` 找 PID `taskkill //F //PID`，别开新端口造成用户困惑。

## 9. 建议的修复顺序

1. **先做疑点 A**：去掉上传门控（一直上传）+ `end_turn` 已有 trim 兜底；或加 500ms 前置环形缓冲。这是最可能一击命中的修复（2730B 碎片=单 buffer 的证据链很强）。
2. 播放期间禁 VAD/上传（防自激，疑点 D）。
3. 用 §6 的畸形流回归 + 正常流回归，确认首音仍 <2s。
4. 若还有问题，换 AudioWorklet（疑点 B），并在页面显示每回合 `speech_end bytes`（现在只在服务端日志），让用户能直接看到录了多少。
5. 全部通过后 commit+push，更新 README 测试台一节。

—— 交接人：ZCode（2026-11-20 18:10）
