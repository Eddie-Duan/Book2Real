---
name: picturebook-one-take
description: |
  把一本绘本做成"一镜到底感"的完整动画短片：先用 Seedream 生成干净的全幅场景板（禁止拿原书页/分格/带字/带水印小图当背景），再用 Seedance 2.5 flash 以"首帧 + 向后延长"链式生成 100–200 秒连续长镜头，旁白与人物对白由 Seedance 原生音画同步生成，最后只做无损流拷贝拼接。所有铁律由代码注入并在生成前后跑硬门禁；失败原因回灌到下一次提示词，不靠模型自觉。
  触发时机：需要把绘本/图画书 PDF 变成完整动画短片、要求人物与画风一致、旁白对白贴合原文、场景智能合并、镜头有动机地移动、场景之间自然过渡、不允许后期剪辑时。
  能力边界：输入是已导入的 books/<id>（含 pages/reader/OCR/curation）；输出 final_movie.mp4（1280×720/24fps/有声）、shot_script.json、prompts/、gate_report.json、issues.md、cost.csv。单次 Seedance 调用上限 30 秒，超过 30 秒必须用 extension 链；这是 API 物理限制，不是剪辑选择。
  不适用情况：1) 只想做静态翻页/2.5D 运镜；2) 不接受任何 API 花费的纯离线演示；3) 需要真人写实素材（Seedance 不允许真人脸参考）。
---

# picturebook-one-take：一镜到底绘本动画

## 0. 一句话

**不是"一页一镜 + 后期拼接"，而是"一条连续长镜头 + 无损接带"。**
Seedance 2.5 单次最多 30 秒，且"视频延长"只返回新增段（实测：输入 4.004 s，延长 5 s，输出 5.000 s）。
所以 100–200 秒只能由若干条**首尾帧严格衔接**的延长段组成；接缝处模型是接着上一段最后一帧继续生成的，
拼接线在画面上不可见。除"无损流拷贝"外不做任何后期：不加片头、不加转场特效、不换配音、不重编码。

> 2026-11-25 实测：第一段末帧与延长段首帧 SSIM≈0.99，帧内容几乎逐像素一致；延长段会严格从上一段末帧继续。

## 1. 七条铁律（由代码注入 + 门禁检查，不靠提示词自觉）

| # | 铁律 | 代码怎么保证 |
|---|---|---|
| R1 | **禁止拿原书页当背景**：分格、带字、带水印的小图一律不许进生成 | 所有首帧/参考图必须来自 `assets/` 下由 Seedream 重新生成的**全幅干净场景板**；`gates.py` 用 SSIM/pHash 把成片帧和原书页比对，>0.80 直接 FAIL |
| R2 | **人物/画风一致**：只允许 bible 里的角色，面容/发型/发卡/服饰/比例/水彩画风锁定 | 每个角色有 headshot 参考图；每段提示词注入 `[Cast Lock]`；`gates.py` 跑人脸数、身份相似度、画风相似度（软门禁 + 联系表人工复核） |
| R3 | **旁白 + 人物对白**：必须覆盖原文，不许自己编剧情 | `script.dialogue` 的每一句必须能在对应页原文里找到（去引号后子串匹配）；`gates.py` 用 ASR 转写成片音轨，与脚本逐句比对，字符重合 <0.55 判 FAIL |
| R4 | **智能分场、镜头有动机地移动、动作要看得见**：不是一页一镜，也不是固定 5 s；禁止只写呼吸/眨眼 | 分镜按"情节节拍"合并 2–3 页；每段 2–4 个镜头；镜头运动必须写动机；`contract.py` 逐个镜头检查动作动词（放/拿/搭/转身/跑/抱…），只有"呼吸、眨眼、轻微重心转移"直接 FAIL |
| R5 | **禁止 zoom（画面整体缩放）**：不许把整幅画面放大/缩小当运镜；物理机位移动（跟拍/横移/轨道/环绕/升降）可以有；近景优先切镜 | 提示词注入 `[Camera Lock]`；`gates.py` 分区域估计光流尺度：整幅均匀缩放且无视差 → `DIGITAL_ZOOM` FAIL；有近/远景视差的物理移动放行 |
| R6 | **场景之间自然衔接**：不许硬切到新场景 | 每个 chunk 结尾必须是一个"前景遮挡/运动模糊/物体入镜"的过渡动作，下一 chunk 从同一动作继续；接缝 SSIM <0.90 判 FAIL |
| R7 | **一次任务链、无人工后期**：同一本书一次跑完 | `generate_chain.py` 顺序生成、失败自动带原因重试；`assemble.py` 只做 `-c copy` 无损拼接；`issues.md` 记录每次失败 |

## 2. "检验"和"反复提示"怎么落地

