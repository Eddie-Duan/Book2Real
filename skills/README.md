# skills/ — 绘本动态化技能包

把一本绘本 PDF 变成一条不可二次编辑的动态片，封装成三个执行 skill + 一个总装 skill。

> **先读这一段：本目录就是交付路径。**
> 2026-11-23 定：非交互成片**只能用 Seedance 生成**（见 [`docs/CONSTRAINTS.md`](../docs/CONSTRAINTS.md)），
> `book2real/storyfilm.py` 的 2.5D 单趟成片**不得提交**。
> 本目录走的就是交付链路：**分镜 → 每段 5/10 秒调用 Seedance 生成 → 拼接成一条**。

## 1. 两条路径的差别

| 维度 | 现有（`book2real/storyfilm.py`） | 本目录（skills/） |
| --- | --- | --- |
| 生成单位 | 帧（逐帧渲染） | **段**（5s / 10s，一次 API 调用） |
| 画面来源 | 原图分层 + 程序化运镜 + I2V 预留 | Seedance 图生视频（首帧/首尾帧双锚定） |
| 拼接 | 不拼（单趟编码） | `concat.py` 拼成一条（可选 xfade） |
| 可重生成粒度 | 整本 | 单段 |
| 成本 | 本地算力 | 按条计费（flash 实测 **1.8 元 / 10s @720p** → 120s ≈ 21.6 元/本） |
| 交付物 | 成片 + `timeline.json` | 成片 `final_movie.mp4` + `final_movie.json`（规格自检）+ `page_timeline.json`（页级契约） |

**口径提示**：Q43 原文只说"必须一次生成，不能二次编辑"。"同一任务内分段生成再拼接不算二次编辑"
是**队内判断，待官方澄清**（[`docs/答疑/答疑总结.md`](../docs/答疑/答疑总结.md) §3.3 的解读相反）。
所以：交付走 Seedance 分段拼接路径；**说明书只写事实，不写"官方允许拼接"**。

## 2. skill 分工

| 顺序 | skill | 输入 | 输出 | 不可替代的职责 |
| --- | --- | --- | --- | --- |
| S1 | `picturebook-to-seedance` | 单页扫描图 + 文字块坐标 | `clean_keyframe.png` / `page_manifest.json` / `qa_report.json` / `seedance_prompt.txt` | 把文字从画面里"拿走"，同时保证其它像素不变 |
| S2 | `picturebook-scene-director` | `pages/` 目录 | `scene_plan.json` / `bridge/T_*.png` / `prompts/seg_###_prompt.txt` | 决定切段、合并、接缝类型、锚点 |
| S3 | `seedance-relay-producer` | `scene_plan.json`（`--plan`） | `seg_###/clip.mp4` / `qc.json` / `final_movie.mp4` / `concat_audit.json` | 调 Ark Seedance、逐段门禁（zoom/时长/文字/水印/接缝）、拼成一条 |
| **总装** | `picturebook-pipeline` | `pages/` | 全程门禁 + `final_movie.mp4` | **唯一编排入口**：只管顺序、交接契约、花钱闸门与失败回退，三棒逻辑一行不复制 |
| S0 可选 | `picturebook-scene-grouper` | `books/<id>/reader/book.json` | `scene_plan.json` / `prompts.json` / `audio_plan.json` | **按文本**先把页聚成场景（官方 Q33），并出旁白计划 |
| 旁路 | `ai-video-prompt-architect` | 交互层提示词需求 | 提示词参考 | 只服务交互层，不参与视频链路 |

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

## 3.1 输入边界

本链只认 `pages/page_###/` 的四件套（`clean_keyframe.png`、`page_manifest.json`、`qa_report.json`、`seedance_prompt.txt`）。
把 `books/<id>/reader/book.json` 转成这套目录、按官方 100–200 s 对齐总时长、对着官方条目判"能不能交"，
都不在本链内；需要时用 S1 的脚本手工把每页做成四件套，再从 `pages/` 进入本链。

## 3.2 场景层（按文本，不按像素）

`picturebook-scene-director` 的合并判据是像素级相似（色彩 ≤0.15、光流 ≥0.90），真实绘本几乎不满足 —— 实测 cn01 12 页 → 12 段，合并率 0。场景这一层改由 `picturebook-scene-grouper` 按**文字**来划：

要求：`books/<id>/reader/book.json` 每页带 `text`。仓库里的 `books/*/book.json` 是 storyfilm 的 card 结构、没有正文，直接跑会报错退出（故意的，不允许在空文本上输出“100% 覆盖”）。

```bash
python skills/picturebook-scene-grouper/scripts/group_scenes.py \
  --book books/cn01 --out .tmp/scene_plan_cn01.json --pages-dir .tmp/skill_pages
python skills/picturebook-scene-grouper/scripts/build_prompts.py \
  --book books/cn01 --scene-plan .tmp/scene_plan_cn01.json \
  --pages-dir .tmp/skill_pages --out .tmp/prompts_cn01.json
```

输出写 `.tmp/`，不要覆盖 S2 的 `book_out/scene_plan.json`（同名不同 schema）。分组规则：续页不切 / 时间地点词切 / 角色全换切 / 场长 ≤3 页；镜头机位恒为固定（不推拉摇移），人物动作要求完整可见、正常速度。段生成后的 QC 由 `seedance-relay-producer/qc.py` 逐段产出。

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
