---
name: picturebook-scene-director
description: |
  绘本多页动态化的「分镜导演 + 接缝验收」skill。读取上游 picturebook-to-seedance 产出的 N 页 clean_keyframe 与元数据，做全局场景划分与页→段切分（合并 / 拆分 / 插桥接），按「语义轴 × 视觉轴」2×2 矩阵为每条接缝定：同镜头 CONTINUOUS、有动机 HARD_CUT、MATCH_CUT、还是 DESIGNED_TRANSITION；需要共享锚点时生成静态桥接帧 T；最后对成片逐条接缝跑量化验收。
  触发时机：绘本/漫画多页要变成一条视频、需要决定"哪两页该并进一个镜头、哪两页必须切开、切开处怎么接才自然"时；或已有分段视频但接缝生硬、需要定位是哪条接缝坏了时。
  输入：pages/page_###/{clean_keyframe.png, page_manifest.json, qa_report.json, seedance_prompt.txt}。
  输出：scene_plan.json（执行段 + 接缝 + 拼接计划）、scene_plan.md（人读表）、prompts/seg_###_prompt.txt、bridge/T_*.png、seam_qa.json。
  能力边界：独立运行，不改上下游源码；纯 numpy + Pillow，CPU 可跑（无 GPU 自动降级）；不生成视频、不调 Seedance API、不做音频。
---

# picturebook-scene-director — 绘本分镜导演与接缝验收

把「N 页绘本」变成「M 段可以分别生成、再拼成一条片子的视频」，并且**为每条接缝给出可复算的决定**。

核心命题只有一句：**接缝自然与否，不取决于"差异有多大"，取决于"差异是哪一种"。**

- 差异大 + 语义连续 → 不该硬拼，该**切**（切是电影语言，不是失败）。
- 差异小 + 语义跳变 → 不该硬拼，该**匹配剪辑**（拿形状/动作/色彩做桥）。
- 差异大 + 语义跳变 → 需要**有动机的转场**（甩、遮、黑、白、推、桥接镜头）。
- 差异小 + 语义连续 → 才允许**同一个镜头**（双锚定 interpolate）。

现有 relay-producer 的 A/B/C 分级只用了这一件事的一个投影（DINOv2 距离），把中间态一律交给 0.4s 叠化，于是必然出现三类假转场：全景→特写被叠化（像 PPT）、跨场景构图接近被硬切（像跳帧）、同场景角色瞬移被强行双锚定（角色融化）。本 skill 就是把那一维拆成两维。

---

## 1. 铁律

