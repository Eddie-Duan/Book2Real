# 按导演计划生成（`--plan`）输入契约

> 适用场景：整本绘本一次生成、之后不允许二次编辑。此时最小可替换单位是导演切出来的**段**，
> 不是页。`preflight.py --plan scene_plan.json` 把「段」当作「页」喂进原有生成链路，
> `generate.py` / `poll_download.py` / `qc.py` / `concat.py` 一行都不用改。

## 1. 为什么需要这一层

`preflight` 原来的入口是 `pages/page_###/`：一个目录一条片，相邻两页是否接力由
`evaluate_relay_edge()` 现场判定（A/B/C 三阶降级）。这条路产不出「设计过的转场」——
A 级双锚定只能做同镜头延续，跨场景必然掉到 C 级硬切。

导演层（`picturebook-scene-director`）在生成**之前**已经决定：哪几页合成一段、每段多长、
接缝用共享桥接帧 T 还是隐藏切、每条片的提示词。这些决定必须**覆盖** relay 的现场判定，
否则生成出来的东西和分镜对不上。

因此 `--plan` 模式下：

- 不调用 `evaluate_relay_edge()`，`relay_level` 直接取计划（有尾帧锚点即 `A`）；
- `duration_sec` 取段时长（5 / 10），不从 DINOv2 距离推；
- 接缝过渡取 `concat_plan`，不由 relay 重新选。

## 2. 字段映射（`inputs/plan_adapter.py`）

| relay 页记录字段 | 来源 |
|---|---|
| `page_id` | `segments[].segment_id` |
| `page_index` | 段在 `segments[]` 中的序号（决定 seed 与拼接顺序） |
| `keyframe_path`（首帧） | `segments[].first_frame`：`kind=keyframe` → `<pages>/<source>`；`kind=bridge_frame` → `<plan_dir>/<source>` |
| `last_frame_path`（尾帧） | `segments[].last_frame`（**本段自己的尾锚点**，见 §3） |
| `prompt_path` | `<plan_dir>/prompts/seg_###_prompt.txt` |
| `duration_sec` | `segments[].duration_sec`，只允许 5 / 10 |
| `target_ratio` | 由首帧实际像素 + `ark.allowed_ratios` 就近取整 |
| `relay_level` | 有尾帧锚点 → `A`；没有 → `INDEPENDENT`（单锚定，qc 跳过接缝门禁） |
| `transition_after` / `transition_sec` | `concat_plan[]`：`cut=hard` → `None` / `0`；`cut=xfade` → `"fade"` / `xfade_sec` |
| `upstream_qa` | 段内各页 `qa_report.json` 取最差（见 §4） |

## 3. 三条不变量（写错就会在接缝处跳一下）

1. **尾帧用本段自己的 `last_frame`，不是下一段的首帧。**
   qc 的接缝门禁验的是「生成视频的最后一帧 ≈ 计划里声明的尾锚点」。
   对 `HIDDEN_CUT_CONTINUOUS`（同一镜头超长被拆段）来说，本段尾帧和下一段首帧**本来就不同**，
   强行统一会把隐藏切变成形变。

2. **`SHARED_T` 接缝两侧必须指向同一张 T。**
   计划若声明 `transition_out.realization == "SHARED_T"`，则
   `seg_k.last_frame` 必须与 `seg_{k+1}.first_frame` 是同一个文件，否则阻断
   （`标了 SHARED_T，但本段尾帧 … 与下一段首帧 … 不是同一张`）。

3. **段时长只能是 5 或 10。**
   Seedance 单次生成上限决定的；8s 这类值直接阻断，不允许四舍五入。

## 4. 上游 QA 聚合

段内每页的 `qa_report.json` 都要读，取最差值：

- `verdict` 必须全部 `PASS`，任一页非 PASS → 阻断；
- `ocr_residual_count` 取 `max`，`> 0` → 阻断；
- `non_edited_ssim` 取 `min`，低于 `thresholds.upstream_min_ssim` → 阻断；
- `protected_zone_intrusion_px` 取 `max`，超过 `thresholds.upstream_max_intrusion_px` → 阻断；
- **指标整体缺失**时不算过门禁，记 warning（`段内各页均缺 …，门禁未验证`），不静默放行。

## 5. 阻断语义：不允许只生成一部分

整本要拼成一条片，所以只要有一段 `BLOCKED`：

