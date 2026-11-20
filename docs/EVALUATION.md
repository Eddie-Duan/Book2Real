# 查询链路评测（ASR / 检索 / 问答）

`book2real evaluate` 把官方录制脚本里的 50 行动作当成评测集，回答一个问题：
**这条链路在真实录音和真实绘本内容上，到底做了什么、哪里做不到？**

它只报告可以实测的东西：ASR 的 CER、检索 top1 的连续分、首响分位数、覆盖率。
没有人工标注时它**不会**给出正确率——报告里 `accuracy` 明确是 `null`。

```powershell
python -m book2real evaluate `
  --book books/cn01 `
  --audio "纸书智伴企业预选赛绘本素材/绘本素材/中文绘本01" `
  [--limit 5] [--asr auto|whisper|sapi|none] [--script <录制脚本>] [--labels labels.json] [--json out.json]
```

产物固定写到 `runs/<book_id>/eval/`：

| 文件 | 内容 |
| --- | --- |
| `query_report.json` | 汇总 + 逐条明细（机器可读） |
| `query_report.md` | 汇总表 + 方法 + 局限（人可读） |

`--json` 只是把同一份 JSON 再复制一份到指定路径。

## 输入与配对

- `books/<id>/book.json`：标题、语言等元数据。
- `books/<id>/reader/book.json`：检索语料（`pages[].text/scene/spots[].name/desc`）。
  缺这个文件会直接报错并提示，不会进入逐条评测。
- 脚本默认按 id 映射：`cn01 → 中文绘本01.txt`，`en01 → 英文绘本01.txt`；
  其他 id 必须用 `--script` 显式指定。
- 音频按文件名数字与脚本行配对（`06.wav → n=6`）。官方素材实收 **223 个 wav**
  （不是 50×6=300），缺号是官方缺失，记 `no_audio` 跳过，不补造。
- 只评测 `kind ∈ {ask, find, highlight, click}`：播放/暂停/继续等 transport 行
  不算 Query，只在 `metrics.transport_skipped` 里计数。
- `--limit N` 只截断 Query 条数（按脚本编号顺序取前 N 条），不改变 transport 统计。

## ASR（三个后端，可切换）

`--asr` 取值：`auto`（默认）= faster-whisper 优先、装不上才退 SAPI；
`whisper` / `sapi` 强制指定；`none` 完全不启动 ASR。

### faster-whisper（推荐，实测可用）

- 依赖装在仓库根 `.deps/`（`faster_whisper` / `ctranslate2` / `av` /
  `opencc-python-reimplemented`），`evaluate` 会自动把该目录加进 `sys.path`，
  不污染系统 Python。正常机器上直接 `pip install faster-whisper` 也行。
- 模型放在 `.cache/whisper/<size>/`（`model.bin` + `config.json` + `tokenizer.json`
  + `vocabulary.txt`，从 `hf-mirror.com` 拉取，不依赖 huggingface_hub）。
- 环境变量：`B2R_WHISPER_MODEL`（`small` / `medium` / 绝对路径，默认 `small`）、
  `B2R_WHISPER_DEPS`、`B2R_WHISPER_DEVICE`（默认 `cpu`）、
  `B2R_WHISPER_COMPUTE`（默认 `int8`）、`B2R_ASR_ENGINE`（`auto|whisper|sapi`）。
- **每个 wav 单独做语种检测**，不传死 `language`：官方脚本中英混排
  （`What happened?` 这类英文句），强行指定中文会被 whisper 译成中文，CER 直接记 1.0。
- **用书内词表做 hotwords**：`build_hotwords()` 取 `reader/book.json` 的
  `spots[].name` + `words[]`，加应用自己的指令词（找到/查找/高亮/播放/暂停/继续/
  画面/文字/单词），中文长词再拆 2 字组。专项名词（`KARATE`→`Carrot`、`城堡`→`重保`）
  全靠这一层救回来。
- 中文结果过 opencc `t2s` 归一为简体，避免繁体字（`為什麼會晃`）抬高 CER。
- 音频用标准库 `wave` + numpy 解码，不需要 ffmpeg。

本机实测（CPU int8、`small`、hotwords 开启）：

| 书 | 识别 | CER 均值 | CER p95 | 检索 accuracy | 关掉 ASR 的 accuracy |
|---|---|---|---|---|---|
| `cn01` | 27/27 | 2.90% | 0.23 | **85.7%** | 85.7% |
| `en01` | 31/31 | 2.15% | 0.00 | **54.5%** | 54.5% |
| `en02` | 33/33 | 3.60% | 0.20 | 无标注 | — |
| `en03` | 31/31 | 3.00% | 0.21 | 无标注 | — |

两条关键结论：

1. **ASR 已经不是瓶颈**：开 ASR 与关 ASR 的检索 accuracy 完全相同（两本有标注的书
   都一分不差），说明识别没有吃掉任何命中。这是 hotwords 换来的——只上 whisper
   不加 hotwords 时 `en01` 只有 36.4%（`KARATE` 被听成 `Carrot`/`Carrotty`，
   两条精确文字检索直接作废）。
2. **英文书的瓶颈在检索，不在语音**：`en01` 的 5 条失败里 3 条是泛问句
   （`What is this?` / `What sport is it?` / `What do they discuss?`），
   关键词检索没有任何锚点，score 直接 0.0 判 `no_match`；另 2 条是翻错页
   （`Is Dad awake?` 命中 p07，标注是 p28）。这类问题需要走 QA/语义匹配，
   而不是靠 IDF 词面覆盖。

### CER 的分词规则（踩过的坑）

CER 按**行**分词，不按书的语言：一行里出现汉字就按字符算，纯英文行才按单词算。
原因是英文书脚本长这样——`查找文字save`，而 whisper 匹配到 hotword 后会插逗号：
`查找, 文字, Save`。按单词算就是 3 个词对 1 个词，CER 直接 2.0～3.0；
按字算两个串的字符序列完全一致，CER 0.0。改这一条把 `en02` 的均值从 60.6% 拉回 3.6%。

### Windows SAPI（后备，本机不可用）

`--asr sapi` 时通过 PowerShell 调 `System.Speech.Recognition`：

- 识别器 culture：中文 `zh-CN`，英文 `en-US`。
- 每个 wav 单独一个子进程，默认超时 30 s（`B2R_ASR_TIMEOUT` 可覆盖）。
- 缺识别器 → `asr_unavailable`；空结果 → `asr_empty`；超时 → `asr_timeout`；
  子进程失败 → `asr_error`。**这些情况逐条降级为 `skipped`，整轮继续跑**。
- 本机实测：`InstalledRecognizers()` 能列出 `MS-2052-80-DESK (zh-CN)`，但构造
  `SpeechRecognitionEngine` 抛 `No recognizer of the required ID found.`
  （按 culture 构造、按 Id 构造、32 位与 64 位 PowerShell 全失败），
  英文 `en-US` 根本没装。**所以 SAPI 在本机永远是 0 条可识别**，
  它只作为没有 `.deps` 时的兜底路径保留。

- `--asr none` 不启动任何子进程，直接用脚本文字当查询；此时 `transcript`、
  `cer` 都是 `null`，`metrics.asr.attempted = 0`。

CER 只在识别成功的条目上计算：中文按字符、英文按单词（Levenshtein / 参考长度）；
转写为空或参考为空时记 `null`。9 条 `skipped` 全部是 `no_audio`——官方素材里
标【必选】的那 13 行没有 wav（`01–05`、`16–18`、`30`、`34`、`38`、`39`、`50`）。

## 检索

不依赖浏览器，只读 `reader/book.json`：

1. 每页把 `text`、`scene`、所有 `spots[].name/desc` 拼成文档。
2. 查询和文档都过 `qa._bigrams`（中文按连续非停用字 2-gram、英文按单词；
   直接复用，未改 `qa.py`）。
3. 用 IDF 加权覆盖率打分：`Σ idf(命中 gram) / Σ idf(整条查询)`；
   命中的 spot 名至少给 0.6，避免被摘要句稀释。
4. 返回 top1 页、连续分和前 3 候选（`candidates`）。

`find` / `highlight` 的 top1 分 ≤ 0.02 时如实标 `no_match: true`——这类行是
“翻到某画面”的导航意图，没有人工标注就无法判断“用户心里那一页”，所以不猜。

## 问答与延迟

对每条查询调用 `qa.answer({"book": id, "page": top1_page, "query": query})`：

- 评测期间清空 `B2R_LLM_KEY` / `DEEPSEEK_API_KEY` / `LLM_API_KEY`，
  强制走 **离线 rule 路径**；评测本身不需要网络，结果可复现。
- 逐条记录 `answer`、`source`（rule/llm）、`fallback`、`latency_ms`（qa 自报）。
- 汇总给 `latency.p50_ms` / `latency.p95_ms`（qa 自报耗时）以及
  `latency.wall_p50_ms` / `wall_p95_ms`（含 ASR、检索、作答的整条耗时）。
- 单条异常被兜住（`status=skipped`，`reason=error:<类型>`），不会中止整轮。

## 准确率：默认不报

没有人工标注就没有正确率。报告固定包含：

- `accuracy: null` 和 `metrics.accuracy: null`（顶层与汇总各一次）；
- `coverage = 成功处理条数 / 评测条数`；
- ASR CER 均值 / p50 / p95；
- 检索 top1 平均分 / p50 / p95、`no_match` 条数；
- 首响 p50 / p95。

只有提供 `--labels labels.json`（形如 `{"4": "p03", "5": "p01"}`，键是脚本编号、
值是页 id）时，才计算 **top1 page 命中率**，并写进 `accuracy` 与
`labels.{labeled_items,hits}`。未出现在标注里的条目不进分母；被跳过（缺音频 /
ASR 失败）的条目也不进分母。

## 测试

```powershell
python tests/test_evaluate.py
```

纯标准库、无网络、不需要真音频：临时目录里造 3 个只有 RIFF 头的假 wav、
假脚本（含 2 条 transport）和假 `book.json`，覆盖报告字段、transport 跳过、
CER/accuracy 为 null、`--limit`、`--labels` 命中率、ASR 失败降级、
缺失音频目录的清晰报错（无 traceback），以及 CLI 末行格式。
末行输出 `N tests passed`。

## 已测结果（2026-11-16，本机）

```powershell
python -m book2real evaluate --book books/cn01 `
  --audio "纸书智伴企业预选赛绘本素材/绘本素材/中文绘本01" --limit 3 --asr none
# 结果：3 条，成功 1，跳过 2
```

