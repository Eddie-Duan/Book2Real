# 生成提示词模板（中英双语）

每个模板都分「中文对照版」与「英文生产版」。**真正送模型的是英文版**，中文版用于人工核对语义。
花括号 `{}` 是运行时注入的变量，来自 `page_manifest.json` / `style_dna.json`。

---

## C.1 识字与空间解析（Stage 1）

用于 VLM。输出严格 JSON，供 Stage 2/3 直接消费。

### 中文对照版

```text
你是一名专业的绘本排版分析与多模态 OCR 专家。请仔细检查这张绘本图像，完成以下任务：
1. 【语种与语境】：自动识别画面文字的原始语种（zh / en / mixed），并用一句简短的话概括本页画面内容。
2. 【全量逐字转写】：提取图像中出现的所有可见文字，包括正文段落、艺术字标题（描边、渐变、弧形、竖排、手写体）、页码、水印以及画面内物品上的文字（招牌、信件、书脊字等）。外文必须保持原语种逐字转写，严禁翻译。
3. 【精准定位与分级】：为每个独立文字块输出归一化边界框 [ymin, xmin, ymax, xmax]（0-1000 范围），并标记层级：
   - body_text（正文）
   - art_title（艺术字标题）
   - page_number（页码）
   - watermark（水印/出版标识）
   - diegetic_text（画中字，如路牌/信件/书名）
4. 【强约束与禁止项】：
   - 保留原有标点符号与换行逻辑。
   - 若字符因低对比度、花体变形或纹理遮挡无法 100% 确认，严禁猜测补全，必须设 confidence < 0.85 并标记 needs_review: true。
   - 严格输出合法 JSON，禁止输出任何解释性文字。
```

### 英文生产版（送入 VLM）

```text
You are an expert picture book layout analyst and multimodal OCR specialist. Inspect the provided picture book page image and execute the following tasks:
1. [Language & Context]: Detect the primary language of the text ("zh", "en", or "mixed") and summarize the visual scene in one concise sentence.
2. [Exhaustive Verbatim Transcription]: Transcribe ALL visible text in the image, including body text, artistic titles (outlined, gradient, curved, vertical, handwritten), page numbers, watermarks, and all diegetic text on scene objects (signs, letters, book spines). Transcribe foreign text strictly in its original language—DO NOT translate.
3. [Precise Localization & Classification]: For each distinct text block, output normalized bounding boxes [ymin, xmin, ymax, xmax] (0-1000 scale) and assign a layer type:
   - "body_text"
   - "art_title"
   - "page_number"
   - "watermark"
   - "diegetic_text"
4. [STRICT CONSTRAINTS & PROHIBITIONS]:
   - Preserve exact original punctuation and line breaks.
   - If any character is low-contrast, heavily stylized, or obscured by textures and cannot be confirmed with 100% certainty, DO NOT guess; set "confidence" < 0.85 and flag "needs_review": true.
   - Output STRICTLY valid JSON only. No markdown fences or conversational text.
```

> 注：VLM 的 bbox 只当**候选框**用，别直接当掩膜。笔画级掩膜走 Stage 3 的 SAM2 + 笔画检测链路，或直接用 `scripts/make_text_mask.py`。

---

## C.2 去字还原（Stage 3）

只用于复杂纹理区域的局部扩散修复；纯色/规律纹理区优先用 LaMa，不必上扩散模型。

### 中文对照版

```text
【任务】：无缝清除掩膜区域内的所有文字（包括排版字、艺术字及画中字），自然还原被遮挡的原始插画底图。
【风格与纹理匹配】：严格延续掩膜周边像素的插画媒介、笔触纹理、纸张颗粒感、色彩过渡与光照强度（注入参数：{style_dna_summary}）。
【强约束】：
- 仅在白色掩膜区域内进行背景或物体原有纹理的无缝还原。
- 100% 保留掩膜外部的所有原始像素、线条与色彩，边界零接缝。
【严格禁止】：
- 禁止在修复区域内生成任何新的文字、字母、数字、符号、伪字形或水印。
- 禁止添加任何原图中不存在的新物体、新角色或装饰元素。
- 禁止改变原图分辨率、宽高比及周围物体的轮廓或颜色。
```

### 英文生产版（送入 Flux Fill / SDXL Inpaint）

```text
Seamlessly remove all text within the masked area (including typography, artistic titles, and diegetic text on objects) and reconstruct the underlying illustration naturally.
Match the exact artistic medium, brushwork texture, paper grain, color gradient, and lighting of the surrounding unmasked pixels: {style_dna_summary}.

[STRICT CONSTRAINTS]:
- Perform seamless texture and surface reconstruction ONLY inside the masked region.
- Preserve 100% of original pixels, outlines, and colors outside the mask with zero visible seams.

[PROHIBITIONS - NEGATIVE CONSTRAINTS]:
- NO text, NO letters, NO numbers, NO symbols, NO pseudo-characters, NO watermarks, NO signatures.
- DO NOT add any new objects, characters, or decorative details not present in the original artwork.
- DO NOT alter the image dimensions, aspect ratio, or the contours and colors of existing surrounding elements.
```

