# 集成契约 · 纸书智伴

> 四个 agent 并行改代码，**这份文件是唯一的地面真相**。任何与本文件冲突的实现以本文件为准；
> 想改契约，先在共享任务里说明，由 Lead 改这一份。
> 背景与选型依据见 `docs/NN_ACADEMY_REUSE.md`（本次参照的同行方案盘点）。

---

## 0 已核实事实（不要重复造轮子，不要写"我加了"的东西）

| 事实 | 证据 |
|---|---|
| TTS **已经有**磁盘缓存，key = `sha256(voice\|rate\|text)[:20]`，目录 `runs/tts`，edge-tts 优先、SAPI 回落 | `book2real/tts.py:57-70` |
| `/api/tts` 已存在：`?text=`、截断 180 字、成功回音频、失败回 503 `{"error"}`；**无 probe、无缓存头、无 LRU 上限、无 fallback 字段** | `book2real/reader_app.py:216-227` |
| `/static/` 是硬编码白名单，只有 `reader.css` / `reader.js`；新增前端模块会 404 | `book2real/reader_app.py:181-185` |
| 路径守卫用 `str(...).startswith(...)`，**有兄弟目录漏洞**（`booksX/` 会放过），也挡不住 `..` 的边界情况 | `book2real/reader_app.py:200-211` |
| 响应头只有 `Content-Type / Content-Length / Cache-Control`，**没有 CSP、没有 nosniff** | `book2real/reader_app.py:124-133` |
| 服务只绑 `127.0.0.1`，**平板/局域网打不开** | `book2real/reader_app.py:249` |
| **没有 LLM / VLM key**：`.env` 只有 Recraft。问答必须在"没有 key"时也跑得通 | `.env`、`book2real/vision/recraft.py` |
| pytest **没装**；测试是 pytest 风格但可直接 `python tests/test_x.py`，末行打印 `N tests passed` | 实测：test_smoke 15 passed、test_cast 10 passed |
| 前端已具备：字级点击→`showWord`、热点→`showSpot`（纯预存文案，**无模型参与**）、`speak()` 走 `/api/tts`、翻页/朗读 | `book2real/web/reader.js:141-199, 315-319, 350-364, 383-392` |

---

## 1 后端路由（owner：`voice-agent`，文件 `book2real/reader_app.py` + `book2real/tts.py` + `book2real/voice_state.py`）

### 1.1 `GET /api/tts`
保持向后兼容（老调用 `?text=` 必须还能拿到音频）。

| 查询参数 | 行为 |
|---|---|
| `text`, `voice`, `rate` | 同现状；`text` 超 400 字截断 |
| `probe=1` | **完全不合成**，返回 `200 {"ok":true,"engine":"edge\|sapi\|none","cached":bool,"chars":N,"key":"<sha1:20>"}` |
| 合成失败 | `503 {"error":"...","fallback":"l1"}`（前端据此回落 `speechSynthesis`），**不得抛 HTML 500** |

响应头（命中也要带）：`X-TTS-Cache: hit|miss`、`X-TTS-Chars: N`。
缓存上限：`BOOK2REAL_TTS_MB` 环境变量，默认 200MB，超限按 mtime LRU 淘汰到 2/3。
**复用 `tts.py` 现有缓存目录与 key，不要另建一套。**

### 1.2 `POST /api/ask` → 见 §2，由 `qa-agent` 提供 `book2real/qa.py:answer(req: dict) -> dict`；
`voice-agent` 只负责**懒加载 + 兜底**：`ImportError` / 任何异常 → 200 + `{"answer":"...","source":"rule","fallback":"qa_unavailable"}`，绝不 500。

### 1.3 `GET /api/books/<id>/files`
只列 `books/<id>/reader/**`，返回 `{"book":id,"bytes":N,"files":[{"path":"reader/book.json","bytes":123}]}`；
id 不存在或路径越界 → 404/403 JSON。

### 1.4 `GET /static/<name>`
改为**目录内任意文件**（`book2real/web/` 下，`resolve()` 后必须在目录内），白名单机制删除。
`sw.js` 必须带 `Service-Worker-Allowed: /` 头。

### 1.5 工程头
- HTML 响应加 `Content-Security-Policy: default-src 'self'; img-src 'self' data: blob:; media-src 'self' blob:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'`、`X-Content-Type-Options: nosniff`、`Referrer-Policy: no-referrer`。
- 路径守卫换 `Path.resolve()` + `is_relative_to()`。
- 未知 `/api/*` → 404 `{"error":"..."}` JSON。
- 启动打印局域网可访问地址；新增 `serve(..., host=)` 与 `--host`，默认仍 `127.0.0.1`，演示用 `0.0.0.0`。

