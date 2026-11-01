# Agent 化：人物、动作、修复

当前仓库里已经有一个可读的互动绘本和一个人物舞台，但**人物的提取/切件/动作**仍有一部分是人工产物。
下一步不是继续手工给每一页写 JSON，而是把这条链拆成可重跑的 agent。

## 角色分工

| Agent | 输入 | 工具 | 输出 | 失败降级 |
|---|---|---|---|---|
| `PageScout` | 页图 + OCR | Paddle/RapidOCR、Qwen3-VL（可选） | 页面实体：人物/动物/物体 + bbox + 描述 + 重要度 | 只留 OCR 文本 |
| `CastClerk` | 实体表 + 全书 | 人脸/角色聚类、CLIP/DINO、SAM | 人物墙：同一角色跨页合并、肖像、别名 | 每页独立角色 |
| `Rigger` | 角色肖像 + 背景 | SAM / anime-segmentation / rembg、OpenCV | 身体/手臂/头等切件 + 关节 pivot + 置信度 | 只做整人肖像 |
| `MotionDirector` | 本页文本 + 情绪 + 角色 | 规则 + LLM JSON Schema | 动作计划：idle / nod / wave / point / shrug，时长/幅度 | 静态 + 轻呼吸 |
| `Critic` | 切件/动作/帧 | 自研指标：脸部完整率、背景残留率、接缝能量、动作幅度 | accept / 回退 / 请求人工 | 回退到整页图 |
| `Publisher` | reader 包 | 现有 storybook/storyfilm | 网页 + 16:9 短片 | 仅网页 |

## 关键契约

```jsonc
// CharacterCard
{ "id": "lola", "name": "劳拉", "role": "妹妹",
  "desc": "...", "pages": [3,4,5,6,7,9,10,12,13,16,17,20,21],
  "portrait": "characters/portrait_lola.jpg",
  "rig": { "kind": "paper_puppet", "confidence": 0.86,
           "parts": ["body", "arm_l", "arm_r"],
           "pivots": {"arm_l": [0.29, 0.53], "arm_r": [0.72, 0.53]} } }
```

## 动作计划

```jsonc
{ "page": "p17", "character": "lola",
  "beats": [
    { "t": 0.0, "action": "idle", "loop": true },
    { "t": 1.2, "action": "wave", "side": "right", "amp": 18, "cycles": 2 },
    { "t": 2.8, "action": "speak", "text": "你是在叫我吗？" }
  ] }
```

`MotionDirector` 只输出这个 JSON，渲染器不认识"故事"，只认识 beat；这样网页和视频可以共用同一份动作。

## Critic 指标（先定规则，再谈模型）

- `face_completeness`：肖像框必须包含双眼、鼻/嘴、下巴；低于 0.98 直接 reject。
- `bg_residue`：透明切件边缘 8px 环带里的背景色占比；>8% 判失败。
- `seam_energy`：关节旋转 20° 时，肩/颈接缝的梯度突变；超过阈值则加重叠边或改静态。
- `motion_naturalness`：角度、频率、相位在人体舒适区（头 2–8°、手 8–25°、1–3 Hz）。
- `text_coverage`：人物墙必须覆盖正文里出现的主要角色，缺一个就要求 `PageScout` 重跑。

## 人工只做两件事

1. 给 `Critic` 标 20–30 个失败样本（脸被切、背景残留、动作假）。
2. 在 agent 连续两次修不好时，点一下"换一张/换一个角色"。

这样下一本新书不需要改代码，也不需要我逐页抠图；agent 会先跑，Critic 不通过才找人。
