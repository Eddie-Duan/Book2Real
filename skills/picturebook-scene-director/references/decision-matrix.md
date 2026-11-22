# 双轴判定矩阵、阈值与冲突裁决

判定分两步，顺序不能反：**先定语义轴（这是不是一个连续的事情），再定视觉轴（这两张图能不能物理插值过去）。** 顺序反了就会犯 relay-producer 的错——只量距离，把"同场景换景别"和"跨场景"混成一类。

## 1. 语义轴

`semantic_continuous = true` 当且仅当以下全部成立：

1. `scene_tag` 相同（或两页都缺，走代理判定）；
2. `character_ids` 有交集，或至少一方为空但画面主体可对应；
3. `seedance_prompt.txt` 里没有时间/空间跳变词。

跳变词表（正则见 `plan_scenes.py` 的 `TIME_JUMP_RE`）：
`next day`、`next morning`、`that night`、`meanwhile`、`later`、`years later`、`suddenly`、`第二天`、`次日`、`到了晚上`、`转眼`、`与此同时`、`多年后`。

**"suddenly" 是弱信号**：它常表示同一场景内的突发动作（熊突然抬头），不一定是跨场景。当前实现把它当跳变词，会偏向保守（多切一刀）。如果你的绘本大量用 "suddenly" 描写同场景动作，把它从表里删掉。

### 代理判定（元数据缺失时）

```
if feature_distance > 0.35 or color_bhattacharyya > 0.22:
    semantic_continuous = false
    basis = "PROXY_DETECTION"   # 置信度上限 0.78
```

## 2. 视觉轴

`visual_interpolable = true` 当且仅当五道闸门同时通过：

| 指标 | 阈值 | 物理含义 | 超了会怎样 |
| :--- | :--- | :--- | :--- |
| `abs(ar_i - ar_j)` | ≤ 0.01 | 两张图能不能放进同一个画幅 | 插值必然裁切或拉伸，角色比例会变 |
| `subject_shift_pct` | ≤ 0.25 | 主体质心位移占画幅比例 | 模型要"凭空搬运"主体，双锚定会融化 |
| `scale_area_ratio` | ∈ [0.67, 1.50] | 景别面积比（0.67 以下是大远景，1.5 以上是特写） | 景别大跳被插值 = 人物逐渐胀大/缩小，恐怖谷 |
| `flow_consistency` | ≥ 0.90 | 前后向光流循环一致性 | 不一致 = 有遮挡/新物体出现，插值会造出幻觉 |
| `color_bhattacharyya` | ≤ 0.15 | HSV 联合直方图巴氏距离 | 光位/色温变了，插值会把两个时段搅成一个 |

`feature_distance > 0.35` 单独一条也直接判"视觉差异大"，因为它是整体外观距离，前面几项漏掉的情况它兜底。

## 3. 四象限与落地

| 语义 | 视觉 | 决策 | 落地 | 说明 |
| :--- | :--- | :--- | :--- | :--- |
| 连续 | 可插值 | `CONTINUOUS` | 双锚定 `first=K_i, last=K_j`，5s 或 10s | 唯一允许"一个镜头里自然衔接"的情况 |
| 连续 | 不可插值 | `HARD_CUT` | 两段独立，接缝 0s | 用动作切 / 视线匹配 / 景别递进给出切点理由 |
| 跳变 | 可插值（构图呼应） | `MATCH_CUT` | 两段独立，接缝 0s | 图形匹配 `shape_iou ≥ 0.60`，或特征距离 ≤ 0.26 且需人工确认 |
| 跳变 | 不可插值 | `DESIGNED_TRANSITION` | `SHARED_T` 或 `BRIDGE_CLIP` 或 `POST` | 甩/遮/黑/白/推/桥接；叠化只用于时间流逝 |

### CONTINUOUS 的时长选择

```
duration = 10 if (feature_distance > 0.20 or subject_shift_pct > 0.10) else 5
```

5s 是给"两页几乎一样、只差一点点动作"的；一旦有可感知的位移或外观差，10s 才有足够时间插值。反过来，5s 硬插一个 20% 位移必然糊。

## 4. Tie-Breaker（一票裁决，按顺序）

1. **比例不一致**（`|Δar| > 0.01`）→ 否决 CONTINUOUS 与 MATCH_CUT，强制 `DESIGNED_TRANSITION` + 留白。
2. **角色身份断裂**（同 `character_id` 相似度 < 0.85，或角色集合无交集且非视线匹配）→ 否决 CONTINUOUS。
3. **光位突变**（主光方向差 > 30° 或色温巴氏 > 0.15）→ 即使 `scene_tag` 相同也当时间跳跃，禁止双锚定。
4. **动作弧超过单次上限**（估算 > 10s）→ 不许压缩进一段 10s，在动作峰值处拆两段 `HARD_CUT / CUT_ON_ACTION`。

## 5. 决策的理由必须可复算

`seam_decisions.json` 里每条都带 `metrics`（原始数值）与 `rationale`（哪一条闸门没过）。返修时看的是这两个字段，不是"感觉不对"。任何改判都要能指向某个指标或某条 tie-breaker；指不出来就是拍脑袋。
