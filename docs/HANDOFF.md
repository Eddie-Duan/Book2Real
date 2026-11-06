# 交接文档 · 纸间生灵（仓库仍名 Book2Real）

> 更新于 2026-11-01。仓库：`C:\Users\DUANboyang\Desktop\Book2Real`
> 状态：**互动绘本阅读器 + 16:9 朗读短片已跑通**；人物链已 agent 化（`python -m book2real cast`）；旧版 2.5D 管线保留；真 I2V 未接。

> **2026-11-01 新增（本轮重点）**：`python -m book2real reader` 提供"导入 → 互动书 → 一键出片"。
> 真书《我特别了解豚鼠》21 页 → 31 个阅读屏（长页按 64 字上限拆屏，每屏一张取景），成片 **210.5 s**、带 edge-tts 旁白。
> 设计、开源选型、许可证与限制见 [`READER.md`](READER.md)。下面 §0–§10 是旧版视频管线的记录。

---

## 0 一分钟现状

```
python scripts/import_book.py --src assets/book2 --book charlie_lola \
       --title "我特别了解豚鼠" --subtitle "查理与劳拉 · 罗伦·乔尔德"
python -m book2real plan --book books/charlie_lola
python -m book2real run  --book books/charlie_lola --out runs --jobs 2
python -m book2real run  --book books/guoqing      --out runs --jobs 1
python -m book2real ui   --runs runs --open       # 浏览器看片 + 质检看板
```

新版（互动绘本 + 人物 agent）：

```
python -m book2real reader --books books --port 8770 --open   # 互动阅读器
python -m book2real analyze --book books/charlie_lola         # OCR / 拼音 / 清字
python -m book2real cast    --book books/charlie_lola         # 人物链（本次实测 12.9 s，随环境波动）
python -m book2real film    --book books/charlie_lola         # 16:9 短片
```

实测（本机 RTX 5070 Laptop 8G / i9-14900HX / ffmpeg 8.1.1）：

| 项 | book2（查理与劳拉，21 页） | book1（迎国庆，1 页） |
|---|---|---|
| 成片 | `runs/charlie_lola/<run>/film.mp4`，**114.0 s / 1280×720 / 24fps** | `runs/guoqing/<run>/film.mp4`，**12.0 s** |
| 镜头 | 21 个，页面覆盖 **100%**，降级 0 次 | 1 个 |
| 首跑耗时 | **195.4 s**（`--jobs 2`） | **20.5 s** |
| 复跑（全缓存） | **15.8 s**，`cache_hits = 21/21` | 同上（复跑仍会重编码） |
| 平均质检分 | **0.911**，**21/21 `ok`** | 0.877，1/1 `ok` |

**结论**：这台笔记本能跑完一本 21 页真书，3 分 15 秒首跑、16 秒复跑，远低于 15 分钟预算。

**关键取舍**：真书页面带印刷文字，分层会让文字被拆到不同层、出现重影（旧版实测可见）。
两本真书因此在 `book.json` 里写 `"layers": false`：整页作为一块刚性板做运镜（推/摇/缩放），
不再做前景视差。**画质优先于分层炫技**；文字 mask + inpaint 接通前，不要对带字页开分层。
样张 `books/moon_rabbit` 仍走 2–3 层视差，用来验证分层链路。

---

## 1 已验证 / 未验证（别把未验证当完成）

