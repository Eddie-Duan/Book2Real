# picturebook-scene-director

绘本分镜导演：把 N 页关键帧切成 M 段可生成的视频，并给每条接缝一个可复算的决定。

## 快速开始

```bash
pip install -r requirements.txt

python scripts/plan_scenes.py --pages-dir ./pages --out-dir ./director_out
python scripts/build_bridge_frames.py --plan ./director_out/scene_plan.json \
    --pages-dir ./pages --out-dir ./director_out
# 按 director_out/scene_plan.json 的 segments 生成 clips/seg_###.mp4
python scripts/qa_seams.py --plan ./director_out/scene_plan.json \
    --clips-dir ./clips --pages-dir ./pages --out-dir ./director_out
```

先看 `assets/example_scene_plan.json`：那是一份 5 页真跑出来的计划（含 2 张桥接帧），比读文档快。

## 它在流水线里的位置

```
绘本页图
  └─ picturebook-to-seedance ──→ pages/page_###/clean_keyframe.png + manifest
        └─ picturebook-scene-director ──→ scene_plan.json + prompts/ + bridge/
              └─ seedance-relay-producer ──→ final.mp4
```

本 skill 不调 API、不生成视频、不做音频。它是**决策层 + 验收层**。

## 目录

```
SKILL.md                         # 操作手册与铁律
NOTES.md                         # 环境、已知边界、校准、冒烟记录
references/
  decision-matrix.md             # 语义轴 × 视觉轴 2×2、阈值、tie-breaker
  transition-library.md          # 12 种转场（禁用书页翻页）
  prompts.md                     # 8 套 ≤1000 字符提示词模板
  scene-plan-schema.md           # 三个 JSON 的字段契约
  metrics.md                     # 分类验收门禁、失败码、返修
scripts/
  common_io.py                   # numpy+Pillow 的 SSIM/pHash/光流/边缘
  plan_scenes.py                 # 指标 → 判定 → 全局切分 → scene_plan
  build_bridge_frames.py         # 黑/白/甩镜模糊/前景遮挡/推镜细节帧
  qa_seams.py                    # 分类接缝验收 → seam_qa.json
assets/
  example_scene_plan.json        # 5 页真实输出
  example_seam_qa.json           # 对应的验收报告
  bridge/                        # 示例桥接帧
```

## 依赖

Python ≥ 3.10、numpy、Pillow。无 GPU、无 OpenCV 也能跑；`.mp4` 解码优先 cv2，退化到 ffmpeg，再退化到帧目录。

## 它解决的三类假转场

1. **全景 → 特写被叠化**（像 PPT）：景别面积比越界，本该硬切，却被当成"差异大 → 转场"。
2. **跨场景构图接近被硬切**（像跳帧）：语义跳变但形状呼应，本该匹配剪辑。
3. **同场景角色瞬移被强行双锚定**（角色融化）：位移超限，本该切开。

判断依据不是"差异有多大"，而是"差异属于哪一轴"。详见 `references/decision-matrix.md`。
