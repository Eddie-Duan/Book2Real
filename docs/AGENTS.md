# Agent 化：人物、动作、修复

一条链，五个 agent，全部可重跑：

```
PageScout → CastClerk → Rigger → MotionDirector → Critic
```

```bash
python -m book2real analyze --book books/charlie_lola   # 先出 reader 包（OCR / 拼音 / 清字）
python -m book2real cast    --book books/charlie_lola   # 再跑人物链
```

真书《我特别了解豚鼠》本次实测 **12.9 s**（22 个阅读屏，纯 CPU，无模型下载；耗时随机器和负载波动）。
代码在 `book2real/cast/`，测试在 `tests/test_cast.py`（10 个 CPU 用例）。

## 现状：什么已经自动化，什么还缺

| Agent | 今天怎么做 | 已自动化 | 仍需模型 / 人工 |
|---|---|---|---|
| `PageScout` | 键色抠底 + 连通域 + 形状/面积过滤；命名只在"一页只有一个名字、一个主体"时成立 | 主体 bbox、面积、主色、颜色签名、置信度 | 图内文字标签回填到像素；多主体页的身份判定 |
| `CastClerk` | 以 `curation.json` 的人工描述为锚，合并 PageScout 的高置信命名，产出人物墙 + 肖像 | 肖像裁切（4:5 只补边不裁脸）、出场页列表、别名 | 跨页同一角色的**身份再认**（需要 CLIP/DINO 或人脸聚类；颜色直方图在绘本书上不可靠，实测余弦 0.3–0.5） |
| `Rigger` | 键色 alpha → 取与选框 IoU 最大的主体 → 开运算找细突起当手臂 → 肩/腕 pivot → 裁到人物框 | 手臂/头的切件、pivot、安全摆幅、置信度；**刚体**（无网格形变） | 半透明边缘、与背景同色的角色（需要 SAM / rembg） |
| `MotionDirector` | 动词/情绪词表 + 引号对白 → beat JSON | idle / wave / point / nod / shrug / bounce / speak，时长与幅度 | 更细的情绪与节奏（可换 LLM，契约不变） |
| `Critic` | 规则指标 + 阈值，不通过就降级 | 眼睛成对、切件边缘背景残留、关节开洞、接缝突变、动作舒适区、正文覆盖率 | 用失败样本继续调阈值 |

**诚实结论**：一页里"谁是谁"这件事，纯几何 + 颜色做不到。当前实书的产出是
5 个人物卡（肖像来自人工 curation）、1 个被 Critic 拒掉的切件、1 个被 Critic
接受的**无名候选切件**（p17 的特写）。要让它自己认出"p17 那个就是劳拉"，
下一步是给 `CastClerk` 接一个特征模型，而不是继续加规则。

## 产物

```
books/<id>/reader/
├─ cast/entities.json         PageScout：每页实体（bbox / 面积 / 主色 / 签名 / 置信度）
├─ cast/clerk.json            人物墙 + 每个角色的候选框（assignments）+ 无名候选
├─ cast/rig/<cid>.json        Rigger：kind / parts / pivot / amp / key / confidence / notes
├─ cast/candidates/<id>.json  无名但已切好的候选（等着被认领）
├─ cast/critic.json           Critic：逐角色、逐页面的分数与理由
├─ cast/report.json           各阶段耗时、计数、降级、被清理的旧文件
├─ characters/characters.json 阅读器真正加载的那份（卡片 + rig + 候选 + 动作索引）
├─ characters/*.jpg|png       肖像、身体板、手臂/头切件、preview
├─ cast/managed_character_files.json  上次运行登记过的产物清单（见下）
└─ motion/<page>.json         每屏一份 beat
```

**拆页与取景**：`analyze` 把长页拆成多屏后，每屏写一张自己的裁切图 `art/<page>-<k>.jpg`，
完整页仍保留在 `art/<page>.jpg`（记为 `art_page`）。Rigger 等 agent 永远读 `art_page`，
所以拆页不会把一个人物切成两个"角色"。

