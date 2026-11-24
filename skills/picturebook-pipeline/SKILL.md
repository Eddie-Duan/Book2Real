---
name: picturebook-pipeline
description: |
  绘本整链总装：把「去字补全 → 分镜 → Seedance 生成拼接」三棒串成一条带门禁的流水线。
  当用户要把一本（或多页）绘本扫描件从头做到一条可交付成片，而不是只做其中一棒时使用。
  只负责顺序、交接契约、成本闸门与失败回退；每一棒都加载并执行 picturebook-to-seedance、picturebook-scene-director、seedance-relay-producer 原 skill，本 skill 不复制它们的规则，也不新增处理层。
  输入：单页图或 pages/page_###/ 目录；输出：pages/ 四件套、book_out/scene_plan.json、book_out/relay/final_movie.mp4 与 qc_report.md。
---

# picturebook-pipeline — 三棒总装

一条链，三个执行者，本 skill 只做编排。

```
S1 去字补全                 S2 分镜                         S3 生成与拼接
picturebook-to-seedance  →  picturebook-scene-director   →  seedance-relay-producer
pages/page_###/ 四件套      book_out/scene_plan.json        book_out/relay/final_movie.mp4
```

## 1. 铁律

（1）顺序不可跳：没有 S1 全页 PASS，不进 S2；没有 S2 的 `scene_plan.json`，不进 S3。
（2）单一事实源：三棒的规则只在各自 skill 里。本 skill 不复述阈值、不改它们的脚本、不替它们做判定。
（3）门禁不过就停：任何一棒报 FAIL / BLOCKED，回到该棒修，不带着坏产物往前跑。
（4）花钱前必须停：S3 的 preflight 结果与预算先给用户看，拿到明确同意再 `generate`。
（5）文件即交接：只通过磁盘产物传递状态，不靠对话记忆；重跑从最近一个通过门禁的检查点开始。
（6）成片一次成型：`final_movie.mp4` 交付后不做二次编辑；要改只能回对应页/段重跑再拼。
（7）成片验收五条硬门，缺一条不算交付：① `qc.json` 的 `zoom_check` PASS（无推近/拉远，元素屏上尺寸恒定）；② `text_zero_check` 与 `watermark_check` PASS，且逐页 `read_image` 复核无普通字/艺术字/页码/水印/淡字，S1 的水印 mask 检查（`watermark_removed`）也要过；③ `page_duration` PASS，每页屏上时长 ≥1s；④ `motion_check` PASS（动作是完整可见的，不是微动/慢动作/定格幻灯片）；⑤ `concat_audit.json` 的 `transition_audit_ok == true` 且不是全硬切——S2 的 `transition_gate` 必须 PASS。
（8）**这就是官方交付路径**：成片只能由本链的 Seedance 生成；`book2real/storyfilm.py` 的 2.5D 成片不得提交（见 `docs/CONSTRAINTS.md`）。
（9）**机位严格静态**：`zoom_check` 阈值 4%；提示词里不许出现推近/拉远/横摇/俯仰/换机位，段尾固定 `[Camera Lock]`。相机锁死不等于动作锁死——人物动作必须完整可见（`[Motion]` + `motion_check`）。

> 2026-11-25 补充：本链是**固定机位**路径。需要"镜头按情节物理移动、但不许 zoom 画面整体缩放"时，走 `picturebook-one-take`（见 `docs/CONSTRAINTS.md` §0.1），不要在本链里松开 `[Camera Lock]`。
（10）**成片必须带旁白**：静音轨（anullsrc −91 dB）不算交付，见 §6.1。

## 2. 目录与产物

```
<proj>/
  pages/page_###/            S1 每页定稿：clean_keyframe.png、page_manifest.json、
                             qa_report.json、seedance_prompt.txt
  work/                      S1 中间物（mask、inpaint 原图等），可随时重做
  book_out/                  S2：scene_plan.json / scene_plan.md / prompts/ / bridge/
  book_out/relay/            S3 正式：preflight.json / seg_###/ / final_movie.mp4 /
                             qc_report.md / qc_report.json / cost.csv
  book_out/relay_sample/     S3 可选 480p 样片，与正式目录隔离，花小钱试错
```

