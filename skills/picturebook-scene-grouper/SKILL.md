---
name: picturebook-scene-grouper
description: 按文本把绘本页聚成场景（S0 可选），并产出场景级提示词与旁白/对白声音契约。当绘本需要先按叙事分场（续页不切、时间地点转换、角色全换、场长≤3页）、再进入 picturebook-scene-director 做接缝判定，或需要 audio_plan.json 的旁白/对白分段（谁在第几秒说什么、什么音色）时使用。输入 books/<id>/reader/book.json（必须带每页 text），输出 scene_plan.json、prompts.json、audio_plan.json。
---

# picturebook-scene-grouper — 按文本把页聚成场景

把一本绘本的**页**重新组织成**场景**：一页不一定是一场，几页也可能是同一场。
判据以**文字**为主（官方要求成片必须覆盖正文每页文字），画面特征只作辅助。

## 为什么需要它

`picturebook-scene-director` 只在"相邻两页能插值"时合并镜头，判据是像素级相似度
（色彩距离 ≤0.15、光流一致性 ≥0.90），真实绘本几乎不可能满足 —— 实测 cn01 12 页
→ 12 段，合并率 0。场景这一层需要按叙事来划，而不是按像素。

## 铁律

1. **输入必须有正文**：`book.json` 每页的 `text` 为空时脚本直接报错退出，不许在空文本上输出“100% 覆盖”的假结果。
2. **场长上限 3 页**（官方 Q33：跨 2–3 页可合并）；到第 4 页必切，续页也不能突破。
3. **机位锁定**：本 skill 只下发固定机位（`static`），不推近、不拉远、不摇、不移、不变焦；景别只在镜头之间变化（建立 LS → 中景 → 收束 CU），镜头内部恒定。
4. **动作不锁**：人物动作必须“完整、可见、按正常速度完成”，禁止只写呼吸/眨眼/微动；无人物的场景写环境动态但主体尺寸位置稳定。
5. **id 两种命名都要认**：book 页 id 可能是 `p01`，S1 目录是 `page_001`；脚本按“同名 → manifest.page_id → 数字序号”三级映射，映射失败要报错，不能静默跳过。
6. **不和 director 抢文件**：本 skill 的 `scene_plan.json` 与 `picturebook-scene-director` 的 `book_out/scene_plan.json` 同名但 schema 不同，务必写到 `.tmp/`，不要覆盖正式 plan，也不要拿它喂 relay；director 通过 `--scene-groups .tmp/scene_groups.json` 消费它（按数字序号映射 `p01 -> page_001`），只取“哪些页同一场”，分镜与接缝仍由 director 决定。

## 输入 / 输出

```
输入：books/<id>/reader/book.json      # 每页 id / text / characters（text 必填）
可选：page 目录（S1 产出的四件套）——给了就把 scene_tag 写回 page_manifest.json

输出：scene_plan.json（写到 .tmp/，schema 与本 skill 一致）
  {
    "plan_version":"1.0.0", "source":"picturebook-scene-grouper (text-first)",
    "book_id", "lang", "page_count", "scene_count", "max_pages_per_scene":3,
    "segments": [
      { "segment_id":"sc_01", "pages":["p02","p03"], "duration_sec":10,
        "shot_plan":[{"kind":"merged","pages":["p02","p03"],"duration_sec":10}],
        "character_ids":["huahua"],
        "transition_out":{"type":"MATCH_CUT","bridge":"none","reason":"角色有交集"} }
    ],
    "coverage":{"pages_total":12,"pages_assigned":12,"text_pct":1.0},
    "estimated_total_sec":100
  }
```

## 四条判定规则（可解释、可复现）

1. **续页不切**：上一页文字没有句末标点（。！？.!?…），这一页留在同一场（但受规则 4 硬上限约束）；
2. **硬边界**：本页开头出现时间/地点转换词（第二天/后来/这时/回到家/Meanwhile/Later…）→ 切；
3. **角色全换**：两页的角色集合完全不重叠（且都非空）→ 切；
4. **场长上限 3 页**（官方 Q33）→ 满 3 页必切。

