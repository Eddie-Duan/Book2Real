---
name: living-picturebook
description: |
  交互式动态绘本的「总装 skill」：把一本绘本 PDF 变成一条**一次生成、不可二次编辑**的连续动态片，
  并向交互层交付「页 ↔ 时间码」契约——读者暂停时查表得知"现在是第几页"，点的元素由主办方限定，
  讲解由 LLM 生成。
  触发时机：①拿到一本绘本 PDF（或已切好的 `pages/`），要交付"整本会动 + 可暂停、可点、可讲解"的产品时；
  ②已有单页零文字关键帧，要把它组装成一条连续片并冻结时；③线上出现"页面对不上、暂停后判错页、
  接缝处跳一下、某段不对要整本重跑"这类问题，需要定位是切页、去字、分镜、生成、拼接、时间码哪一段坏的时。
  输入：绘本 PDF 或 `pages/page_###/` 目录；主办方给的可交互元素清单（元素名、出现页、一句出处文案）。
  输出：`pages/`（逐页零文字关键帧）、`director_out/`（分镜计划 + 桥接帧 + 分段提示词）、`out/`（按段生成的片与 QC）、
  `out/final_movie.mp4`（冻结的唯一成片）、`book_out/page_timeline.json`（页↔时间码契约）。
  能力边界：只做编排、契约与验收，不重复实现子 skill 的算法。PDF 切页后的单页去字/补全 → `picturebook-to-seedance`；
  分镜切段/接缝/桥接帧 → `picturebook-scene-director`；按段生成、QC、拼接 → `seedance-relay-producer`（`--plan` 模式）；
  交互层的点击元素描述与讲解文案写法 → `ai-video-prompt-architect`（**只服务交互部分，视频链路不用它**）。
---

# living-picturebook — 把一本绘本做成"一条不能改的活片"

一句话：**这不是一堆可替换的循环片段，而是一条一次生成、之后只能播不能改的片子；
交互不产生新画面，只在暂停时解释画面。**

和"把整本绘本做成一条成片"的区别不在画面，而在**契约**：

| 普通成片 | 本项目 |
| :--- | :--- |
| 交付一个 mp4 就完了 | 还要交付 `page_timeline.json`，否则交互层不知道"现在第几页" |
| 觉得哪段不好就重渲那一段 | 一处不满意 = **整本重跑重花钱**，QC 必须卡在冻结前 |
| 交互可以触发画面变化 | 交互只暂停 + 点击 + LLM 说一句话，**不碰视频** |
| 页是独立的 | 页只是时间轴上的区间，段才是生成单位（可合并多页） |

---

## 1. 六条铁律（统领级；子 skill 各自的铁律继续有效）

1. **整本一次生成，生成后不可二次编辑。** 包括"只重跑一段""只修一个接缝"——主办方的原话是
   「同一册绘本的视频必须一次生成，不能二次编辑（含生成视频与交互视频）」。所以任何改动都等于整本重来。
2. **最小单位是段，不是页。** 页可以被合进同一段（一镜到底），也可以在一段里被切在动作峰值处。
   把页当单位生成，就会退化成"幻灯片 + 硬切"。
3. **导演在生成之前，不在之后。** 先有 `scene_plan.json`（哪几页合成一段、多长、接缝用哪张 T、提示词），
   才有生成。反过来"先生成每页视频、再想办法串起来"，后期只有硬切和叠化两种手段。
4. **页时间码是交付物，不是副产品。** 没有 `page_timeline.json`，交互层暂停后无法判断读者在第几页，
   整套点击讲解就不成立。
5. **交互不产生新视频。** 暂停 + 点击元素 + LLM 讲解，全程不回写、不重生成、不改缓存。
6. **画幅与风格由首段锁定。** 跨页只许留白不许裁切；比例不一致的页在 S1 就要退回重做，不能拖到拼接。

---

## 2. 六段流水线（S0–S5）

