# 后端融合说明（paperbook-backend → backend/）

> 本文记录融合后的**唯一运行形态**。冲突时以 `backend/`（原
> [paperbook-backend](https://github.com/chhaaabbbccc/paperbook-backend)，commit
> `54fbc62`）的代码与方案为准；`book2real/` 保留生成链（成片、分层、人物）与
> 阅读器前端。
>
> 合并记录：首次合入 `d9ae446`；2026-11-20 同步到 `54fbc62`（7 个提交：MiniMax
> TTS/ASR 接入、`/test` 实时语音页、客户端 VAD、打断回声修复、实时 MiniMax LLM、
> 麦克风增益与降噪修正）。同步时保留了本仓库的融合层 `app/frontend.py`、
> `app/compat.py` 以及 `app/main.py` / `app/config.py` / `.env.example` 的本地改动。
>
> **2026-11-25 同步到 `0daa3ed`**（2 个提交：`5f3e432` ack-then-verified answers、`0daa3ed` prewarm 50 queries）。
> 新增 `app/book/characters.py`、`app/pipeline/acks.py`、`answer_guard.py`、`query_cache.py`、`scripts/prewarm_queries.py` 及 3 个测试；
> `app/pipeline/turn.py` 三方合并时保留本地文风钩子，并合入上游"人物一致性/引号归属"规则；`cd backend && python -m pytest -q` = **51 passed**。
>
> **2026-11-22 同步到 `4ef57b4`**（5 个提交：闲聊 LLM 缓存预热、情绪感知检索、barge_in ack
> 修复、整本书问答检索快路径 + AudioWorklet 采集重写、语音测试页交接文档）。
> 这次合并方式：上游 `54fbc62..4ef57b4` 逐文件三方合并（`git merge-file`），
> 16 个文件直接取上游、4 个文件自动合并（`.env.example` / `app/config.py` / `app/main.py` /
> `app/providers/llm.py`）、`app/pipeline/turn.py` 人工解冲突，保留了 skill4 的文风钩子。
> 解冲突时发现并修掉一个真实问题：**文风卡的样句会把别的页原文带进提示词**，
> 现在指定页码时只用该页建卡（`test_explicit_page_replaces_open_page_in_answer_context` 覆盖）。
> `cd backend && python -m pytest -q` = **36 passed**。

## 1 目录与职责

```
Book2Real/
├─ backend/            # 唯一后端服务（FastAPI，8790）：入库/OCR、ASR、TTS、问答、视频检索、会话、指标
│  ├─ app/main.py      #   HTTP + WebSocket 入口
│  ├─ app/frontend.py  #   融合层：阅读器/影院页面、旧 API 适配、/api/tts GET 兼容
│  ├─ app/compat.py    #   融合层：老请求/老响应字段 ↔ backend 契约
│  ├─ tools/           #   ingest_competition.py、ws_latency_probe.py、eval_queries.py
│  └─ docs/            #   后端自己的契约文档（INTEGRATION.md / MINIMAX.md / OCR.md ...）
├─ book2real/          # 生成链 + 阅读器前端（web/）+ 旧 StdHTTP 服务（legacy）
├─ books/<id>/         # 页图、curation、reader 包（由 backend 入库结果重建）
├─ data/               # backend 运行时数据：data/books/<id>/book.json、页图、SQLite、缓存
└─ scripts/            # sync_reader.py、start_backend.ps1、ingest_all.ps1
```

- **只跑一个进程**：`scripts\start_backend.cmd`（= 仓库根 `.venv` 里的 `uvicorn app.main:app`，端口 8790）。
  阅读器页面 `/`、影院 `/film?book=cn01` 与 API 同源，避免 CORS/端口漂移。
- **入库只有一条路**：backend 的 `app.book.ingest`（PDF 文字层 + RapidOCR 兜底 +
  脚本/音频解析），产出 `data/books/<id>/book.json`。
- **阅读器包由 backend 数据重建**：`book2real/backend_bridge.py` 把 backend 的
  blocks/story_text/元素卡翻译成 `storybook.build()` 的输入，阅读器不再自己重跑 OCR。
  没有 backend 数据时自动退回本地 OCR（离线夹具/自造书不受影响）。

## 2 一次性准备

```powershell
.venv\Scripts\python.exe -m pip install -r requirements.txt   # 或先跑 scripts\setup_env.ps1

# 1) 6 本官方素材入库（PDF → data/books/<id>，含 OCR，约 10–20 分钟；已入库可省）
py backend/tools/ingest_competition.py --path "纸书智伴企业预选赛绘本素材" --force

# 2) 用 backend 的入库结果重建 books/<id>/reader
py scripts/sync_reader.py

# 3) 启动唯一服务（.cmd 绕过 PowerShell 执行策略；.ps1 需要 -ExecutionPolicy Bypass）
scripts\start_backend.cmd               # http://127.0.0.1:8790/  ; /film?book=cn01
```

`scripts/ingest_all.ps1` 把 1–2 步串起来。

## 3 契约对照（老 → 新）

| 能力 | 老实现（book2real/reader_app.py） | 融合后 |
| --- | --- | --- |
| 问答 | `POST /api/ask` `{book,page:"p03",mode,query,entity,box,history}` → `answer/citations/source/socratic` | **backend**：`{book_id,text,page:int,session_id,element}` → `answer/action/evidence/timings/metrics/audio_b64`；`app/compat.py` 补回 `citations/source/socratic/page` 供页面渲染 |
| TTS | `GET /api/tts?text=` → edge-tts 缓存 WAV/MP3 | `GET /api/tts?text=` 保留，引擎换成 backend 的 TTS Provider（SAPI/CosyVoice/MiniMax）→ WAV；`POST /api/tts` 是 backend 原生 PCM16 流 |
| ASR | 无 | `POST /api/asr` → `WS /ws/voice/{session}`（VAD→ASR→检索→LLM→TTS，2s 首音指标） |
| 绘本列表 | 扫描 `books/*/reader/book.json` | `GET /api/books` 由 backend 给出（`pages` 字段已补齐，供阅读器下拉框） |
| 绘本详情 | `GET /api/book?book=` → 阅读器包 | 保留原样（阅读器包仍由 `books/<id>/reader/book.json` 提供） |
| 成片/导入 | `/api/film/*`、`/api/import`、`/api/video` 后台任务 | 原逻辑复用，挂到 backend 进程（`app/frontend.py`），同一端口 |
| 会话历史 | 前端 `history` 数组 | backend SQLite（`/api/sessions/{id}/history`），前端只发 `session_id` |
| 指标 | 无 | `/api/metrics/latency`（p50/p90/p95、2s 达标率、填充音比例） |

开关与配置只读仓库根 `.env`（`backend/app/config.py` 先读根 `.env`，再读
`backend/.env` 补缺）；`LLM_API_KEY/DEEPSEEK_API_KEY`、`LLM_API_MODEL` 等旧命名
已做别名，有 key 时自动切 OpenAI 兼容，否则用提取式兜底。

## 4 测试与冒烟

```powershell
cd backend
..\.venv\Scripts\python.exe -m pytest -q    # 36 passed（pipeline/检索/切句/VAD/ASR）

# 进程级冒烟
scripts\start_backend.cmd
#   GET /health                 -> warm / providers / books
#   GET /api/books              -> 6 本
#   POST /api/ask               -> {"book_id":"cn01","text":"他们在搭什么？","page":3}
#   GET  /api/tts?text=你好      -> audio/wav
```

`book2real` 的测试是脚本式的（`python tests/test_x.py`，末行打印 `N tests passed`）。
其中 `test_server.py` / `test_film.py` 靠模块级 `main()` 起进程，从仓库根跑 `pytest`
时 `pytest.ini` 会跳过它们；其余文件 pytest 与脚本两种跑法都成立。

## 5 验证记录（2026-11-16，本机实测）

| 项目 | 命令 | 结果 |
| --- | --- | --- |
| backend 单元测试 | `cd backend; ..\.venv\Scripts\python.exe -m pytest -q` | 36 passed |
| 融合层测试 | `py tests/test_fusion.py` | 11 passed（页面/CSP/静态/绘本包/兼容层/数据同源） |
| 老阅读器回归 | `py tests/test_film.py` | 9 passed |
| 启动 | `scripts\start_backend.cmd` | 监听 0.0.0.0:8790，warm=True，6 本已加载 |
| 接口 | `/health`、`/api/books`、`/api/book`、`/api/film/cn01` | 200，12/28 页，成片 timeline 114.79 s |
| 问答 | `POST /api/ask`（新字段与 `{book,page:"p03",query}` 老字段各一次） | 两次都 200，`citations` 3 条，`latency_ms` 有值，SAPI 22050 Hz |
| TTS | `GET /api/tts?text=你好` → WAV；`POST /api/tts` → `application/x-pcm` | 200，60 KB / 100 KB |
| 页面 | 浏览器打开 `/`（cn01）→ 问“积木城堡是怎么倒的？” | 面板显示答案 + p05/p02/p03 引用 |

顺带修掉的两个问题（都属于融合后暴露出来的）：

- `backend/app/providers/tts_local_stream.py`：`_stream_http` 把 `self._payload`
  （方法）当 JSON 发出去，本地 TTS 预热必然失败；改成 `self._payload(text, language)`。
- `book2real/web/reader.js`：英文单词在 `inline-block` 里结尾的空格被吞，页面显示
  “ChoplinIlike”；空格改渲染成 `&nbsp;` 后正常。

## 5.1 文字层之外的“画中字”（2026-11-17 补）

官方绘本里有压在插图上的对白，例如 cn01 第 6 页红色斜排的“我又不是故意的！”：
PDF 文字层不包含它，但录制脚本第 18 条（必选）要求“查找”这句话。原来的入库
“文字层够长就完全跳过 OCR”，所以这类字既不进正文，也查不到。

现在的做法（`backend/app/book/ocr.py`，开关 `OCR_SUPPLEMENT=true`）：

- 文字层可用时**也跑一遍 OCR**，只补文字层没有的行；与已有行同文本、落在页脚
  教学栏的，一律丢弃；OCR 常把赛事水印粘在同一行，先剥掉水印碎片。
- 补出来的行标成 `kind="art_text"`：**进 blocks（查字要 bbox），不进正文
  story_text，也不在插图清洗时擦掉**——它本来就印在画里，擦掉会拖出涂抹痕。
- 实测（cn01，page 6）：`POST /api/ask {"text":"查找“我又不是故意的”"}` →
  `action.type=highlight_text`，bbox `[0.2673,0.4454,0.4274,0.5492]`，页码 6；
  正文仍是文字层原文，插图保留印章，无涂抹。

顺带修了拼音多音字：pypinyin 把“倒一地”的“倒”读成 `dào`，`storybook._POLYPHONE`
按词改成 `dǎo`（只收绘本里会误读的几条，不做全表）。

## 6 已知边界（下一步）

1. **苏格拉底追问**：backend 回合管线没有状态机，`compat.py` 目前返回
   `socratic.directive="none"`；要保留该能力需把 `book2real/socratic.py` 接进
   `pipeline/turn.py`（接口是纯函数，工作量小）。
2. **旧 StdHTTP 服务**（`book2real/reader_app.py`）暂留作回退与测试夹具；
   `app/frontend.py` 已覆盖同一组路由，确认无回归后可删。
3. **ASR 未在本机验证**：缺 `faster-whisper/ctranslate2` 的 3.14 wheel；语音回合
   用 `ASR_PROVIDER=http`（9881）或 `mock` 冒烟，正式环境装 CUDA 版即可。
4. **TTS 音色**：本地 SAPI（Windows 系统音色，10–80ms）是联调默认；正式演示切
   MiniMax（填 key）或 CosyVoice（9890 服务）。
5. `backend/requirements.txt` 是上游独立清单，融合后以仓库根 `requirements.txt`
   为准（OpenCV 用 headless，别同时装 `opencv-python`）。