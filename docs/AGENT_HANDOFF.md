# 把工作交给另一个 agent（不依赖我这个会话）

这篇文档的目的：**下一个 agent 在别的机器、别的会话里，能自己把活干完。**
它不需要读过我们的对话，只需要仓库 + 这篇文档 + `.env` 里的 key。

## 为什么要拆出来

两件事证明这种拆法是必要的：

1. 我在这里做的每一步都依赖本机路径、本机的 `.env`、本机的 GPU 缓存；
2. 真正该由 agent 完成的是**判断**（选哪一页、框哪块、切多少），
   不是**重新实现切图数学**。把判断和实现分开，换谁来做都一样。

## 一条命令一个决定

```bash
python -m book2real agent help          # 列出所有 verb 与契约

# 1) 切一块圆形区域（补手臂、补头）
python -m book2real agent rig-gap --from <frame.jpg> --to <out> \
    --cx 0.55 --cy 0.72 --r 0.18 [--pivot 0.52,0.55] --name arm_r

# 2) 按矩形切一条刚体肢体（可选 --step 采样多个姿态）
python -m book2real agent rig-arm --from <frame.jpg> --to <out> \
    --bbox 0.615,0.63,0.235,0.37 [--step 0.03] --name arm_r

# 3) beats JSON -> 循环 GIF/MP4（刚体旋转 + 采样姿态交叉淡入）
python -m book2real agent motion --plan <plan.json> --out <loop.gif>

# 4) 一页拆成背景 + 若干人物层
python -m book2real agent leaf --reader books/<id>/reader --page p05 \
    --art art/p05.jpg --boxes "0.02,0.10,0.30,0.82;0.60,0.02,0.38,0.95" --out <out>
```

每个 verb 写出**约定好的 JSON**，下一个 agent 只读这些文件，不读我的对话：

| 文件 | 内容 |
|---|---|
| `<name>.json` | `kind` / `art` / `key` / `parts[]`（`image`,`pivot`,`amp`） |
| `<name>_meta.json` | `offset_px`（切件在原图上的位置）、`pivot` |

## 一个真实例子：p17 劳拉挥手

我们这样做，你可以照抄流程、换参数：

```bash
# 1. Recraft 补手腕缺口（可选，没有它手和前臂是断开的）
python scripts/recraft_wrist.py --go       # $0.04，输出 runs/recraft/wrist_after.jpg

# 2. 把修好的整页当底图，切出手臂（含手）
python -m book2real agent rig-arm \
    --from runs/recraft/wrist_after.jpg --to runs/agent/p17 \
    --bbox 0.615,0.63,0.235,0.37 --name arm_full

# 3. 写一个 beats 计划（这是"决定"，该由 agent 或人给）
#    pivot 取肩膀端 = [0.42, 0.97]，否则手臂会甩出画面
python scripts/p17_wave.py                 # 渲染 runs/agent/p17/p17_wave.gif

# 4. 注册到阅读器
```

## 踩过的坑（直接省你时间）

| 现象 | 原因 | 做法 |
|---|---|---|
| 手臂转到一半飞出画面、画面顶部出现碎片 | 先粘贴后旋转，rotated 画布不够大 | 先在带 padding 的独立画布上旋转，再 paste |
| 挥手时手和前臂断开 | 原图手腕处就断开（手叠在脸颊上） | 先用 Recraft 补那条缝，再切 |
| 一个框里有三个角色，SAM 只给一个 | 多框提示在 transformers 5 被拼成错误形状 | 一框一次调用（`segment_each` 已经这么做了） |
| 背景修补出现条纹 | Telea/NS 对密集纹理无能为力 | 把掩膜收到贴边 0.8%，或交给 Recraft 只修远离人物的区域 |
| Recraft inpaint 凭空画出人物 | 掩膜贴到主角，模型按自己理解重画 | **掩膜只能盖背景**；贴到人物就会重画人物（已实测） |
| Recraft 400 “Mask must contain only black or white” | 掩膜有抗锯齿灰边 | 二值化到 0/255 |

## 你（另一个 agent）该做的事

1. 读 `docs/AGENTS.md` 了解五个 agent 的分工；
2. 读这篇的 verb 契约；
3. 对每一页**先判断**：这页的人物能不能干净切开（`book2real layers` 的
   `outline` 字段是 `sam` 还是 `box`）；
4. 能切的用 `rig-arm` / `rig-gap` + `motion` 生成循环；
5. 切不干净的把掩膜交给 Recraft，**只碰背景**；
6. 结果用 `<name>.json` 交付，不需要写别的。

## 不要做的事

- 不要改 `book2real/cast/critic.py` 的阈值来"通过"；
- 不要为了省钱去掉 `RECRAFT_MAX_SPEND_USD`；
- 不要把 `.env` 提交；
- 不要假设 `runs/` 里的东西还在（它被 gitignore 了），产物要用 `--to` 指定到 `books/<id>/reader/`。