| 阶段 | 谁做 | 关键命令 / 输入 | 产物 | 通过条件 |
| :--- | :--- | :--- | :--- | :--- |
| **S0 切页** | 你 | `pdftoppm -r 200 -png book.pdf pages/page_%03d` | `pages/page_001.png…` | 一页一图、不跨页、不裁切、≥200dpi |
| **S1 去字补全** | `picturebook-to-seedance` | `make_text_mask.py` → `composite.py` → `qa_check.py` | `clean_keyframe.png`、`page_manifest.json`、`qa_report.json`、`seedance_prompt.txt` | `qa_report.verdict = PASS`、`ocr_residual_count = 0` |
| **S2 分镜 + 桥接** | `picturebook-scene-director` | `plan_scenes.py` → `build_bridge_frames.py` | `director_out/scene_plan.json`、`bridge/T_*.png`、`prompts/seg_###_prompt.txt` | 每页都被分进某段、`seams[]` 无 `needs_human_review` |
| **S3 按段生成** | `seedance-relay-producer`（`--plan`） | `preflight.py --plan` → `generate.py` → `poll_download.py` → `qc.py` | `out/seg_###/clip.mp4`、`qc.json` | `preflight` 无 BLOCKED、每段 `qc.verdict = PASS` |
| **S4 拼一条并冻结** | `seedance-relay-producer` | `concat.py` → `report.py` | `out/final_movie.mp4`、`cost.csv` | 段数与 `segments[]` 一致、时长误差 ≤0.45s/段 |
| **S5 交付时间码** | `living-picturebook` | `book_timeline.py --plan … --clips-dir …` | `book_out/page_timeline.json`、`page_timeline.md` | 页区间连续、无空洞、估算页已标注 |

> 注意两件事：
> 1. `picturebook-to-seedance` **不负责把 PDF 拆成一页页**，它从"一张单页图"开始。切页是 S0。
> 2. `preflight` 必须用 `--plan`，不要用 `--pages-dir`。按页跑会把每一页都变成一条独立片，
>    丢掉合并与桥接帧，产出的东西和分镜对不上。

---

## 3. 目录布局

```
book/
├─ book.pdf
├─ pages/                      # S0 + S1 产物，一页一个目录
│  └─ page_001/
│     ├─ clean_keyframe.png
│     ├─ page_manifest.json
│     ├─ qa_report.json
│     └─ seedance_prompt.txt
├─ director_out/               # S2 产物
│  ├─ scene_plan.json          # 分镜、接缝、锚点、concat_plan
│  ├─ scene_plan.md
│  ├─ prompts/seg_001_prompt.txt
│  └─ bridge/T_002_foreground_occlude.png
├─ out/                        # S3 + S4 产物
│  ├─ preflight.json
│  ├─ pipeline_state.json
│  ├─ seg_001/clip.mp4
│  ├─ seg_001/qc.json
│  ├─ final_movie.mp4          # 唯一成片，冻结后不许再改
│  └─ cost.csv
└─ book_out/                   # S5 产物，交付给交互层
   ├─ page_timeline.json
   └─ page_timeline.md
```

---

## 4. 段即页：`--plan` 的契约

`preflight.py --plan director_out/scene_plan.json` 把 `segments[]` 翻译成 relay 的"页记录"：

| relay 看到的 | 实际来自 |
| :--- | :--- |
| `page_id` | `segment_id`（如 `seg_002`） |
| 首帧 | 段 `first_frame`（`keyframe` → `pages/…`；`bridge_frame` → `bridge/T_*.png`） |
| 尾帧 | 段**自己的** `last_frame`（`SHARED_T` 时必然等于下一段首帧） |
| 提示词 | `prompts/seg_002_prompt.txt` |
| 时长 | 段 `duration_sec`（只允许 5 / 10） |
| 接缝过渡 | `concat_plan`：`hard` → 无过渡；`xfade` → 叠化 |

三条不变量（写错就在接缝处跳一下）：

1. 尾帧用本段自己的 `last_frame`，不是下一段首帧（隐藏切的两侧本来就不同）；
2. `SHARED_T` 接缝两侧必须指向同一张 T，否则阻断；
3. 段时长只能 5 或 10，8 秒这种值直接拒绝，不四舍五入。