除以上目录不新增层；任何中间态放 `work/` 或对应 `book_out/` 子目录。

## 3. 执行前

（1）项目根 `<proj>` 下 `pages/` 已按页建好。源是 PDF 时先 `pdftoppm -r 200 -png book.pdf pages/page` 再改成 `page_001.png` 这类命名；S1 从单页图开始，不接 PDF。
（2）首次先跑 `python "$SKILLS/seedance-relay-producer/scripts/bootstrap_env.py"`（缺 ffmpeg / Python 依赖自动补装），再 `preflight.py --check-env` 确认环境；`ffmpeg` 必须在 PATH。
（3）密钥：`ARK_API_KEY` 放环境变量，或 `<proj>/.env` / relay 目录 `.env`。只做免费预检时可不设。
（4）下文命令默认在 `<proj>` 下执行；`$SKILLS` 是 skill 安装根目录，Windows 为 `C:\Users\<用户名>\.agents\skills`。

## 4. S1 — 去字补全（picturebook-to-seedance）

先加载 `picturebook-to-seedance`，按它的 Stage 0→7 逐页执行。本 skill 只规定交接形态：

（1）每页定稿的四件套落到 `<proj>/pages/page_###/`，文件名去掉 S1 的 `{page}_` 前缀：`clean_keyframe.png`、`page_manifest.json`、`qa_report.json`、`seedance_prompt.txt`。
（2）`page_manifest.json` 按 S1 §9.1 补齐 relay adapter 必填键（`status`、`resolution{width,height}` 等），`qa_report.json` 保留 `ocr_residual_count`、`non_edited_ssim`、`protected_zone_intrusion_px`。
（3）全书画幅比一致；不一致先修，不要指望 S3 补救。

门禁：逐页 `qa_report.verdict == "PASS"`。任一页不是 PASS，修该页重跑，禁止交接。

## 4.5 S0（可选）— 文本分场（picturebook-scene-grouper）

只有当 `<proj>/books/<id>/reader/book.json` 每页都有正文时才跑；没有正文直接跳过，S2 按像素自行判断。

```bash
python "$SKILLS/picturebook-scene-grouper/scripts/group_scenes.py" \
  --book books/<id> --out .tmp/scene_groups.json --pages-dir pages
```

产物必须写 `.tmp/`：grouper 的 `scene_plan.json` 与 S2 的 `book_out/scene_plan.json` 同名不同 schema，覆盖正式 plan 会让 relay 直接解析失败，也不要拿它喂 relay。接缝、时长、转场仍由 S2 决定；grouper 只提供「哪些页属于同一场」这一个约束。

## 5. S2 — 分镜（picturebook-scene-director）

先加载 `picturebook-scene-director`。

```bash
python "$SKILLS/picturebook-scene-director/scripts/plan_scenes.py" \
  --pages-dir pages --out-dir book_out \
  [--scene-groups .tmp/scene_groups.json]   # 有 S0 分场时带上
```

（1）读 `book_out/scene_plan.md`，把页数 N、段数 M、接缝类型分布、`cost_estimate_cny`、`needs_human_review` 项报给用户。
（2）只有计划里出现 `SHARED_T` 接缝才跑桥接帧：

```bash
python "$SKILLS/picturebook-scene-director/scripts/build_bridge_frames.py" \
  --plan book_out/scene_plan.json --pages-dir pages --out-dir book_out
```

门禁（全过才往下）：

