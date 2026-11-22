# skills/ 技能包 × 官方要求 · 核对与改动记录

> 2026-11-22，分支 `feat/skills-official-align`。对象是 `skills/` 下五个 skill 与
> `docs/前端skill-yhl.docx` 里那 5 条目标。所有数字都是本机实跑，命令见文末。

## 1. 一句话结论

**能力上 5 条目标基本落地（文案那条在 backend），但按官方口径现在还不能交**：
`check_official.py` 的判定是「不可交（有阻断）」——阻断项是 **Q43 一次生成**，
缺口项是**交互录屏、语音问答**，未知项是**成片帧率**。

## 2. 5 条目标 → 落地情况

| yhl 文档里的目标 | 落在哪 | 实跑证据 | 结论 |
|---|---|---|---|
| 1 生成提示词 skill | `ai-video-prompt-architect` + 每页 `seedance_prompt.txt` | 12 页都写出了带风格 DNA 的提示词 | ✅ |
| 2 扣掉绘本文字 + 按文字丰富图片 | `picturebook-to-seedance`（掩膜）+ 本轮新增 `prepare_from_book.py` | 12 页 OCR 残留 0、SSIM 1.0000、保护带入侵 0px | ✅ 但要靠本地 OCR 复检 |
| 3 文生视频 / 一致性 / 自检 / 扣水印 | `seedance-relay-producer`（双锚定 + `qc.py`） | preflight 12 页就绪、0 阻断；真实生成未跑（要花钱） | ⚠️ 代码齐，未实测 |
| 4 文案像绘本、面向儿童 | `backend/app/skill4`（另一个包） | `pytest backend/tests/test_style.py` = 7 passed | ✅（不在 skills/ 里） |
| 5 分场景 / 转场 / 合并镜头 | `picturebook-scene-director` | 12 页 → 12 段、11 条接缝、桥接帧 11 张 | ✅ |

## 3. 官方要求逐条

`python skills/living-picturebook/scripts/check_official.py` 的实测输出（cn01，dry-run）：

| 官方条目 | 状态 | 说明 |
|---|---|---|
| 总时长 100–200 s（Q26/Q39） | ✅ | 原 plan 只有 60 s，本轮加 `ensure_duration` 后 **120 s** |
| 正文页覆盖（Q33） | ✅ | 书 12 页 / 分镜 12 页 |
| ≥720P（Q26） | ✅ | 关键帧 1920×1080，短边 1080 |
| ≥24 fps（Q26） | ❓ | 由 Seedance 输出决定，生成后必须 `ffprobe` 复核 |
| 视频内检索（Q11） | ⚠️ | `page_timeline.json` 12 页/12 段/120.0 s 有了；四能力仍要接到播放器 |
| 触控对象级（Q28/Q35） | ⚠️ | `book.json` 有 34 个 spots，但 skills 产线不产出、不消费 spots |
| 两段视频（Q4） | ❌ | 非交互成片没生成；**交互操作录屏这套产线不产出** |
| 一次生成（Q43） | 🚫 **阻断** | 现在是 12 段分别生成再 `concat.py` 拼接；README 自己也写了"严格口径下不宜作官方交付" |
| 语音问答 2 s（Q9/Q34） | ❌ | 不在 skills 范围，由 `backend/` 负责，要单独验收 |

## 4. 本轮改了什么

| 文件 | 改动 | 为什么 |
|---|---|---|
| `skills/living-picturebook/scripts/prepare_from_book.py` | **新增**：`books/<id>` 的阅读包 → skills 要的 `page_###/` 四件套；掩膜用 S1 的笔画级脚本，清字用 OpenCV Telea + 掩膜外像素锁定；三项 QC 是**真算**（RapidOCR 复读残字 / 掩膜外 SSIM / 掩膜外改动像素数） | 没有这个文件，skills 产线根本接不上本仓库的书；之前 `run_pipeline` 直接报"没有 clean_keyframe.png" |
| `skills/living-picturebook/scripts/ensure_duration.py` | **新增**：把总时长在 5/10 s 档位内对齐到 100–200 s | `plan_scenes` 固定每页 5 s，12 页只有 60 s，低于官方下限 |
| `skills/living-picturebook/scripts/check_official.py` | **新增**：对着官方条目逐项判定，输出 JSON + 结论行 | 让"能不能交"变成可复跑的一条命令，而不是靠人回忆 |
| `skills/living-picturebook/scripts/run_pipeline.py` | 加 `--target-seconds`，plan 之后插 S3c 时长对齐；stdout 强制 UTF-8 | Windows 控制台 GBK 下 `¥` 会直接抛 `UnicodeEncodeError`（实测必现） |

