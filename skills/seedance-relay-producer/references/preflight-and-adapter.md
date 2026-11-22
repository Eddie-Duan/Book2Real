# 输入契约、字段适配器与 Preflight 预检规程

## 1. 输入目录物理契约
每个页面目录 `pages/page_###/` 必须包含且仅依赖以下四个核心文件：
1. `clean_keyframe.png`：上游去字与安全区补全后的纯净 PNG 图像，是本流水线的**唯一视觉真值**。
2. `page_manifest.json`：记录原文、文字块位置、补全项与锁定分辨率的元数据清单。
3. `qa_report.json`：上游质检报告，决定该页是否具备进入视频生成的准入资格。
4. `seedance_prompt.txt`：已定稿的英文图生视频运动提示词。

## 2. Fail-Closed 适配器准则 (`inputs/adapter.py`)
- **禁止猜测**：若 `page_manifest.json` 或 `qa_report.json` 出现 `MANIFEST_ALLOWED_TOP_KEYS` / `QA_ALLOWED_TOP_KEYS` 之外的未知顶层键，立即抛出 `ContractSchemaError` 并退出。
- **字段探测**：接入新批次上游输出前，先执行：

```bash
python inputs/adapter.py --pages-dir ./pages --probe
```

检查打印出的实际键名是否与 `inputs/adapter.py` 映射表一致。

- **允许的附加元数据键**：`MANIFEST_ALLOWED_TOP_KEYS` 除适配器要读的字段外，还接受下游导演层要用的只读元数据，例如 `time_tag`（时间标签，`picturebook-scene-director` 靠它判断"白天→夜晚"这类时间跳跃）、`scene_context`、`characters`。这类键只做白名单放行、不参与本流水线判定；新增同类只读元数据时，按"上游会写 + 下游只读"的成对关系一次性登记，不要临时放过。

## 3. Preflight 预检门禁（每条检查四件套）

| 检查项 | 量化指标 | 合格阈值 | 失败判定与动作 |
| --- | --- | --- | --- |
| 文件完整性 | 4 个契约文件是否存在且非空 | 全部存在且字节数 `> 0` | 判定 `BLOCKED`，记录缺失文件名 |
| 图像可解码与色彩 | `PIL.Image.open().verify()` 与 mode | 可正常解码且为 `RGB` / `RGBA` | 判定 `BLOCKED`，拒绝入列 |
| 跨页尺寸一致性 | `PNG (W, H)` vs `manifest.locked_resolution` | `ΔW == 0` 且 `ΔH == 0` | 判定 `BLOCKED`，报错 `ERR_DIMENSION_DRIFT` |
| 上游质检准入 | `qa_report.verdict` & `ocr_residual` & `ssim` & `intrusion` | `verdict == "PASS"` 且 `ocr == 0` 且 `ssim >= 0.9990` 且 `intrusion == 0` | 判定 `BLOCKED`，跳过生成节省 API 预算 |
| 提示词结构完整性 | 是否包含 `[Camera]`、`[Subject Motion]`、`[Environment`、`[Negative Constraints]` 四段 | 长度 `10 ~ 1000` 字符；缺段发 `WARNING` | 空文件或超长判定 `BLOCKED` |

## 4. `preflight.json` 输出规范

`preflight.py` 必须输出 `out/preflight.json`，包含：

- `total_pages`、`ready_count`、`blocked_count`
- `relay_enabled`（布尔值）与 `relay_edges`（每条相邻边的计算指标与降级决策）
- `cost_estimate`（按当前目标分辨率与每条边推导出的 `5s`/`10s` 计算出的精确预估总金额）
- `pages` 列表（每页 `status: READY | BLOCKED` 及具体阻断原因）