### 2.1 三道门

1. **写前门（pre-generation，确定性，零成本）**
   - `script.schema`：字段/类型/时长/镜头数。
   - `script.coverage`：每页都被某个 chunk 覆盖。
   - `script.dialogue_fidelity`：对白逐句能在原文找到；旁白与原文重合度 ≥0.60；不许出现 bible 之外的人名。
   - `script.cast`：角色只在原文出现的页出现（例：黄米团 p09 前不许出现）。
   - `script.camera`：禁 zoom 词；每 chunk 至少一个动机镜头；转场类型在白名单。
   - `prompt.contract`：提示词必须含 `[Style][Cast][Scene][Shots][Audio][Camera Lock][Iron Rules][Self Check]` 八块；禁词扫描；音频行必须非空。
2. **写后门（post-generation，逐 chunk 硬门禁）**
   - `spec`：1280×720 / 24 fps / 时长误差 ≤1.5 s / 有音轨。
   - `audio`：非静音（峰值 > −25 dB）。
   - `asr`：旁白/对白与脚本比对。
   - `text_free`：每秒抽帧 OCR，检出文字/水印即 FAIL。
   - `no_raw_page`：每秒抽帧与原书页 SSIM >0.80 即 FAIL。
   - `seam`：与上一段末帧 SSIM <0.90 即 FAIL。
   - `digital_zoom`：分区域光流尺度；整幅均匀缩放（区域间尺度差 <3%）且 |scale−1| >8% → FAIL；有近/远视差的物理机位移动放行，只记 `camera_move` 数值。
   - `style/cast`：MobileNet 特征相似度 + 人脸计数（软门禁，输出数值和联系表；`--strict` 时计入 FAIL）。
3. **失败回灌（repeated prompting，必须做）**
   - 失败后**不是原样重跑**：把 `gate_report.json` 里的 `failures[].fix` 拼成 `[PREVIOUS FAILURE — MUST FIX]` 放到下一次提示词**最顶部**；
   - 同一个 `[Iron Rules]` 和 `[Self Check]` 在提示词**开头和结尾各出现一次**（模型对首尾更敏感）；
   - 重试上限 3 次；仍失败则 `ESCALATE`，写进 `issues.md`，不许静默 PASS。

### 2.2 门禁纪律

- `verdict` 只有 `PASS` / `FAIL` / `ESCALATE`；**没有 DEGRADED=PASS**。
- 任何跳过的检查必须写成 `skipped` + 原因，并在 `verdict` 里算 FAIL（strict 模式）。
- `gate_report.json` 是唯一放行依据；**不许**用"我看过了"代替。

## 3. 生成架构

```
books/<id> ──► bible.json（角色/页面/原文/画风）
              │
              ├─► assets/  Seedream 5.5 全幅场景板 + 角色 headshot
              │       plate_01_room.png (16:9, 无字无 watermark)
              │       plate_02_meadow.png
              │       plate_03_birthday.png
              │       head_huahua.png / head_xiaoman.png / head_dog.png
              │
              ├─► shot_script.json  (5–6 chunks × 2–4 shots, 100–200 s)
              │
              └─► prompts/chunk_##.txt  (八块契约 + 首尾重复铁律)
                        │
        chunk_01: first_frame=plate_01 + 30 s + audio
                        │
        chunk_02: reference_video=chunk_01 + headshots + plate_02? + extend 30 s
                        │
        chunk_03: reference_video=chunk_02 + ... + extend 30 s
                        │
                   gates.py 逐段放行
                        │
        assemble.py: ffmpeg -f concat -c copy  (无损，不重编码)
                        │
                   final_movie.mp4 150 s
```

### 3.1 为什么是 extension，不是独立分段

- 独立分段：每段只拿到一张静帧，人物/画风/光线各生成各的，接缝必跳。
- 尾帧回灌：把上一段生成帧当下一段首帧，扩散重编码逐段累积漂移，且重生成一段会级联重跑下游。
- **extension**：模型拿到上一段**完整视频**（含运动、光线、构图），从最后一帧继续；人物、画风、光线、动作惯性都连续。实测首帧 SSIM≈0.99。

### 3.2 时长与分场

- 单 chunk 4–30 s；默认 30 s。
- 总时长 100–180 s（官方 Q26/Q39 取交集）；默认 150 s = 5×30 s。
- 一页不是一镜：12 页压成 5 个叙事段，每段 2–4 个镜头；页码只用于覆盖检查。
- 镜头时长按对白长度和动作节拍定：特写/反应 2–4 s，中景叙事 4–8 s，全景建立 6–10 s。

### 3.3 动作幅度（2026-11-25 团队反馈）