1. **转场不是遮羞布。** 叠化只能表达"时间流逝/梦境/抒情"，不能用来掩盖景别跳变、比例不一致、角色身份不匹配。违反即返修。
2. **彻底禁用书页翻页（PAGE_TURN）。** 任何"书页卷曲/翻页"特效都不允许出现在成片里。跨场景一律用电影语言：甩镜、前景遮挡、黑场、白场、推镜穿入、独立桥接镜头、匹配剪辑、时间流逝叠化。
3. **一页 ≠ 一段。** 段数 M 与页数 N 无关。两页可以并成一段，一页也可以拆成两段（动作弧超过单次生成上限时），跨场景可以额外插一段桥接。若出现 `len(segments) == len(pages)` 且每条接缝都是硬切，视为未真正执行本 skill。
4. **同镜头 = 真·一镜到底。** 一个 `CONTINUOUS` 段里只能有一条运镜、一条动作弧，不许暗藏切点。单次生成受 Seedance 5s/10s 限制；同场景叙事超过 10s 时**拆成多段连续镜头**（动作峰值处切），不许硬拉成一段双锚定。
5. **跨场景禁双锚定，异比例禁同镜头。** 不同 `scene_id` 不直接双锚定；`|ar_i - ar_j| > 0.01` 不同镜头，且**不许裁切/拉伸原图**，成片只允许留白（pillarbox/letterbox）。
6. **固定锚点，单段返修隔离。** 每段的 `first_frame`/`last_frame` 只能是静态资产（`clean_keyframe.png` 或桥接帧 `T`）。**严禁把上一段生成视频的尾帧回灌成下一段首帧**——那会把一次失败级联成整片。
7. **不覆盖上游原稿。** `seedance_prompt.txt` 只读；增量指令写到独立的 `prompts/seg_###_prompt.txt`，≤ 1000 字符，锁定词置顶，超长先删形容词。
8. **存疑即熔断。** 置信度 < 0.70、元数据缺失且代理信号互相矛盾、或上游 `qa_report` 非 PASS 时，标 `needs_human_review: true` / `BLOCKED`，不自动推进。
9. **镜头锁死、禁 zoom、动作不许微动。** 每段 prompt 先剥离上游运镜词，再由脚本强制追加 `[Camera Lock]`（禁推近/拉远/摇移/构图变化，元素屏上尺寸与位置恒定）与 `[Motion]`（完整、可见、正常速度的动作，动作在段内推进到完成；禁定格、禁微动、禁慢动作）。"镜头锁死"锁的是相机，不是人物——把动态绘本做成几乎静止的画面同样是失败；生成后 QC 报 `ZOOM_DRIFT` 或 `MOTION_TOO_SMALL` 即返修，不能靠 prompt 自觉。
10. **每页屏上 ≥1s，转场必须可验收。** `short_page_segments` 必须为空（`page_screen_sec < 1.0` 不许放行）；`transition_gate` 必须为 `PASS`、`transition_summary.effective_transition_ratio` 不得为 0（全是 0s 硬切就是分镜失效），必须补 SHARED_T 桥接或 POST 叠化；成片后以 `concat_audit.json` 的 `transition_audit_ok` 复核，转场类型与接缝必须逐条过 `qa_seams.py`。

---

## 2. 输入契约

```
pages/
└── page_001/
    ├── clean_keyframe.png     # 必须：零文字、画幅锁定
    ├── page_manifest.json     # 必须：page_id / scene_tag / character_ids / time_tag（后三项可缺）
    ├── qa_report.json         # 必须：verdict == "PASS"，否则阻断
    └── seedance_prompt.txt    # 必须：上游 I2V 提示词原文
```

- `scene_tag` 缺失时走**代理判定**：扫描 `seedance_prompt.txt` 的时间/空间跳变词（`next morning` / `meanwhile` / `第二天` / `与此同时` …）+ 特征距离与色彩距离，并在 `seam_decisions.json` 写 `basis: "PROXY_DETECTION"`，置信度打折。
- 上游质检不通过时**不猜**，直接 `BLOCKED` 并报出是哪一页、哪一项没过。

---

## 3. 四步工作流

```bash
# Step 1 分镜规划（纯 CPU 可跑）
python scripts/plan_scenes.py --pages-dir ./pages --out-dir ./director_out

# Step 2 构造共享锚点帧 T（只有 SHARED_T 接缝才需要）
python scripts/build_bridge_frames.py --plan ./director_out/scene_plan.json \
    --pages-dir ./pages --out-dir ./director_out

# Step 3 交给执行器（relay-producer 或自写脚本）按 segments 逐段生成
#        prompt_file 读出来，尾部追加 --resolution/--ratio/--duration/--seed/--watermark false

# Step 4 接缝验收
python scripts/qa_seams.py --plan ./director_out/scene_plan.json \
    --clips-dir ./clips --pages-dir ./pages --out-dir ./director_out
```

- `clips/` 里每段一个 `seg_###.mp4`，或一个 `seg_###/` 帧目录（至少 4 帧）——后者用于没有 ffmpeg/cv2 的环境，也方便你只拿首尾帧先验流程。
- `plan_scenes.py --calibrate` 不产出计划，只打印本批样页的 7 项指标 P10/P50/P90，用于把阈值调到你的画风上。

---

## 4. 判定矩阵（完整版见 `references/decision-matrix.md`）