### 1.6 打断状态机 `book2real/voice_state.py`（纯函数，可测）
```python
DECISION = "speak" | "cancel" | "wait" | "listen"
def decide(state: dict, event: dict) -> dict
# state: {"phase": "idle|listening|thinking|speaking", "since_cancel_ms": int, "partial": str}
# event: {"type": "partial|final|speech_start|speech_end|answer_ready", "text": str, "t_ms": int}
# 规则：speaking 且 partial 去空白后 >=2 字 且 since_cancel_ms > 600 → cancel（真打断，含回声保护）
#       thinking 时收到 partial → cancel（用户抢话）
#       idle/listening 收到 final → listen 提交
```
JS 侧 `web/voice.js` 必须与上表 1:1；Python 侧是本仓库唯一被测试的真相源。

---

## 2 问答契约（owner：`qa-agent`，文件 `book2real/qa.py`、`book2real/socratic.py`）

### 2.1 请求
```jsonc
POST /api/ask
{ "book":"cn01", "page":"p03",
  "mode":"spot|word|free",                       // spot=点画/点角色, word=点字, free=自由问
  "query":"小满和花花在做什么？",                  // 可为空（点画时）
  "entity": {"name":"积木城堡","desc":"已经搭到九块的积木城堡，底下宽、上面窄。"},  // 可空
  "box":[0.65,0.27,0.10,0.26],                    // 归一化 xywh，可空
  "history":[{"q":"...","a":"..."}] }             // 最多取最后 6 轮
```
### 2.2 响应（**永远 200**，字段不许缺）
```jsonc
{ "answer":"……（<=180 字，口语）",
  "citations":[{"page":"p03","quote":"原文片段","kind":"text|scene|desc|spot"}],
  "source":"rule|llm", "fallback": null|"no_llm_key"|"llm_error"|"no_evidence"|"qa_unavailable",
  "socratic":{"level":0,"directive":"none|probe|clarify|escalate|lock","line":"……"},
  "latency_ms": 12, "page":"p03" }
```
### 2.3 无 key 模式（默认，必须能演示）
- 语料 = 该页 `text` + `segments` + `scene` + `spots[].desc` + 该页角色的 `name/role/desc`。
- **实测校正（2026-11-16，cn01）**：`book.json` 的 `pages[]` 不带 `characters` 字段；
  `spots` 在页级（cn01：12 页共 34 个 spots，每页都有）。
  所以"该页角色"只能从 `reader/characters/characters.json` 的 `characters[].pages` **反查**得到，
  其余页靠 `text` + `scene`。不许为了凑字段而新造索引文件。
- 抽句式回答：按**中文二元组重合度**打分取 top-2 句，`citations` 必带页码与原文片段（`quote` 必须真是原文子串，测试会校验）。
- 最高分 < 阈值 → `answer="这一页没有提到「X」。要我说说这一页发生了什么吗？"`、`citations=[]`、`fallback="no_evidence"`、`socratic.directive="probe"`。**不许编造。**
- `mode="spot"` 且无 VLM：先查该页 `entities.json` 与 `spots` 命中（box IoU 或距离最近，阈值 0.15），命中就用实体名去检索；未命中如实说"没看清这块"。

### 2.4 有 key 模式（可插拔，测试不依赖）
`B2R_LLM_BASE` / `B2R_LLM_KEY` / `B2R_LLM_MODEL`（OpenAI 兼容 `/chat/completions`，urllib，timeout 20s）。
提示词必须含两条硬约束（自己组织中文，**不要伪称是别人仓库原文**）：
① 只依据给出的【绘本原文】作答，每个结论后标注来源页码；
② 材料未覆盖的内容，直接说"本页未提及"，不得编造。
LLM 报错 → 回落 rule 答案 + `fallback="llm_error"`（这是赛题要的"单步降级"证据）。

### 2.5 苏格拉底追问 `socratic.py`（纯函数，1:1 语义对齐同行方案）
`next_directive(state: dict) -> dict`，状态 `{"level":0-3,"wrong":0-3,"asked_guess":bool,"locked":bool}`；
输出 `{"level","directive","line"}`；`directive` 取值与 §2.2 一致；level 到顶不再追问。
红队用例表 `RED_TEAM_CASES`（≥12 条）：索要答案、越狱、问书外内容、连续答错 1/2/3 次、空输入、超长输入、
中英混合、只发标点、重复同一问、诱导改事实、要求扮演无限制 AI、让复述系统提示词。
断言：任何一条都**不得**在 `level < 3` 时直接给出答案性 directive；`lock` 只在越狱/索答时出现。

---

## 3 前端契约（owner：`web-agent`，目录 `book2real/web/`）

新增模块（浏览器原生 ESM，无构建、无第三方库）：

