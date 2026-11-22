# 多页首尾帧接力规则与降级链协议

## 1. 核心拓扑：首尾帧双锚定 (Dual-Anchor Relay)
- 对于已通过预检的有序页面序列 $P_1, P_2, \dots, P_M$：
  - 第 $i$ 页（$1 \le i < M$）生成边 $E_i: K_i \to K_{i+1}$。
  - 任务参数绑定 `first_frame = K_i` 且 `last_frame = K_{i+1}`。
  - 末页 $P_M$ 绑定 `first_frame = K_M`，不传 `last_frame`，并在独立提示词附加文件中追加自然收束描述：
    `, gentle natural deceleration at the end of the clip, holding final composition steadily.`
- **严禁尾帧回灌**：绝不允许提取第 $i$ 段生成视频的末帧像素作为第 $i+1$ 段的输入首帧。

## 2. 不接力语义与代理判据
当相邻两页 $P_i$ 与 $P_{i+1}$ 满足以下任一条件时，**禁止使用 Level A 双锚定**（强行双锚定会导致画面发生诡异的融化变形 Morphing）：
1. **显式语义跳变**：`page_manifest.json` 中的 `scene_tag` 发生变化，或 `character_ids` 交集为空（主角更换），或 `seedance_prompt.txt` 包含 `page turn` / `cut to` / `next morning` 等转场词。
2. **图像特征代理判据（当元数据缺少场景标签时启用）**：
   - 计算 $K_i$ 与 $K_{i+1}$ 的 `DINOv2` 特征余弦距离 $D_{\text{dino}} = 1 - \cos(f(K_i), f(K_{i+1}))$（CPU 兜底模式下使用 HSV 色彩直方图巴氏距离 + 结构边缘特征加权距离）。
   - 若使用代理判据，必须在 `preflight.json` 与 `qc_report.md` 中显式标记 `"判定依据: 代理判定 (Proxy Metric)"`。

## 3. 三阶降级链与可计算触发阈值

| 降级级别 | 生成策略 | 拼接策略 | 触发条件（全部满足则命中该级） |
| :--- | :--- | :--- | :--- |
| **Level A (双锚定)** | `first=K_i, last=K_{i+1}` | 直接硬切无缝拼接（0s 转场） | 1) 模型支持 `last_frame`；<br>2) 宽高比差 `<= 0.01`；<br>3) 无语义场景跳变；<br>4) $D_{\text{dino}} \le 0.35$ |
| **Level B (单锚定+软转场)** | `first=K_i, last=None` | `ffmpeg xfade=fade` (`0.4s`) | 未满足 Level A，但满足：<br>1) 宽高比差 `<= 0.01`；<br>2) 非硬切场景且 $0.35 < D_{\text{dino}} \le 0.55$ |
| **Level C (独立硬切)** | `first=K_i, last=None` | 直接硬切（0s 转场） | 宽高比不一致，或明确翻页/换场景，或 $D_{\text{dino}} > 0.55$ |

## 4. Level A 动态时长自适应与防形变锁定
- 当 $D_{\text{dino}} \le 0.20$：画面元素高度一致（如同一场景角色微动），采用 `duration = 5s`。
- 当 $0.20 < D_{\text{dino}} \le 0.35$：画面存在明显位移或视角推进，5s 极易产生几何扭曲，自动升级为 `duration = 10s`，并生成 `out/page_###/prompt_override.txt` 追加：
  `, smooth natural motion transition between start and end composition, maintaining rigid anatomical and architectural structure without morphing.`

## 5. 接缝门禁 (Seam Gate)
- 对所有 Level A 片段，抽取其生成视频的最后一帧 $F_{\text{last}}$，与目标关键帧 $K_{i+1}$ 做双指标校验：
  - **指标**：`SSIM(F_last, K_{i+1})` **且** `pHash(F_last, K_{i+1})` 汉明距离
  - **阈值**：`SSIM >= 0.88`（`480p` 下放宽至 `>= 0.85`）**且** `pHash <= 6`（`config.yaml` 的 `seam_max_phash_dist`）
  - **为什么必须叠加 pHash**：SSIM 是全图统计量，对"背景不变、角色局部错位"极不敏感——实测角色横向偏移 `165px` 时 `SSIM` 仍有 `0.9749`，而 pHash 距离已到 `32`。只靠 SSIM 会让接力断裂静默通过。
  - **失败动作**：若任一指标未达标，说明模型未在规定时长内走到目标尾帧，判定为 `SEAM_MISMATCH`，触发单页返修（优先将 `5s` 升至 `10s` 或更换 `seed`），**严禁用后期溶解掩盖失败的接缝**。
