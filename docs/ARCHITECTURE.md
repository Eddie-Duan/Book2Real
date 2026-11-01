# 架构

> 一句话：**把一页静态绘本拆成 2–3 个深度层，用虚拟相机重拍；再给少数"高光镜头"叠加程序化微动。**
> 输出是标准的 H.264 电影文件，不是"会动的图"。

## 1 边界

```
页图 ──► prepare()            decompose()            分层 + 补洞
          │                        │
          │                        ├─ far   (depth 0.0)
          │                        ├─ mid   (depth 0.5)
          │                        └─ near  (depth 1.0)
          ▼
     ShotSpec ──► Backend ──► frames ──► encode ──► clip.mp4
                    │                                 │
                    ├ parallax  (A)                   ▼
                    ├ procedural (B)               QC 判定
                    ├ pregen    (B, 外部 I2V)         │
                    └ static    (兜底)               ▼
                                                  assemble ──► film.mp4
```

- **输入**：`books/<id>/book.json` + `pages/*.png`
- **输出**：`runs/<id>/<run>/film.mp4` + `manifest.json` + `timeline.json` + `events.jsonl`
- **不做**：不碰 ASR/TTS/问答；上游 OCR/VLM 的产物（角色框、文字 mask）预留为 `ShotSpec.text_mask` / `characters` 字段。

## 2 分层职责（按"可替换缝"切）

| 层 | 文件 | 职责 | 明确不做 |
|---|---|---|---|
| 契约 | `contracts.py` | ShotSpec / ClipOut / Manifest / QCVerdict | 无业务逻辑 |
| 分镜 | `storyboard.py` | book.json → ShotSpec[]，选轨、定时长 | 不渲染 |
| 分解 | `layers.py` | 图/底分离，补洞，产出 RGBA 层 | 不认识相机 |
| 相机 | `camera.py` | 运动轨迹 + 每层视差系数 | 不认识像素 |
| 渲染 | `render.py` | 层 + 相机 → 1280×720 帧 | 不编码 |
| 后端 | `backends.py` | 四种生成策略的统一接口 | 不重试（交给编排） |
| 编码 | `encode.py` | 帧 → mp4；mp4 → 小帧（给 QC） | 不做决策 |
| 质检 | `qc.py` | 6 项指标 + accept/retry 建议 | 不生成 |
| 合成 | `assemble.py` | 片头/字幕/拼接/封装 | 不生成画面 |
| 编排 | `pipeline.py` | 缓存、并行、预算、降级、清单 | 不写渲染代码 |
| 展示 | `webapp.py` + `web/` | 播放器 + 质检看板 | 不做生产 |

## 3 关键设计决策

### 3.1 为什么主轨是 2.5D 而不是生成模型

| 事实 | 后果 |
|---|---|
| 观众感知的"动"主要来自相机运动与视差 | 2.5D 用 5% 的算力拿到 70% 的观感 |
| 生成模型会重绘像素 ⇒ 重绘文字就是伪字 | 教科书式扣分项，直接绕开 |
| 20–30 页全生成 ⇒ 时间与一致性双崩 | 覆盖率必须靠确定性轨道 |

### 3.2 分解不用神经网络深度

绘本是平面插画：**图/底分离**比"连续深度"更有用。指标 = 与边框主色的色彩距离（YCbCr 加权）+ 边缘能量，再经 Otsu 阈值、形态学开闭、连通域过滤。

- 好处：区域是**连通的**，视差看起来像"纸片分层"，而不是"画面融化"。
- 代价：前景如果和背景同色会漏；此时退化为单层 + 纯运镜（不会崩，只是平淡）。

**印刷文字的例外（2026-11-01 实测）**：扫描页上的文字会被拆进 near/mid 层，层间相对位移让同一行字出现两份鬼影。
`book.json` 因此支持 `"layers": false`：整页作一块刚性板，只做相机运动。真书两本都走这条路；
等文字行检测 + 把文字钉到 far 层做完，再对带字页开分层。