| 能力 | 状态 | 证据 |
|---|---|---|
| 真书导入 + 按创建时间排序 | ✅ 验证 | `scripts/import_book.py`，21 页 `ORDER.csv` 可追溯 |
| 平面板运镜（`layers:false`） | ✅ 验证 | 114 s 成片，逐镜帧检查文字无重影 |
| 帧 → mp4（ffmpeg 管道） | ✅ 验证 | `ffprobe`: h264 1280×720 24fps 114.0s |
| 片头 / 尾卡 / 拼接 | ✅ 验证 | 中文标题与尾卡正常（旧版 mojibake 已修） |
| 缓存键 + 命中 | ✅ 验证 | 复跑 `cache_hits=21`，195.4 s → 15.8 s |
| QC 判据（按真书重标） | ✅ 验证 | 21/21 accept；static/black/wrong-page 三个负控全部拒绝 |
| `fill` 画幅（跨页） | ✅ 验证 | 11 个跨页全幅无黑边（修了 `fit` 按卡片尺寸算的 bug） |
| 降级链 | ⚠️ 未触发 | 无 fallback 记录（没有坏镜头，属预期） |
| 预算守门（deadline） | ⚠️ 未触发 | 未跑到 900 s |
| Web 播放器 | ⚠️ 部分验证 | `/` 200、`/api/manifest` 21 chapters、`/media` Range 206；浏览器 JS 未点 |
| 真 I2V（Wan/Kling） | ❌ 未接 | 仅 `pregen` 接口 |
| 带字页视差 | ❌ 主动关闭 | 见上；需要文字 mask + 分层固定文字后才能开 |


---

## 2 目录地图

```
Book2Real/
├─ book2real/                 # 包（约 2400 行）
│  ├─ contracts.py            # ★ 数据契约：ShotSpec / ClipOut / Manifest / QCVerdict
│  ├─ config.py               # 片长、画幅、QC 阈值、预算
│  ├─ storyboard.py           # ★ book.json → ShotSpec[]（选轨、定时长、字幕）
│  ├─ layers.py               # ★ 图/底分离 + 补洞 → 2–3 个 RGBA 层
│  ├─ imgops.py               # 纯 numpy+Pillow 的图像原语（无 OpenCV）
│  ├─ camera.py               # 运动轨迹 + 每层视差系数
│  ├─ render.py               # ★ 层+相机 → 1280×720 帧（卡片构图）
│  ├─ backends.py             # ★ 四个后端：parallax / procedural / pregen / static
│  ├─ encode.py               # 帧→mp4；mp4→小帧（给 QC）
│  ├─ qc.py                   # ★ 6 项指标 + accept/retry 判定
│  ├─ assemble.py             # 片头/尾卡/ASS 字幕/拼接/封装
│  ├─ pipeline.py             # ★ 编排：缓存、并行、预算、降级、manifest
│  ├─ cache.py                # 内容寻址 + shot 缓存键
│  ├─ cli.py / __main__.py    # plan / run / ui / sample / doctor
│  ├─ webapp.py + web/        # 标准库 HTTP + 播放器（Range 请求已实现）
│  └─ util.py                 # 哈希、原子写、事件日志、ffmpeg 封装
├─ books/charlie_lola/        # 真书①：21 页（含 11 个跨页）+ book.json + ORDER.csv
├─ books/guoqing/             # 真书②：单页《迎国庆》
├─ books/moon_rabbit/         # 合成样张（8 页，走分层视差）
├─ runs/<id>/<run>/           # 产物：film.mp4、manifest.json、timeline.json、events.jsonl、thumbs/
├─ configs/default.json       # 阈值都在这里，改这里不用改代码
├─ samples/make_book.py       # 样张生成器（可当画风模板）
├─ scripts/                   # setup_env.ps1 / run_demo.ps1 / import_book.py
├─ tests/test_smoke.py        # 9 个纯 CPU 冒烟测试（无需 ffmpeg）
└─ docs/ARCHITECTURE.md       # 设计理由与取舍
```

★ = 改代码时最常动的文件。

---

## 3 三个核心概念

1. **A 轨 vs B 轨**
   - A `parallax`：分层 + 虚拟相机。**确定性、快、像素忠实**，负责覆盖率。
     `layers:false` 时退化为单块刚性板的运镜（真书带字页的默认），仍属 A 轨。
   - B `procedural`：单层 + 光影/粒子/呼吸微动。负责"有生气"。
   - B `pregen`：外部生成的片段（Wan/可灵/Seedance），按 `clips/<shot_id>.mp4` 放入即可接入。
2. **契约先行**：`ShotSpec` 进、`ClipOut` 出。任何后端只要遵守这两张表就能插进来。
3. **缓存键**：`sha256(shot 规格 + 页图哈希 + 轨道 + 画幅参数)`。同一页永远出同一段，二次运行 0 秒。

