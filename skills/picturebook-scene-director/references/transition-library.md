# 转场库（12 种，禁用书页翻页）

**总原则**：自然 ≠ 无痕。一个转场要成立，三件事缺一不可：

1. **叙事动机** —— 观众能说出"为什么要在这里换"（时间过了 / 他看向了别处 / 他走进了门）。
2. **画面机制** —— 有一个具体的视觉动作在桥接（甩、遮、暗、白、推、形状对齐）。
3. **生成路径** —— 能在 Seedance 里真的做出来，不是只能后期叠加。

**书页翻页（PAGE_TURN）不在库里，且永久禁用。** 它把"这是本书"的元信息塞进画面，破坏沉浸感，也不是电影语言。

| # | 手法 | realization | 桥接帧 T | 适用语义 | 本 skill 自动产出 |
| :-- | :--- | :--- | :--- | :--- | :--- |
| 01 | 动作切 | `IN_CLIP` | 无 | 连续 + 换景别/机位 | 判定 + 提示词 |
| 02 | 匹配剪辑 | `IN_CLIP` | 无 | 跳变 + 图形呼应 | 判定 + 提示词 |
| 03 | 视线匹配 | `IN_CLIP` | 无 | 连续或跳变 + 有视线 | 判定 + 提示词 |
| 04 | 景别递进 | `IN_CLIP` | 无 | 连续 + 面积比越界 | 判定 + 提示词 |
| 05 | 甩镜 | `SHARED_T` | `whip_blur` | 跳变 + 水平位移大 | 判定 + 合成 T |
| 06 | 前景遮挡 | `SHARED_T` | `foreground_occlude` | 跳变 + 空间穿越 | 判定 + 合成 T |
| 07 | 叠化 | `POST` | 无 | 跳变 + 时间流逝 | 判定（xfade 0.5–1.0s） |
| 08 | 黑场 | `SHARED_T` | `black` | 跳变 + 入睡/落幕 | 判定 + 合成 T |
| 09 | 白场 | `SHARED_T` | `white` | 跳变 + 光/苏醒/回忆 | 判定 + 合成 T（需手动指定） |
| 10 | 推镜穿入 | `SHARED_T` | `push_detail` | 跳变 + 有门/洞/窗 | 判定 + 合成 T（需手动指定） |
| 11 | 独立桥接镜头 | `BRIDGE_CLIP` | 无 | 跳变 + 空间跨度过大 | 仅 schema，需人工补素材 |
| 12 | 延时/光影流转 | `SHARED_T` 或双锚定 | 无 | 连续 + 同构图不同时段 | 判定 + 10s 双锚定 |

> "仅 schema" 的意思是：`scene_plan.json` 支持它，但规划器不会自动生成，需要你补一段真实桥接素材。诚实标注比假装能做强。

---

## 01 动作切 `CUT_ON_ACTION`

- **动机**：动作正在进行时换景别/机位，借运动掩盖切换。
- **机制**：上段收在动作速度峰值（不落定），下段从峰值后 1–2 帧起手，动量与屏幕方向一致。
- **Prompt 出点**：`accelerate toward peak velocity at the final frames without settling`
- **Prompt 入点**：`begin already in mid-motion with matching momentum and screen direction`
- **禁用**：前后运动方向相反（破 180° 轴线）；两页都是完全静止构图。
- **失败特征**：上段先停稳再切（二次起动感）；左右方向跳反。

## 02 匹配剪辑 `GRAPHIC_MATCH_CUT`

- **动机**：用形状/轨迹/色彩的相似，把两个时空缝在一起（树冠 → 满月，滚苹果 → 滚皮球）。
- **机制**：切点两侧主形态轮廓 IoU ≥ 0.60，或质心偏差 ≤ 5% 画幅宽。
- **Prompt**：`lock the {motif} at {normalized_coords}, matching silhouette scale and trajectory across the cut`
- **禁用**：两页主体毫无几何对应。
- **失败特征**：切点瞬间主体大小/位置跳位，呼应断掉。

## 03 视线匹配 `EYELINE_MATCH`

- **动机**：交代角色在看什么，建立主体与环境/新角色的空间关系。
- **机制**：上段角色转头凝视画外某方向，下段切到视线落点（主观镜头或客体），守 180° 轴线。
- **Prompt 出点**：`turns head and gazes intently off-screen right, holding eyeline steady`
- **Prompt 入点**：`reveal the object of attention from a matching viewing angle`
- **禁用**：下段物体朝向与上段视线方向冲突。
- **失败特征**：往右看，下段物体从左侧入画且朝左。

## 04 景别递进 `SHOT_SCALE_STEP`

- **动机**：从环境交代聚焦到情绪细节，或从细节拉出展示环境。
- **机制**：面积比 > 1.5× 或 < 0.67×，且机位水平偏移 ≥ 30°（避免同轴跳切）。
- **Prompt**：`steady {shot_size} framing angled 35 degrees off-axis, subtle micro-push-in`
- **禁用**：机位不变只等比放大（Jump Cut 跳帧感）。
- **失败特征**：背景透视线没变，只有主体突然变大。

## 05 甩镜 `WHIP_PAN` — `SHARED_T`

- **动机**：同一空间内快速转移注意力；紧迫、惊讶的节奏切换。
- **机制**：高速横摇产生方向性模糊，模糊峰值帧作为共享锚点。上段 `last=T_blur`，下段 `first=T_blur`，接缝 0s。
- **Prompt 上段**：`rapid lateral whip pan right building strong directional motion blur into the final frame`
- **Prompt 下段**：`starting from rapid lateral motion blur, whip pan decelerates smoothly to lock onto the scene`
- **禁用**：抒情、静谧、睡前安抚段落（甩镜会破坏情绪）。
- **失败特征**：模型没生成模糊，只是平移——可在 T 处补 `0.15s` 后期方向模糊兜底。

