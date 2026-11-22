---
name: picturebook-to-seedance
description: 把绘本单页（或多页）扫描图转换成「零文字、按原文补全画面缺口、原画幅与原画风零篡改」的 Seedance 图生视频首帧关键帧，并附运动提示词。用于：绘本动态化、有声书/短片视频制作、插画全量去字（含艺术字、页码、水印、画中字）、文字驱动的画面补全。输入中文/英文单页或多页图像，输出 clean_keyframe.png、page_manifest.json、qa_report.json、seedance_prompt.txt。不适用：文字压盖角色五官或核心交互手部且遮挡率 > 30%；漫画多格分镜页（需先拆格）；未裁切的跨页扫描件（需先从中缝裁开）。
---

# 绘本 → Seedance 关键帧流水线

先擦字，再补画，全程不动已有画面，最后交给 Seedance 当首帧。

## 1. 铁律（违反任一条即停）

1. **画幅锁定**：输出与原图像素尺寸、宽高比 100% 一致。不扩画布、不裁剪、不缩放。
2. **只增不改、只增不删**——精确定义：
   - 已有元素（角色、动物、道具、建筑、轮廓线、光影关系）不得移动、缩放、改色、重绘、删除；
   - `Edit_Mask`（补全落点）内的背景像素允许被替换，但必须与周边纹理连续，并在 `page_manifest.json` 记录坐标与面积；
   - 上述两个掩膜之外的像素，必须逐像素等于原图。
3. **去字即还原**：Stage 3 只推断并填补被文字遮挡的底图，不美化、不加细节、不改局部光影。
4. **落点唯一**：新增元素只能落在低信息安全区（天空、空白地面、纯色墙面、留白），且与任何已有主体保持 ≥ 25px。
5. **原文直驱**：自动识别语种（zh / en / mixed），外文绘本按原文做差异分析，不翻译。
6. **风格 DNA 强制绑定**：每次生成都挂原图作参考并注入 `style_dna` 描述；禁止 3D 渲染感、摄影质感、硬边贴纸感。
7. **交付图零文字**：正文、艺术字标题、页码、水印、出版标识、画中字全部清除。画中字（招牌、信件、书名）去字后按原材质补非可读笔触（木纹、涂漆、压痕），不留纯色空块。
8. **存疑即熔断**：置信度不足或触及阻断条件 → `NEEDS_HUMAN_REVIEW` + 诊断报告，禁止猜测推进。
9. **补全可溯源**：每个新增元素必须对应一段 `source_text_span`；原文没写的不许加。

## 2. 流水线

```
输入（1..N 页）
  ↓
Stage 0 预检 / 单多页路由 ── 短边<1024、疑似跨页 ──► 提示确认，必要时重扫或裁切
  ↓
Stage 1 全量识字（正文/艺术字/页码/水印/画中字）── 低置信 ──► 人工框选修正
  ↓
Stage 2 原文 × 画面差异分析 ──► 补全清单 + 落点可行性
  ↓
Stage 3 掩膜 + 分区去字 ── OCR 复检非空 / 相似度不达标 ──► 加膨胀重跑
  ↓
Stage 4 风格 DNA（多页时 + 角色锚点）
  ↓
Stage 5 安全区补全 + 保护区像素回贴 ── 入侵保护带 / 贴纸感 ──► 调掩膜或权重重跑
  ↓
Stage 6 量化质检 ── 任一项 Fail ──► 回退对应 Stage
  ↓
Stage 7 首帧封装 + 运动提示词
```

## 3. 分步 SOP

### Stage 0 预检与资产登记
- **输出**：`work/{page}_00_normalized.png`（sRGB 8-bit，尺寸不变）、`manifest.asset_info`。
- **做**：记录 `locked_resolution`，后续每阶段校验偏差为 0 px；剥离异常 CMYK ICC；判定单/多页（N ≥ 2 时初始化 `series_bible.json`）。
- **通过**：短边 ≥ 1024 px、sRGB、单页构图。低于 1024 或宽高比异常（如 > 1.8）→ **提示用户确认是否跨页**，不直接阻断。
- **回退**：分辨率不足重扫；跨页先从中缝裁开。

### Stage 1 文字全量识别与定位
- **输出**：`work/{page}_01_ocr_overlay.png`、`manifest.text_blocks`。
- **工具**：VLM 负责语种判定、艺术字与画中字转写；Surya OCR 负责像素级坐标精修。二者字符编辑距离 > 0.2 → `low_confidence: true`。
- **难例**：描边字、渐变字、弧形/竖排、手写体、压在深色插画纹理上的字 → 送灰度反相 + CLAHE 增强副本交叉核验。
- **层级**：`body_text` / `art_title` / `page_number` / `watermark` / `diegetic_text`，全部 `action: REMOVE_AND_INPAINT`。
- **提示词**：`references/prompts.md` §C.1
- **通过**：VLM 与 OCR 匹配度 ≥ 95%；无未确认的低置信块。
- **回退**：输出 overlay 交人工框选/改字后重跑。