空文本页（OCR 漏页）不新开场景，挂到当前场；整本书都没有正文则整体报错。

## 场景内镜头规划

- 1 页 → 单页镜头 5 s
- 2 页 → 合并成一个镜头 10 s（双锚定）
- 3 页 → 前两页合并 10 s + 单页 5 s

`fit_duration` 会把总时长抬进官方 100–200 s 区间；抬不够或超上限只在 stderr 警告。

## 场景间转场建议

- 角色有交集 → `MATCH_CUT`（0 s 硬切，构图呼应）
- 角色无交集 → `DESIGNED_TRANSITION`，桥接帧 `black`（跨场）或 `occlude`（同场换景）

真值仍由 `picturebook-scene-director` 的接缝判定兜底；本 skill 只定"哪几页是一场"。

## 摄影机语言（camera_language.py）

受 Stable Cinemetrics（arXiv 2509.26555，把电影控制拆成 Setup/Event/Lighting/Camera 四类，
共 76 个控制节点）启发，每镜下发结构化字段，而不是一句"镜头平稳"：

| 字段 | 取值 |
|---|---|
| shot_size | ELS 大远景 / LS 全景 / MS 中景 / CU 近景 |
| angle | eye 平视 / low 仰拍 / high 俯拍 |
| move | **static（唯一允许值）** |
| speed | slow |
| subject | 人物完整动作 或 环境动态 |

规则：场首镜 = LS + eye，场末镜 = CU + eye，场中镜在 MS/CU 交替、角度轮换，
**相邻两镜景别不重复**；所有镜头共用“固定机位、不推拉摇移变焦”的锁句。
动作文案：有角色时下发“肢体、躯干与表情明显幅度，按正常速度完成”，
无角色时下发“环境动态可见，但主体轮廓、尺寸与位置稳定”。

## 声音计划（build_audio_plan.py）

参考 MM-StoryAgent（arXiv 2503.05242：storybook 视频的音频是旁白 + 角色对白 + 音乐 + 音效
**多通道**，且角色要有稳定的声音形象），本 skill 只产出契约，不做合成：

```json
{ "scene_id": "sc_01", "start_sec": 4.2, "end_sec": 6.1,
  "speaker": "花花", "kind": "dialogue", "text": "我们一起搭城堡！",
  "voice": "zh-CN-YunjianNeural", "rate": "+4%", "pitch": "+8Hz", "mood": "excited" }
```

- 引号内 = 对白，引号外 = 旁白；
- 说话人判定顺序：引号前 12 字里的角色名 > 本页唯一角色 > 本页出现最多的角色；
- 音色按角色 id 排序轮流分配（哈希取模会撞，实测四个角色全落到同一个音色）；
- 旁白 zh-CN-XiaoxiaoNeural / en-US-AriaNeural，语速 -8%；角色语速 +4%、音高 +8Hz；
- `--scene-plan` 可以是 director 的 `book_out/scene_plan.json`：按数字序号把 `page_001` 映射回 `p01`，`total_sec` 缺省时按各段时长求和；
- `start_sec/end_sec` 是整片全局时间轴：每段时长累加，不按段重置；`total_sec` 等于各段时长之和；
- **0 行直接返回 2**，不再输出空计划。

## 事后校验

- `build_prompts.py`：缺首帧、正文为空、id 映射失败都会写进 `problems` 并返回 2；必须处理完再进 S2。
- `build_audio_plan.py`：0 行返回 2；`channels.narration/dialogue` 与 `lines` 时长单调递增可自检。
- 本 skill 不判“画面是否真静止/动作是否够大”，那是 `seedance-relay-producer/qc.py` 的 `zoom_check`/`motion_check` 与 `picturebook-scene-director` 的接缝验收负责。

**尚未做的**：音乐与音效通道（MM-StoryAgent 有独立轨），以及真正的 TTS 合成与混音。