| 语义轴 \ 视觉轴 | 视觉差异小 | 视觉差异大 |
| :--- | :--- | :--- |
| **语义连续** | `CONTINUOUS` 双锚定 5s/10s | `HARD_CUT` 动作切 / 视线匹配 / 景别递进 |
| **语义跳变** | `MATCH_CUT` 图形/动作/色彩匹配 | `DESIGNED_TRANSITION` 甩/遮/黑/白/推/桥接 |

视觉轴的"小"是五个闸门同时成立：画幅一致、主体位移 ≤ 25%、景别面积比 ∈ [0.67, 1.50]、光流一致性 ≥ 0.90、色彩巴氏 ≤ 0.15。任一不成立就是"大"——不是打分，是准入。

---

## 5. 输出契约（完整字段见 `references/scene-plan-schema.md`）

`scene_plan.json` 顶层：`plan_version` / `degraded_mode` / `master_ratio` / `page_turn_transition_enabled: false` / `pages[]` / `segments[]` / `seams[]` / `concat_plan[]` / `cost_estimate_cny`，以及验收汇总 `min_page_screen_sec` / `short_page_segments[]` / `transition_summary{shared_t_bridge, post_xfade, hard_cut_in_clip, min_page_screen_sec, hard_cut_ratio}`。

每个 `segment` 至少包含：`segment_id`、`pages[]`、`segment_kind`（`SINGLE_PAGE` / `MERGED_PAGES` / `SPLIT_PART` / `BRIDGE_CLIP`）、`first_frame` / `last_frame`（`{source, kind: keyframe|bridge_frame}`）、`duration_sec`（只允许 5 或 10）、`page_screen_sec`（该段每页平均屏上秒数，<1.0 进 `short_page_segments`）、`ratio`、`prompt_file`、`transition_out`（`type` + `subtype` + `realization`）、`fallback_if_no_last_frame`、`risk_flags`、`confidence`。

`realization` 只有五种：`IN_CLIP`（两段独立、接缝 0s 硬切）、`SHARED_T`（两段共享一张 T）、`POST`（后期 xfade）、`BRIDGE_CLIP`（独立桥接段）、`NONE`（片尾）。

---

## 6. 与 relay-producer 的对接

- `segments[]` 的 `first_frame` / `last_frame` 直接映射到 `first_frame` / `last_frame` 参数；`last_frame: null` 即单锚定。
- 比例用 `ratio` 字段，不要从关键帧现算；成片统一到 `master_ratio`，异比例段在 `concat_plan` 里 `pad_to_master_ratio: true`。
- **未知能力降级**：每段自带 `fallback_if_no_last_frame`。若执行端拒绝 `last_frame`，按该字段回退（合并段 → 5s 单锚定 + 动作切；SHARED_T 段 → 后期 `CROSS_DISSOLVE`），**不要重跑导演规划**。
- 本 skill 不替 relay-producer 做 cheap-first、预算熔断、SHA-256 锁定——那是执行层的事。两套逻辑不许打架：接缝判定的唯一来源是 `scene_plan.json`。

---

## 7. 失败处理

| 情况 | 动作 |
| :--- | :--- |
| 上游 `qa_report` 非 PASS | `BLOCKED`，报页码与失败项，不继续 |
| 指标缺失/元数据全缺 | 走代理判定，`basis: PROXY_DETECTION`，置信度上限 0.78 |
| 置信度 < 0.70 | `needs_human_review: true`，停在规划阶段等人确认 |
| 桥接帧目标被合并段占用 | 该接缝降级 `HARD_CUT`，加 `risk_flags: ["TRANSITION_TARGET_MERGED"]` |
| 接缝 QA 失败 | 只重跑报告点名的那一段（≤ 3 次，换 seed 或 5s→10s），不级联重生成 |

---

## 8. 验收（`seam_qa.json`）

按接缝类型分别验收，不是一把 SSIM 量到底：