旧 skill 的默认句是"人物自然动作：呼吸、眨眼、轻微重心转移；表情克制"，结果视频里人物几乎不动。
现在硬性要求：**每个镜头必须有一个看得见的动作**，从本页文字里取（放上木门、搭积木、跺脚、蹲下、跑向门口、抱起、递卡片、叼信封、追、飞）。
呼吸/眨眼只能作为副动作，不能是唯一动作。`contract.py` 的 `action.generic` 会逐个镜头拦。

### 3.3.1 镜头语言（反 zoom = 反画面整体缩放）

允许：
- 横移/跟拍（摄影机与主体距离不变）
- 摇摄/俯仰（原地转机位）
- 环绕（小幅度，主体保持画面中心）
- 升降/轨道平移
- **切镜**到近景/特写（硬切是镜头语言，不是变焦）

禁止的是 **zoom = 画面整体缩放**：
- 变焦镜头（zoom lens）、数字推拉、把整幅画面等比放大/缩小
- 用"镜头缓缓推近"制造特写（模型容易做成平面放大）
- 一个镜头里同时横移+推拉+摇移

允许的是 **物理机位移动**：
- 摄影机真的向前/向后移动（dolly in/out），前提是画面有近远景视差，不是整幅等比放大
- 跟拍、横移、摇摄、环绕、升降
- 近景/特写优先用 **切镜**，不要用推近硬凑

提示词固定句：
```
[Camera Lock] 摄影机只做物理移动：横移、跟拍、摇摄、环绕、升降，或真实的向前/向后移动；
禁止 zoom 镜头、数字缩放、整幅画面等比放大/缩小；需要近景时切镜，不要靠平面放大。
如果机位向前/向后移动，必须产生近远景视差，不能像把画面整体拉近。
```

### 3.4 转场库（chunk 结尾必须选一个）

| 类型 | 写法 | 适合 |
|---|---|---|
| 前景遮挡 | 一片大树叶/气球/狗尾巴从镜头前扫过，遮满画面，下一段从遮挡物移开开始 | 换场景 |
| 物体入镜 | 积木/卡片/虫子飞向镜头，占满画面 | 同场景换动作 |
| 运动模糊 | 快速摇摄/甩镜，下一段从甩镜余势继续 | 情绪转折 |
| 匹配剪辑 | 圆形积木→生日蛋糕/太阳；城堡→蛋糕上的城堡 | 时间跳跃 |
| 穿过门窗 | 摄影机跟着角色穿过门/窗，下一段在另一侧 | 空间转换 |
| 水彩晕染 | 颜色从边缘晕开覆盖画面，下一段从晕染中显影 | 绘本风格收尾 |

硬切只允许发生在 **chunk 内部**的镜头之间，不许发生在 chunk 接缝。

## 4. 提示词契约（八块，顺序固定）

```
[Iron Rules]  ← 必须原样包含 7 条（见 references/prompt-contract.md）
[Style]       ← 画风 DNA + 负向（不要3D/写实/赛璐璐）
[Cast]        ← 角色短名 + 参考图编号 + 只允许这些角色
[Scene]       ← 地点/时间/光线/道具；首帧或参考板编号
[Shots]       ← 镜头1/2/3，每镜：运镜（带动机）→ 动作 → 空间变化 → 声音
[Audio]       ← 旁白{} / 对白{} / 音效<> / 音乐()=不要BGM
[Camera Lock] ← 反 zoom 固定句
[Self Check]  ← 结尾重复 7 条 + 本段页码/对白/转场
```

硬性写法：
- 台词用 `{}`，音效用 `<>`，音乐用 `()`，字幕用 `【】`（官方符号规范）。
- 每个角色第一次出现写"名字（参考图片N：2–3 个稳定特征）"，之后只用短名。
- 不用三视图；headshot + 全幅板即可（三视图会让模型当成多个角色）。
- 提示词总字数 ≤ 1200 汉字；超了先删重复形容，不删运镜/音频/约束。
- 开头第一句固定：`画面无任何字幕、文字、水印、logo。`

## 5. 执行流程（必须按顺序）