## 06 前景遮挡 `FOREGROUND_WIPE` — `SHARED_T`

- **动机**：跟随角色穿过树干、灌木、门框、人群，空间自然过渡。
- **机制**：风格一致的前景深色/虚化物体横扫并占满 85%–95% 画幅，形成自然遮挡。上段 `last=T_occlude`，下段 `first=T_occlude`。
- **Prompt 上段**：`camera tracks sideways as a close foreground tree trunk sweeps across to fill 90% of the frame`
- **Prompt 下段**：`starting behind a close-up tree trunk, camera continues tracking laterally to reveal the new clearing`
- **禁用**：两页横向运动方向相反。
- **失败特征**：遮挡物笔触/配色脱离原画风；两侧矢量反向（撞墙反弹）。

## 07 叠化 `CROSS_DISSOLVE` — `POST`

- **动机**：**只用于明确的时间流逝**（黄昏→夜、四季、梦境/回忆）或极柔和的抒情缓转。
- **机制**：后期 `ffmpeg xfade=fade` 0.5–1.0s 混合。
- **Prompt**：`calm, minimal camera drift, gentle ambient breathing motion suitable for a soft lyrical dissolve`
- **禁用**：同场景景别跳切、动作切、掩盖比例/身份不匹配。**全片不超过 2 次。**
- **失败特征**：重叠期出现双头/重影，像 PPT 翻页。

## 08 黑场 `FADE_THROUGH_BLACK` — `SHARED_T`

- **动机**：入睡、熄灯、章节落幕、跨整夜。
- **机制**：上段压暗到纯黑 `T_black`，下段从纯黑渐亮。接缝 0s。
- **Prompt 上段**：`lighting dims smoothly and uniformly into complete darkness over the final 0.6 seconds`
- **Prompt 下段**：`emerging gently from complete darkness as warm morning light illuminates the room`
- **禁用**：白天快节奏连续动作。
- **失败特征**：暗部色带断层；上段没压到纯黑，接缝亮度跳变。

## 09 白场 `FADE_THROUGH_WHITE` — `SHARED_T`

- **动机**：魔法光芒、推门迎向阳光、苏醒、回忆闪回。
- **机制**：高光扩散过曝到纯白 `T_white`，再从柔白光晕显影。
- **Prompt 上段**：`warm sunlight blooms across the frame, gently brightening into pure soft white at the end`
- **Prompt 下段**：`fading in smoothly from soft luminous white glow to reveal the sunlit meadow`
- **禁用**：暗夜/压抑段落。
- **失败特征**：硬闪白（违背绘本柔和感）；过曝把纸纹洗掉后恢复不回来。

## 10 推镜穿入 `PUSH_THROUGH` — `SHARED_T`

- **动机**：进入微观世界、穿过窗/树洞/门缝进入另一空间。
- **机制**：镜头沿光轴前推到某局部纹理 `T_detail`，下段从同纹理拉出。本 skill 用“中心 1/3 裁切放大 3×”合成 T，**粗糙**——真实制作建议用原图高清局部或补一张特写。
- **Prompt 上段**：`smooth continuous dolly push-in toward the dark doorway texture until it fills the frame`
- **Prompt 下段**：`smooth dolly pull-back emerging from the textured shadow into the new interior space`
- **禁用**：画面中心没有通道/窗口/纯色纹理。
- **失败特征**：前推时背景透视扭曲融化；下段拉出纹理跳变。

## 11 独立桥接镜头 `BRIDGE_CLIP`

- **动机**：空间跨度过大（森林→卧室），需要一个物理过渡动作交代因果（爪子推门、风吹落叶）。
- **机制**：独立生成 5s，成片截 1.0–2.0s 插入，两端 0s 硬切。
- **Prompt**：`Close-up insert shot in exact matching {style_dna} style: a small paw pushes open a wooden door, warm light spilling through`
- **禁用**：全片桥接镜头超过总页数 20%。
- **失败特征**：写实 3D 质感脱离画风；没裁剪导致 5s 拖沓。

## 12 延时/光影流转 `TIME_LAPSE_LIGHT`

- **动机**：同地点长时间等待（山坡上从下午等到星空）。
- **机制**：主体静止或极微呼吸，光位与色温连续演变到目标页。若同构图，直接 `first=K_i, last=K_j, 10s`，并锁死主体。
- **Prompt**：`locked tripod framing, character remains peacefully seated while afternoon sunlight shifts into deep twilight blue and stars appear`
- **禁用**：两页角色姿态或机位大跳。
- **失败特征**：光影变化时角色跟着背景一起变形重绘。

---

## 选择顺序（写进代码的判断顺序）

```
时间流逝词 + 色彩差大        → 07 叠化 / 08 黑场
水平位移 > 30% 且同比例      → 05 甩镜
有明确前景物 + 横移         → 06 前景遮挡
中心有通道/暗部纹理          → 10 推镜穿入
空间跨度过大且无共享元素      → 11 桥接镜头
图形/轨迹对齐               → 02 匹配剪辑
以上都不满足                 → 08 黑场（最后兜底，不要用叠化兜底）
```

最后一条很重要：**兜底用黑场，不要用叠化。** 黑场是"明确的时间/章节分隔"，观众接受；叠化是"我懒得想"，观众感到廉价。
