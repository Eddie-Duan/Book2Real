---
name: seedance-relay-producer
description: |
  读取上游 picturebook-to-seedance 产出的单页或多页绘本交付物，调用火山方舟 Ark Seedance API 生成图生视频片段；多页输入时默认启用首尾帧双锚定接力串联成片，执行基于 ffprobe/SSIM/DINOv2/CLIP/OCR 的量化自检与自动化返修闭环，最终交付可直接拼接成片的无水印/零文字视频资产与质检报告。
  触发时机：当需要将已去字并补全的绘本关键帧序列批量转化为连贯动态绘本视频、执行首尾帧接力生成、或对 Seedance 生成视频进行一致性与画幅保真质检时。
  能力边界：仅接受已通过上游去字质检的纯净关键帧；支持单页独立生成与多页双锚定接力；自动识别跨场景/翻页并执行降级转场或硬切；内置本地离线测试桩与预算熔断。
  不适用情况：1) 未去字的原始带字绘本扫描件（需先走 picturebook-to-seedance）；2) 音频配音与字幕合成；3) 第三方版权水印去除。
---

# `seedance-relay-producer` 执行总控与决策规程

## 1. 铁律与架构原则 (Iron Rules)
1. **`clean_keyframe.png` 是唯一视觉真值**：单页生成时 `first_frame = K_N`；多页接力时 `first_frame = K_N, last_frame = K_{N+1}`。**严禁将任何模型生成的视频帧回灌作为下一页的 `first_frame`**（见第 2 节架构原理）。
2. **绝不篡改创作提示词**：`seedance_prompt.txt` 原文只读，仅在尾部追加运行参数后缀（`--resolution`、`--ratio`、`--duration`、`--seed`、`--watermark false`），并记录原文 SHA-256。若因接力平滑或返修必须追加锁定词，必须独立写入 `prompt_override_p###.txt` 并生成 unified diff 附带理由，严禁覆盖原文件。
3. **画幅原样锁定**：`clean_keyframe.png` 尺寸禁止任何缩放、裁切、补边。若实际输出视频尺寸与 `K_N` 宽高比偏离 `> 0.5%` 或首帧对齐失败，立即判定不合格并拒绝。
4. **便宜先行 (Cheap-First)**：标准执行顺序为 `preflight --dry-run`（零成本预检与估价） → `--resolution 480p` 低分样片验证接力与动态 → `--resolution 1080p` 全量正式渲染。
5. **未知键 Fail-Closed**：所有上游 JSON 解析必须经过 `inputs/adapter.py` 映射；遇到未映射字段或缺失必填契约键立即报错阻断，严禁猜测。
6. **合规底线**：`watermark=false` 仅用于抑制平台推广角标；严禁去除法定 AI 生成标识或第三方版权水印；成片合并阶段统一保留合规 AI 生成声明元数据与片尾声明。
7. **请求与产物双重锁**：请求 prompt 已带镜头锁时不得重复追加；缺失时按 `STABILITY_LOCK_CLAUSE`（禁 zoom、元素尺寸恒定、相机锁死不等于动作锁死）、`MOTION_BOOST_CLAUSE`（完整可见动作、正常速度、段内完成）与 `CLEAN_FRAME_LOCK_CLAUSE`（无字、无水印、无淡出字形）写 `prompt_override` 并留 diff。QC 的 `ZOOM_DRIFT`、`MOTION_TOO_SMALL`、`PAGE_TOO_SHORT`、`TEXT_DETECTED`、`WATERMARK_DETECTED` 任一命中都不得 PASS；`concat_audit.json` 的转场计数、`hard_cut_ratio` 与 `transition_audit_ok` 必须随成片一起验收，全硬切不得交付。

## 2. 首尾帧双锚定原理与接力降级链
### 为什么强制「首尾帧双锚定」而禁止「尾帧像素回灌」？
- **双锚定拓扑**：第 `N` 页生成任务的入口锁定为固定资产 `K_N`，出口锁定为固定资产 `K_{N+1}`。模型在时间轴上自动完成 `K_N → K_{N+1}` 的运动插值，使第 `N` 段尾帧在生成阶段即收敛至 `K_{N+1}`，与第 `N+1` 段首帧（`K_{N+1}`）天然无缝闭合。**独立返修隔离性**：若第 `N` 页因漂移或瑕疵重生成，其入口 `K_N` 与出口 `K_{N+1}` 均未改变，**无需重生成第 `N+1` 至末页**，返修影响面严格收敛在单页。
- **尾帧回灌的级联灾难**：若将第 `N` 页生成的尾帧 `G_N_end` 作为第 `N+1` 页的入口，不仅每页叠加扩散模型重编码损失导致画风逐页漂移、偏离原绘本 `K_{N+1}`，更致命的是：**一旦重生成第 `N` 页，其尾帧必然变化，迫使下游 `N+1 .. End` 全部级联重生成**，成本与失败面呈 O(N) 放大。

