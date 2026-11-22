# 数据契约：`scene_plan.json` / `seam_decisions.json` / `seam_qa.json`

三个文件都是 UTF-8 JSON，字段只增不改。执行层与验收层只认这里的键名。

## 1. `scene_plan.json`

### 顶层

| 字段 | 类型 | 说明 |
| :--- | :--- | :--- |
| `plan_version` | string | 固定 `"1.0.0"` |
| `degraded_mode` | bool | `true` = 无 GPU，特征走 CPU 代理 |
| `master_ratio` | string | 全片基准比例，取首页最近合法值 |
| `page_turn_transition_enabled` | bool | 恒 `false` |
| `pages[]` | array | 见下 |
| `segments[]` | array | 见下 |
| `seams[]` | array | 与 `seam_decisions.json` 同内容（冗余写入，便于单文件消费） |
| `concat_plan[]` | array | 见下 |
| `cost_estimate_cny` | number | 按 1080p 单价估算（¥1.60/5s，¥3.20/10s），不含重试 |

### `pages[]`

```json
{"page_id": "page_001", "scene_id": "sc_forest", "time_tag": "afternoon",
 "character_ids": ["bear"], "ratio": "4:3"}
```

### `segments[]`

| 字段 | 类型 | 约束 |
| :--- | :--- | :--- |
| `segment_id` | string | `seg_001` 起，三位递增 |
| `pages[]` | string[] | 覆盖的原始页；`BRIDGE_CLIP` 为 `[]` |
| `segment_kind` | enum | `SINGLE_PAGE` \| `MERGED_PAGES` \| `SPLIT_PART` \| `BRIDGE_CLIP` |
| `scene_id` | string | 继承自首个页面；缺失填 `sc_auto` |
| `first_frame` | object \| null | `{"source": "<相对 out_dir 的路径>", "kind": "keyframe"\|"bridge_frame"}`；`bridge_frame` 额外带 `bridge_kind` |
| `last_frame` | object \| null | 同上；`null` = 单锚定 |
| `duration_sec` | int | **只允许 5 或 10** |
| `ratio` | string | `16:9` \| `4:3` \| `1:1` \| `3:4` \| `9:16` \| `21:9` |
| `prompt_file` | string | 相对 `out_dir`，≤ 1000 字符 |
| `trim_window_sec` | [float, float] \| null | 仅 `BRIDGE_CLIP`：成片保留区间 |
| `transition_out` | object | `{"type", "subtype", "realization"}` |
| `fallback_if_no_last_frame` | object \| null | 见 §4 |
| `risk_flags` | string[] | 已知：`TRANSITION_TARGET_MERGED` |
| `confidence` | number | 0–1 |

### `transition_out.type` 枚举

`CONTINUOUS` \| `HARD_CUT` \| `MATCH_CUT` \| `DESIGNED_TRANSITION` \| `END_OF_BOOK`

### `transition_out.realization` 枚举

| 值 | 含义 | 执行层动作 |
| :--- | :--- | :--- |
| `IN_CLIP` | 段内完成，接缝 0s 硬切 | 两段独立生成，直接裸切 |
| `SHARED_T` | 两段共享一张静态 T | 上段 `last=T`，下段 `first=T` |
| `POST` | 后期混合 | 两段独立生成，`xfade` 时长见 `concat_plan` |
| `BRIDGE_CLIP` | 独立桥接段 | 生成后按 `trim_window_sec` 截取插入 |
| `NONE` | 片尾 | 不处理 |

### `concat_plan[]`

```json
{"after_segment": "seg_001", "cut": "hard", "xfade_sec": 0.0, "pad_to_master_ratio": false}
```

`cut` 只有 `"hard"` 或 `"xfade"`；`pad_to_master_ratio: true` 表示该段比例与 `master_ratio` 不同，需要留白而非裁切。

## 2. `seam_decisions.json`

数组，每项一条接缝：

```json
{
  "seam_id": "seam_seg_002_seg_003",
  "from_segment": "seg_002",
  "to_segment": "seg_003",
  "semantic_continuous": false,
  "visual_interpolable": false,
  "ratio_match": true,
  "decision": "DESIGNED_TRANSITION",
  "subtype": "FOREGROUND_WIPE",
  "metrics": {"feature_distance": 0.73, "ssim": 0.32, "...": 0},
  "rationale": "双轴跳变（场景切换 sc_forest -> sc_night）...",
  "confidence": 0.84,
  "needs_human_review": false
}
```

- `metrics` 是原始数值（不四舍五入到结论），返修时据此复算。
- 合并页内部的接缝 `from_segment` / `to_segment` 形如 `"seg_001 (page_001)"`，`seam_id` 以 `_merged` 结尾。
- 元数据缺失时额外写 `"basis": "PROXY_DETECTION"`。

## 3. `seam_qa.json`

```json
{
  "overall_verdict": "PASS",
  "total_seams": 4,
  "failed": 0,
  "skipped": 0,
  "seams": [
    {"seam_id": "...", "decision": "HARD_CUT", "verdict": "PASS",
     "metrics": {...}, "failure_code": null, "repair_action": null}
  ]
}
```

`verdict` ∈ `PASS` \| `FAIL` \| `SKIP`。`SKIP` 表示片段缺失，不代表通过。

失败码全集：`SEAM_MISMATCH`、`MORPH_RISK`、`FLASH_FRAME_ERROR`、`AXIS_FLIP_180`、`ACTION_PEAK_MISS`、`MATCH_CUT_FAIL`、`BRIDGE_T_MISS`、`BRIDGE_DURATION_ERR`、`BRIDGE_STYLE_DRIFT`。

`metrics` 里可能出现的键：`ssim`、`ssim_to_keyframe`、`ssim_to_T`、`phash_dist`、`luminance_jump`、`flow_angle_deg`、`speed_ratio`、`screen_dir_dot`、`shape_iou`、`warning`（非失败，如 `VELOCITY_MISMATCH_AT_T`）。以下划线开头的键（`_v_out`、`_v_in`、`_flow_dx`）是内部量，执行层可忽略。

## 4. `fallback_if_no_last_frame`

执行端不支持 `last_frame` 时**直接读这个字段**，不要重跑导演规划：

```json
{"last_frame": null, "duration_sec": 5,
 "transition_out": {"type": "HARD_CUT", "subtype": "CUT_ON_ACTION", "realization": "IN_CLIP"}}
```

- 合并段：退成 5s 单锚定 + 动作切。
- SHARED_T 段：退成 `DESIGNED_TRANSITION / CROSS_DISSOLVE / POST`（叠化在这里是**能力不足的降级**，不是首选，验收时可放宽但需记录）。