```bash
# 0) 预算与环境（零成本）
python skills/picturebook-one-take/scripts/one_take.py doctor --book books/cn01

# 1) 读原文 + 角色表 + 画风（零成本）
python skills/picturebook-one-take/scripts/one_take.py bible --book books/cn01 \
  --out runs/cn01/one_take

# 2) 写分镜（默认规则模板；--llm 用 deepseek-flash 生成后仍要过写前门）
python skills/picturebook-one-take/scripts/one_take.py plan --book books/cn01 \
  --bible runs/cn01/one_take/bible.json --out runs/cn01/one_take \
  --target-seconds 150 --llm

# 3) 写前门（不过不许花钱）
python skills/picturebook-one-take/scripts/one_take.py preflight \
  --bible runs/cn01/one_take/bible.json --script runs/cn01/one_take/shot_script.json \
  --out runs/cn01/one_take

# 4) 生成干净场景板 + headshot（Seedream 5.5，约 ¥0.12/张）
python skills/picturebook-one-take/scripts/one_take.py assets \
  --bible runs/cn01/one_take/bible.json --out runs/cn01/one_take --which room,meadow,birthday,dog

# 5) 装配提示词（八块 + 首尾铁律 + 失败回灌）
python skills/picturebook-one-take/scripts/one_take.py prompts \
  --bible runs/cn01/one_take/bible.json --script runs/cn01/one_take/shot_script.json \
  --out runs/cn01/one_take

# 6) 逐段生成 + 写后门（训练/排练默认 480p；正式交付用 720p）
python skills/picturebook-one-take/scripts/one_take.py generate \
  --bible runs/cn01/one_take/bible.json --script runs/cn01/one_take/shot_script.json \
  --out runs/cn01/one_take --resolution 480p --max-cny 120

# 正式交付：只换分辨率，提示词/分镜/门禁不变
#   --resolution 720p --max-cny 260

# 7) 无损拼接 + 终检
python skills/picturebook-one-take/scripts/one_take.py assemble --out runs/cn01/one_take
python skills/picturebook-one-take/scripts/one_take.py verify --out runs/cn01/one_take              # 训练：接受 480p/720p
python skills/picturebook-one-take/scripts/one_take.py verify --out runs/cn01/one_take --delivery   # 交付：必须 720p
```

## 6. 费用与账号事实（2026-11-25 实测）

| 项 | 事实 |
|---|---|
| DeepSeek | 直连 `/models` 只有 `deepseek-flash` / `deepseek-v4-pro`；`.env` 的 `deepseek-chat` 实际路由到 `deepseek-flash`。**用 flash** |
| Seedream | `doubao-seedream-5-0-flash-260915` **本账号未开通**（ModelNotOpen）；已开通且实测可用的是 `doubao-seedream-5-5-261028`。Seedream 5.5 出图 ¥0.12/张 |
| Seedance | `doubao-seedance-2-5-flash-261115` 实测可用；720p / 24fps / 原生音轨；单次 4–30 s；extension 只返回新增段 |
| 30 s 上限 | 物理限制，不能一次出 100–200 s；"一镜到底"靠 extension 链 + 无缝接带实现 |
| 帧率 | Seedance 输出 24 fps；官方要求 ≥24 fps |
| 训练/正式 | 训练、排练、提示词验证默认 **480p**（约半价）；正式交付才用 **720p**。不要在一条 extension 链中途换分辨率。 |
| 旁白/对白 | Seedance 原生音频实测能逐字说出中文旁白与对白；ASR 校验通过 |

## 7. 与旧 skill 的关系

- `picturebook-to-seedance`：只保留"去字/关键帧清洗"的工具价值；**不再用它的分格原图当首帧**。
- `seedance-relay-producer`：双锚定接力 + QC 的思路保留；本 skill 改为 **extension 链**，不再用首尾帧 A/B/C 判定。
- `picturebook-scene-grouper` / `picturebook-scene-director`：分场规则可参考，但本 skill 以 `shot_script.json` 为唯一真值。
- `docs/CONSTRAINTS.md`：旧的"严格静态机位"已被用户 2026-11-25 指令取代；本 skill 允许**有动机的物理机位移动**（横移/跟拍/摇摄/环绕/真实推拉），**禁止 zoom 画面整体缩放**。
- `living-picturebook`：最新仓库已删除，由 `picturebook-pipeline` 承接；本 skill 不再依赖它。旧的 `enrich_seg_prompts.py` 里"呼吸/眨眼"默认句已改成"按本段文字做看得见的动作"。

## 8. 自检清单（交付前逐条打勾）

- [ ] `preflight` 全绿，`prompt.contract` 全绿
- [ ] 每个 chunk `gate_report.json` verdict=PASS
- [ ] 全片 `digital_zoom` 无 FAIL；整幅均匀缩放（无视差）为 0；物理推拉有视差、幅度受控
- [ ] 全片 `no_raw_page` 无 FAIL；任一帧与原书页 SSIM <0.80
- [ ] 全片 `text_free` 无 FAIL；OCR 0 条文字
- [ ] 接缝 SSIM ≥0.90；无黑场
- [ ] ASR 覆盖每句旁白/对白；无新增人名
- [ ] 成片 1280×720 / 24 fps / 100–180 s / 有音轨
- [ ] `issues.md` 已记录所有失败与修复；没有未处理的 ESCALATE
- [ ] 花费 ≤ 预算，`cost.csv` 与实际任务 id 对账