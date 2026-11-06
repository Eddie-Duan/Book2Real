# 部署：本地 demo 机 vs GPU 服务器

本仓库的 demo 目标机器是 **RTX 5070 Laptop / 8 GB 显存**。下面两台机器各跑什么、
哪些必须用 API，写清楚，避免在 8 GB 上硬跑 14B/20B 白白浪费时间。

## 方案 A：本地 demo 机（8 GB）

### 定位

本地只做三件事：

1. 互动绘本（OCR / 拼音 / 点读 / TTS / 纸偶）；
2. 2.5D 分层（SAM 轮廓 + 深度位移 + 背景修补），以及 2–4 秒循环；
3. 质量基线：少量关键镜头走 API 验证效果，不在本地硬跑大模型。

### 软件栈

| 层 | 选择 | 本机实际 |
|---|---|---|
| 系统 | Windows 11 | 已具备 |
| 驱动 | NVIDIA 570+ | 582.05 |
| CUDA | 12.8+ | `torch 2.11.0+cu130`（可用） |
| Python | 3.11 venv 更稳；3.14 亦可 | 3.14.4 |
| GPU 推理 | torch / diffusers / transformers | 已装 |
| 工作流 | CLI（`book2real cast/layers`），不是 ComfyUI | 够用，可后期接 ComfyUI |
| 视频 | FFmpeg | 8.1.1 |

### 模型清单（8 GB 可跑）

| 用途 | 模型 | 实测 |
|---|---|---|
| 人物轮廓 | `facebook/sam-vit-base` | 0.7 s/页，约 2 GB |
| 深度 | `depth-anything/Depth-Anything-V2-Small-hf` | 0.3 s/页，95 MB |
| 局部修补 | `stable-diffusion-v1-5/stable-diffusion-inpainting` | 0.6–1.5 s/图，2.5 GB |
| 文字识别 | RapidOCR（CPU） | 已在用 |
| 旁白 | edge-tts（GPL，仅 demo） | 已在用 |

**本地不装**：Qwen-Image-Edit 20B、Wan2.2 14B、HunyuanVideo 13B、32B LLM。
不是"能不能加载"，是"跑一帧要几分钟，没有迭代价值"。

### 需要的 API（本地做不了的部分）

| 用途 | 推荐 | 备注 |
|---|---|---|
| 多图角色编辑/补肢 | Qwen-Image-Edit API | 一致性最强 |
| 风格化插画/背景延展 | Recraft API | 风格参考是强项 |
| 关键镜头 I2V | Kling Elements / Veo 3.1 | 4–8 个镜头即可 |
| 商用 TTS | Azure / 火山 / MiniMax | 替换 edge-tts |

## 方案 B：GPU 服务器

| 档 | GPU | 显存 | 能跑 |
|---|---|---|---|
| B0 | RTX 4090 / 5090 | 24/32 GB | Wan2.2 5B、FLUX FP8、Qwen-Image-Edit 4bit |
| B1（推荐） | L40S / A6000 | 48 GB | Qwen-Image-Edit FP8、Wan2.2 14B FP8、HunyuanVideo FP8 |
| B2（产能） | A100 / H100 | 80 GB | 多模型并发、Wan Animate 14B、BF16 全量 |

### 整机

- CPU 16–32 核，内存 128 GB，系统盘 1 TB，模型盘 2 TB NVMe；
- Ubuntu 22.04 + Docker + NVIDIA Container Toolkit；
- CUDA 12.8 / PyTorch 2.7+。

### 软件架构

```
Nginx -> FastAPI(任务) -> Redis + Celery
        -> ComfyUI headless（图像/视频工作流）
        -> Diffusers 微服务（Qwen / Wan / FLUX）
        -> ffmpeg worker
   Postgres 元数据 + MinIO 资产 + Prometheus 监控
```

### 分工原则

| 任务 | 放哪 |
|---|---|
| 2.5D/分层/纸偶 | 本地或 CPU 节点即可 |
| 局部补图/去字/扩背景 | 服务器 Qwen-Image-Edit / FLUX Kontext |
| 角色一致性静帧 | 服务器 Qwen 多图 + IP-Adapter |
| 高光 I2V | 优先 Kling/Veo API；量大再迁 Wan |
| 整本批量 I2V | 不推荐（角色会漂），至少人审 |

## 授权提醒

- 书本身有版权；扫描、训练、生成衍生内容都需要授权；
- FLUX.1 Kontext dev 非商用；HunyuanVideo 是腾讯社区许可；LTX 自有协议；
- edge-tts 是 GPL-3.0，商用必须替换；
- 不要用未授权插画训 LoRA 再商用。