### 3.3 补洞（`imgops.inpaint`）

近层移动后会露出原来的位置，那里必须"有东西"。做法：半分辨率下的**扩散填充**（初值取大半径模糊，再迭代 26 次邻域平均），只把洞里的像素贴回原图。
真实绘本里露出的多是天空/草地/水面这类低频区域，扩散填充足够；高频区域建议后续换 patch-match。

### 3.4 缓存键

```
sha256(canonical(spec) + page_digest + track + film_config + backend_params)
```

命中即 0 秒。这是"单本 ≤15 min"的主要来源，也是**可重跑**的证据：同一页永远得到同一分段。

### 3.5 降级链

`procedural → pregen → parallax → static`，每一步失败都写 `events.jsonl`，最终 `ClipOut.fallback_from` 记录来源。
预算到点后新的镜头直接走 `static`——**宁可平淡，不能没有**，覆盖率是硬指标。

## 4 数据契约（摘要）

```jsonc
// ShotSpec —— 生成阶段的输入
{ "shot_id": "p03", "page": 3, "duration": 6.0, "fps": 24,
  "track": "procedural",                       // auto|parallax|procedural|i2v|static
  "camera": { "move": "dolly_out", "amount": 0.10, "mode": "card", "parallax": 0.55 },
  "motion": { "light": 0.55, "dust": 0.35, "breathe": 0.5 },
  "text_cues": [ { "start": 0.2, "end": 5.75, "text": "..." } ],
  "budget_class": "hero", "seed": 1003 }
```

```jsonc
// ClipOut —— 生成阶段的输出，QC 与合成的输入
{ "shot_id": "p03", "path": "moon_rabbit/_cache/ab12cd/clip.mp4",
  "width": 1280, "height": 720, "fps": 24, "frames": 144, "duration": 6.0,
  "backend": { "id": "procedural", "seed": 1003, "params": {...} },
  "cost": { "wall_s": 4.1 }, "qc": { "accept": true, "score": 0.82, "metrics": {...} },
  "status": "ok", "fallback_from": [], "sha256": "..." }
```

## 5 QC 指标

| 指标 | 含义 | 抓什么错 |
|---|---|---|
| `motion` | 相对首帧的平均变化 | 静止冒充视频 |
| `flicker` | 相邻帧差均值 | 时间闪烁、层间抖动 |
| `still_ratio` | 连续 ≥3 帧冻结的帧对占比 | 卡住不动 |
| `color_sim` | 卡片区域与源页的 RGB 直方图交集 | 串页、黑屏、调色崩坏 |
| `sharpness_ratio` | 卡片区域拉普拉斯方差 / 同尺寸原页 | 糊、编码过度 |
| `black_ratio` | 暗帧占比 | 编码/素材错误 |
| `drift_ncc` | 首尾帧中心互相关（仅诊断，不判） | 运动让首尾天然不同，不再作为拒收理由 |

阈值在 `configs/default.json`，当前值按 21 页真书 + 3 个负控重标（见 `HANDOFF.md` §6.1）。

## 6 前端

纯标准库 HTTP 服务（`webapp.py`，含 Range 请求，`<video>` 可拖动），零构建步骤：

- `/` 播放器：视频 + 分镜列表（缩略图/时间码/字幕）+ 质检看板
- `/api/runs` 运行列表，`/api/manifest?book=&run=` 清单，`/media/<rel>` 产物（带 Range）

## 7 性能（当前实测环境）

| 项 | 数值 |
|---|---|
| 单页分层（720px 工作分辨率） | 约 1–3 s（CPU） |
| 单镜头 6 s @1280×720（单块板） | 约 12–21 s 渲染 + 1 s 编码 |
| 21 页真书整片（`--jobs 2`） | 195 s 首跑 / 15.8 s 全缓存复跑（114 s 成片） |
| 8 页样张整片 | 见 `docs/HANDOFF.md` |
