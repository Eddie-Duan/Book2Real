# `seedance-relay-producer`

把「已经去字、补全好的绘本关键帧」变成一段段互相接得上的视频，并且自己把关。

上游 [picturebook-to-seedance](../picturebook-to-seedance) 负责把绘本页擦干净、去掉文字；本 skill 负责把干净的关键帧动起来、串成片、量化自检、不合格自动返修，最后交付成片与质检报告。

## 1. 它解决什么问题

| 问题 | 不做会怎样 | 本 skill 的做法 |
| :-- | :-- | :-- |
| **页与页接不上** | 每页独立生成，第 2 页首帧与第 1 页尾帧对不上，拼起来像闪断 | **首尾帧双锚定**：第 `N` 页同时指定首帧 `K_N` 与尾帧 `K_{N+1}`，由模型完成这段位移；条件不满足时按 `Level A → B → C` 降级为软转场或硬切 |
| **生成结果跑偏没人管** | 画幅被缩放、角色时序漂移、画面里冒出字幕、角落出现平台角标，只能人眼一页页看 | 六类量化门禁：宽高比、首帧 `SSIM`、接缝 `SSIM + pHash`、时间漂移、文字 `OCR`、角标覆盖率 |
| **重做成本失控** | 改一页就要重跑后面所有页，费用与失败面按 `O(N)` 放大 | 双锚定保证单页重生成不影响下游；预算熔断 + 单页 3 次返修上限后转人工 |

## 2. 输入契约（每页一个目录）

```text
pages/
  page_001/
    clean_keyframe.png     # 唯一视觉真值：零文字、尺寸与原图完全一致，禁止缩放/裁切/补边
    page_manifest.json     # 原文字内容、擦除位置、补全内容与依据原句
    qa_report.json         # 上游质检：尺寸、SSIM、像素差、保护带入侵
    seedance_prompt.txt    # 镜头运动 + 主体微动 + 环境动态（只读，仅在尾部追加运行参数）
  page_002/
  ...
```

字段名不做猜测：所有上游 JSON 必须经 `inputs/adapter.py` 映射，遇到未注册的键或缺失的必填键立即 fail-closed 阻断。首次接一个新上游目录，先跑：

```bash
python inputs/adapter.py --pages-dir ./pages --probe
```

## 3. Step 0–6 全流程

```powershell
cd "<本目录>"
```

**Step 0 · 环境自检与自动补装**（缺 `ffmpeg` 就装、缺依赖就装进隔离 `.venv`，只补不改其他）

首次运行还没有 `.venv`，用系统解释器跑一次即可（脚本只用标准库，会自己建 `.venv` 并装依赖）：

```powershell
python scripts\bootstrap_env.py --check   # 只体检
python scripts\bootstrap_env.py           # 缺什么装什么
```

之后各步统一用 `.venv` 里的解释器，避免误用系统环境：

**Step 1 · 零成本预检 + 估价**（不调 API，不花钱）

```powershell
.\.venv\Scripts\python.exe scripts\preflight.py --pages-dir .\pages --out-dir .\out --relay auto --dry-run
```

产出 `out/preflight.json`、接力拓扑（每页命中 `Level A/B/C`、`DINOv2` 距离）与预估费用表；上游 `qa_report` 不合格的页直接置为 `BLOCKED`，不会浪费一次生成。

**Step 2 · 低分样片验证**（先花几毛钱确认节奏与提示词响应）

```powershell
.\.venv\Scripts\python.exe scripts\generate.py --out-dir .\out --resolution 480p --yes
.\.venv\Scripts\python.exe scripts\poll_download.py --out-dir .\out
.\.venv\Scripts\python.exe scripts\qc.py --out-dir .\out
```

**Step 3 · 正式生成与异步下载**（超预算会拒绝，必须显式 `--yes`）

```powershell
.\.venv\Scripts\python.exe scripts\preflight.py --pages-dir .\pages --out-dir .\out --relay auto --resolution 1080p --dry-run
.\.venv\Scripts\python.exe scripts\generate.py --out-dir .\out --resolution 1080p --yes
.\.venv\Scripts\python.exe scripts\poll_download.py --out-dir .\out
```

任务 ID 幂等，中断后重跑接着走，不会重复扣费；视频下载后立即落盘。

**Step 4 · 量化质检**（可单页排查）

```powershell
.\.venv\Scripts\python.exe scripts\qc.py --out-dir .\out --page-id page_003
```

合格页置 `PASS`；若仅存在平台角标且位于安全区，才允许 `cleanup.py` 评估处置（`--method delogo|crop`，其余情况一律重生成，不做涂抹擦除）。

