# `page_manifest.json` 字段说明

一页一份，贯穿全流程；多页时另建全局 `series_bible.json`。示例见 `assets/example_manifest.json`。

## 顶层

| 字段 | 类型 | 说明 |
|---|---|---|
| `manifest_version` | string | 结构版本，当前 `2.0.0` |
| `pipeline_status` | enum | `IN_PROGRESS` / `COMPLETED_QA_PASSED` / `NEEDS_HUMAN_REVIEW` / `BLOCKED` |
| `multi_page_mode` | bool | 本次输入 N ≥ 2 时为 `true`，激活 `series_bible.json` |
| `human_review_required` | bool | 任一熔断触发即 `true` |
| `asset_info` | object | 见下 |
| `stage_artifacts` | object | 每个 Stage 的产物路径，便于回滚 |
| `text_blocks` | array | Stage 1 产物 |
| `style_dna` | object | Stage 4 产物 |
| `completion_items` | array | Stage 2/5 产物 |
| `qa_metrics` | object | Stage 6 产物 |
| `seedance_output` | object | Stage 7 产物 |

## `asset_info`

| 字段 | 说明 |
|---|---|
| `batch_id` / `page_id` | 批次与页标识 |
| `source_file` / `normalized_file` | 原图与 Stage 0 标准化图 |
| `locked_resolution` | `{width, height}`，后续所有阶段偏差必须为 0 |
| `locked_aspect_ratio` | 如 `4:3`，只用于展示，不参与重算 |
| `is_spread` | 是否判定为跨页（仅提示，不自动裁切） |
| `color_space` | 统一 `sRGB_8bit` |
| `detected_language` | `zh` / `en` / `mixed` |

## `text_blocks[]`

| 字段 | 说明 |
|---|---|
| `block_id` | `tb_001` 递增 |
| `layer_type` | `body_text` / `art_title` / `page_number` / `watermark` / `diegetic_text` |
| `language` | 该块语种 |
| `text_verbatim` | 逐字原文，不翻译、不改标点 |
| `bbox_norm` | `[ymin, xmin, ymax, xmax]`，0–1000 归一化；**只当候选框**，掩膜另算 |
| `style_notes` | 字体/颜色/压在什么底图上，供 Stage 3 选修复策略 |
| `confidence` | 0–1；< 0.85 且 `needs_review: true` 时熔断 |
| `action` | 固定 `REMOVE_AND_INPAINT` |
| `inpaint_engine` | `LAMA`（平滑区）/ `FLUX_FILL`（复杂纹理） |
| `occlusion_on_core_subject_pct` | 与角色五官/核心手部重叠率，> 30% 熔断 |

## `style_dna`

| 字段 | 说明 |
|---|---|
| `medium` | 媒介与笔触，如 "Gouache and soft colored pencil on warm cream textured paper" |
| `line_work` | 线宽区间 + 线色 HEX，如 "Warm sepia (#4A3728), 2.0–3.0px" |
| `color_palette_hex` | Top-8 主色 |
| `lighting` | 光源方向与色温 |
| `paper_texture_laplacian_var` | 纸纹强度基准值，Stage 5 与 QA 都用它 |

## `completion_items[]`

| 字段 | 说明 |
|---|---|
| `item_id` | `comp_001` 递增 |
| `traceability.source_block_id` | 对应哪条 `text_blocks` |
| `traceability.source_text_span` | **原文原句片段**，补全的唯一依据 |
| `traceability.gap_reason` | 画面缺什么、为什么算缺 |
| `visual_spec` | 给生成模型的具体视觉描述（含颜色、数量、朝向） |
| `placement_strategy` | `INPAINT_SAFE_ZONE` / `SKIPPED_NO_SAFE_ZONE` |
| `target_bbox_norm` | 安全区坐标（0–1000） |
| `character_anchor_ref` | 多页模式下引用的角色锚点 id，单页为 `null` |
| `status` | `APPROVED_AND_GENERATED` / `SKIPPED_NO_SAFE_ZONE` / `REJECTED_BY_HUMAN` |

## `qa_metrics`

| 字段 | 阈值 | 说明 |
|---|---|---|
| `resolution_delta_px` | `[0, 0]` | 与 `locked_resolution` 的差 |
| `ocr_residual_count` | `0` | 有效文本框数 |
| `art_text_edge_density_diff_pct` | ≤ 15 | 艺术字边缘残影 |
| `non_edited_ssim` | ≥ 0.9995 | 掩膜外结构相似度 |
| `max_pixel_diff_p999` | ≤ 2 | 非编辑区 99.9 分位差 |
| `pixels_over_5_pct` | 0 | 差值 > 5 的像素占比 |
| `protected_zone_intrusion_px` | 0 | 保护带入侵面积 |
| `style_histogram_bhattacharyya` | ≤ 0.25 | 新增区色彩分布 |
| `laplacian_var_ratio` | 0.75–1.30 | 纹理匹配 |
| `qa_verdict` | `PASS` / `FAIL` | 汇总 |

## `seedance_output`

| 字段 | 说明 |
|---|---|
| `frame_resolution` / `aspect_ratio` | 首帧规格 |
| `version_compatibility_note` | 提交前需核对的时长/分辨率上限（不写死版本号） |
| `prompt_en` / `prompt_zh` | 运动提示词，模板见 `references/prompts.md` §C.4 |
