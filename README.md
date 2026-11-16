# 纸书智伴

把一本**纸质绘本 PDF** 变成可点、可问、可检索的互动绘本视频。

这条分支（`main`）按官方赛题口径重建：输入只有绘本 PDF，提交 1 中 1 英两本，
成片 100–200 s / ≥720P / ≥24 fps / 一次生成不剪辑，交互侧覆盖语音问答、
视频内检索（跳帧、点选、描述帧、查字并框出）与触控式万物识别。
官方要求逐条对照见 [docs/OFFICIAL_REQUIREMENTS.md](docs/OFFICIAL_REQUIREMENTS.md)，
合规与素材来源见 [docs/COMPLIANCE.md](docs/COMPLIANCE.md)。

官方成片是**单趟逐帧渲染**（`storyfilm.py` → `encode_frames` → 只混音轨，不拼镜头），
满足答疑 Q43"同一册必须一次生成、不能二次编辑"。

## 快速开始

```bash
pip install -r requirements.txt          # 另需 ffmpeg 在 PATH
cp .env.example .env                    # 填 DeepSeek / 火山方舟 key（见 docs/API_KEYS.md）

# 1) 官方绘本 PDF -> books/<id>（页图 + 文本层 + 元数据）
python -m book2real import-pdf "纸书智伴企业预选赛绘本素材/绘本素材/中文绘本01.pdf" --id cn01
python -m book2real import-pdf "纸书智伴企业预选赛绘本素材/绘本素材/英文绘本01.pdf" --id en01

# 2) OCR / 清字 / 拼音 -> reader 包
python -m book2real analyze --book books/cn01
python -m book2real analyze --book books/en01

# 3) 单趟成片（100–200 s，1280×720，24 fps，带旁白与 timeline）
python -m book2real film --book books/cn01

# 4) 阅读器 + 视频内检索播放器
python -m book2real reader --books books --port 8770 --open
#   浏览器打开 http://127.0.0.1:8770/          互动阅读
#              http://127.0.0.1:8770/film?book=cn01   视频内检索
```

> 官方 6 本样书带"请勿外传"水印，`books/*/pages/`、`books/*/reader/`、`books/*/text/`
> 已进 `.gitignore`：只在本地生成，不进仓库、不进提交包。

## 三件套（官方指定）

```bash
python -m book2real cloud --check                     # 谁已就绪、花了多少钱
python -m book2real cloud --image "..." --out a.png --dry-run   # 只看价
python -m book2real story   --book books/cn01 --apply  # DeepSeek 写画面说明/问题
python -m book2real visuals --book books/cn01         # Seedream 补"重新生成"插图
python -m book2real clip    --book books/cn01 --page p03 --prompt "..." --seconds 5  # Seedance 试片
```

所有调用先过 `CLOUD_MAX_SPEND_CNY` 预算护栏，流水在 `runs/cloud/ledger.jsonl`。
`clip` 只做能力演示，**不拼进交付成片**（Q43）。

## 视频内检索

成片旁边不是只有播放键：`/film?book=cn01` 把 `timeline.json` 变成可检索的时间轴。

![视频内检索](docs/images/film-search.png)

- 「找到搭了九块积木的画面」→ 跳到那一帧并暂停，框出积木城堡，右下角给出介绍与页码；
- 点画面里的目标 → 暂停 + 展示它是什么；
- 「查找字/词」→ 在生成画面的 OCR 行里定位并只框那一行；
- 「描述这一帧」→ 当前页的 scene 与正文。

## 2.5D 分层（本地 demo 档）

静止页不再靠"整页推近"，也不再靠整页晃动。`python -m book2real layers` 把每一页拆成
**背景板 + 人物层**，并给人物层一个来自深度图的独立位移量：

| 步骤 | 工具 | 8GB 实测 |
|---|---|---|
| 人物轮廓 | SAM ViT-B（box 提示） | ~0.7 s / 页 |
| 背景修补 | OpenCV NS/TELEA 自选 | < 0.1 s |
| 深度 | Depth Anything V2 small | ~0.3 s / 页 |
| 切件修补 | SD-1.5 inpainting（`cast --models` 开关） | ~1.5 s / 图 |

网页用 CSS `transform` 做视差（鼠标移动），16:9 成片复用同一份 `layers/index.json`。
每页记录 `outline`（sam/box）与 `inpaint`（inpaint/none），**没做干净就如实标注**，
不拿一块色块冒充修复。

已知边界：两个人物贴得很近、共用一个挖补区域时，背景修补会在人物边缘留下一点过渡；
干净平涂页没有这个问题。细节见 [docs/READER.md](docs/READER.md) 的 2.5D 一节。

## 本地 / 服务器分工

- **本地 8GB 能跑**：互动阅读、纸偶、分层、深度、SAM、SD-1.5 局部修补；
- **本地跑不动也不值得跑**：Qwen-Image-Edit 20B、Wan2.2 14B、HunyuanVideo —
  放到 GPU 服务器或调用 API，见 [docs/DEPLOY.md](docs/DEPLOY.md)。

## 已有能力

