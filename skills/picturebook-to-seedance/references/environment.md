# 工具链与运行前提

## 1. 各阶段用的东西

| Stage | 工具 | 类型 | 说明 |
|---|---|---|---|
| 0 | Pillow / OpenCV | 本地 CPU | 尺寸、色彩空间、单多页路由 |
| 1 | VLM（Gemini 2.5 Pro / GPT-4o 级） | API | 语种判定、艺术字与画中字转写、bbox |
| 1 | Surya OCR | 自托管（CPU 可跑） | 像素级坐标精修、多语种 |
| 2 | 同 Stage 1 的 VLM | API | 原文 × 画面差异分析 |
| 3 | SAM2 | 自托管（GPU 推荐） | bbox → 实例掩膜 |
| 3 | LaMa | 自托管（GPU） | 平滑区修复 |
| 3 | Flux.1 Fill [dev] / SDXL Inpaint | 自托管（GPU，≥12–16 GB 显存） | 复杂插画结构边缘修复 |
| 4 | VLM + OpenCV / scikit-image / ColorThief | 混合 | 风格 DNA、主色盘、纸纹方差 |
| 5 | Grounded-SAM + Depth Anything V2 | 自托管（GPU） | 保护区分割 |
| 5 | Flux.1 Fill + Redux / IP-Adapter | 自托管（GPU） | 风格参考注入下的补全 |
| 6 | `scripts/qa_check.py`（numpy + Pillow） | 本地 CPU | 尺寸、SSIM、像素差、保护带入侵 |
| 6 | Surya OCR / PaddleOCR | 本地 CPU | 交付图文字残留复检 |
| 7 | LLM 提示词模块 | API | 运动提示词生成 |

## 2. 硬前提

- **GPU**：Stage 3 的 Flux Fill 与 Stage 5 是显存大头，建议 ≥ 16 GB；只跑 LaMa + SAM2 可降到 8 GB。
- **模型权重需要自备**：SAM2、LaMa、Flux.1 Fill、Grounded-SAM、Depth Anything V2、Surya。本 skill 不附带权重、不附带 ComfyUI 工作流 JSON、不附带推理脚本，需要自行下载安装。
- **磁盘**：Flux.1 Fill dev 单权重约 20+ GB（fp16），全套装下来预留 60 GB 比较稳妥。
- **单页耗时**：GPU 全流程约 3–8 分钟；无 GPU（LaMa CPU + 人工涂抹掩膜）约 20–40 分钟。

## 3. 无 GPU 时的降级路径（组装时补充，非原始文档内容）

目标：不装本地扩散模型，也能出可用的去字 + 补全结果。

1. **掩膜**：`scripts/make_text_mask.py`（OCR bbox + 笔画检测 + 自适应膨胀），完全 CPU。
2. **去字**：用云端图像编辑模型（Gemini 图像编辑 / Nano Banana 级别）按 `references/prompts.md` §C.2 编辑；输出尺寸常与输入不一致，**必须先缩回原尺寸再回贴**，否则合成会错位。
3. **回贴**：`scripts/composite.py` 只让掩膜内的像素变化，掩膜外强制等于原图——这一步是像素锁定的唯一保险。
4. **补全**：同一图像编辑模型，按 §C.3 只在 `Edit_Mask` 内编辑，再回贴一次。
5. **质检**：`scripts/qa_check.py`。

代价：云端模型会整图重编码，非编辑区无法做到逐像素相等；`qa_check.py` 的 SSIM 门槛需从 0.9995 放宽（建议 0.98 起，按实测校准），并在 manifest 标注 `degraded_mode: true`。**追求"零篡改"就必须走本地 + 像素回贴。**

## 4. 版本核对

Seedance 的时长、分辨率、首帧格式按平台当前可选版本核对；不要把版本号写进提示词模板。