---

## 4 换一本真实绘本（三步）

1. **导入页面**（自动按文件创建时间排序，适合从相册/聊天记录顺序复制出来的图）：
   ```
   python scripts/import_book.py --src assets/book2 --book mybook \
          --title "书名" --subtitle "副标题"
   ```
   产物：`books/<id>/pages/p01.jpg…`、`book.json`、`ORDER.csv`（页码 ↔ 源文件 ↔ 创建时间）。
2. 按需改 `book.json` 的 `move` / `duration` / `mode` / `layers`：

```jsonc
{
  "book_id": "mybook", "title": "书名", "fps": 24,
  "layers": false,                 // 带印刷文字的扫描页：必须 false，避免文字重影
  "pages": [
    { "file": "p01.jpg", "move": "dolly_in", "mode": "card", "duration": 3.5 },
    { "file": "p06.jpg", "move": "pan_lr",   "mode": "fill", "duration": 6.0 }
    // 跨页（宽高比 ≥1.2）用 fill 满幅；单页用 card 卡片
  ]
}
```

3. `python -m book2real plan --book books/mybook` 先看分镜表，再 `run`。

**拍照页建议**：正对拍摄、四边留 5% 白边、避免反光和手指。分层算法靠"边框主色"判背景，歪着拍会掉精度。

---

## 5 常见改法

| 想改什么 | 改哪里 |
|---|---|
| 运镜种类/幅度 | `book.json` 的 `move`/`amount`，或 `camera.py:pose()` 加分支 |
| 画幅/卡片大小/背景压暗 | `configs/default.json` 的 `film.*` |
| 微动强度 | `book.json` 的 `motion: {light, dust, breathe}`（0–1） |
| 分层灵敏度 | `layers.py:decompose()` 的 `min_area_frac` / Otsu 前权重 0.68/0.32 |
| QC 判据 | `configs/default.json` 的 `qc.*`（当前值已按两本真书重标，见 §6.1） |
| 是否分层 | `book.json` 的 `layers`（默认 true；带字扫描页设 false） |
| 加一个新后端 | 继承 `backends.Backend`，实现 `render()`，注册进 `REGISTRY` |

---

## 6 已知问题（按"最便宜先修"排序）

### 6.1 QC 已重标（2026-11-01，两本真书 + 三个负控）

旧版 8/8 误判，根因与新判据：

| 规则 | 旧问题 | 新做法 | 真书实测 |
|---|---|---|---|
| `still_ratio` | `Δ<0.0015` 把慢运镜当静止 | 只统计"连续 ≥3 帧 Δ<1e-5"的冻结段 | 0.000–0.012 |
| `sharpness_ratio` | 整帧(含背景)比整页，不可比 | 只测卡片/满幅区域，原页缩到同一尺寸 | 0.788–0.931 |
| `drift_ncc` | 推/摇让首尾帧天然不同 | **不再作为判据**，仅留作诊断 | 0.36–0.97 |
| `color_sim`（新增） | — | 卡片区域与源页的 RGB 直方图交集 | 0.801–0.963 |

21/21 accept，均分 0.911。负控（2 s / 24fps，`runs/_qc_*.mp4` 可复现）：

| 负控 | 结果 | 触发理由 |
|---|---|---|
| 静态帧重复 | 拒绝 | `motion 0.0001`、`still 0.70` |
| 全黑 | 拒绝 | black 1.00 + 4 条 |
| 用 p12 的片比 p17 的页 | 拒绝 | `color 0.17`、`sharpness 0.46` |

复现负控的脚本没有入库（一次性验证）；`tests/test_smoke.py` 覆盖了 `still` 与 `color_sim` 的纯函数逻辑。

### 6.2 性能（单页平面板 8–21 s，首跑 195 s / 21 页）

