# Book2Real

A multimodal AI tool that converts static picture books into vivid story videos.

把一页静态绘本拆成 2–3 个深度层，用虚拟相机"重拍"，再给少数高光镜头叠加
光影/粒子微动；输出是标准 H.264 电影文件，而不是"会动的图"。
带印刷文字的扫描页默认走**刚性板运镜**（`layers:false`），避免文字被分层拆开产生重影。

```bash
python scripts/import_book.py --src assets/book2 --book charlie_lola \
       --title "我特别了解豚鼠" --subtitle "查理与劳拉 · 罗伦·乔尔德"   # 按创建时间排序导入
python -m book2real plan   --book books/charlie_lola          # 看分镜表
python -m book2real run    --book books/charlie_lola --jobs 2 # 21 页真书 ~195 s
python -m book2real run    --book books/guoqing --jobs 1      # 单页 ~20 s
python -m book2real ui     --runs runs --open                 # 播放器 + 质检看板

python -m book2real sample --out books                        # 8 页合成样张（走分层）
python -m book2real run    --book books/moon_rabbit --jobs 3
```

依赖只有 `numpy` + `Pillow` + `ffmpeg`（无 torch、无 OpenCV、无 node）。

## 它做/不做什么

| 做 | 不做 |
|---|---|
| 页面覆盖 100%（分层视差/刚性板运镜都是确定性轨道） | 不重演剧情、不生成新场景 |
| 相机运动 + 前景/背景视差 + 微动 | 不做角色走位、转身、口型 |
| 字幕、片头片尾、章节时间轴 | 不做 ASR/TTS/问答（预留接口） |
| 逐镜头缓存、降级、可审计 manifest | 不做端到端黑盒 |

## 两条轨道

- **A · 分层视差**（`parallax`）：把页面分成 2–3 个 RGBA 层，按深度给不同视差系数，虚拟相机带动。
  带字扫描页设 `"layers": false`，退化为单块板的推/摇/缩放，文字不会重影。
- **B · 微动**（`procedural` / `pregen`）：单层上的光影扫过、尘埃漂移、呼吸缩放；或直接吃外部
  I2V 生成的片段（放进 `books/<id>/clips/<shot_id>.mp4`）。

## 文档

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — 设计取舍（为什么主轨不是生成模型）
- [`docs/HANDOFF.md`](docs/HANDOFF.md) — 交接：实测数据、已知问题、下一步

## 目录

```
book2real/     包（契约 / 分层 / 相机 / 渲染 / 后端 / 质检 / 编排 / Web）
books/         绘本：<id>/pages/*.jpg|png + book.json（真书样例：charlie_lola / guoqing）
runs/          产物（已 gitignore）：film.mp4、manifest.json、events.jsonl
configs/       阈值与画幅（改这里不用改代码）
samples/       合成样张生成器
scripts/       环境安装 / 演示 / import_book.py（按创建时间归档）
tests/         冒烟测试
```

## 环境

Python ≥ 3.10，`ffmpeg` 在 PATH（或设 `BOOK2REAL_FFMPEG`）。
`powershell -File scripts\setup_env.ps1` 一键装依赖并自检。