**文件清理规则**：`characters/` 里只有"上次清单登记过的文件"和"本次刚写出的文件"可能被删除，
且只删本次不再引用的那些；目录里手工放的文件、旧版本产物不会被误删。
清单由 `_prune()` 读写，失败时整条链直接报错，不做猜测。

## 关键契约

```jsonc
// CharacterCard（characters/characters.json 的 characters[]）
{ "id": "lola", "name": "劳拉", "role": "妹妹", "desc": "...",
  "pages": ["p03", "p06-1", "p06-2", "p16", "p17"],
  "portrait": "art/char_lola.jpg",
  "confidence": 0.78, "evidence": "curation|spot|text|none",
  "rig": { "kind": "one_arm|paper_puppet|head_nod|portrait|none",
           "confidence": 0.88,
           "art": "characters/lola_body.jpg", "art_w": 508, "art_h": 438,
           "key": [227, 73, 56],
           "parts": [{ "name": "arm_l", "image": "characters/lola_arm_l.png",
                       "pivot": [0.2087, 0.8356], "amp": 14.2 }],
           "notes": ["no neck line found; head split estimated at 30% height"] } }
```

```jsonc
// 无名候选（cast/candidates/<id>.json，阅读器里显示为"agent 切好的候选"）
{ "id": "anon_p17", "page": "p17", "box": [0.19, 0.14, 0.60, 0.86],
  "area": 0.35, "rig": {}, "preview": "characters/anon_p17_preview.jpg",
  "verdict": { "accept": true, "scores": {}, "reasons": [] } }
```

```jsonc
// MotionPlan（motion/<page>.json）
{ "page": "p17", "character": "lola", "duration": 4.46, "emotion": "neutral",
  "beats": [
    { "t": 0.0,  "action": "idle",  "dur": 4.46, "loop": true },
    { "t": 1.11, "action": "speak", "dur": 1.8, "text": "波特……彻底不见了！" }
  ] }
```

`MotionDirector` 只输出这份 JSON：网页按 beat 驱动 DOM 动画，视频管线将来可以直接
复用同一份 beat 渲染帧，不需要认识"故事"。

## Critic 指标（已实现，规则先行）

| 指标 | 判据 | 不通过时 |
|---|---|---|
| `face_completeness` | 头部切件必须含**成对的双眼**（两个近似等大的深色块并排）；肖像只需能读出脸 | 降级到 portrait |
| `cut_face` | 切出来的主体上半身必须有脸 | 判为"不是人物"，整个 rig 丢弃 |
| `bg_residue` | 切件边缘 8px 环带里键色占比 > 8% | 判失败（切到了背景） |
| `hole` | 关节旋转 20° 后**新增**的空洞占联合轮廓 > 2% | 判失败（肩上开了洞） |
| `seam` | 旋转后边缘梯度 / 静止边缘梯度 > 2.0 | 记警告（接缝过硬） |
| `motion_naturalness` | 手 8–25°、头 2–8°、1–3.2 Hz、`t` 落在 `duration` 内 | 该 beat 判失败 |
| `text_coverage` | 正文出现 >=3 次的主要角色必须在人物墙上 | 报 `missing` |

阈值都是模块级常量（`critic.py` 顶部），改规则不用改流程。

## 人工只做两件事

1. **认领候选**：agent 已经把无名人物切好、验收过了。要把 p17 那个特写认给劳拉，
   在 `books/<id>/curation.json` 里加一行（既有的人工校订层，不是代码）：

   ```jsonc
   { "characters": [ { "id": "lola", "name": "劳拉", "rig_from": "p17" } ] }
   ```

   下一次 `python -m book2real cast` 会把该页主体切成劳拉的刚体 puppet，
   Critic 仍然可以否决它。

2. **改描述**：`role` / `desc` / `portrait` 永远以 curation 为准，agent 不覆盖人工文字。

## 已知边界

- 带字页、照片页、与背景同色的角色：抠不干净，`bg_residue` 会拒掉，最终退回肖像。
- 一页多个角色且正文点了多个名字：不猜，全部留作无名候选。
- 切件只做**刚体**：手臂绕肩、头绕颈旋转，没有网格形变，所以不会抖、不会拉丝。