| 接缝 | 主判据 | 失败码 |
| :--- | :--- | :--- |
| CONTINUOUS | 接缝 SSIM ≥ 0.88、pHash ≤ 6；全段时间漂移余弦 ≥ 0.78 | `SEAM_MISMATCH` / `MORPH_RISK` |
| HARD_CUT | 亮度跳变 ≤ 0.18、屏幕方向不反转；若声明动作切，出点应落在速度峰值 ±2 帧 | `FLASH_FRAME_ERROR` / `AXIS_FLIP_180` / `ACTION_PEAK_MISS` |
| MATCH_CUT | 切点两侧边缘轮廓 IoU ≥ 0.60 | `MATCH_CUT_FAIL` |
| DESIGNED_TRANSITION (SHARED_T) | 两侧到 T 的 SSIM ≥ 0.85（位置连续优先）；速度夹角 > 60° 记 `warning: VELOCITY_MISMATCH_AT_T` | `BRIDGE_T_MISS` |
| BRIDGE_CLIP | 成片保留 1.0–2.0s；与相邻场景色彩巴氏 ≤ 0.25 | `BRIDGE_DURATION_ERR` / `BRIDGE_STYLE_DRIFT` |

缺片段记 `SKIP`（不算通过，也不阻断）。

---

## 9. 已知限制

见 `NOTES.md`。最关键三条：**桥接帧是合成的静态图**（不是真渲染的运动模糊，执行端若不接受纯色/模糊帧需换真实帧）；**合并策略是贪心两页**，不做全局最优切分；**阈值是通用插画初值**，换画风先跑 `--calibrate`。

---

## 10. 交接（Handoff）

> **DSH 事实：skill 之间不会自动互相触发。** skill 只是一份按需加载的指令集——模型看到 catalog 里的 `name` + `description`，自己决定加载哪一个；加载后按正文执行。没有“A skill 调用 B skill”的内建机制。所以接力靠三样东西落地：
> 1）这一步把成果写成**约定好的文件**；
> 2）下一步有一条**确定的命令**可跑；
> 3）失败时知道**回退到哪一步**。
>
> 本节的 Handoff 就是这三样的书面化。整链编排与门禁在总装 skill `picturebook-pipeline`（顺序、交接契约、花钱闸门、失败回退）。

### 10.1 出口条件

- `scene_plan.json` 存在，且 `pages[].ratio` 全等于 `master_ratio`；`page_turn_transition_enabled` 为 `false`。
- 计划里引用的每个锚点都真实存在：`kind=keyframe` 的指向 `pages/<page>/clean_keyframe.png`；`kind=bridge_frame` 的指向 `bridge/T_*.png`（由 `build_bridge_frames.py` 生成）。
- 每个 `segments[i].prompt_file` 都写好了（`prompts/seg_###_prompt.txt`）。
- 接缝两侧的锚点关系符合计划语义：`SHARED_T` 两侧是同一张 T；`HIDDEN_CUT_CONTINUOUS` 两侧本来就不同。

### 10.2 下一棒

```bash
python "$SKILLS/seedance-relay-producer/scripts/preflight.py" \
  --plan book_out/scene_plan.json --pages-dir pages \
  --out-dir book_out/relay --resolution 1080p
```

（`$SKILLS` 指 skill 安装根目录，Windows 为 `C:/Users/<用户名>/.agents/skills`。）

下游 skill：`seedance-relay-producer`（按计划生成）。它通过 `inputs/plan_adapter.py` 把“段”翻译成“伪页”，**下游一行代码都不用改**。整链（S1→S2→S3）走 `picturebook-pipeline`，本 skill 只负责 S2。

### 10.3 三条不许破的约定

1. 段的 `last_frame` 是本段自己的尾锚点，**不是下一段的首帧**；qc 验的就是“生成尾帧 vs 计划声明的尾锚点”。
2. 标了 `SHARED_T` 的接缝，两侧锚点必须是同一个文件；不一致由 plan_adapter 直接阻断。
3. 时长只能是 5 或 10；8 秒这类值会被拒绝，因为平台没有这个档位。

### 10.4 失败回退

`preflight --plan` 报 BLOCKED → 按它的 `block_reasons` 回 S1 修页或回本 skill 改锚点，**不要**手动改 `preflight.json`。
