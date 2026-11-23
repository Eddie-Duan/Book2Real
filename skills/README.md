# skills/ — 绘本动态化技能包

把一本绘本 PDF 变成一条不可二次编辑的动态片，封装成三个执行 skill + 一个总装 skill。

> **先读这一段：这是并行方案，不是现有交付路径。**
> 本仓库的官方交付仍走 `book2real/storyfilm.py` 的单趟逐帧渲染（只混音轨、不拼镜头）。
> 这四个 skill 走的是另一条路：**分镜 → 每段 5/10 秒调用 Seedance 生成 → 拼接成一条**。
> 两者互不依赖；本目录不修改、也不替换 `book2real/`、`backend/` 的任何代码。

## 1. 两条路径的差别

| 维度 | 现有（`book2real/storyfilm.py`） | 本目录（skills/） |
| --- | --- | --- |
| 生成单位 | 帧（逐帧渲染） | **段**（5s / 10s，一次 API 调用） |
| 画面来源 | 原图分层 + 程序化运镜 + I2V 预留 | Seedance 图生视频（首帧/首尾帧双锚定） |
| 拼接 | 不拼（单趟编码） | `concat.py` 拼成一条（可选 xfade） |
| 可重生成粒度 | 整本 | 单段 |
| 成本 | 本地算力 | 按条计费（720p 5s ≈ 0.9 元，1080p ≈ 1.6 元；本仓库实测 1.00 元/秒 → 5s=5 元，见账本） |
| 交付物 | 成片 + `timeline.json` | 成片 `final_movie.mp4` |

**口径提示**：`concat` 在字面上就是「拼镜头」，但 **2026-11-23 已确认：同一任务内分段生成再拼接不算二次编辑**（只禁人工后期剪辑）。所以本路径与 `book2real/storyfilm.py` 单趟渲染**都可以作为官方交付成片**，二选一；本路径更适合在需要"单段可重生成"时使用。

## 2. 四个 skill 各管什么

| 顺序 | skill | 输入 | 输出 | 不可替代的职责 |
| --- | --- | --- | --- | --- |
| S1 | `picturebook-to-seedance` | 单页扫描图 + 文字块坐标 | `clean_keyframe.png` / `page_manifest.json` / `qa_report.json` / `seedance_prompt.txt` | 把文字从画面里"拿走"，同时保证其它像素不变 |
| S2 | `picturebook-scene-director` | `pages/` 目录 | `scene_plan.json` / `bridge/T_*.png` / `prompts/seg_###_prompt.txt` | 决定切段、合并、接缝类型、锚点 |
| S3 | `seedance-relay-producer` | `scene_plan.json`（`--plan`） | `seg_###/clip.mp4` / `qc.json` / `final_movie.mp4` | 调 Ark Seedance、逐段门禁、拼成一条 |
| 总装 | `picturebook-pipeline` | `pages/` | `final_movie.mp4` 全程门禁 | 只管顺序、交接契约、花钱闸门与失败回退；三棒逻辑一行不复制 |

`picturebook-pipeline` 是唯一的编排入口：它固定 S1→S2→S3，在每个门禁处停下确认，不新增处理层。
旁路的 `ai-video-prompt-architect` 只服务交互层提示词，不进这条链。

## 3. 怎么跑

整链推荐让 agent 加载 `picturebook-pipeline`：它会依次执行 S1→S2→S3，在「S1 全页 PASS」「S2 计划确认」「preflight 预算确认」「480p 样片确认」处停下等你。

想手工只跑某一棒时：

```bash
# 0) 依赖：numpy/Pillow（导演层）+ opencv/scikit-image/requests/PyYAML（relay）+ ffmpeg 在 PATH
pip install -r skills/seedance-relay-producer/requirements.txt

# 1) S2 分镜（免费）
python skills/picturebook-scene-director/scripts/plan_scenes.py --pages-dir pages --out-dir book_out

# 2) S3 零成本预检（不调用 API）
python skills/seedance-relay-producer/scripts/preflight.py \
  --config skills/seedance-relay-producer/config.yaml \
  --plan book_out/scene_plan.json --pages-dir pages \
  --out-dir book_out/relay --resolution 1080p --dry-run

# 3) S3 真跑（会按条计费；先 480p 样片，再 1080p）
set ARK_API_KEY=...            # PowerShell: $env:ARK_API_KEY="..."
python skills/seedance-relay-producer/scripts/generate.py \
  --config skills/seedance-relay-producer/config.yaml \
  --out-dir book_out/relay --resolution 480p --yes
python skills/seedance-relay-producer/scripts/poll_download.py \
  --config skills/seedance-relay-producer/config.yaml --out-dir book_out/relay
```

模型默认 `doubao-seedance-2-5-flash-261115`（见 `skills/seedance-relay-producer/config.example.yaml`）。
注意 **2.5 带首帧时不允许下发 `ratio`**，配置项 `ratio_follows_first_frame: true` 已默认打开。

## 4. 测试

全部是纯标准库脚本，几秒跑完，不需要 API Key：

```bash
python -m pytest skills/seedance-relay-producer/tests -q   # 段→伪页映射/锚点/QA 聚合/漂移/水印
```

## 5. 已知限制

- **拼镜头与 Q43 的口径差异**：见第 1 节末尾。用作官方交付前请先与赛题口径对齐。
- **本链不产出页 ↔ 时间码表**：交互层若需要，另行生成，不要塞回三棒。
- **风格漂移取决于提示词**：每页 `seedance_prompt.txt` 必须带风格 DNA（画风/笔触/配色/光线），否则模型会把平涂插画渲染成写实照片。
- **QC 是一致性检查，不是审美判断**：它只保证"首帧对得上、尾帧落在锚点上、没有文字和水印"，人物崩坏仍需人工抽检。
- **桥接帧是合成图**：黑场/模糊/遮挡是静态合成，不是真实运动模糊；个别模型可能不接受，需要首次生成时验证。