---

## C.3 风格锁定内容补全（Stage 5）

### 中文对照版

```text
【任务】：在原画布指定的低信息安全区掩膜内，自然绘制以下原文描述但原画缺失的元素：{completion_target_description}。
【风格 DNA 强制绑定】：
- 媒介与笔触：{medium_and_brushwork}
- 轮廓线规范：{line_work_spec}
- 配色与光影：{color_palette_and_lighting}
- 纸张与肌理：{paper_texture_spec}
{character_anchor_clause_if_multipage}
【强约束】：
- 新增元素必须融入原画的透视比例、景深关系与纸张肌理中，保持原画幅尺寸绝对不变。
- 仅在掩膜允许的低信息安全区内绘制，严格避开原有主体保护边界，不得压盖、移动或修改任何已有元素。
【严格禁止】：
- 禁止出现任何文字、字幕、页码、标牌字或水印。
- 禁止出现"贴纸感"硬边缘、AI 3D 渲染质感、照片级写实光影或违和高饱和色块。
- 禁止修改、重绘或删除原图中的任何已有角色或前景物体。
```

### 英文生产版（送入 Flux Fill + Redux/IP-Adapter）

```text
Naturally illustrate the missing narrative elements within the designated low-information safe-zone mask on the original canvas: {completion_target_description}.
Strictly adhere to the original illustration's visual DNA:
- Medium & Brushwork: {medium_and_brushwork}
- Line Work: {line_work_spec}
- Color Palette & Lighting: {color_palette_and_lighting}
- Paper Grain & Texture: {paper_texture_spec}
{character_anchor_clause_if_multipage}

[STRICT CONSTRAINTS]:
- Integrate new elements naturally into the original perspective scale, depth, and paper grain while keeping exact canvas dimensions unchanged.
- Generate ONLY inside the active safe-zone mask; strictly respect protected subject boundaries without overlapping, shifting, or altering any existing elements.

[PROHIBITIONS - NEGATIVE CONSTRAINTS]:
- NO text, NO typography, NO captions, NO page numbers, NO sign lettering, NO watermarks.
- NO sticker-like cut-out edges, NO 3D CGI rendering, NO photorealistic lighting, NO oversaturated AI artifacts.
- DO NOT modify, repaint, scale, or remove any existing characters or foreground objects from the original image.
```

---

## C.4 Seedance 图生视频运动提示词（Stage 7）

### 中文对照版

```text
【镜头运动】：{camera_movement}（物理机位移动；不要用 zoom 把画面整体放大；近景用切镜）。
【主体主动作】：{subject_action}（从本页文字取一个看得见的动作：拿起、放下、搭、转身、走向、蹲下、抱起；呼吸/眨眼只作副动作）。
【环境与新增元素动态】：{environment_and_added_motion}（描述环境氛围及 Stage 5 补全元素的细腻动态）。
【画风与质感锁定】：严格保持首帧的手绘媒介质感与纸张纹理静止稳定，色彩与光影柔和连贯，呈现细腻的绘本动态感。
【负向约束】：画面中严禁出现任何文字、字幕、页码、水印或对话框；角色面部与肢体结构保持稳定不变形，无闪烁伪影，无画面裁切或比例跳变。
```

### 英文生产版（送入 Seedance I2V）

```text
[Camera]: {camera_movement} (e.g., Ultra-slow, gentle push-in, maintaining steady original picture book framing).
[Subject Action]: {subject_action} (a visible plot action from the page text: pick up, put down, stack, turn, walk, crouch, hug; breathing/blinking only as secondary motion).
[Environment & Added Elements]: {environment_and_added_motion} (e.g., Delicate ambient motion of background foliage, soft light particles, and newly added narrative elements).
[Style Lock]: Strictly preserve the hand-crafted artistic medium, brushwork, and static paper grain of the first frame, keeping lighting and colors cohesive with gentle picture-book pacing.
[Negative Constraints]: NO text, NO subtitles, NO captions, NO page numbers, NO watermarks, NO speech bubbles; no facial distortion, no limb morphing, no flickering artifacts, no sudden crop or aspect ratio jump.
```

### 填参规则（很重要）

- `{camera_movement}`：优先"极缓慢推/移"，别写快切、甩镜——绘本首帧一动就崩。
- `{subject_action}`：只写首帧里**已经存在**的角色；必须写一个看得见的动作，禁止只写呼吸/眨眼/重心转移。
- `{environment_and_added_motion}`：**只给会自然运动的东西写动态**（鸟、风、水、光、烟）；路牌、房子、桌椅这类静物只写"光影与氛围微动"，否则模型会把结构扭变形。
- 提交前按平台实际可选版本核对时长（5s/10s）与分辨率上限；不要把版本号写进提示词。