完整字段表见 `seedance-relay-producer/references/plan-adapter.md`。

---

## 5. 页时间码契约（交付给交互层的唯一契约）

`book_timeline.py` 产出 `page_timeline.json`：

```json
{
  "timeline_version": "1.0.0",
  "mode": "measured",            // measured = 用 ffprobe 读真实片段；plan = 只有分镜计划
  "total_sec": 95.2,
  "segments": [{"segment_id": "seg_001", "pages": ["page_001","page_002"],
                "start_sec": 0.0, "end_sec": 10.0, "duration_source": "clip"}],
  "pages":    [{"page_id": "page_001", "start_sec": 0.0, "end_sec": 5.0,
                "estimated": true, "segment_id": "seg_001"}],
  "checks":   {"max_abs_delta_sec": 0.12, "warnings": []},
  "interaction_contract": {
    "lookup": "按 t 在 pages[] 里二分查找，取 start_sec <= t < end_sec 的那一页",
    "pause_event": "暂停即查表，不需要视频侧发任何事件",
    "rule": "本文件是视频侧唯一交付给交互层的时间契约；视频不可切、不可重生成"
  }
}
```

三个必须理解的细节：

1. **`estimated: true` 表示这一页的时间码是猜的。** 合并段是一镜到底，页与页之间没有硬边界，
   只能按页数均分。交互层要接受这个不确定性，否则就去找主办方要"这句文字在画面上出现的大致时刻"。
2. **`mode` 决定可信度。** `measured` 来自真实片段的 `ffprobe`，`plan` 只是分镜计划；
   交付前必须跑成 `measured`。
3. **`checks.max_abs_delta_sec` 是自检。** 计划时长与真实片段差超过 0.45s/段时会在 `warnings` 里告警，
   通常意味着某段被平台截短了。

查询单点时间属于哪一页：

```bash
python living-picturebook/scripts/book_timeline.py --plan director_out/scene_plan.json \
    --clips-dir out --out-dir book_out --at 13.5
# t=13.5s -> page_004 [10.0, 15.0)
```

---

## 6. 交互层接口（本 skill 只约定形状，不实现）

1. 视频暂停 → 读 `currentTime` → 在 `pages[]` 二分查找 → 得到 `page_id`；
2. 用主办方给的元素清单（元素名 / 出现页 / 热区）过滤出"这一页可点的元素"；
3. 点击 → 把元素名 + 所在页 + 绘本原文一句交给 LLM → 输出讲解文本；
4. 全程**不触发任何视频生成**。如果讲解文案要配一段新画面，那就不属于本项目（违反"不可二次编辑"）。

热区标注是**一次性对位分析**（在冻结后的 `final_movie.mp4` 上标 bbox），不是编辑视频；
标注结果建议存 `book_out/element_timeline.json`，本 skill 只规定字段形状，不消费。

---

## 7. 执行命令串（复制即用）

### 7.1 首选：一条命令跑完 S3–S5

```powershell
# 免费演练：分镜 + 桥接帧 + 预检 + 打印生成 payload + 页时间码（不调 API、不花钱）
python living-picturebook\scripts\run_pipeline.py --pages-dir pages --out-dir book_out --dry-run

# 真跑（需要 $env:ARK_API_KEY；会产生费用）
python living-picturebook\scripts\run_pipeline.py --pages-dir pages --out-dir book_out `
    --resolution 1080p --yes

# 断点续跑 / 只跑某几步
python living-picturebook\scripts\run_pipeline.py --list
python living-picturebook\scripts\run_pipeline.py --from qc --to timeline --pages-dir pages --out-dir book_out
```

它按 `plan → bridge → preflight → generate → poll → qc → concat → timeline` 顺序调用下游三个 skill 的脚本，
**任何一步非零退出就整条停**；产物统一落在 `book_out/`：`scene_plan.json`、`bridge/`、`prompts/`、
`relay/final_movie.mp4`、`page_timeline.json`。

### 7.2 需要手工介入时：拆开跑

```powershell
# S0 切页
pdftoppm -r 200 -png book.pdf pages/page

