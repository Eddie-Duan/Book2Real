# 火山方舟 Ark Seedance 视频生成 API 接入与错误码规范

## 1. 接口端点与鉴权
- **创建异步生成任务**：`POST https://ark.cn-beijing.volces.com/api/v3/contents/generations/tasks`
- **查询任务状态与结果**：`GET https://ark.cn-beijing.volces.com/api/v3/contents/generations/tasks/{task_id}`
- **鉴权头**：
  - `Authorization: Bearer ${ARK_API_KEY}`
  - `Content-Type: application/json`
- **安全约束**：密钥仅允许从环境变量 `ARK_API_KEY` 读取，任何日志文件及标准输出中必须脱敏为 `sk-***XXXX`。

## 2. 请求体契约（图生视频 & 首尾帧双锚定）

> **模型与 2.5 的差异（先读这段）**
> - 默认模型：`doubao-seedance-2-5-flash-261115`；高质量档 `doubao-seedance-2-5-260628`。
> - 下面示例里的 `ratio` 后缀只在 1.x / 纯文生视频下有效。**2.5 带 `first_frame`（或首尾帧）时不允许下发 `ratio`**，否则服务端返回
>   `400 InvalidParameter.TaskTypeConstraint: For first-frame or first-last-frame generation, the output ratio follows the first-frame image`。
>   配置 `ratio_follows_first_frame: true` 后，`generate.py` 既不拼 `--ratio`，也会从请求体里移除该字段。
> - 2.5 默认 `generate_audio: true`（自动生成环境音），绘本片用 `generate_audio: false` 关掉；实测成片会带一条 `has_audio_stream: false` 的无音轨。

### 单页图生视频（仅首帧）

```json
{
  "model": "doubao-seedance-2-5-flash-261115",
  "content": [
    {
      "type": "text",
      "text": "<seedance_prompt.txt 原文> --resolution 1080p --duration 5 --seed 42001 --watermark false"
    },
    {
      "type": "image_url",
      "image_url": {
        "url": "data:image/png;base64,<BASE64_K_N>"
      },
      "role": "first_frame"
    }
  ]
}
```

### 多页接力生成（首尾帧双锚定）

```json
{
  "model": "doubao-seedance-2-5-flash-261115",
  "content": [
    {
      "type": "text",
      "text": "<seedance_prompt.txt 原文> --resolution 1080p --duration 5 --seed 42001 --watermark false"
    },
    {
      "type": "image_url",
      "image_url": {
        "url": "data:image/png;base64,<BASE64_K_N>"
      },
      "role": "first_frame"
    },
    {
      "type": "image_url",
      "image_url": {
        "url": "data:image/png;base64,<BASE64_K_N_PLUS_1>"
      },
      "role": "last_frame"
    }
  ]
}
```

## 3. 参数后缀规则与比例回退策略

- `--resolution`：`480p` | `720p` | `1080p`
- `--ratio`：由输入 `clean_keyframe.png` 的 `width / height` 自动计算最匹配合法比例（集合：`["16:9", "4:3", "1:1", "3:4", "9:16", "21:9"]`）。
- **严格原画幅原则**：传入图像本身不做任何裁切或补边。若接口因非标准比例拒绝请求（HTTP 400 `InvalidParameter.Ratio`），回退至绝对误差最小的合法比例并在 `run_log.jsonl` 记录 `ratio_fallback`，随后在 `qc.py` 中核验输出帧是否发生非法裁剪或拉伸。
- `--duration`：`5` 或 `10`（单位：秒）。
- `--seed`：每页固定确定性整数（`base_seed + page_idx * seed_step + attempt`），保证完全可复现。
- `--watermark false`：请求关闭平台角标水印。

## 4. 轮询状态机与下载时效

- **轮询策略**：初始间隔 `3.0s`，退避系数 `1.5`，最大单次间隔 `20.0s`，单任务总超时上限 `600s`。
- **任务状态流转**：
  - `queued` / `running`：继续轮询。
  - `succeeded`：提取 `content.video_url`（部分版本返回 `data.video_url`），**必须在 60 秒内完成流式下载落盘**（临时签名 URL 存在过期销毁机制）。
  - `failed` / `cancelled`：提取 `error.code` 与 `error.message` 写入任务记录。

## 5. 常见错误码与处置动作

| HTTP / 业务错误码 | 含义 | 自动化处置动作 |
| --- | --- | --- |
| `401 Unauthorized` | `ARK_API_KEY` 无效或过期 | 立即终止全流程，提示检查 `.env` |
| `429 TooManyRequests` | QPS 或并发数超限 | 指数退避重试（上限 5 次），并发度降为 1 |
| `400 UnsupportedRole.last_frame` | 当前模型端点不支持尾帧 | 自动降级当前边为 Level B（仅首帧 + 软转场），记录降级原因 |
| `400 ContentModerationBlocked` | 提示词或画面触发风控 | 标记该页 `ESCALATE`，移入 `manual_review/` |
| `500 / 503 InternalError` | 服务端瞬时故障 | 保留 `task_id` 退避重试，不计入画质返修次数 |