- `preflight.json` 里 `ready_for_generation: false`；
- 进程以退出码 `1` 结束，stderr 打印
  `[preflight] 有段被阻断：整本一次生成不允许只出一部分，修完再跑。`；
- 已就绪的段仍然写进 `pipeline_state.json`（`PREFLIGHT_READY`），修完直接重跑即可，
  已 `PASS` 的段不会被覆盖（幂等）。

## 6. 常用命令

```bash
# 1. 导演：整本分镜 + 桥接帧
python _build/picturebook-scene-director/scripts/plan_scenes.py --pages-dir pages --out-dir director_out
python _build/picturebook-scene-director/scripts/build_bridge_frames.py \
    --plan director_out/scene_plan.json --pages-dir pages --out-dir director_out

# 2. 按段预检（不花钱）
python scripts/preflight.py --plan director_out/scene_plan.json --out-dir out --resolution 1080p

# 3. 生成 → 下载 → 质检（与按页模式完全相同）
python scripts/generate.py --out-dir out --resolution 1080p
python scripts/poll_download.py --out-dir out
python scripts/qc.py --out-dir out

# 4. 拼接与报告
python scripts/concat.py --out-dir out
python scripts/report.py --out-dir out
```

`--pages-dir` 可覆盖页目录；不给时默认取计划文件的 `../pages`
（即 `director_out/scene_plan.json` → `pages/`）。

## 7. 与按页模式的区别一览

| | `--pages-dir`（按页） | `--plan`（按段） |
|---|---|---|
| 最小单位 | 一页一条片 | 一段一条片（可合并多页） |
| 接力级别 | 现场判定 A/B/C | 计划指定（有尾帧即 A） |
| 时长 | 由 DINOv2 距离推 5/10 | 计划指定 |
| 转场 | relay 选（xfade / 硬切） | `concat_plan` 指定 |
| 段内页时间码 | 等于段起点 | 需要 `book_timeline.py` 估/量 |
| 失败重跑 | 单页重跑 | 整本重跑（不可二次编辑） |

## 8. 回归

```bash
.venv\Scripts\python.exe tests\test_plan_adapter.py    # 7 条：隐藏切 / SHARED_T / 缺锚点 / 时长 / QA / 空计划
```

## 9. 桥接帧锚点下的 QC 行为

计划模式下，段的首/尾帧经常是导演合成的桥接帧 T（`kind: bridge_frame`）。`qc.py` 以 `preflight.segment.first_frame_source / last_frame_source` 判断锚点类型，并按三条规则降级，避免把"计划内的转场"判成缺陷（`qc.json` 每条都写明跳过原因）：

| 情形 | 跳过的检查 | 仍然执行的检查 |
|---|---|---|
| 参照帧近乎纯色（黑场 T） | 首帧 pHash、接缝 pHash | 首帧 SSIM、接缝 SSIM |
| 起点是 T（段从黑/模糊里长出来） | 逐帧时间漂移、角落水印、无中生有 | 首帧 SSIM、接缝 SSIM、文字检测 |
| 终点是 T（整段任务就是离开当前画面） | 逐帧时间漂移、角落水印、无中生有 | 首帧 SSIM、接缝 SSIM、文字检测 |

接缝门禁（尾帧 vs 本段 `last_frame`）任何情况都不豁免。

一次真实冒烟（5 页 → 4 段、720p）的读数：首帧 SSIM 0.9462 / 0.9473 / 0.9930 / 0.9972，接缝 SSIM 0.9458 / 0.9902 / 0.9913 / 0.9686，全部越过 0.92 / 0.88 门禁。

## 10. 拼接分辨率

`concat.py` 以首张合格段的**成片分辨率**为基准（成片大于页图时不再压回页图尺寸）。720p 生成、640×480 页图的素材，最终成片是 1112×834；若仍按页图尺寸归一，会白白损失清晰度。

## 11. 桥接帧的画质

`build_bridge_frames.py` 的 `foreground_occlude` 不是"用 15 分位暗色铺满画面"（那样会得到一张平涂色卡，0.2 秒遮盖里被读成"闪了一下绿屏"），而是：参考页压暗（×0.26 + 10）→ 半径 `min(h,w)/16` 的高斯失焦 → 径向暗角。它读作"有东西贴近镜头"，两段共享同一张时拼接处看不见硬缝。