# S1 去字补全（每页重复，或用上游脚本批处理）
python _build\picturebook-to-seedance\scripts\make_text_mask.py --image pages\page_001.png --blocks blocks.json
python _build\picturebook-to-seedance\scripts\composite.py --original pages\page_001.png --edited edited.png
python _build\picturebook-to-seedance\scripts\qa_check.py --original pages\page_001.png

# S2 分镜 + 桥接帧
python _build\picturebook-scene-director\scripts\plan_scenes.py --pages-dir pages --out-dir book_out
python _build\picturebook-scene-director\scripts\build_bridge_frames.py `
    --plan book_out\scene_plan.json --pages-dir pages --out-dir book_out

# S3 按段预检 → 生成 → 下载 → 质检
cd seedance-relay-producer
python scripts\preflight.py --plan ..\book_out\scene_plan.json --pages-dir ..\pages --out-dir ..\book_out\relay --resolution 1080p
python scripts\generate.py --out-dir ..\book_out\relay --resolution 1080p
python scripts\poll_download.py --out-dir ..\book_out\relay
python scripts\qc.py --out-dir ..\book_out\relay

# S4 拼一条，冻结
python scripts\concat.py --out-dir ..\book_out\relay
python scripts\report.py --out-dir ..\book_out\relay

# S5 交付时间码（measured）
cd ..
python living-picturebook\scripts\book_timeline.py --plan book_out\scene_plan.json `
    --clips-dir book_out\relay --out-dir book_out
```

---

## 8. 成本与"重跑经济学"

- 单价（Seedance pro，1080p）：5s = 1.6 元、10s = 3.2 元；480p 5s = 0.4 元。
- 24 页绘本大约切成 9–12 段，一次过 **30–40 元**；5 页冒烟 **约 6.4 元**。
- **不可二次编辑 = 一处不满意就是整本重跑。** 所以：
  - 先用 480p 跑通全链路（成本 1/4），确认叙事与接缝都对，再整本 1080p；
  - QC 必须卡在冻结前，事后没有"修一下"这个选项；
  - 预算闸门 `budget_guard_limit` 默认 20 元会直接拦住整本，先调到 ≥100。

---

## 9. 验收清单

1. `director_out/scene_plan.json` 里每页都出现在某个 `segments[].pages` 中，无遗漏、无重复。
2. `preflight.py --plan` 输出 `ready_for_generation: true`、`blocked_count: 0`。
3. 每段 `qc.json` 的 `verdict == PASS`，且 `checks.seam.passed` 为真（有尾帧锚点的段）。
4. `concat.py` 产出 `final_movie.mp4`，段数 = `segments[]` 数，总时长与计划差 ≤ `0.45s × 段数`。
5. `book_timeline.py` 的 `mode == "measured"`，`checks.warnings` 为空，无 `estimated` 页（或已与主办方确认可接受）。
6. 抽 3 个时间点手工核验：`--at` 出来的页号与画面对得上。
7. 本 skill 自身回归（纯标准库，几秒跑完，不需要 API Key）：
   - `python tests/test_book_timeline.py` → 5 条：页区间布局、`[start, end)` 边界、叠化重叠、空 plans、CLI 落盘；
   - `python tests/test_run_pipeline.py` → 5 条：八步顺序与"花钱"标记、页目录校验、`--from/--to` 顺序、`--scene-scripts` 定位。

---

## 10. 失败处理表

| 现象 | 最可能坏在 | 处理 |
| :--- | :--- | :--- |
| `preflight --plan` 报段 BLOCKED | S1 的页 QA 或 S2 的锚点缺失 | 修上游页 / 重跑 `build_bridge_frames.py`，不要手改 `scene_plan.json` |
| 某段 `qc` 反复不过 | 提示词或锚点冲突 | 换 seed 重试 ≤3 次；仍不过 → 回 S2 调分镜，不要局部修 |
| 接缝处跳一下 | `SHARED_T` 两侧 T 不是同一张，或尾帧取错 | 重跑 `plan_scenes.py`，检查 `seams[]` 的 `realization` |
| `concat` 后总时长对不上 | 某段被平台截短 | 看 `checks.max_abs_delta_sec`，必要时升 10s 重生成该段 |
| 暂停后判错页 | 时间码是 `plan` 模式或估算页太多 | 用 `--clips-dir` 重跑 `book_timeline.py` 拿 `measured` |
| 需要改一处画面 | 违反"不可二次编辑" | 只能整本重跑；先把改动固化为提示词/分镜，再一次性生成 |

---

## 11. 已知限制

- **合并段的页时间码只能是估计值**。一镜到底没有硬边界，`estimated: true` 会一直存在。
- **QC 是一致性检查，不是审美判断**。人物崩坏、画风漂移仍需人工抽检。
- **整本重跑成本高**，所以本 skill 的验收重心全在"冻结前"。
- 本 skill 不生成文字、配音，也不做交互逻辑；它只保证"一条不能改的片 + 一张页时间表"。
- `picturebook-scene-director` 的 `qa_seams.py` 针对**离线成片**的接缝复核；本项目在 S3 用 relay 的
  `qc.py` 做逐段门禁，二者不重复执行。

---

## 12. 交接（Handoff）：本 skill 是入口，不是又一层执行器

> **DSH 事实：skill 之间不会自动互相触发。** skill 只是一份按需加载的指令集——模型看到 catalog 里的 `name` + `description`，自己决定加载哪一个；加载后按正文执行。没有“A skill 调用 B skill”的内建机制。所以接力靠三样东西落地：
> 1）这一步把成果写成**约定好的文件**；
> 2）下一步有一条**确定的命令**可跑；
> 3）失败时知道**回退到哪一步**。
>
> 本节的 Handoff 就是这三样的书面化。机器可读的那一半在 `living-picturebook/scripts/run_pipeline.py`（阶段化串联，失败即停）。

### 12.1 一条命令跑完整本

```bash
# 免费演练（不调 API）
python living-picturebook/scripts/run_pipeline.py --pages-dir pages --out-dir book_out --dry-run

