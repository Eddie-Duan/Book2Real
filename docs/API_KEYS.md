# 密钥放哪里：两类 key，两条路线

结论先给：

1. **图像/视频 API（Kling、Veo、Qwen、Recraft…）**：写进项目根的 `.env`，或当前 shell 的环境变量。
   它们只被 `book2real` 脚本读，不参与 DSH 自己的模型路由。
2. **DSH 自己的模型 key（DeepSeek）**：**不要**放进项目 `.env`。在 DSH 的 **设置 → 模型（Models）**
   里填，DSH 会把它写进 `~/.dsh/.credentials.yaml` 的 `refs` 里，从下一次请求开始生效。

本项目已经把 `.env` 加进 `.gitignore`（`.env.example` 除外），别用 `git add -f` 绕过去。

---

## 一、图像/视频 API 放在哪

### 位置

```
Book2Real/
├─ .env                  ← 放这里（已被 gitignore）
├─ .env.example          ← 提交进仓库的模板，只写变量名，不写值
└─ book2real/
```

`.env` 的写法（以本项目的变量名为准）：

```ini
# 必填：选一个图像编辑/生成服务
IMAGE_API_PROVIDER=openai-compatible
IMAGE_API_BASE_URL=https://api.example.com/v1
IMAGE_API_KEY=
IMAGE_API_MODEL=

# 可选：视频生成
VIDEO_API_PROVIDER=
VIDEO_API_BASE_URL=
VIDEO_API_KEY=
VIDEO_API_MODEL=

# 可选：如果走 DashScope / 火山 / 智谱等，用它们的统一变量名
DASHSCOPE_API_KEY=
VOLCENGINE_API_KEY=
```

> 现在仓库里**还没有**读这些变量的代码（见下面的"现状"），所以填进去不会立刻生效；
> 变量名先定下来，接入时按这个名字读。

### 验证放对没有

```bash
# PowerShell：确认当前 shell 能看到（.env 不会自动进环境，脚本自己会读文件）
python -c "import os;print(sorted(k for k in os.environ if 'API' in k))"
```

### 注意

- `.env` 只在**项目根**被读取；换目录启动时用 `python -m book2real --env <路径>` 指定。
- 不要把 key 写进 `curation.json`、`books/`、`docs/`。
- 把 key 贴进聊天/截图/PR 都算泄露；泄露后立刻在服务商后台轮换。

---

## 二、DSH 自己的模型 key

你看到的这个 Agent（就是现在在跑的这个 DSH 会话）用哪个模型、怎么鉴权，由 DSH 管，不由本仓库管。

- **在 DSH 设置里填**：设置 → 模型（Models）→ DeepSeek 行 → API 密钥。
  它会被写进 `~/.dsh/.credentials.yaml` 的 `refs.DEEPSEEK_API_KEY`，立刻生效、不用重启。
- **或者**在启动 DSH 的那个终端里 `setx DEEPSEEK_API_KEY ...`，然后**重启 DSH**（环境只在启动时快照一次）。
- **不要**放项目 `.env`：项目 `.env` 是给仓库脚本用的；而且项目 `.env` 也进不了 DSH 自己的 provider 解析，
  容易造成"我明明配了为什么不生效"的错觉。

> 我实测过：从当前会话的 shell 里，`process.env` 里**没有** `DEEPSEEK_API_KEY`（DSH 不会把存下来的 key
> 注入到 Agent 的工具环境）。所以想让仓库脚本直接调 DeepSeek，得自己在 `.env` 里再写一份。

---

## 三、Agent 链现在哪些环节调模型（重要）

你问的"是不是还需要一个模型来决策和检验"，答案是：**设计上需要，现在还没有接**。

| 环节 | 现在怎么判断 | 需要大模型吗 |
|---|---|---|
| `PageScout` 找人物 | 键色抠底 + 连通域 + 形状过滤 | 需要（尤其"谁是谁"），现在没有 |
| `CastClerk` 合并角色 | 颜色直方图 + curation 人工描述 | 需要（身份再认），现在没有 |
| `Rigger` 切件 | 几何轮廓 + SAM | 不需要 |
| `MotionDirector` 写动作 | 动词词表 + 情绪词表 → beat JSON | 可选（更细的节奏/情绪），现在规则化 |
| `Critic` 验收 | 规则指标：脸完整度、眼睛成对、背景残留、关节开洞、动作舒适区、正文覆盖 | **不需要**（已经能跑，0.0 s/页） |

所以：**"检验"这一环不需要第二个大模型**，规则 + 视觉指标已经能拒掉坏件（实测能区分"有眼的头"和"没眼的头"）。
需要额外 API 的是**决策类**判断：

- "这一页谁是主角、在干什么"（视觉语言模型，看整页图 + 文字）；
- "这段文字适合配什么运镜/动作"（语言模型，读旁白给 beat）；
- "这个候选切件是不是同一个角色"（身份再认）。

### 建议的模型分工

| 角色 | 建议模型 | 放哪 |
|---|---|---|
| 视觉理解（人物/场景/身份） | 多模态 VLM（如 Qwen-VL、GPT-4o/4.1、Gemini） | API |
| 故事/镜头决策 | 文本 LLM（DeepSeek / Qwen / GPT） | API |
| 切件与轮廓 | SAM + 深度（已本地） | 本地 GPU |
| 验收 | 规则指标（已本地，已实现） | 本地 CPU |

一个小坑：**模型调用的成本/延迟要按页算**。一本 30 页的书，如果每页都让 VLM 看一遍，
就是 30 次多模态请求；可以先跑规则，只把"规则判不准的页"（多角色、切件被 Critic 拒）交给 VLM，
这样一本通常只花 3–8 次调用。

---

## 四、接入时我会怎么改（还没动手）

1. `book2real/agents_brain.py`：读 `.env` → 统一 `VisionClient` / `LLMClient`，带重试、超时、预算上限；
2. `PageScout` 增加 `--vision` 分支：规则先跑，低置信度页才调 VLM 复核；
3. `Critic` 增加可选的"语义复核"，但默认关闭，规则为主；
4. 所有调用写 `runs/api_log.jsonl`（模型、耗时、token、费用估算），便于复盘；
5. 没 key 时整条链**静默降级**到现有规则路径，不报错。

在没接之前，现在的 `cast` / `layers` / `reader` / `film` 都能完整跑，不需要任何 API。
