# picturebook-scene-grouper — 按文本把页聚成场景

把一本绘本的**页**重新组织成**场景**：一页不一定是一场，几页也可能是同一场。
判据以**文字**为主（官方要求成片必须覆盖正文每页文字），画面特征只作辅助。

## 为什么需要它

`picturebook-scene-director` 只在"相邻两页能插值"时合并镜头，判据是像素级相似度
（色彩距离 ≤0.15、光流一致性 ≥0.90），真实绘本几乎不可能满足 —— 实测 cn01 12 页
→ 12 段，合并率 0。场景这一层需要按叙事来划，而不是按像素。

## 输入 / 输出

```
输入：books/<id>/reader/book.json      # 每页 text / characters / scene
可选：page 目录（prepare_from_book 的产物）——给了就把 scene_tag 写回 page_manifest.json

输出：scene_plan.json
  {
    "book_id", "lang", "scene_count", "page_count",
    "scenes": [
      { "scene_id": "sc_01", "pages": ["p02","p03"], "text_chars": 96,
        "est_seconds": 10, "shot_plan": [{"kind":"merged_2p","pages":[...],"duration_sec":10}],
        "transition_out": {"type":"MATCH_CUT","bridge":"none"} }
    ],
    "coverage": {"pages_assigned":12, "pages_total":12, "text_pct":1.0},
    "boundaries": [{"at":"p04","signals":["时间/地点转换词:后来"],"score":2}]
  }
```

## 四条判定规则（可解释、可复现）

1. **续页不切**：上一页文字没有句末标点（。！？.!?…），这一页必须留在同一场；
2. **硬边界**：本页开头出现时间/地点转换词（第二天/后来/这时/回到家/Meanwhile/Later…）→ 切；
3. **角色全换**：两页的角色集合完全不重叠（且都非空）→ 切；
4. **场长上限 3 页**（官方 Q33：跨 2–3 页可合并）→ 到第 4 页必须切。

空文本页（OCR 漏页）不新开场景，挂到当前场。

## 场景内镜头规划

- 1 页 → 单页镜头 5 s
- 2 页 → 合并成一个镜头 10 s（双锚定）
- 3 页 → 前两页合并 10 s + 单页 5 s

## 场景间转场建议

- 角色有交集 → `MATCH_CUT`（0 s 硬切，构图呼应）
- 角色无交集 → `DESIGNED_TRANSITION`，桥接帧 `black`（跨场）或 `occlude`（同场换景）

真值仍由 `picturebook-scene-director` 的接缝判定兜底；本 skill 只定"哪几页是一场"。