### Stage 2 原文 × 画面差异分析
- **输出**：`manifest.completion_items`。
- **做**：逐句拆具象实体、动作、环境细节、光源线索，核对画面是否已有；只取可视觉化的客观元素（"他很伤心"不编造物体）。为每条补齐项预判落点：原文字去字留白区优先，其次低频背景区。
- **无安全区**：整页画满 → 该条标 `SKIPPED_NO_SAFE_ZONE`，宁可不补。
- **通过**：每条都绑定 `source_text_span` 与可用安全区坐标。
- **回退**：代词指代不清时，多页模式检索前序页 manifest 消歧。

### Stage 3 掩膜生成与分区去字
- **输出**：`work/{page}_03_text_mask.png`、`work/{page}_03_clean_base.png`。
- **掩膜链**：bbox → SAM2 box prompt 得实例掩膜 → 与笔画检测（与局部背景的明度/饱和度差、边缘密度）取交 → **按笔画宽度自适应膨胀**（约短边 0.4%–0.8%，不是固定 10px）。渐变/镂空这类无实体笔画的字，用"与估计背景的差分"取掩膜。
- **羽化**：先向外膨胀，再**只向掩膜内部**羽化 3px。向外羽化会留下半透明文字残影。
- **分区**：平滑背景用 LaMa；复杂插画结构边缘用 Flux.1 Fill（denoise 0.55，出现杂物降到 0.40）。
- **像素回贴**：`Clean_Base = Original*(1-SoftMask) + Inpainted*SoftMask`。
- **熔断**：文字掩膜与角色五官/核心手部重叠 > 30% → `ERR_CRITICAL_OCCLUSION`。
- **提示词**：`references/prompts.md` §C.2
- **通过**：掩膜外 SSIM ≥ 0.9995（原始像素回贴后成立）；OCR 复检无「面积 ≥ 页面 0.01% 且置信度 ≥ 0.5」的文本框。
- **回退**：残影 → 膨胀 +5px 重跑；多余杂物 → 降 denoise 重跑。

### Stage 4 风格 DNA 与跨页角色锚定
- **输出**：`work/{page}_04_style_dna.json`（多页同步 `series_bible.json`）。
- **提取**：媒介与笔触、轮廓线宽与色值、Top-8 主色 HEX、光影方向、纸纹 Laplacian 方差。
- **分页**：单页跳过角色库；多页新角色建档，已建档角色继承前序页锚点，防画风漂移。
- **通过**：字段完整；主色盘匹配 ≥ 0.95。
- **回退**：大面积单色背景干扰 → 局部采样主体区域重算。

### Stage 5 安全区补全
- **输出**：`work/{page}_05_edit_mask.png`、`work/{page}_05_protected.png`、`work/{page}_05_completed_raw.png`。
- **保护区**：Grounded-SAM / Depth Anything V2 分割已有角色、动物、前景道具、核心建筑，外扩 25px。
- **安全区**：保护区之外 + 原文字去字留白 + Laplacian 方差 < 50 的低频区。清单为空或无安全区 → 直接透传 Stage 6。
- **生成**：原图作风格参考（权重 0.72；多页同角色额外挂角色图 0.65），注入 `style_dna` 英文描述；完成后做 10px 多频段接缝融合，再把保护区像素强制覆写回原图。
- **提示词**：`references/prompts.md` §C.3
- **通过**：保护区篡改率 0%；尺寸 100% 一致；新增元素无截断、无贴纸感。
- **回退**：贴纸感/光影脱节 → 参考权重提到 0.82 或微调安全区重跑。

### Stage 6 量化质检
- **输出**：`deliverables/{page}_clean_keyframe.png`、`deliverables/{page}_qa_report.json`。
- **做**：跑 `scripts/qa_check.py`，逐项过 `references/qa-checklist.md`。
- **回退**：按失败项回到 Stage 3 / 5。

### Stage 7 Seedance 首帧封装
- **输出**：`deliverables/{page}_seedance_prompt.txt`、定稿 manifest。
- **规格**：无损 PNG，保持原宽高比与分辨率；按你所用 Seedance 版本核对时长（5s/10s）与分辨率上限，不写死版本号。
- **提示词**：`references/prompts.md` §C.4。运动描述只写首帧已有或已补的元素；补的是静物（路牌、房子）时不要描述运动，只写光影/氛围微动，否则视频模型会扭曲结构。
- **通过**：无凭空动作；含防文字/防形变负向约束。

## 4. 质检

见 `references/qa-checklist.md`。四条硬门槛：

