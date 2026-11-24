# 交互（触控 / 检索）交接：还欠什么、怎么做

> 写于 2026-11-24 23:00。分支 `feat/skills-tuning`（HEAD `05d81f4`，含队友的 `feat/one-take-merge`）。
> 这份文档只讲**交互层**（Q11 视频内检索四能力 + Q28/Q35 触控）。视频生成、2 s 首响不在本文范围。

## 0. 先看这里：为什么会"文档提到、树里没有"

| 你找不到的 | 原因 | 怎么拿到 |
|---|---|---|
| `scripts/one_take_timeline.py` | 我写完只在工作区，**没提交**；已在本次提交补上 | `git pull` 后就在 `scripts/` 下 |
| `docs/HANDOVER_INTERACTION.md` | 同上 | `git pull` |
| `runs/cn01/one_take/{shot_script.json, preview_150s.mp4}` | `runs/**` 是**运行时产物，被 .gitignore 排除**，永远不会进 git | 由视频侧（`skills/picturebook-one-take`）跑出来；或从队友机器拷贝 |
| `runs/cn01/seedance_film/<stamp>/` | 同上，且它是我用 `one_take_timeline.py` **生成**的 | 用 §7 的命令自己生成（1 条命令，几秒钟） |
| `books/<id>/click_targets.json` | **这个在仓里**（本次之前已提交） | `git pull` 即可 |

> 结论：只有 `runs/` 两处是"跑出来才有"，其余都在仓里。schema 样例见
> [`docs/examples/reader_timeline.example.json`](examples/reader_timeline.example.json)（截取的真实产物，可直接对照字段）。

## 1. 一句话

**数据链路已经通了、UI 也能跑**：`one_take` 成片能被播放器读、能按页跳、能用官方脚本的固定元素表。
**还差三件**：① 生成时没记元素在片中的真实位置 → 点选不准；② 页覆盖缺封面、待逐项点验；
③ en02/en03 没有 spots（连可点元素都没有）。

## 2. 现在的链路（谁产什么）

```
runs/<id>/one_take/{shot_script.json, preview_150s.mp4}      ← 视频侧（picturebook-one-take）
        │
        ├─ scripts/one_take_timeline.py     页级时间码 + spots/ocr/text，发布成播放器一档
        ▼
runs/<id>/seedance_film/<stamp>/{film.mp4, timeline.json, meta.json}
        │
        ├─ B2R_FILM_SEEDANCE=1 时 reader_app._film_dir() 优先读它（否则读 2.5D 的 reader_film/）
        ▼
/film?book=<id>  →  film.js：按句/词跳帧、点选暂停+介绍、描述当前帧、查字框选
                          + 「◎ 可点元素」提示圆圈层（H 键，读 books/<id>/click_targets.json）
```

相关文件：

| 文件 | 作用 |
|---|---|
| `scripts/one_take_timeline.py` | one_take 成片 → 页级 timeline（**独立脚本**，不依赖已删除的 living-picturebook） |
| `scripts/build_click_targets.py` | 官方脚本 `[点击]/[高亮]` 行 → `books/<id>/click_targets.json` |
| `book2real/reader_app.py` | `/api/film/<id>`、`/api/click_targets/<id>`、`_film_dir()` 双源切换 |
| `book2real/web/film.js` | 四能力 + 提示圆圈层（`drawHints` / `toggleHints`） |

## 3. 已经实测能用的（证据）

| 项 | 证据 |
|---|---|
| 成片能被播放器读 | `/api/film/cn01` → 200，11 段，duration 150.0，run `20261124-224116` |
| 页级时间码 | 11 段 `p02…p12`，150.0 s，31 个可点元素、144 条 OCR 行 |
| 按 t 落页 | 浏览器跳 36 s → 标题「一起搭积木吧」，角标「第 4 页 · 15s」 |
| 提示圆圈层 | 「◎ 可点元素」按钮可用并点亮；`/api/click_targets/cn01` → 200，7 项 |
| 规格 | 150 s、1280×720、24 fps（A5/A8 过） |

## 4. 还欠什么（按优先级）

### P0 —— 点选位置不准（根因，必须视频侧配合）

`one_take` 的画面是**重新生成**的，机位/构图与原页不一致（例：p04 画面里城堡在正中，页图里偏左）。
现在的 `spots[].box` 是**页图归一化坐标**，直接套到生成帧上会偏，点不中。

两条可行路线（选一）：

1. **生成时记账（推荐）**：`picturebook-one-take` 在出每个 chunk 时，把该 chunk 里"官方点击元素"在帧中的 bbox 写进 `shot_script.json`
   （例如 `chunk.shots[i].targets[] = {name, box:[x,y,w,h] 归一化}`）。`one_take_timeline.py` 优先用这份框，没有才退回页图框。
2. **逐帧对齐**：对每页首帧做模板/相位相关，把页图框映射到帧（`scripts/consistency_audit.py` 里的 `phase_shift` 可复用）。
   精度会打折，且每段要多算一次。

验收：官方 7 个点击项各自"点中心 ±5% 区域"能命中该元素 ≥ 4/5 次（抽该元素出现页的 5 个时间点）。

### P0 —— 页覆盖缺封面

chunks 从 `p02` 起，`p01`（封面）不在时间码里。官方 A10 要求正文每页有体现，封面可作片头但**不能凭空丢**。

验收：`timeline.json` 的 `segments[].id` 覆盖书里全部页（或明确标注"封面并入片头，映射到 0–5 s"）。

### P1 —— 四项能力只验了"跳帧"，其余三项待点验