前 3 条 Query 是 n=4/5/6；官方音频从 `06.wav` 起，n=4/5 无音频故跳过，
n=6 命中 p01（score 0.6）。`accuracy` 为 null。

`--asr auto` 的实测：

- `--limit 1`：第 1 条正是缺音频的 n=4，结果 `1 条，成功 0，跳过 1`；
  ASR 后端本身是 `available: true`，只是没轮到有音频的条目。
- 换 `--limit 6`（同一本书，仅用于看转写）：4 条有音频的条目全部识别成功，
  `结果：6 条，成功 4，跳过 2`；CER 均值 0.6694、p50 0.69，例如
  n=6 “高亮积木城堡” 转成 “高粱进入重蹈”（CER 0.8333），
  转写失真后检索分数为 0，但条目仍如实标 `no_match`。
- 英文 `en01`：本机无 `en-US` 识别器，`--limit 2 --asr auto` 得
  `结果：2 条，成功 0，跳过 2`，有音频的那条标 `asr_unavailable`。

## 实测 · 人工页级标注（2026-11-16 修正后）

> 打分器随后做了四处修正：去掉“找到/画面/查找”等任务套话、封面页降权、
> “结尾/开头”位置词、`拥抱→抱`等同义词与中英对象词桥接；无候选不再回退封面，
> 而是计为 miss。下表是修正后的重跑结果。