1. 尺寸偏差 = 0 px；
2. 非编辑区 SSIM ≥ 0.9995，且 99.9% 像素最大通道差 ≤ 2；
3. 保护区入侵 = 0 px²；
4. OCR 复检无有效文本框。

## 5. 交付物

```
deliverables/
├── {page}_clean_keyframe.png
├── {page}_seedance_prompt.txt
├── {page}_qa_report.json
└── {page}_manifest.json
```

中间产物留在 `work/`，**不要覆盖原图**；同一页重跑用 `v2/v3` 后缀，便于回滚比对。

## 6. 脚本

```bash
python scripts/make_text_mask.py --image work/p_00_normalized.png --blocks work/p_01_ocr.json \
    --out work/p_03_text_mask.png --dilate 6 --debug-overlay work/p_03_mask_check.png

python scripts/composite.py --original work/p_00_normalized.png --edited work/p_03_inpainted.png \
    --mask work/p_03_text_mask.png --out work/p_03_clean_base.png --feather 3

python scripts/qa_check.py --original work/p_00_normalized.png \
    --final deliverables/p_clean_keyframe.png \
    --text-mask work/p_03_text_mask.png --edit-mask work/p_05_edit_mask.png \
    --protected-mask work/p_05_protected.png --out deliverables/p_qa_report.json
```

依赖只有 numpy + Pillow。

## 7. 文件索引

| 需要什么 | 读哪个 |
|---|---|
| 四套生成提示词（中英双语） | `references/prompts.md` |
| 量化质检清单与阈值 | `references/qa-checklist.md` |
| manifest 字段说明 | `references/manifest-schema.md` |
| 工具链与运行前提 | `references/environment.md` |
| 完整 manifest 示例 | `assets/example_manifest.json` |
| 已知问题与待确认项 | `NOTES.md` |

## 8. 千万别做

- 别用生成模型「整图重绘」来去字——会顺手改掉画风。
- 别把补全元素画在角色身上或压住轮廓线。
- 别为了「补得更满」而翻译或改写原文。
- 别在交付图里留下任何字形，包括装饰性符号和出版社 logo。

---

## 9. 交接（Handoff）

> **DSH 事实：skill 之间不会自动互相触发。** skill 只是一份按需加载的指令集——模型看到 catalog 里的 `name` + `description`，自己决定加载哪一个；加载后按正文执行。没有“A skill 调用 B skill”的内建机制。所以接力靠三样东西落地：
> 1）这一步把成果写成**约定好的文件**；
> 2）下一步有一条**确定的命令**可跑；
> 3）失败时知道**回退到哪一步**。
>
> 本节的 Handoff 就是这三样的书面化。机器可读的那一半在 `living-picturebook/scripts/run_pipeline.py`（阶段化串联，失败即停）。

### 9.1 出口条件（不满足不许交接）

- 每页 `page_###/` 下四件套齐全：`clean_keyframe.png`、`page_manifest.json`、`qa_report.json`、`seedance_prompt.txt`。
- 每页 `qa_report.verdict == "PASS"`；有任何一页不是 PASS，下游导演会整本 BLOCKED，不要“先跑下去再说”。
- `page_manifest.json` 要同时满足两边的读取方：导演要 `scene_tag` / `character_ids` / `time_tag`；relay 的 `inputs/adapter.py` 还要求 `status`、`resolution{width,height}`，`qa_report.json` 要求 `ocr_residual_count` / `non_edited_ssim` / `protected_zone_intrusion_px`。以上游该 adapter 的必填键为准——缺键会被 fail-closed 拒收，交付前补齐。
- 全书画幅比一致（都是同一 ratio），否则 S3 判定会掉进硬切分支。

### 9.2 下一棒（改完页图之后）

```bash
# 单跑导演（推荐先看判定结果再花钱）
python _build/picturebook-scene-director/scripts/plan_scenes.py --pages-dir pages --out-dir book_out

# 或直接走整条链（dry-run 免费）
python living-picturebook/scripts/run_pipeline.py --pages-dir pages --out-dir book_out --dry-run
```

下游 skill：`picturebook-scene-director`（分镜）。**不要**跳过它直接调 relay——没有 scene_plan，就没有段、锚点和接缝，relay 只能按页各生成各的，拼起来是幻灯片。

### 9.3 不归本 skill 管的部分

- 翻页/交互层的提示词 → `ai-video-prompt-architect`（只在交互部分用）。
- PDF 拆页 → 调用方自己做（`pdftoppm -r 200 -png`），本 skill 从“一张单页图”开始。

### 9.4 失败回退

去字不过质检 → 修文字块坐标或换 Inpaint 工具，重跑本页；不要把“看起来还行”的页交给下游，下游的返修手段只有换 seed，治不了画错的输入。