（1）`plan_scenes.py` 退出码 0，`scene_plan.json` 存在且 `plan_version` 可解析；
（2）`page_turn_transition_enabled == false`；
（3）每个 `pages[].ratio == master_ratio`，所有 `first_frame` / `last_frame` 指向的文件真实存在；
（4）没有 `needs_human_review: true` 的段或接缝，没有 `confidence < 0.70` 自动定案；
（5）计划不是「M == N 且全是 HARD_CUT」——那说明分镜没真正执行（S2 铁律 3）。
（6）`short_page_segments` 为空：每段 `page_screen_sec ≥ 1.0`，不能有"一秒不到就刷过去"的页。
（7）带 `--scene-groups` 时，`pages[].scene_id` 必须等于 S0 的场号，同一 `segments[].pages` 里的页 `scene_id` 必须相同（跨场页不许被合并进一个镜头）。
（8）`transition_gate == "PASS"` 且 `transition_summary.effective_transition_ratio > 0`（全是 0s 硬切就是没执行转场）；逐段 prompt 尾部都有 `[Camera Lock]` 与 `[Motion]`，正文没有 dolly/zoom/推拉/摇移，`[Motion]` 写的是完整可见动作。

分镜本身免费，但它是花钱前最后一道人工闸门：把上面摘要给用户确认后再进 S3。

## 6. S3 — 生成与拼接（seedance-relay-producer）

先加载 `seedance-relay-producer`。以下记 `R="$SKILLS/seedance-relay-producer"`、`C="$R/config.yaml"`；所有命令都带 `--config`，避免 workdir 不同读到 `config.example.yaml`。

（1）零成本预检（正式目录 1080p）：

```bash
python "$R/scripts/preflight.py" --config "$C" \
  --plan book_out/scene_plan.json --pages-dir pages \
  --out-dir book_out/relay --resolution 1080p --dry-run
```

读 `book_out/relay/preflight.json`：`mode == "PLAN"`、`ready_for_generation == true`、`cost_estimate` 逐段合计。把「段数 × 单价 = 总价」给用户；没有明确同意，停在这里。

（2）（推荐）480p 样片。**必须用独立 out-dir**：正式目录里已 PASS / QC_CHECKING 的段会被 `generate.py` 跳过，之后升 1080p 不会重生成，只会拼出混分辨率的片子。

```bash
S=book_out/relay_sample
python "$R/scripts/preflight.py" --config "$C" --plan book_out/scene_plan.json \
  --pages-dir pages --out-dir "$S" --resolution 480p --dry-run
python "$R/scripts/generate.py" --config "$C" --out-dir "$S" --resolution 480p --page-id seg_001 --yes
python "$R/scripts/poll_download.py" --config "$C" --out-dir "$S"
python "$R/scripts/qc.py" --config "$C" --out-dir "$S"
```

把样片与 `qc.json` 给用户看；出现画风漂移、角色融化、接缝跳变，回 S1/S2 改，不升 1080p。

（3）1080p 正式生成：

```bash
python "$R/scripts/generate.py" --config "$C" --out-dir book_out/relay --resolution 1080p --yes
python "$R/scripts/poll_download.py" --config "$C" --out-dir book_out/relay
python "$R/scripts/qc.py" --config "$C" --out-dir book_out/relay
```

门禁：每个 `book_out/relay/seg_*/qc.json` 的 `verdict == "PASS"`，且 `checks.seam.passed == true`、`checks.zoom_check.passed == true`（无推近/拉远）、`checks.page_duration.passed == true`（每页 ≥1s）、`checks.motion_check.passed == true`（动作够大够快，不是微动）、`checks.text_zero_check.passed == true`、`checks.watermark_check.passed == true`；残留 `POLLING` 就再 poll；有 FAIL 只重跑点名段（S3 §5.4，≤3 次），不整本推倒。QC 的 OCR 只是代理信号——成片抽帧 + 上游关键帧仍要用 `read_image` 逐页看一遍普通字/艺术字/页码/水印/淡字，确认没有"文字慢慢淡没"。

（4）拼接与报告：

```bash
python "$R/scripts/concat.py" --config "$C" --out-dir book_out/relay
python "$R/scripts/report.py" --out-dir book_out/relay
```

门禁：`book_out/relay/final_movie.mp4` 存在且时长约等于各选中段之和；`qc_report.md`、`qc_report.json`、`cost.csv` 已生成；`concat_audit.json` 存在，不是全部 `hard_cut`（若告警，说明 S2 的 SHARED_T/POST 没生效，回 S2 查，不要直接交付"简单切换"的片子）。

