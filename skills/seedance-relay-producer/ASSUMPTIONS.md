# 工程架构假设与设计边界说明

1. **Ark Seedance API 交互模式**：默认对接火山方舟 Ark 异步视频生成接口 `POST /api/v3/contents/generations/tasks` 与查询接口 `GET /api/v3/contents/generations/tasks/{id}`。图像传输默认采用 `data:image/png;base64,...` 内联编码，消除公网图床依赖；单张关键帧 Base64 编码后限制在 `10MB` 以内。
2. **默认模型与参数格式**：默认模型为 `doubao-seedance-2-5-flash-261115`（可在 `config.yaml` 中替换为 `doubao-seedance-2-5-260628` 或其他 Seedance 端点；老模型 `doubao-seedance-1-0-pro-250528` 仍可用）。参数注入采用 Ark 官方的文本指令后缀格式（`--resolution 1080p --ratio 4:3 --duration 5 --seed 12345 --watermark false`），同时在 `ark-seedance-api.md` 中保留 JSON 结构化参数对照说明。**2.5 的例外**：带 `first_frame`（或首尾帧）时不允许下发 `ratio`，输出比例强制跟随首帧图，因此配置项 `ratio_follows_first_frame: true` 会让 `generate.py` 跳过 `--ratio` 并从请求体移除该字段。
3. **动态物体白名单提取**：`seedance_prompt.txt` 中明确描述的环境与新增动态元素（如落叶、波纹、炊烟、萤火虫、风吹草动）经关键词解析进入单页允许动态集合；除此之外视频中凭空出现的高置信度独立前景物体（如现代汽车、未在绘本出现的人物）判定为“无中生有”。
4. **低分样片预跑标准**：Cheap-First 阶段默认采用 `480p`（成本约为 `1080p` 的 25%），正式全量输出采用 `1080p`。
5. **预算熔断阈值**：单批次预估总花费默认上限为 `20.00 RMB`（可在 `config.yaml` 调整），超出时必须显式传入 `--yes` 确认，防止误操作批量消耗额度。
6. **非接力降级默认转场**：当相邻两页满足 Level B 降级条件（需软过渡但无法双锚定）时，默认采用 `0.4s` 交叉溶解（`xfade=fade`），保持绘本叙事柔和度。
7. **音轨齐平策略**：Seedance 图生视频默认输出无音轨视频流。为保证 `concat` 拼接稳定性，所有片段统一注入标准静音音轨（AAC, 48000Hz, Stereo），并在最终成片中保留合规的“AI 生成”标识说明。
