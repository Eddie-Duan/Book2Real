# NOTES — 工程备忘、已知边界、校准方法

## 1. 为什么只用 numpy + Pillow

本机 PATH 上的 `python` 装不了 OpenCV（沙箱禁止写临时目录，`pip install` 报 `[Errno 13] Permission denied` 于 `%TEMP%\dsh-*\pip-unpack-*`），而用户可能在任何一台没装 cv2 的机器上跑。所以：

- 全部核心指标用 numpy 重写：SSIM（积分图 box filter）、pHash（DCT）、HSV 巴氏、边缘掩码 IoU、FFT 相位相关光流。
- **可选加速**：装了 `opencv-python-headless` 也不冲突；`ffmpeg` 可用来把 mp4 解成帧目录。
- `load_clip_frames()` 的取帧顺序：`seg_###.mp4`（cv2 → ffmpeg → 报错）> `seg_###/` 帧目录 > 单张图。

环境自检：

```bash
python -c "import numpy, PIL; print(numpy.__version__, PIL.__version__)"
```

要求：Python ≥ 3.10，numpy ≥ 1.24，Pillow ≥ 10。

## 2. 无 GPU 时的降级（`degraded_mode: true`）

没有 torch/CUDA 时不会去下载 DINOv2，改用「16×16 HSV 直方图 + 梯度方向直方图」的余弦距离作为语义代理。它比 DINOv2 粗，但对绘本这种平涂、色块、线条明确的画风够用；代价是两个不同角色但同色系的画面可能被误判为"相似"。

`degraded_mode` 会写进 `scene_plan.json`，验收时看到 `true` 就知道置信度上限打了折。

## 3. 已知边界（按重要性排）

1. **桥接帧是合成的，不是渲染的。** `build_bridge_frames.py` 只做五种静态图：纯黑、纯白、水平方向模糊（15 次位移平均）、前景遮挡（暗部 15% 分位色 + 大矩形 + 高斯）、推镜细节（中心 1/3 裁切放大 3×）。它们能让"两侧都落在同一张图"成立，但**不是**真实运动模糊。若执行端对首尾帧做严格内容检查，可能拒绝。替代方案：用一张真实拍摄/生成的模糊帧替换 `bridge/T_*.png`，`scene_plan.json` 里的路径不变即可。
2. **合并是贪心两页，不是全局最优。** 当前规则：从前往后扫，`CONTINUOUS` 就把相邻两页并成一段，最多 2 页。好处是简单可预测，坏处是三页连续同镜头时会被切成 2+1，并在中间留一条 `HIDDEN_CUT_CONTINUOUS`。要改全局最优得换成 DP，代价是解释性变差——暂时不做。
3. **同一镜头跨段只做硬切。** 页数超过单次 10s 上限时，本 skill 不会硬拉双锚定，而是在段间显式标 `HARD_CUT / HIDDEN_CUT_CONTINUOUS`。它看起来像一镜到底，实际上是两个生成段在动作峰值处接的——这是有意的，因为双锚定跨越长动作弧必然融化。
4. **阈值是通用插画初值。** `0.35 / 0.25 / 0.15 / 0.90 / 0.60` 这些数来自一般水彩/平涂绘本的分布。极简黑白线条、高对比拼贴、厚涂油画都要重标（见第 4 节）。
5. **比例不一致只能留白。** 不裁切、不拉伸。`master_ratio` 取首页的合法比例，异比例段 `pad_to_master_ratio: true`，实际渲染成 pillarbox/letterbox。若上游能保证所有页同比例，这条永远不触发。
6. **没有音频。** 所有 J-cut/L-cut、音效先入、音乐卡点都不在范围内。接缝的"自然"只从画面判定。
7. **QA 的时间漂移只对 CONTINUOUS 段算。** 其他接缝没有稳定的时间对应关系，算了也是噪声。
8. **`SPLIT_PART` 与 `BRIDGE_CLIP` 是枚举值，但当前规划器不自动产出。** 前者留给人工把一页拆两段（例如一页画了两个动作），后者需要真实桥接镜头素材。schema 先留着，避免以后改契约。

## 4. 阈值校准（10 组样页）

1. 备 10 组相邻页：4 组"同场景同机位"、3 组"同场景换景别"、3 组"跨场景"。
2. 跑 `python scripts/plan_scenes.py --calibrate --pages-dir ./calib_10 --out-dir ./calib_out`，看 7 项指标的 P10/P50/P90。
3. 取"同场景同机位"组 `feature_distance` 的 **P90** 作为 `feature` 上限；取"跨场景"组 `feature_distance` 的 **P10** 作为下界；两者若有重叠，说明这批画风的语义轴不可靠，应优先依赖 `scene_tag` 元数据而不是视觉代理。
4. 把新阈值写回 `plan_scenes.py` 顶部常量（`TH_FEATURE` / `TH_SHIFT` / `TH_SCALE_LO` / `TH_SCALE_HI` / `TH_FLOW` / `TH_COLOR`），并在 `scene_plan.json` 里把 `calibration_status` 改成 `CALIBRATED_10_SAMPLES`。

## 5. 冒烟测试记录（2026-11-21）

合成 5 页（森林走 → 森林拉远 → 夜空 → 卧室 "next morning"），端到端跑通：

- `plan_scenes.py`：5 页 → 4 段、4 条接缝、预估 ¥6.4（1080p）。
- 切分结果：`seg_001` 合并 page_001+002（CONTINUOUS 5s）；`seg_002` 单页（出口 FOREGROUND_WIPE）；`seg_003` 单页（出口 FADE_THROUGH_BLACK）；`seg_004` 单页（END_OF_BOOK）。
- `build_bridge_frames.py`：2 张 T（`T_002_foreground_occlude.png`、`T_003_black.png`）。
- `qa_seams.py`：`[PASS] seams=4 failed=0 skipped=0`。
- 期间修掉的坑：`qa_seams.py` 首版把"跨 T 的运动夹角"当硬门禁，两侧明明都精确落在 T 上却报 `BRIDGE_T_MISS`。改为**位置连续优先**（`ssim_to_T ≥ 0.85`），角度只做 warning。

## 6. 与上游的契约变更

- 只读 `page_manifest.json` 的 `page_id / scene_tag / character_ids / time_tag`；`scene_tag` 与 `scene_id` 两种键名都接受。
- 只读 `qa_report.json` 的 `verdict` 或 `qa_verdict`。
- 不修改、不移动、不重命名上游任何文件；所有产物落在 `--out-dir`（默认 `./director_out`）。