### 6.1 旁白铺轨（本链不自带 TTS）

提示词增强不单独跑：S2 的 `write_segment_prompt` 已剥离运镜词并强制追加 `[Camera Lock]` 与 `[Motion]`，S1 的 `seedance_prompt.txt` 自带 `[Camera]/[Subject Motion]/[Environment]/[Negative Constraints]` 段；`preflight` 对缺段落只告警、不拦截。

成片必须带旁白（静音轨 −91 dB 不算交付）。本链不内置 TTS，用你已有的 TTS 按 `audio_plan.json` 合成 `narration.mp3` 即可（`start_sec/end_sec` 是整条成片的全局时间轴，已跨段累加，直接照它铺音轨）：

```bash
# 1) 声音计划（旁白 + 对白，带起止秒、音色、情绪）
python "$SKILLS/picturebook-scene-grouper/scripts/build_audio_plan.py" \
  --book books/cn01 --scene-plan book_out/scene_plan.json --out book_out/audio_plan.json

# 2) 用你自己的 TTS（MiniMax / 火山 / 其他）按 audio_plan.json 逐行合成，拼成 book_out/narration.mp3

# 3) 拼接时铺轨（这一步替代 §6(4) 的裸 concat）
python "$R/scripts/concat.py" --config "$C" --out-dir book_out/relay --audio book_out/narration.mp3
```

门禁：`book_out/relay/final_movie.json` 的 `spec_ok` 四项全真
（`min_720p` / `min_24fps` / `in_100_180s` / `audio_not_silent`）。页↔时间码不在本链交付范围，需要时按各段时长累加自行生成。

## 7. 失败回退

| 症状 | 动作 |
| --- | --- |
| S1 某页 qa 非 PASS | 回 S1 该页重做；不许手改 `qa_report.json` |
| `plan_scenes.py` BLOCKED | 上游 qa 未过或 manifest 缺字段 → 回 S1 对应页补齐 |
| 计划出现 `needs_human_review` | 停，把该页/接缝给用户判断；不自动猜 |
| preflight BLOCKED | 按 `block_reasons` 回 S1/S2 修锚点；不许手改 `preflight.json` |
| 单段 qc FAIL | `generate.py --page-id seg_003` 换 seed 重跑该段，再 qc；同一段 ≤3 次 |
| 接缝 FAIL | 回 S2 改该接缝锚点/转场类型，重跑 plan；已生成的段按计划幂等保留 |
| 缺 `ARK_API_KEY` 或余额不足 | 停在 preflight，不产生费用，先解决凭据 |

## 8. 交付

（1）`book_out/relay/final_movie.mp4` —— 唯一成片。
（2）`book_out/relay/qc_report.md`、`cost.csv` —— 质量与花费凭证。
（3）`pages/page_###/` 与 `book_out/scene_plan.md` —— 可复查的中间契约。
（4）`book_out/relay/concat_audit.json` —— 每条接缝的实际转场与 `hard_cut_ratio`，交付时与成片一起给用户看。
页↔时间码表不在本链范围内，需要时另行生成，不要塞回三棒。

## 9. 不许做

（1）不许跳过 S2 直接调 relay——没有 scene_plan 就没有段、锚点、接缝，拼出来是幻灯片。
（2）不许复制三棒的规则进本 skill 或第四个脚本；要改行为，改对应 skill。
（3）不许把生成段的尾帧当作下一段首帧回灌（S3 铁律）。
（4）不许在 FAIL 后手改 `pipeline_state.json` / `preflight.json` / `scene_plan.json` 蒙混过关。
（5）不许在用户未确认预算时调用 `generate.py`。
（6）不许在 `final_movie.mp4` 上做二次剪辑；要改就回段重跑。
（7）不许带着 zoom 推拉、残留文字/水印、不足 1 秒的页、或全硬切的 `concat_audit` 交付——这四条是验收硬门，不是建议。