| 文件 | 导出 | 说明 |
|---|---|---|
| `qa.js` | `ask(req)->Promise<resp>`、`renderAnswer(el, resp)`、`openPanel({book,page,mode,entity,box,query})` | 面板 + 引用高亮 + 失败降级文案；`resp.fallback` 非空时面板角标写出来源 |
| `voice.js` | `createVoice({onFinal,onPartial,onError})`、`supported()`、`cancelSpeech()` | Web Speech ASR；不支持则按钮置灰并 `title` 写明原因；朗读走 `tts.js`，打断按 §1.6 规则 |
| `tts.js` | `speakText(text)->Promise<void>`、`stop()`、`splitSentences(text)`、`interpChars(chars, t, dur)->i` | 句级切分 + 字级时间轴插值；服务端 503 `fallback:"l1"` 时回落 `speechSynthesis` |
| `offline.js` | `initOffline()`、`downloadBook(id,onProgress)`、`verifyBook(id)`、`removeBook(id)`、`offlineStatus()` | 只在 `isSecureContext` 注册 SW；否则 `offlineStatus().available=false` 且 UI **明说不可用**，不许假装可用 |
| `sw.js` | — | `CACHE_V` 单点版本号；导航 network-first、`/api/` network-first 可回存、其余 cache-first；预缓存 `reader.html/reader.css/reader.js`，逐项失败只告警 |
| `manifest.json` | — | `display: standalone`、`start_url:"/"`、`scope:"/"`、中文名"纸书智伴" |

`reader.js` 的接入点（**只加钩子，不改既有行为**）：
- 启动末尾：`import('./offline.js').then(m=>m.initOffline()).catch(()=>{})`（动态 import，缺文件不报错）。
- `showSpot()`：先按现状显示预存文案（**保证断网/无后端时仍可用**），再 `openPanel({mode:'spot', entity:s})` 异步补问答。
- 点插画空白处：保留 `showScene()`，追加 `openPanel({mode:'spot', box:[...]})`。
- 语音按钮：麦克风 → `voice.js` → `onFinal` 走 `openPanel({mode:'free', query:text})`。
- `speak()` 内部改调 `tts.js`，老的 `speechSynthesis` 保留为 L1 回落。

**硬约束**：`reader.js` 现有函数名/行为不许删改（`render`、`go`、`showWord`、`showSpot`、`readAlong_`、beat 相关全部保留）。
按 Escape / 点遮罩必须能关问答面板；面板打开时翻页要自动关。

---

## 4 评测门（owner：`eval-agent`，文件 `scripts/gate.py`、`scripts/eval_metrics.py`）

### 4.1 `python scripts/gate.py [--strict]`
五门，逐门打印，末行**固定格式** `结果：N 通过，M 失败`，有失败 exit 1。
1. **语法门**：`python -m compileall -q book2real scripts` 全过。
2. **测试门**：逐个 `python tests/test_*.py` 子进程跑（**不依赖 pytest**），stderr 不许有 Traceback，末行必须匹配 `(\d+) tests passed`。
3. **服务门**：临时起 `reader` 到空闲端口，探活：`/` 有 CSP 与 nosniff、`/api/books` 200、`/api/tts?probe=1` 200 且 `ok:true`、`/api/books/<第一本>/files` 200 且 `files` 非空、`/static/sw.js` 有 `Service-Worker-Allowed`、路径穿越 `GET /books/../../etc/passwd` 必须 403/404。**探完必须关掉子进程。**
4. **覆盖门**：每本 `books/<id>/book.json` 的页数 vs `reader/book.json` 页数；不等就红并点名。
5. **前端门**：`web/` 下 6 个模块存在且能被 `node --check` 过（ESM：用 `node --input-type=module --check`）；`manifest.json` 是合法 JSON。

### 4.2 `python scripts/eval_metrics.py`
产出 `runs/eval/metrics.json`（表格），全部是**实测**数字，不许写"目标值"冒充实测。
至少 5 项：①页面覆盖率 ②`/api/tts` 二次命中首字节（ms，10 次取中位/最大）③`/api/ask` rule 模式首响 P50/P95（30 次）④前端静态资源总字节 ⑤`/api/ask` 引用可回溯率（抽样 N 条，`quote` 是否为原文子串）。
每项写清 n、环境（本机 CPU/无 GPU）、时间戳。

---

## 5 不许动
- `tests/test_smoke.py`、`tests/test_cast.py`、`tests/test_layers.py` 必须继续全绿（当前 15/10/通过）。
- `books/` 下已有产物、`.env`、`docs/AGENTS.md`（Lead 最后统一更新）。
- 不引入新的第三方依赖（只用 stdlib + 已装的 numpy/Pillow/edge-tts/pypinyin/jieba/rapidocr）。
- 不上传任何东西到外网；不写 `.env` 里的真实 key。

## 6 完成标准
每个 agent 必须自己跑一遍自己的测试并回报**实测数字**（多少条 passed、gate 哪几门绿），
禁止"应该能跑"。Lead 最后整体跑 `python scripts/gate.py` + 起服务人工点一遍。