- 真书用平面板（单层），单镜头 3–6 s 内容要 8–21 s 渲染；`--jobs 2` 下整本 195 s。
- 跨页 `fill` 用 1280 宽的板，比卡片模式慢约 30%（p13 18.5 s vs p05 14.2 s）。
- B 轨 `procedural` 仍是 35–98 s/镜；真书没用 B 轨，留作高光镜头。
- 复跑命中缓存只要 15.8 s（含整片重编码）。

### 6.3 其余缺口

| 缺口 | 影响 | 建议 |
|---|---|---|
| 带字页不能开分层 | 目前整页刚性运镜，没有前景视差 | 先做文字 mask（行检测）+ 把文字固定到 far 层，再按组件排除含字前景；`decompose()` 里已有逐组件过滤的位置 |
| 无字幕/旁白 | 页上已有印刷字，未再叠字幕；成片无音轨 | 接 TTS 出 wav → `assemble.finish(audio=...)`；字幕另起一层，别重复页上文字 |
| 未接角色一致性度量 | 无"跨页一致率"证据 | 加 DINO/CLIP 嵌入（需 torch），或先用颜色直方图占位 |
| Web 未在浏览器点过 | JS 可能报错 | 起 `ui` 后开 F12 看 console（API/Range 已测通） |


---

## 7 接真 I2V 的两条路

**路线 1（零代码，今天就能用）**：`pregen` 后端
1. 在任意有卡/有额度的地方跑 Wan / 可灵 / Seedance，得到 `p06.mp4` 等；
2. 放进 `books/<id>/clips/`，文件名 = `shot_id`；
3. `book.json` 里把该页写成 `"track": "pregen"`；
4. `run` 会自动把它归一化到 1280×720/24fps、跑 QC、参与拼接。

**路线 2（写后端）**：`backends.py` 里加 `WanLocalBackend`，`produces="video"`，返回 `ShotRender(video_path=...)`。
接口已经能同时吃"逐帧"和"整段文件"，不需要改编排。

> 本机 8 GB VRAM 跑 14B 级 I2V 不现实，建议预生成 + 缓存（现场断网也有片可放）。

---

## 8 排障

| 症状 | 原因 | 处理 |
|---|---|---|
| `ffmpeg not found` | 不在 PATH | `winget install Gyan.FFmpeg` 或设 `BOOK2REAL_FFMPEG` |
| 字幕/中文变方块 | 缺字体 | 装微软雅黑；`assemble._font()` 会回退 simhei |
| 所有镜头都 `static` | 预算到点 | 看 `events.jsonl` 的 `budget.exceeded`，调大 `--budget` |
| 分层没效果（还是整张动） | 该页/该书 `layers:false`，或前景与背景同色 | 真书带字页是**故意**关闭的；要开先解决文字 mask（§9） |
| 画面边缘有拖影 | 分层补洞的扩散填充 | 属已知代价，高频区域建议换 patch-match |
| `plan` 里时长都是 6.0s | 文案长度普遍 >15 字，被 clamp 到 6.2 | `storyboard.plan()` 里改 `clamp(3.2+0.20*len, 3.6, 6.2)` |

---

## 9 建议的下一步（一周内）

1. **带字页的视差恢复**（1–2 天）：在 `layers.py` 加文字行检测（水平连通域），把文字钉到 far 层并在近层 alpha 上挖洞；先拿 book2 的 p06/p13 两页试，逐帧看有没有重影。这是目前唯一挡住"真书也能分层"的问题。
2. **旁白 + 字幕**（1 天）：TTS 出 wav → `assemble.finish(audio=...)`；字幕只放页外/底部，别复述页上印刷字。
3. **Web 播放器实测**（半天）：`python -m book2real ui --runs runs --open`，点开 21 镜的 timeline，F12 看 console。
4. **I2V 高光镜头**：先 `pregen` 跑通 4 个跨页，再考虑本地后端。
5. **性能**：只有重启 B 轨时才需要（光影蒙版降采样、`jobs=2` 固定）。

---

## 10 一句话提醒

这套东西的**卖点不是"能生成视频"，是"整本书都在动且不会崩"**。
任何让覆盖率下降、让画面出现伪字的改动，都比"多一个炫技镜头"更亏。
