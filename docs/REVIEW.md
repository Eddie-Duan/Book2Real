# 审查：还值得做的改进

按"投入产出比"排序。每条写清楚**现状证据 → 建议 → 代价**，不做空泛建议。

## 一、已验证可用，值得立刻推广

### 1. 用 p17 左臂这套流程批量做表情/动作（最高优先级）

刚跑通的流程（`agent part` 单 cell → `review` 通过 → `agent motion` 出循环）：

```
p17 left_arm:  area 2447 px, components 1, paper 0.0%, pivot on piece → accept
```

**建议**：对全书按"是否存在落在单色背景上的分离肢体"筛一遍，自动生成候选清单，
逐个跑 `part` + `review`，通过的自动出 GIF。

**代价**：纯 CPU，每页几秒；没有 API 费用。

### 2. `review` 应该成为 CI 的一部分

它今天拦下了两个错误：
- 我的矩形手臂：`components 2, paper 0.236, pivot_on_piece false`
- p16 整页切：`paper_share 0.929`

**建议**：`books/<id>/reader/` 每次重建后自动跑
`agent review` + `castcheck`（重复肖像）+ `critic`（正文覆盖），任一不过就拒绝写盘。

**代价**：一次构建多 1–2 秒。

## 二、产品上明显缺失（我看代码时发现的）

### 3. 热点只覆盖 7/31 屏，靠文字自动补只能补 4 个

实测：`pages mentioning a character but with no hotspot: 19 / 31`。
我写了 `book2real/spots.py`（规则：正文只点名一个角色 + 该页最大人形框 → 该角色），
**实际只补出 4 个**（p06-2、p09-2、p11-2、p17），因为多数页 PageScout 的框是
"整页大块"或根本没有框，而不是单个角色。

**结论**：这条不能靠规则硬补，需要真正的角色检测（VLM 或 SAM + 身份再认）。
短期替代：用 `agent probe` 的局部色块（能分出脸/头/衣服）合成"人物候选框"，
替代 PageScout 的整页连通域。

### 4. 词语解释只有 70 条，平均每页 2.2 个字

`words total 70 / 31 页`。对"教认字"这个定位太少。

**建议**：
- 短期的：`segments()` 已经用 jieba 分词了，把"本页出现的名词/动词"自动加入词表，
  释义用现有 `DEFAULT_GLOSSARY` 的风格补；
- 长期的：接一个 LLM 批量生成"儿童可懂的一句话释义"，一次跑完整本（成本约几分钱）。

**代价**：短期 1 小时；接 LLM 需要你决定是否用 API。

### 5. 拼音已经有了，但缺"朗读跟读"

点字有 TTS，但没有"读到哪个字高亮"。

**建议**：edge-tts 提供 `WordBoundary` 事件（`edge_tts.Communicate` 的 `stream()` 里有
`offset/duration`）。用它把"朗读"变成卡拉OK式高亮。这是本书最像"智能"的一处，且**不需要任何图像模型**。

**代价**：半天，纯本地。

## 三、技术债（不修会咬人）

### 6. `book2real/layers.py` 与 `vision/layers.py` 同名

第一次 import 时我就踩了（`relative import beyond top-level package`）。
**建议**：把 `book2real/layers.py` 改名为 `layers_legacy.py`，或加 `__all__` 说明。

### 7. `runs/` 被 gitignore，但 `reader/layers/` 在仓库里（24 页 ≈ 21 MB）

目前每页有 `_bg.jpg` + `_fg.png` + `_depth.png` + json。体积可接受，但**每次重建都改二进制**，
diff 会很大。**建议**：`layers/` 也进 gitignore，改为构建产物（`book2real layers` 可重跑）。

### 8. Recraft 客户端缺少幂等

现在每次调用都重新生成。**建议**：按 `(图片哈希 + 掩膜哈希 + prompt)` 缓存响应，
避免重复烧钱。今天就有一次 400 重跑，如果成功就是白花 $0.04。

## 四、需要你拍板的

| 事项 | 选项 |
|---|---|
| 是否接 LLM 批量生成词义 | 是（几分钱）/ 否 |
| 是否接受"人工补墨线"再自动切 | 是（2 分钟/页）/ 只做能自动切的页 |
| `layers/` 是否移出 git | 是 / 否 |
| 是否做卡拉OK式朗读高亮 | 做 / 先不做 |

## 五、我不建议做的

- **继续调分割阈值**：p17 右臂、p05 黑发已证明是"轮廓不存在"，调参无解；
- **用 Recraft 补语义明确的空白**：两次实测都画错（鸟、写实人）；
- **整本 I2V**：角色会漂，且成本与你的 demo 阶段不匹配。
