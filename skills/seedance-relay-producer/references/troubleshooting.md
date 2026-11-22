# 常见故障排查与轻量化降级指南

1. **无 NVIDIA GPU 或 PyTorch/CUDA 版本冲突**：
   - 运行 `python scripts/preflight.py --check-env` 查看硬件探测结果。
   - 当 `torch.cuda.is_available()` 为 `False` 时，`scripts/common.py` 会自动将 `DINOv2` 特征提取切换至 CPU 模式；若 `transformers` 或 `easyocr` 未安装，系统自动降级为基于 OpenCV 的**结构特征+色盘直方图距离**与**高频笔画边缘区域启发式检测**，并在 `qc.json` 与 `qc_report.md` 中显式打标 `"degraded_mode": true`。
2. **火山方舟 Ark API 返回 429 限流或连接超时**：
   - 检查 `config.yaml` 中的 `poll_initial_interval_sec`，适当调大初始轮询间隔。
   - `generate.py` 与 `poll_download.py` 具备完整的状态持久化（`pipeline_state.json`），中断后直接重新运行相同命令即可从断点续传，已成功的页面不会重复扣费。
3. **下载视频链接失效 (403 Forbidden)**：
   - Ark 返回的 `video_url` 为临时签名地址。`poll_download.py` 在检测到 `succeeded` 后会立即流式下载至本地 `raw_clip.mp4`；若因网络中断导致下载失败，可携带 `--refresh-url` 重新查询任务详情获取新链接或重新提交该页。
4. **`ffmpeg` 拼接报错 `Non-monotonous DTS` 或分辨率不一致**：
   - 确保所有片段均已通过 `qc.py` 的宽高比门禁；`concat.py` 在最终合并前会自动执行一次标准归一化转码（统一帧率 `24fps`、像素格式 `yuv420p`、采样率 `48kHz` 静音轨），彻底消除异构流拼接崩溃。