## 5. 实测数字（cn01，720p，dry-run）

```
[prepare] 12 页 PASS 12 / FAIL 0；关键帧统一 1920×1080；OCR 残留 0
[plan]    12 页 -> 12 段，11 条接缝，预估 ¥19.2
[duration] 60s -> 120s（改 12 段）
[preflight] 总页数 12 | 就绪 12 | 阻断 0 | 预估成本 21.6 RMB（熔断阈值 20.0）
[timeline] 12 页 / 12 段 / 总 120.0s
[official] 不可交（有阻断）
```

两点要注意：

- **成本**：720p × 120 s ≈ **21.6 元/本**，已超过 preflight 默认熔断 20 元；官方总额度 500 元（Q42），6 本 ≈ 130 元，够用但要设护栏。
- **关键帧宽度**：`reader/art` 原图只有 1400 px 宽，本轮为了统一比例补边放大到 1920；如果要 1080p 不虚，应该从 `books/<id>/pages` 的源页重新清字（源页 3079 px 宽，已支持）。

## 5.1 第二轮：把三个缺口补上（2026-11-22 晚）

| 缺口 | 现在有什么 | 实测 |
|---|---|---|
| `scene_plan.json`（页→场景） | 新 skill `picturebook-scene-grouper`：按**文本**分场（续页不切 / 时间地点词 / 角色全换 / 场长 ≤4 页），并给出每场镜头与转场建议 | cn01 12 页 → **3 场**、en01 28 页 → **7 场**，文字覆盖都 **100%** |
| `prompts.json`（首帧+prompt+时长+负面词） | `build_prompts.py`：每场一条，含 first_frame / duration_sec / prompt / negative | cn01 3 条，prompt 212–268 字 |
| `qc_report.json`（统一自检） | `build_qc_report.py`：场景/文字覆盖/时长/关键帧 QC/提示词契约/一致性/成片规格 七个检查 | cn01 **6 项 PASS**（成片 SKIP） |
| 一致性度量 | HSV 直方图余弦（同角色出现的页两两比），阈值 0.60 当**异常检测**，不当风格一致率 | cn01 **0.7147**（28 对）；art 上 0.685，作为基线 |

时长：grouper 内部把每镜抬到 5/10 s 并做无损合并，cn01 = 100 s、en01 = 140 s，都落在官方 100–200 s。

测试：`pytest skills` = **29 passed**（新增 6 条 grouper 规则测试）。

## 6. 还没解决的（要人拍板或要真跑）

1. **Q43**：要么找组委会确认"同模型多次调用后拼接算不算二次编辑"，要么这条产线只作能力储备，交付仍走 `book2real/storyfilm.py` 单趟渲染；
2. **交互录屏**：官方要的第二段视频这套产线不产出，得从播放器录（≤5 min，一镜到底）；
3. **帧率**：生成后 `ffprobe` 复核 ≥24 fps，并把结果写回 `qc.json`；
4. **真实生成**：`ARK_API_KEY` 已在 `.env`，但一次 120 s 大约 21.6 元，未跑；
5. **spots 接入**：`book.json` 的 34 个 spots 要进播放器，否则触控这一项没有验收物。

## 7. 复现命令

```bash
# 1) 本仓库的书 -> skills 输入
python skills/living-picturebook/scripts/prepare_from_book.py --book books/cn01 --out .tmp/skill_pages

# 2) 分镜 + 时长对齐 + 桥接 + 预检 + 时间码（不花钱）
python skills/living-picturebook/scripts/run_pipeline.py \
  --pages-dir .tmp/skill_pages --out-dir .tmp/skill_out \
  --resolution 720p --target-seconds 120 --dry-run

# 3) 官方口径核对
python skills/living-picturebook/scripts/check_official.py \
  --plan .tmp/skill_out/scene_plan.json --pages-dir .tmp/skill_pages \
  --book books/cn01 --out .tmp/skill_out/official_check.json
```