| 能力 | 实现 |
|---|---|
| 输入 | `python -m book2real import-pdf <pdf> --id <id>`：抽页图 + PDF 文本层（有则用），记录 sha256 |
| 页面排序 | PDF 页序；图片导入按 `st_ctime` 归档，`ORDER.csv` 可追溯 |
| 文字识别 | rapidocr_onnxruntime（PP-OCR，离线）；英文/中文分别选正文列，滤掉官方水印与阅读指导栏 |
| 拼音 | pypinyin，逐字声调；按词换行（jieba）；英文书自动跳过拼音 |
| 插画清字 | OpenCV Telea inpaint，去掉页上印刷字；官方样书保留整页框（`trim:false`）以对齐点击框 |
| 点字 | 读音 + 本页词义 + 朗读 |
| 点画 | 场景讲解 + 人物热点 |
| 视频内检索 | `web/film.*` + `timeline.json`：按句子跳帧、点选目标暂停并展示、描述当前帧、查字/词并框出 |
| 人物卡 | 从原图裁出，作为一致性/后续 I2V 的参考 |
| 人物 agent | `python -m book2real cast`：PageScout → CastClerk → Rigger → MotionDirector → Critic，自动切件/关节/动作/质检（[docs/AGENTS.md](docs/AGENTS.md)） |
| 2.5D | 阅读页保持原图静止；人物墙里用原图切件做肩部转轴、挥手/说话 |
| 问答与追问 | `POST /api/ask` + `qa.py`/`socratic.py`：点画、点字、自由问三种入口，答案带**页码 + 原文引用**；无 key 时走抽取式规则作答，书外问题如实拒答；有 `DEEPSEEK_API_KEY` 时接 DeepSeek，失败单步降级回规则 |
| 语音 | 麦克风提问（Chrome Web Speech，不支持的浏览器按原因置灰）+ 句级 TTS（`/api/tts` 带缓存/probe）+ 打断状态机（`voice_state.py`，600ms 回声保护） |
| 离线 | 整书离线包：`sw.js` + `offline.js`；非安全上下文（局域网 http）会明说不可用，不假装可用 |
| 评测 | `scripts/gate.py` 五门 + `scripts/eval_metrics.py` → [`docs/METRICS.md`](docs/METRICS.md)（实测值/目标值分列，n 与环境齐全） |
| 旁白 | edge-tts（中文晓晓 / 英文 Aria），缓存复用；失败回退 Windows SAPI |
| 视频 | 16:9 单趟渲染，`film_target` 控制 100–200 s；AAC 音轨；timeline 供检索 |
| 云模型 | `cloud.py`：DeepSeek / 豆包 Seedream / Seedance，预算护栏 + 流水账 |

## 两条产品线

- **互动阅读**：`book2real/storybook.py` 生成 `reader/book.json` + 清洗后的插图，`reader_app.py` + `web/reader.*` 提供阅读器。
- **成片导出**：`book2real/storyfilm.py` 复用同一份 reader 包，渲染 16:9 短片；旧的 `run` 管线（分层视差 / 程序化微动 / pregen）仍在，用于样张与 I2V 预留。

## 文档

- [`docs/OFFICIAL_REQUIREMENTS.md`](docs/OFFICIAL_REQUIREMENTS.md) — 官方硬性要求逐条对照与验收清单
- [`docs/COMPLIANCE.md`](docs/COMPLIANCE.md) — 素材/模型来源、IP 承诺、儿童内容安全
- [`docs/API_KEYS.md`](docs/API_KEYS.md) — DeepSeek / 火山方舟 key 与预算护栏
- [`docs/EVALUATION.md`](docs/EVALUATION.md) — 50 条 Query 音频的评测口径与已知局限
- [`docs/READER.md`](docs/READER.md) — 互动绘本的产品设计、分析管线、视频版式
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — 旧版 2.5D 渲染与编排
- [`docs/HANDOFF.md`](docs/HANDOFF.md) — 实测数据、已知问题、下一步
- [`docs/METRICS.md`](docs/METRICS.md) — 实测指标（脚本生成，勿手改）
- [`docs/INTEGRATION_CONTRACT.md`](docs/INTEGRATION_CONTRACT.md) — 问答/语音/离线/评测门的接口契约

## 目录

```
book2real/
  ingest.py         PDF -> books/<id>（页图 / 文本层 / book.json）
  storybook.py      OCR → 正文选择（中/英）→ 拼音/分词 → 清字 → reader 包
  storyfilm.py      16:9 单趟朗读短片（文字左 / 插图右 / 旁白 / timeline.json）
  cloud.py          DeepSeek + Seedream + Seedance，预算与流水账
  creative.py       文生故事 / 文生图补图 / Seedance 试片
  reader_app.py     互动阅读器 HTTP 服务 + /api/film 视频内检索 + 导入/出片任务
  qa.py             页级问答（抽取式规则 / 可选 DeepSeek，引用必须回溯原文）
  socratic.py       追问状态机（纯函数，15 条红队用例）
  voice_state.py    打断决策（纯函数，JS 侧 1:1 镜像）
  web/reader.*      阅读器界面（无构建步骤）
  web/film.*        视频内检索播放器（跳帧 / 点选 / 描述帧 / 查字框出）
  web/qa.js voice.js tts.js offline.js sw.js   问答面板 / 语音 / 句级 TTS / 离线包
  tts.py            edge-tts 旁白 + 缓存（probe / LRU 上限）
  ocr / pinyin / segmentation 依赖放在 vendor/（离线演示）
books/<id>/          pages/ + text/ + book.json + curation.json + reader/
runs/                产物（gitignore）：成片/timeline、TTS 缓存、cloud/ledger.jsonl
scripts/gate.py      五门质量门；eval_metrics.py 出实测指标
scripts/package_submission.py  官方 4 件套打包（学校-院系-姓名.zip）
vendor/              pypinyin / jieba / edge-tts（比赛演示用，见许可证说明）
docs/                设计与交接
```

## 环境

> 仓库名仍保留 `Book2Real`（代码包名 `book2real` 不变），产品名称以官方赛题为准：**纸书智伴**。

Python ≥ 3.10，`ffmpeg` 在 PATH。依赖见 `requirements.txt`；仓库内 `vendor/` 已带
pypinyin、jieba、edge-tts，离线可直接跑（edge-tts 仍需网络；无网时回退浏览器/系统语音）。

```bash
pip install -r requirements.txt
```