**Step 5 · 自动返修与人工升级**

`qc.py` 按 `references/repair-playbook.md` 的缺陷映射表调整 `seed` / `duration` / 锁定后缀后重跑，单页上限 3 次；仍不过的页打包进 `out/manual_review/page_###/`（问题帧、contact sheet、改进建议）并置 `ESCALATE`。

**Step 6 · 拼接与报告**

```powershell
.\.venv\Scripts\python.exe scripts\concat.py --out-dir .\out
# 要带旁白（推荐）：给一条音轨，拼接后自动铺上，并写 out/final_movie.json 做规格自检
# .\.venv\Scripts\python.exe scripts\concat.py --out-dir .\out --audio ..\book_out\narration.mp3
.\.venv\Scripts\python.exe scripts\report.py --out-dir .\out
```

交付物：`out/final_movie.mp4`、`out/concat.txt`、`out/qc_report.md`、`out/qc_report.json`、`out/cost.csv`、`out/run_log.jsonl`。
`out/final_movie.json` 记录成片规格（分辨率/帧率/时长/音轨）与 `spec_ok` 四项自检（≥720p、≥24fps、100–180s、音轨非静音）。

运行离线测试套件（零 API 消耗）：

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q
```

### 每页状态机

`PENDING` → `BLOCKED` | `PREFLIGHT_READY` → `GENERATING` → `POLLING` → `QC_CHECKING` → `PASS` | `REPAIRING` → `ESCALATE`

## 4. 环境自检与自动补装 (`scripts/bootstrap_env.py`)

一条命令覆盖全部前置依赖，缺什么装什么，不修改其他文件与配置：

| 检查项 | 缺失时的行为 |
|---|---|
| `ffmpeg` / `ffprobe` | Windows 走 `winget install Gyan.FFmpeg`；macOS 走 `brew`；Linux 走 `apt` / `dnf` / `pacman` |
| Python 依赖（numpy / Pillow / opencv-python-headless / PyYAML / scikit-image / imagehash / requests） | 用内置 3.12 运行时建 `.venv`（继承其 numpy 与 Pillow），只补装缺的那几个；**按 `requirements*.txt` 的钉版/区间安装**（3.12 用钉版、3.13 用区间），避免裸包名拉到 OpenCV 5.x 这类全新大版本 |
| `ffmpeg` 已装但不在 `PATH`（winget 便携包常见） | `scripts/common.py` 会自动定位 `%LOCALAPPDATA%\Microsoft\WinGet\Packages\Gyan.FFmpeg_*\...\bin` 并临时加入进程 `PATH`，无需手工配置 |
| 复核 | 装完再次逐项导入验证；仍有缺口则打印手工命令并以退出码 `1` 结束 |

```bash
python scripts/bootstrap_env.py               # 检测 + 自动补装（默认）
python scripts/bootstrap_env.py --check       # 只体检，不改动系统
python scripts/bootstrap_env.py --json        # 机器可读，便于 CI
python scripts/bootstrap_env.py --with-torch  # 需要 DINOv2 语义级判据时再补 torch
python scripts/bootstrap_env.py --mirror https://pypi.tuna.tsinghua.edu.cn/simple   # 国内加速
```

脚本只用标准库，因此可以拿"一个第三方包都没有"的裸解释器直接运行。与 `preflight.py --check-env` 的分工：`bootstrap_env.py` 装环境，`preflight.py` 校验业务前置（上游契约、API Key、预算）。

## 5. 开跑前需要准备的三件事

1. **Ark API Key**：`$env:ARK_API_KEY="..."`（火山方舟），或复制 `.env.example` 为 `.env` 后填入。接入点与模型在 `config.example.yaml`（默认 `ark.model: doubao-seedance-2-5-flash-261115`，北京节点；pro 版换 `doubao-seedance-2-5-260628`）。注意 2.5 带首帧时**不能下发 ratio**，`ratio_follows_first_frame: true` 已默认打开。
2. **把上游产物整理成 `pages/page_###/` 结构**：`picturebook-to-seedance` 默认输出扁平的 `deliverables/{page}_clean_keyframe.png` 等带前缀文件，需要按第 2 节的目录约定重排；同时上游 `qa_report.json` 含 `checks` / `thresholds` / `original` / `final` / `*_size` 等未注册顶层键且缺必填的 `ocr_residual_count`，直接喂入会被 fail-closed 拦下——这是有意设计（宁可报错，不猜数据），需按 `inputs/adapter.py` 的键映射取合规子集并补齐缺失字段。目前尚未内置一键 ingest 脚本，接入前请先用 `--probe` 核对字段。
3. **若要让 AI 自动按本 skill 执行**：把整个文件夹复制进 `C:\Users\lenovo\.dsh\skills\`；仅手动跑命令时留在当前位置即可。

## 6. 单页成本估算基准（参考 `config.example.yaml`）

- **低分验证阶段 (`480p`, 5s)**：约 `¥0.40 / 页`（10 页绘本整本验证约 `¥4.00`）。
- **正式成片阶段 (`1080p`, 5s 标准双锚定)**：约 `¥1.60 / 页`。
- **正式成片阶段 (`1080p`, 10s 中等差异平滑过渡)**：约 `¥3.20 / 页`。
- **返修冗余预留**：建议按总页数预算的 `1.15x` 预留返修余量（双锚定隔离架构下，单页返修仅重跑当前页，不触发级联重生成）。

## 7. 已知限制

1. **非标准画幅兼容**：若输入绘本关键帧为极特殊异形比例（如 `1.414:1` A4 比例），Ark API 会自动匹配最接近的合法比例（如 `4:3`）；若平台在推理侧执行了微小黑边填充或边缘裁切导致首帧 `SSIM < 0.92`，质检门禁将严格拦截。
2. **离线/无 GPU 降级精度**：在未安装 `torch`/`easyocr` 或无本地 HuggingFace 权重缓存的纯 CPU 环境中，视觉特征与文字检测会自动切换至 OpenCV 梯度+直方图启发式兜底，对极低对比度艺术字残影的检出率略低于深度学习模型（报告中会明确标注 `degraded_mode: true`）。
3. **跨页长距离动作**：当相邻两页角色位置跨越超过画面 `40%` 时，即使处于同一场景也会自动降级为 Level B（单锚定 + 0.4s 交叉溶解），避免生成模型强行"瞬移变形"。
4. **Python 版本与依赖曲线**：`requirements.txt` 为面向 `3.12` 的钉版曲线（`numpy==1.26.4`、`opencv-python-headless==4.10.0.84`、`scipy==1.14.1`、`torch==2.4.1` 均无 cp313 wheel）。若本机解释器为 `3.13`，请改用 `pip install -r requirements-py313.txt`，或直接用 3.12 解释器建 venv。
5. **`ffmpeg`/`ffprobe` 硬依赖**：`qc.py`（结构体检与抽帧）、`concat.py`（归一化与 xfade）、`cleanup.py`（delogo）均直接调用 `ffmpeg`/`ffprobe` 可执行文件。未安装时 `run_ffprobe_info` 会抛 `FileNotFoundError`；若已装但不在 `PATH`（winget 便携包典型情况），`common.py` 会自动定位，无需手工配置（`preflight.py --check-env` 可自检）。
6. **中文/非 ASCII 路径**：OpenCV 原生 `cv2.imread` / `cv2.imwrite` 在非 ASCII 路径下会**静默失败**（读不到、写入返回 `False`），导致预检误判"关键帧缺失"。本项目已统一改用 `scripts/common.py` 的 `imread_bgr` / `imwrite_bgr`（`Path.read_bytes` + `cv2.imdecode`），并把 `ffprobe` 等子进程输出显式按 UTF-8 解码；中文目录（`桌面\绘本\第001页\` 等）已实测可跑通 preflight 与 QC 全流程。
7. **水印判据的已知盲区**：角落角标判据以"贴片式覆盖"（大块变更 + 高对比边缘）为主，细笔画、半透明文字型水印在角落的变更面积过小可能漏检；此类情况需人工目检 `contact_sheet.jpg`。角落环境动效大（持续雨丝/落叶）时可通过 `watermark_corner_changed_frac_min` / `watermark_corner_changed_edge_min` 上调阈值降低误判。

## 8. 参考文档

| 文档 | 内容 |
| :-- | :-- |
| `SKILL.md` | 铁律、双锚定原理、降级链、状态机与决策规程 |
| `references/ark-seedance-api.md` | Ark Seedance 接口契约与错误码 |
| `references/preflight-and-adapter.md` | 输入字段映射与预检规程 |
| `references/relay-protocol.md` | 接力算法、代理判据、接缝门禁（含 `SSIM`/`pHash` 双指标） |
| `references/consistency-rubric.md` | 一致性量化阈值与样本标定方法 |
| `references/qc-rubric.md` | 自检门禁四件套与清理合规边界 |
| `references/repair-playbook.md` | 缺陷返修映射表与重生成影响面 |
| `references/troubleshooting.md` | 故障排查与硬件降级兜底 |
