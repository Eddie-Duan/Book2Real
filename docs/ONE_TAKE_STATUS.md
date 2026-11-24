# one-take 交付状态（2026-11-25）

## 1. 一句话

cn01 已产出 **150.08 s / 1280×720 / 24 fps / 有原生音轨** 的连续长片预览；5 段里 **4 段门禁 PASS**，最后 1 段只有一句"数积木"ASR 少识别两个数字。**火山账号已欠费（AccountOverdueError）**，第 5 段需充值后重跑一次即可收尾。

## 2. 实测产物

| 项 | 路径 |
|---|---|
| 150 s 预览（无损拼接，未重编码） | `runs/cn01/one_take/preview_150s.mp4` |
| 正式候选（同内容，chunk_05 未过门禁） | `runs/cn01/one_take/final_movie.mp4` |
| 分镜/门禁/提示词 | `runs/cn01/one_take/{shot_script.json,chunk_0*_gate.json,prompts/}` |
| 干净场景板/角色头 | `runs/cn01/one_take/assets/` |
| 联系表 | `runs/cn01/one_take/chunk_01..05_sheet.jpg` |

## 3. 门禁结果

| chunk | 时长 | verdict | 接缝 SSIM | 数字 zoom | OCR 文字 | 原书页 SSIM | ASR 覆盖 |
|---|---|---|---|---|---|---|---|
| 01 | 30.048 | PASS | —（首段） | 1.017 | 0 | 0.337 | 1.0 |
| 02 | 30.000 | PASS | 0.9699 | 1.008 | 0 | 0.373 | 1.0 |
| 03 | 30.000 | PASS | 0.9676 | 1.008 | 0 | 0.413 | 1.0 |
| 04 | 30.000 | PASS | 0.9457 | 1.007 | 0 | 0.355 | 1.0 |
| 05 | 30.000 | **FAIL** | 0.9698 | 1.006 | 0 | 0.328 | 0.926 |

chunk_05 唯一失败：旁白"一块、两块、三块、四块、五块、六块、七块、八块、九块……"被念得太快，ASR 听到"一块两块三块四块六块七块九块"（少 五、八）。其余 9 句对白/旁白全部 1.0。

## 4. 花销（本地估算，以火山后台为准）

| 项 | tokens | 上限估算（46 元/M） |
|---|---|---|
| chunk_01 | 648,900 | 29.85 |
| chunk_02–05 | 4 × 1,296,000 | 4 × 36.29 |
| 前期探针 | 774,197 | ≈ 35.6 |
| Seedream 7 张 | — | ≈ 0.84 |
| **合计** | **6,607,097** | **≈ 211 元** |

账号当前返回 `403 AccountOverdueError`；继续生成前需要充值/恢复额度。

## 5. 收尾步骤（充值后）

1. 把 chunk_05 的计数旁白拆成两句、放慢：
   `{一块、两块、三块、四块、五块。}` + 短暂停顿 + `{六块、七块、八块、九块。}`
2. 只重跑第 5 段：
   `python skills/picturebook-one-take/scripts/one_take.py generate --out runs/cn01/one_take --start 5 --end 5 --resolution 720p --force
# 注意：现有 1–4 段是 720p；第 5 段必须同分辨率，不能默认 480p`
3. 重新拼接与终检：
   `python skills/picturebook-one-take/scripts/one_take.py assemble --out runs/cn01/one_take`
   `python skills/picturebook-one-take/scripts/one_take.py verify --out runs/cn01/one_take --delivery`

## 6. 上游提交审阅

远端 `main` / `feat/skills-tuning` / `feat/picturebook-skills` 各前进 7 个提交：

- `82c1ff7` 删除 `skills/living-picturebook`；入口统一到 `picturebook-pipeline`。
- `4ddc549`（picturebook-skills）新增：可见动作锁、真实转场门禁、水印 mask、Farneback 运动检查。
- `cd80c83`（skills-tuning）也修了"呼吸/眨眼"默认句，但**机位仍锁死静态**（`CAMERA_LOCK_SENTENCE` 明确不推不摇不移）。
- 与本轮用户指令冲突：用户要"镜头按情节移动、不要 zoom 画面整体缩放"。已在 `docs/CONSTRAINTS.md` §0.1 改机位政策；新 skill 走物理机位 + 数字 zoom 门禁。
- 本地已提交 `feat(one-take): extension-chain picturebook film + visible-action/zoom gates`，**尚未 push / 合并**。

