# 交互视频录制/验收 · 队友交接

> 2026-11-25，代码基线 feat/skills-tuning（feat/skills-tuning = feat/tuning-sync）。
> 结论：可以交给队友，但代码在 git 里、成片/时间轴/官方 wav 不在 git 里，必须把非 git 产物单独打包发过去。

## 1. 为什么不能只丢 git 仓库

当前 .gitignore 会忽略这些录制必需的东西：

| 需要的东西 | 路径 | git 状态 | 队友怎么拿 |
|---|---|---|---|
| 新成片 | runs/<id>/seedance_film/<stamp>/film.mp4 | 忽略（runs/） | 交接 zip / 网盘 |
| 时间轴 | 同目录 timeline.json、meta.json | 忽略 | 交接 zip |
| 可点元素表 | books/<id>/click_targets.json | 已跟踪 | git pull |
| 官方 Query wav | 纸书智伴企业预选赛绘本素材/... | 忽略（官方素材不重分发） | 队内共享盘/私发 |
| .env（ASR/TTS/LLM key） | .env / backend/.env | 忽略 | 私发，不进 git |
| 交互页/时间轴代码 | book2real/reader_app.py、web/film.js、scripts/one_take_timeline_shots.py | 已跟踪 | git pull |

所以队友只 clone 仓库：页面能打开，但没有新成片/时间轴，/film?book=cn01 会回退到旧 2.5D 片。

## 2. 交接包（推荐）

在出片机器上跑：

    python scripts/make_interactive_handoff.py --book cn01 --out dist/interactive_handoff_cn01.zip

包内含：

    interactive_handoff_cn01/
      film.mp4                 # 150.08s / 1280x720 / 24fps / 有声
      timeline.json            # 16 段（p01 + 15 shot），41 spots，197 OCR
      meta.json
      click_targets.json       # 官方 [点击]/[高亮] 项
      query_wavs/              # 官方中文 Query 前 10 条
      HANDOFF.md               # 本文件

队友解压到仓库根，保持 runs/cn01/seedance_film/<stamp>/ 结构。

## 3. 队友环境

    git clone git@github.com:Eddie-Duan/Book2Real.git
    cd Book2Real
    git checkout feat/skills-tuning
    # 解压交接包，把 film.mp4/timeline.json 放到 runs/cn01/seedance_film/<stamp>/
    $env:B2R_FILM_SEEDANCE="1"
    python -m book2real reader --books books --port 8796

打开 http://127.0.0.1:8796/film?book=cn01 。

需要 .env 里至少 MINIMAX_API_KEY（语音问答）；只录点击/检索可以先不配。

## 4. 录制要求（官方口径）

- Q39：交操作录屏即可，不需要真人出镜、不需要别人拍。
- ≤5 分钟，一镜到底，不剪辑；和绘本视频分别计时。
- 录浏览器 + 系统声音；播放官方 Query wav 或用麦克风说话。
- 1080p/720p 都行；画面里要能看清鼠标/触控和右侧介绍卡。
- 文件名：绘本名称+交互演示视频.mp4。

## 5. 建议演示顺序（60–120 秒）

1. 打开 /film?book=cn01；先看第一帧封面 2 秒、听书名，再进入正文。
2. 拖到 0:12，点 积木城堡（p02/p03）→ 暂停 + 介绍。
3. 拖到 1:48，点 生日卡片（p09 s2）→ 暂停 + 介绍。
4. 拖到 1:58，点 黄米团（p09 s3）→ 暂停 + 介绍。
5. 输入 我又不是故意的 → 跳到 p06 62.35s 并框出。
6. 输入 找到搭了九块积木的画面 → 跳 p12 并框出。
7. 点 描述这一帧 → 右侧引用当前页正文。
8. 点语音提问，播放 query_wavs/32.wav（“这张卡片送给谁？”）→ 2 秒内回答（送给花花）。
9. 切到 en01，播放 query_wavs/30.wav（“Who buys snacks?”）→ 2 秒内回答（Dad/爸爸）。

## 6. 验收清单

- [ ] 点 3 个官方必选元素：每次都 暂停 + 右侧介绍卡，不是只跳页
- [ ] 查字“我又不是故意的”能跳 p06 并框出
- [ ] “找到搭了九块积木的画面”能跳 p12
- [ ] 描述帧有当前页正文
- [ ] 语音问答首音 ≤2s（录屏里能看到回答开始）
- [ ] 视频 ≤5 min、有声、一镜到底、未剪辑
- [ ] 文件名 绘本名称+交互演示视频.mp4

## 7. 已知边界（录之前知道）

- 点击框是 shot 级，同一 shot 内角色走动时框不跟随；官方“所有可见帧可点”严格说还差逐帧跟踪。
- cn01 已加“成片第一帧定格 2s + 书名朗读”；en01 仍只有 p01 元数据段，画面里没有封面原图。
- en01 现在是 720p 拉伸版，且 shot_script 已有 page/targets；交接包里是 180s 无封面版。
- 语音编号：cn01 用 32.wav，en01 用 30.wav；06.wav 是“高亮积木城堡”，不要拿来做提问。