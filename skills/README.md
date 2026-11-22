# skills/ — 绘本动态化技能包

把一本绘本 PDF 变成「一条不可二次编辑的动态片 + 一张页 ↔ 时间码表」，封装成五个可组合的 skill。

> **先读这一段：这是并行方案，不是现有交付路径。**
> 本仓库的官方交付仍走 `book2real/storyfilm.py` 的单趟逐帧渲染（只混音轨、不拼镜头）。
> 这五个 skill 走的是另一条路：**分镜 → 每段 5/10 秒调用 Seedance 生成 → 拼接成一条**。
> 两者互不依赖；本目录不修改、也不替换 `book2real/`、`backend/` 的任何代码。

## 1. 两条路径的差别

| 维度 | 现有（`book2real/storyfilm.py`） | 本目录（skills/） |
| --- | --- | --- |
| 生成单位 | 帧（逐帧渲染） | **段**（5s / 10s，一次 API 调用） |
| 画面来源 | 原图分层 + 程序化运镜 + I2V 预留 | Seedance 图生视频（首帧/首尾帧双锚定） |
| 拼接 | 不拼（单趟编码） | `concat.py` 拼成一条（可选 xfade） |
| 可重生成粒度 | 整本 | 单段 |
| 成本 | 本地算力 | 按条计费（720p 5s ≈ 0.9 元，1080p ≈ 1.6 元） |
| 交付物 | 成片 + `timeline.json` | 成片 + `page_timeline.json`（页级契约） |

**口径提示**：`concat` 在字面上就是「拼镜头」。若按官方 Q43「同一册必须一次生成、不能二次编辑」的严格口径，本路径不宜作为官方交付成片，更适合作为
① 能力储备与对照实验；② 允许分段生成时的备选产线；③ 页级时间码这套契约的参考实现。

## 2. 五个 skill 各管什么

| 顺序 | skill | 输入 | 输出 | 不可替代的职责 |
| --- | --- | --- | --- | --- |
| S1 | `picturebook-to-seedance` | 单页扫描图 + 文字块坐标 | `clean_keyframe.png` / `page_manifest.json` / `qa_report.json` / `seedance_prompt.txt` | 把文字从画面里"拿走"，同时保证其它像素不变 |
| S2 | `picturebook-scene-director` | `pages/` 目录 | `scene_plan.json` / `bridge/T_*.png` / `prompts/seg_###_prompt.txt` | 决定切段、合并、接缝类型、锚点 |
| S3–S4 | `seedance-relay-producer` | `scene_plan.json`（`--plan`） | `seg_###/clip.mp4` / `qc.json` / `final_movie.mp4` | 调 Ark Seedance、逐段门禁、拼成一条 |
| S5 | `living-picturebook` | `scene_plan.json` + 成片 | `page_timeline.json` / 编排与验收 | 把"页"翻译成时间码 |
| 旁路 | `ai-video-prompt-architect` | 交互层提示词需求 | 提示词参考 | 只服务交互层，不参与视频链路 |

`living-picturebook` 同时是总装层：`scripts/run_pipeline.py` 把八步串成一条命令。

## 3. 怎么跑

```bash
# 0) 依赖：numpy/Pillow（导演层）+ opencv/scikit-image/requests/PyYAML（relay）+ ffmpeg 在 PATH
pip install -r skills/seedance-relay-producer/requirements.txt

# 1) 免费彩排：分镜 + 桥接帧 + 生成前检查 + 打印请求体，不花钱
python skills/living-picturebook/scripts/run_pipeline.py \
  --pages-dir pages --out-dir book_out --resolution 1080p --dry-run

# 2) 真跑（需要 ARK_API_KEY，会按条计费）
set ARK_API_KEY=...            # PowerShell: $env:ARK_API_KEY="..."
python skills/living-picturebook/scripts/run_pipeline.py \
  --pages-dir pages --out-dir book_out --resolution 1080p --yes

# 3) 只查某一时刻是第几页
python skills/living-picturebook/scripts/book_timeline.py \
  --plan book_out/scene_plan.json --at 13.5
```

模型默认 `doubao-seedance-2-5-flash-261115`（见 `skills/seedance-relay-producer/config.example.yaml`）。
注意 **2.5 带首帧时不允许下发 `ratio`**，配置项 `ratio_follows_first_frame: true` 已默认打开。

## 4. 测试

全部是纯标准库脚本，几秒跑完，不需要 API Key：

```bash
python skills/living-picturebook/tests/test_book_timeline.py      # 5 条：页区间/边界/叠化/CLI
python skills/living-picturebook/tests/test_run_pipeline.py       # 5 条：八步顺序/目录校验/--from--to
python skills/seedance-relay-producer/tests/test_plan_adapter.py  # 7 条：段→伪页映射/锚点/QA 聚合
```

## 5. 已知限制

- **拼镜头与 Q43 的口径差异**：见第 1 节末尾。用作官方交付前请先与赛题口径对齐。
- **合并段的页时间码是估算值**：一镜到底没有硬边界，`page_timeline.json` 里会标 `estimated: true`。
- **风格漂移取决于提示词**：每页 `seedance_prompt.txt` 必须带风格 DNA（画风/笔触/配色/光线），否则模型会把平涂插画渲染成写实照片。
- **QC 是一致性检查，不是审美判断**：它只保证"首帧对得上、尾帧落在锚点上、没有文字和水印"，人物崩坏仍需人工抽检。
- **桥接帧是合成图**：黑场/模糊/遮挡是静态合成，不是真实运动模糊；个别模型可能不接受，需要首次生成时验证。