# 真跑（需要 ARK_API_KEY；会产生费用）
python living-picturebook/scripts/run_pipeline.py --pages-dir pages --out-dir book_out \
  --resolution 1080p --yes

# 断点续跑 / 只跑某几步
python living-picturebook/scripts/run_pipeline.py --list
python living-picturebook/scripts/run_pipeline.py --from qc --to timeline --pages-dir pages --out-dir book_out
```

`run_pipeline.py` 的 8 步与本文档的 S3–S5 一一对应：`plan` → `bridge` → `preflight` → `generate` → `poll` → `qc` → `concat` → `timeline`。**任何一步非零退出，整条链立刻停止**——因为整本要拼成一条片，缺一段就拼不出来。

### 12.2 上游入口条件（S0/S1 不在这条链里）

- `pages/page_###/` 下四件套齐全且 `qa_report.verdict == PASS`（`picturebook-to-seedance` 的出口）。
- 全书画幅比一致。
- 不满足就别启动 `run_pipeline.py`——它的第一步 `plan_scenes` 会整本 BLOCKED。

### 12.3 交付给交互层（本 skill 的终点）

| 交付物 | 位置 | 谁消费 |
|---|---|---|
| 冻结成片 | `book_out/relay/final_movie.mp4` | 播放器 |
| 页时间码 | `book_out/page_timeline.json`（`mode: measured`） | 交互层，暂停时“秒 → 页” |
| 段计划 | `book_out/scene_plan.json` | 排障、重跑、对账 |

交互层的提示词与元素讲解 → `ai-video-prompt-architect`，本 skill 不参与。

### 12.4 边界（谁不改谁的文件）

- 本 skill **不改**上游三个 skill 的脚本；它只调用它们，并把结果汇总成时间码。
- 上游任何一步改了输入（页图、计划、锚点），本 skill 的时间码就要重算；`checks.max_abs_delta_sec` 会告警。