### 接力模式判定与三阶降级链 (`--relay auto|on|off`)
1. **模式开关**：`auto` 模式下输入页数 `> 1` 自动开启接力，`== 1` 自动关闭。
2. **不接力语义阻断**：读取 `page_manifest.json` 与 `seedance_prompt.txt` 判断相邻页是否存在**翻页、场景切换、时间跳跃、主角更换**；若语义字段缺失，使用图像 `DINOv2` 余弦距离作为代理判据（报告中显式标注 `"代理判定"`）。
3. **三阶降级链（按序判定并记录实际命中级别）**：
   - **Level A（双锚定直连）**：API 支持 `last_frame`、两页宽高比差 `<= 0.01`、无跨场景语义跳变、且 `DINOv2_dist(K_N, K_{N+1}) <= 0.35`。
     - 若 `DINOv2_dist <= 0.20`：`duration = 5s`。
     - 若 `0.20 < DINOv2_dist <= 0.35`：自动升 `duration = 10s`（防止 5s 强行形变），并独立输出追加“平滑过渡、不形变”锁定后缀的提示词文件。
   - **Level B（仅首帧 + 后期软转场）**：触发条件为 API 不支持 `last_frame`、两页宽高比不一致、或 `0.35 < DINOv2_dist <= 0.55` 且非硬切场景。第 `N` 页仅传 `first_frame = K_N`，`concat` 阶段插入 `0.4s` 交叉溶解（`xfade=fade`）。
   - **Level C（硬切不接力）**：触发条件为明确跨场景/翻页、或 `DINOv2_dist > 0.55`。两段均仅传各自 `first_frame`，拼接处零过渡硬切（Hard Cut）。
4. **末页收束规则**：队列最后一页不设 `last_frame`，独立生成带“末尾自然收束、动作渐缓”锁定后缀的提示词，避免画面被强行拉向静止。

### 按导演计划生成（`--plan`，整本绘本用这个）

当上游是`picturebook-scene-director`已经切好段的整本绘本时，用 `preflight.py --plan book_out/scene_plan.json` 取代 `--pages-dir`：

- **段即页**：`segments[]` 每段当一条片，`keyframe_path` 取段 `first_frame`（`keyframe` 去 `pages/`，`bridge_frame` 去 `bridge/`），`last_frame_path` 取**本段自己的 `last_frame`**；
- **不再现场判 A/B/C**：`relay_level`、`duration_sec`、`transition_after/transition_sec` 全部取自计划与 `concat_plan`；
- **`SHARED_T` 接缝两侧必须指向同一张 T**，否则阻断（拼起来会跳）；
- **不许只生成一部分**：任一段 `BLOCKED` 时 `ready_for_generation=false` 且退出码 1，修完重跑（已 `PASS` 的段幂等保留）；
- `generate / poll_download / qc / concat / report` 用法完全不变。

字段映射、三条不变量、QA 聚合规则见 `references/plan-adapter.md`；回归 `tests/test_plan_adapter.py`。

### 接入 Seedance 2.5（`doubao-seedance-2-5-*`）

2.5 系列与 1.0 的行为差异（已实测，写进 `config.yaml` 的 `ark` 段即可生效）：

- **带首帧时禁止下发 `ratio`**。传 `ratio` 会直接 400：`InvalidParameter.TaskTypeConstraint: For first-frame or first-last-frame generation, the output ratio follows the first-frame image`。用 `ratio_follows_first_frame: true` 让 `generate.py` 不下发比例（输出比例自动跟随首帧图）。
- **默认开环境音**（`generate_audio: true`）。绘本片一般不要，配 `generate_audio: false` 显式关掉；`qc.json` 的 `structure.has_audio_stream` 可验证是否生效。
- `duration` 只接受 5 / 10；分辨率用 `480p / 720p / 1080p`（具体档位以账号为准）。

### QC 的三种「参照物例外」

`qc.py` 的首帧/接缝/漂移/无中生有全部以「关键帧」为参照。当锚点是导演合成的桥接帧 T 时，这些检测会必然误报，脚本按下面三条自动降级（`qc.json` 里每条都写了 `phash_skipped` / `applicable` / `skipped` 说明原因）：

1. **近乎纯色的参照帧跳过 pHash**：黑场 T 与任何黑场的 pHash 都能差 32 位，只有 SSIM 有意义；
2. **起点是桥接帧 T**：不做逐帧时间漂移、不做角落水印、不做无中生有（画面本来就该从黑/模糊里长出来）；
3. **终点是桥接帧 T**（整段任务就是离开当前画面）：不做逐帧一致性，改用首帧 SSIM + 接缝 SSIM + 文字检测把关。

接缝门禁（尾帧 vs 计划声明的尾锚点）在任何情况下都不豁免——它是"这一条片有没有走到该到的位置"的唯一判据。


## 3. 端到端执行工作流与状态机
每页严格遵循状态机：`PENDING` → `BLOCKED` | `PREFLIGHT_READY` → `GENERATING` → `POLLING` → `QC_CHECKING` → `PASS` | `REPAIRING` → `ESCALATE`。