| 书 | 进入分母的标注 | 命中 | accuracy | top1 均分 | no_match | 首响 p50/p95 (ms) | coverage |
| --- | --- | --- | --- | --- | --- | --- | --- |
| cn01 | 7 | 6 | 0.8571 | 0.5185 | 13 | 0.0 / 0.0 | 0.75 |
| en01 | 11 | 6 | 0.5455 | 0.6045 | 11 | 0.0 / 0.5 | 0.775 |

- 分母只含“有音频 + 有标注 + 成功处理”的条目；官方缺音频的行（cn01 5 条、en01 3 条）
  不进分母，但仍计入覆盖率。
- **en01 的 11 条分母里，8 条是英文问句，命中 6 条（6/8）**；另 3 条中文页面动作查询
  只测“中→英”桥接，不代表英文问答能力。
- 首响是规则检索+抽取式问答的进程内耗时（亚毫秒级），**不含浏览器录音、ASR 与 TTS**；
  官方 2 s 口径见 README 的“实测”说明；端到端（话音结束→TTS 首音）尚未测。

### 仍未命中（修正后）

| 书 | n | 脚本原句 | 预测 | 标注 | 原因 |
| --- | --- | --- | --- | --- | --- |
| cn01 | 20 | 找到花花独处的画面 | p02 | p07 | “花花”在多数页都有 spot，内容 gram 只剩人名，同分按页 id 取 p02；需要场景理解而不是字面匹配 |
| en01 | 30 | Who is he? | p14 | p16 | 爸爸出现在 p14/p16，问句没有可区分的内容词 |
| en01 | 43 | What sport is it? | 无候选 | p25 | 页面只有 KARATE，没有“sport”这个词，字面检索无法跨到“运动”概念 |
| en01 | 46 | What do they discuss? | 无候选 | p27 | “discuss”全书没有，需要“晚餐时聊了什么”的语义推理 |
| en01 | 49 | Is Dad awake? | p07 | p28 | “dad”多页出现；是否“醒着”需要看图判断 |

### 结论（诚实版）

- **中文检索已达到可用线**：cn01 6/7 = 0.857；失败是“同分排序”而不是找不到内容。
- **英文问句 6/8**；失败集中在“问句概念词不在页面文字里”（sport/discuss/awake），
  这是纯字面检索的上限，需要 VLM/翻译或 DeepSeek 语义扩展。key 填入后应重测。
- **中文问句查英文页**靠 `_BRIDGE` 小词表（电影海报→film poster 等），只覆盖官方脚本
  点名的对象；不是通用翻译。
- 分母仍小（7 / 11），不能外推为隐藏集准确率；官方 ≥0.60 的正确率门槛要用
  50 条 Query（含人工答案）在完整链路上测，当前数字只作工程回归基线。
