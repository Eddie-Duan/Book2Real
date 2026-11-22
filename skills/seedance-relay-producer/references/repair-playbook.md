# 缺陷返修映射表与重生成影响面分析

## 1. 缺陷类型 → 自动化返修动作映射表

| 缺陷代码 (Defect Code) | 根因诊断 | 自动化返修动作 (Repair Action) | 参数调整规则 |
| :--- | :--- | :--- | :--- |
| `FIRST_FRAME_SHIFT` / `ASPECT_RATIO_VIOLATION` | API 比例回退导致首帧裁切或比例畸变 | 修正 `--ratio` 映射并更换 `seed` 重生成 | `seed = seed + step` |
| `SEAM_MISMATCH` | 双锚定模式下末帧未在规定时长内走到 `K_{N+1}` | 若当前为 `5s`，自动升级为 `10s` 并生成平滑锁定提示词覆盖文件；若已为 `10s`，更换 `seed` 重生成 | `duration: 5 -> 10`，生成 `prompt_override.txt` + diff |
| `TEMPORAL_DRIFT` | 动作幅度过大导致中段角色/画风崩坏 | 若当前为 `10s` 且非长距离接力，降为 `5s` 抑制累积漂移；追加角色结构锁定词并换 `seed` | `duration: 10 -> 5`（非长接力时），`seed += step` |
| `TEXT_DETECTED` | 模型幻觉生成动态字幕或气泡文字 | 更换 `seed` 重生成（不改原提示词正文，仅依赖强负向约束重新采样） | `seed += step` |
| `WATERMARK_DETECTED` | 角落检出平台水印 | 若位于安全区外围，先调用 `cleanup.py` 执行保护带安全清理；否则更换 `seed` 重生成 | 先 `cleanup`，失败则 `seed += step` |
| `HALLUCINATED_OBJECT` | 画面出现白名单之外的无关物体 | 更换 `seed` 重生成 | `seed += step` |

## 2. 重生成影响面分析 (Blast Radius Analysis)
报告中每当发生返修重生成，必须显式输出以下影响面评估：
- **场景一：双锚定接力模式（Level A，标准默认）**
  - **影响范围**：**仅限当前页 $P_N$ 本身（影响面 = 1 页）**。
  - **技术原因**：$P_N$ 的入口锚定 $K_N$、出口锚定 $K_{N+1}$ 均为固定静态 PNG 文件。重生成 $P_N$ 后，只要其末帧通过接缝门禁（`SSIM >= 0.88`），与 $P_{N+1}$ 的入口 $K_{N+1}$ 依然无缝闭合，**上游 $P_{N-1}$ 与下游 $P_{N+1} \dots P_M$ 均无需重生成**。
- **场景二：Level B / Level C 独立生成模式**
  - **影响范围**：**仅限当前页 $P_N$ 本身（影响面 = 1 页）**。重新执行 `concat.py` 刷新转场即可。
- **场景三：违规降级为尾帧像素回灌（本 Skill 严禁，仅作风险警示）**
  - **影响范围**：**级联影响 $P_N, P_{N+1}, \dots, P_M$ 共 $(M - N + 1)$ 页**。一旦启用回灌，重生成 $P_N$ 会改变其尾帧像素，导致后续所有页面的入口帧失效，产生严重成本倍增。

## 3. 人工升级出口 (`out/manual_review/page_###/`)
当单页返修次数达到 `max_retries_per_page`（默认 `3` 次）仍未通过 `qc.py` 时，状态置为 `ESCALATE`，自动打包以下文件供人工介入：
- `failed_clip.mp4`：最近一次失败的视频文件
- `contact_sheet.jpg`：包含参考关键帧与 `0/25/50/75/100%` 采样帧的高清对比拼图（红框标注异常帧）
- `escalation_report.json`：命中规则、历次尝试的 `seed`/参数/得分轨迹、以及针对性的提示词或手工剪辑修改建议