## 7. API 事实（2026-11-25）

- DeepSeek 直连 `/models`：`deepseek-flash`、`deepseek-v4-pro`；`deepseek-chat` 实际路由到 flash。
- Seedream 5.0 flash `doubao-seedream-5-0-flash-260915` 本账号 **ModelNotOpen**；已开通可用的是 `doubao-seedream-5-5-261028`（¥0.12/张）。
- Seedance `doubao-seedance-2-5-flash-261115`：单次 4–30 s；720p/24fps/原生音轨；**extension 只返回新增段**（实测输入 4.004 s + 延长 5 s → 输出 5.000 s）。
- 360p **不在 Seedance 2.5 flash 可选档位**（只有 480p/720p）。测试可用 480p（约半价），但正式交付必须 720p；不要在同一链路中途换分辨率。

## 8. env 策略

**没有把真实 `.env` 推到 git**。原因：里面有 DeepSeek / Ark / MiniMax 四把 key，推到 GitHub 或进源码包等于公开额度；`.env` 已在 `.gitignore`。

团队快速搭建用：
```bash
cp .env.example .env
python scripts/setup_env.py   # 交互填 3 个 key，本机保存
python -m book2real cloud --check
```
## 9. DeepSeek 在链路里的位置

- `planner.py --llm` 用 `deepseek-flash` 做分场/分镜（新书走这条）；cn01 用手写基准脚本 + DS 复核。
- cn01 的 DS 复核（`runs/cn01/one_take/ds_review.json`）指出：p03 的"积木城堡"、p05 的数积木没被完整覆盖；chunk_04/05 有"叼信封跑出门、击掌、抱住"等原文未明写的动作；chunk_04 的 `location` 与 `location_name` 不一致。
- 处理原则：**不新增剧情**；数数句拆短/放慢；"抱住/击掌"这类只保留能由原文对白直接支撑的，其余删掉。第 5 段重跑时按这个清单改。

## 10. 当前阻塞

火山 Ark 账号返回 `403 AccountOverdueError`；Seedance/Seedream 都不能再发。需要充值（≥100 元留余量）后只重跑 chunk_05，再 assemble + verify。
## 11. 2026-11-25 新口径：训练一律 480p

- 训练/排练/提示词验证：`--resolution 480p`（Seedance 2.5 flash 输出 854×480；约半价）。
- 正式交付：`--resolution 720p`，`verify --delivery` 必须过 1280×720。
- 不要在一条 extension 链中途换分辨率；480p 排练通过后，整条重跑 720p 正式版。
- 360p 不在 API 档位里（只有 480p/720p），不要试。
## 12. 2026-11-25 英文绘本 en01（480p）中途状态

- 已过门禁：chunk_01–04（4×30s = 120.08s，854×480/24fps/有声），预览 `runs/en01/one_take/preview_120s.mp4`。
- 未生成：chunk_05–06（还差 60s 到 180s）；再充值约 40–60 元即可跑完。
- 英文实测花费：**14 次调用，217.38 元（上限估算）**；账号再次 `AccountOverdueError`。
- 钱花在哪：chunk_03 因"包装文字 / 黑场 / 数字 zoom"连续返修 8 次；chunk_01 因 style 误报 3 次。
- 已改：
  - `generate --attempts` 默认 **1**（不再自动 3 连跑）；
  - `--max-cny` 改为**累计预算**，从 `cost.csv` 读已花，超了就停；
  - `style_cast` 只在 `style_mean < 0.22` 判漂移，单帧 `style_min` 不再误杀；
  - 深色眼睛特写/闭眼接开眼允许 `dark_match_cut`（prev_mean<15 且 seam≥0.70），不再当硬切。
- 已加：`[Props]` 道具锁、`[Voice Lock]` 声线锁、`[Audio Continuity]` 段尾静音；`check_audio_cut` 在段尾语音 >-20 dB 时打回。
- DeepSeek 视觉（实测可用）：`one_take.py vision` 把页图发给 `deepseek-flash`，产出画风/角色/道具/地点；en01 的画风 DNA 就是它写的。角色身份仍以 `book.json` 为准，不让视觉模型改名（否则 Kuan/Dad 会张冠李戴）。