1. **Step 0 环境与契约探测**：先跑 `python scripts/bootstrap_env.py`（缺 `ffmpeg`/依赖会自动补装，只补不改），再执行 `bash scripts/setup.sh`（或 `setup.ps1`）及 `python scripts/preflight.py --check-env`；首次接入新上游目录时运行 `python inputs/adapter.py --probe --pages-dir ./pages` 核验字段映射。
2. **Step 1 预检与成本估算**：运行 `python scripts/preflight.py --pages-dir ./pages --out-dir ./out --relay auto --dry-run`，生成 `preflight.json`、接力拓扑与预估费用表；上游 `qa_report` 不合格的页直接置为 `BLOCKED`。
3. **Step 2 低分样片验证**：执行 `python scripts/generate.py --out-dir ./out --resolution 480p --yes` 与 `poll_download.py`、`qc.py`，验证接力节奏与提示词响应。
4. **Step 3 正式生成与异步下载**：执行 `python scripts/generate.py --out-dir ./out --resolution 1080p`（超预算需显式 `--yes`），内置任务 ID 幂等续跑与下载立即落盘。
5. **Step 4 量化质检与局部清理**：运行 `python scripts/qc.py --out-dir ./out` 执行全维度指标门禁；若仅存在平台角标水印且位于安全区，调用 `cleanup.py` 评估是否安全处置；其余缺陷进入返修循环。
6. **Step 5 自动返修与人工升级**：`qc.py` 按 `references/repair-playbook.md` 缺陷映射表自动调整 `seed` / `duration` / 锁定后缀重跑（单页上限 `3` 次）；超限页自动移入 `out/manual_review/page_###/` 打包问题帧、contact sheet 与改进建议。
7. **Step 6 音画齐平拼接与报告**：运行 `python scripts/concat.py --out-dir ./out` 与 `python scripts/report.py --out-dir ./out`，输出 `final_movie.mp4`、`concat.txt`、`qc_report.md`、`qc_report.json`、`cost.csv` 与 `run_log.jsonl`。

## 4. 关键参考文档索引
- Ark Seedance 接口契约与错误码：`references/ark-seedance-api.md`
- 输入字段映射与预检规程：`references/preflight-and-adapter.md`
- 按导演计划生成（段即页）的输入契约：`references/plan-adapter.md`
- 接力算法、代理判据与接缝门禁：`references/relay-protocol.md`
- 一致性量化阈值与样本标定方法：`references/consistency-rubric.md`
- 自检门禁四件套与清理合规边界：`references/qc-rubric.md`
- 缺陷返修映射表与重生成影响面：`references/repair-playbook.md`
- 故障排查与硬件降级兜底：`references/troubleshooting.md`

---

## 5. 交接（Handoff）

> **DSH 事实：skill 之间不会自动互相触发。** skill 只是一份按需加载的指令集——模型看到 catalog 里的 `name` + `description`，自己决定加载哪一个；加载后按正文执行。没有“A skill 调用 B skill”的内建机制。所以接力靠三样东西落地：
> 1）这一步把成果写成**约定好的文件**；
> 2）下一步有一条**确定的命令**可跑；
> 3）失败时知道**回退到哪一步**。
>
> 本节的 Handoff 就是这三样的书面化。整链编排与门禁在总装 skill `picturebook-pipeline`（顺序、交接契约、花钱闸门、失败回退）。

### 5.1 出口条件（冻结前必须全绿）

- `preflight.json` 的 `mode == "PLAN"` 且 `ready_for_generation == true`（整本不可部分生成）。
- 每个 `seg_###/qc.json` 的 `verdict == "PASS"`，且 `checks.seam.passed == true`（尾帧真的落到锚点上）。
- `pipeline_state.json` 里没有 `POLLING` 的段；`final_movie.mp4` 已生成。
- `cost.csv` 与实际提交段数一致（对账用）。

### 5.2 交付终点

本 skill 的出口就是整链终点：`final_movie.mp4`、`qc_report.md`、`qc_report.json`、`cost.csv`。页↔时间码表不在本链范围内，需要时另行生成。

整链（S1→S2→S3）由总装 skill `picturebook-pipeline` 编排；本 skill 只负责 S3。

### 5.3 与上游的约定

- 只吃 `--plan` 指向的 `scene_plan.json`，不自己切段、不改提示词（提示词由导演写好，本 skill 只在 `needs_smooth_lock` 时追加锁定子句）。
- 段数 ≥ 2 时拼接依赖 `concat_plan`；`hard` 走 concat demuxer，`xfade` 走滤镜图。

### 5.4 失败回退

- qc 不过 → 换 seed 重跑该段（`--page-id seg_003`）；三次不过升级人工，不要无限重试烧钱。
- 某段一直不过 → 回导演改锚点或改提示词（`plan_scenes` 重跑），而不是在 relay 里手改 `pipeline_state.json`。
- 缺 `ARK_API_KEY` / 余额不足 → 停在 preflight，不产生费用。

### 5.5 不要做的事

- 不要把 `final_movie.mp4` 拆回单段再喂给导演“重拼”——违反“一次生成、不可二次编辑”。
- 不要用生成视频的尾帧回灌成下一段首帧（会累积漂移，且破坏可重生成性）。