| 能力 | 现状 | 待做 |
|---|---|---|
| 按句/关键词跳帧 | 已验 | 用官方脚本第 04 行「找到搭了九块积木的画面」再验一次 |
| 点选→暂停→介绍 | 未验 | 选 p04 的 `积木城堡`，点击后应 `video.paused === true` 且 `#info` 可见 |
| 描述当前帧 | 未验 | 暂停后点「描述」，检查 `#desc` 有内容且引用了当前页文本 |
| 查字/词并框出 | 未验 | 搜「我又不是故意的」（A13 官方必选），应跳到对应页并画框 |

### P1 —— en02/en03 没有 spots

`books/en02`、`books/en03` 的 reader 包 `spots = 0`，`click_targets.json` 全部 `matched=false`、`needs_spots=true`。
需要先跑 `python -m book2real analyze --book books/en02`（CPU 重活）再重跑 `build_click_targets.py`。

### P2 —— 高亮项

官方每本有 3 处 `[高亮]`（如「高亮"积木城堡"」「高亮这句话」）。`build_click_targets.py` 已把「高亮某句」映射到 OCR 行（`kind: highlight_text`），
但「高亮这句话」这种**回指上一句**的仍没解（cn01 n=19 就是它）。做法：按脚本顺序取上一条带引号的文本作为目标。

## 5. 坑与约定（踩过的）

1. **提示圆圈不许烤进成片**：Q43 一次生成、不得二次编辑；圆圈只画在 `#mark` canvas（UI 层），录屏时自然带出。数据来源是 `click_targets.json`，**不是**成片像素。
2. **`B2R_FILM_SEEDANCE=1` 才读 one_take 那一档**，否则读 2.5D 的 `runs/<id>/reader_film/`（两套互不覆盖）。起服务：
   `B2R_FILM_SEEDANCE=1 python -m book2real reader --books books --port 8792`
3. **端口要错开**：我测试时 `ui` 和 `reader` 抢过 8791，表现是"接口 404 但服务在"——404 说明你连的是 `ui` 不是 `reader`。
4. **`living-picturebook` 已被删除**（`05d81f4` 起）。旧的 `book_timeline.py` reader 版时间码在历史 `2f815bd` 里，需要可 `git show 2f815bd:skills/living-picturebook/scripts/book_timeline.py` 取回；现在**以 `scripts/one_take_timeline.py` 为唯一入口**。
5. `film.js` 的框口径：`spots[].box` 是 `[x, y, w, h]` 归一化；`ocr_lines[].box` 是 `[x0,y0,x1,y1]`，且靠 `source_w/source_h=1` 走恒等映射（timeline 里已这么写，别改成像素）。
6. 时间码里交叉溶解要摊平：上一页 `t1` 贴到下一页 `t0`（`[t0,t1)` 单一归属），否则边界会抖页。

## 6. 验收清单（可勾选）

- [ ] `/api/film/cn01` 返回的 `segments[].id` 覆盖全部正文页（含封面处理说明）
- [ ] 官方 7 个点击项：每项在出现时间点抽 5 帧，"点中心 ±5%" 命中 ≥4/5
- [ ] 「找到搭了九块积木的画面」→ 跳到正确页并框出
- [ ] 「我又不是故意的」→ 跳到正确页并框出
- [ ] 点选某元素 → 视频暂停 + 右侧介绍面板出现（截图留证）
- [ ] 描述当前帧 → 面板内容引用当前页文本（截图留证）
- [ ] en02/en03 的 `click_targets.json` 无 `needs_spots: true`
- [ ] 交互录屏（≤5 min）用新成片重录一次（旧录屏对应 2.5D 片）

## 7. 复现命令

> 前提：`runs/<id>/one_take/{shot_script.json, preview_150s.mp4}` 已存在（视频侧产出，**不在 git 里**）。
> 缺 `shot_script.json` 就跑 `skills/picturebook-one-take` 的 planner；只缺时间码就跑下面第 1 步。

```powershell
# 0) 依赖：只用到 stdlib + ffprobe（不需要 cv2/torch）
python -c "import json,shutil;print(shutil.which('ffprobe'))"

# 1) 生成页级时间码并发布（one_take 成片 -> 播放器一档）
python scripts/one_take_timeline.py --book books/cn01 --run runs/cn01/one_take `
    --publish-root runs/cn01/seedance_film
#   产物：runs/cn01/seedance_film/<stamp>/{film.mp4, timeline.json, meta.json}
#   自检：段数=书页数、时间连续无重叠、每个 spot 框在 [0,1] 内（脚本会打印 [warn]）

# 2) 起交互服务（B2R_FILM_SEEDANCE=1 才读上面这一档，否则读 2.5D 的 reader_film/）
$env:B2R_FILM_SEEDANCE="1"; python -m book2real reader --books books --port 8792
# 打开 http://127.0.0.1:8792/film?book=cn01

# 3) 重新生成固定元素表（改了脚本或补了 spots 之后）
python scripts/build_click_targets.py
```

## 8. 边界

- 2 s 首响（Q9/Q34）**已单独达标**：MiniMax ASR+TTS，cn01 15/15、en01 10/10，P95 ≈1.07 s。不在本文范围，别重复做。
- OCR 是**本地 RapidOCR**（入库阶段），不在交互链路里。
- 视频生成侧的机位政策（严格静态 + zoom 4% 门禁）见 `docs/CONSTRAINTS.md`；交互只需要视频侧**提供元素在片中的坐标**，不必改机位。
