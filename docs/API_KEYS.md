# 密钥放哪里：官方三件套 + DSH 自己的 key

结论先给：

1. **官方赛题三件套**（DeepSeek / 豆包 Seedream / Seedance）写进项目根的 `.env`，
   只被 `book2real` 读；`.env` 已被 `.gitignore` 忽略。
2. **DSH 自己的模型 key** 不要放进 `.env`：在 DSH **设置 → 模型** 里填，写进
   `~/.dsh/.credentials.yaml`，从下一次请求生效。两者互不影响。

---

## 一、官方三件套：变量名与出处

| 能力 | 变量 | 默认值 | 说明 |
|---|---|---|---|
| LLM（故事/问答/判断） | `DEEPSEEK_API_KEY` | — | [DeepSeek 开放平台](https://platform.deepseek.com/api_keys) |
| | `DEEPSEEK_BASE_URL` | `https://api.deepseek.com` | OpenAI 兼容，代码会自动补 `/v1/chat/completions` |
| | `DEEPSEEK_MODEL` | `deepseek-chat` | 组委会若发 `deepseek-v4.1-flash` 之类，照填 |
| 文生图 | `ARK_API_KEY` | — | [火山方舟 API Key](https://console.volcengine.com/ark/region:cn-beijing/apiKey) |
| | `ARK_BASE_URL` | `https://ark.cn-beijing.volces.com/api/v3` | 图片与视频共用一个 key |
| | `SEEDREAM_MODEL` | `doubao-seedream-3-0-t2i-250415` | 若拿到接入点 id（`ep-…`）就填 id |
| 文生视频 | `SEEDANCE_MODEL` | `doubao-seedance-1-0-lite-t2v-250428` | 同上，可填 `ep-…` |
| 花费护栏 | `CLOUD_MAX_SPEND_CNY` | `50` | 账本累计超过就拒绝发请求 |
| | `SEEDREAM_CNY_PER_IMAGE` | `0.20` | 按官方账单改 |
| | `SEEDANCE_CNY_PER_SEC` | `1.00` | 按官方账单改 |
| | `DEEPSEEK_CNY_PER_1K_TOKENS` | `0.002` | 按官方账单改 |

> 官方额度：总额 ≤500 元、并发 ≤20、2026.11.30 开通（答疑 Q42）。
> 本地默认 50 元/账本只是防误跑，不是官方上限。

`.env` 写法：

```ini
DEEPSEEK_API_KEY=sk-...
ARK_API_KEY=...
SEEDREAM_MODEL=doubao-seedream-3-0-t2i-250415
SEEDANCE_MODEL=doubao-seedance-1-0-lite-t2v-250428
CLOUD_MAX_SPEND_CNY=50
```

## 二、验证与调用

```bash
# 谁已就绪、用了多少钱
python -m book2real cloud --check

# 只估价、不发请求（不需要 key）
python -m book2real cloud --image "水墨风的小狗" --out .tmp/dog.png --dry-run
python -m book2real cloud --video "积木城堡缓缓搭起" --out .tmp/castle.mp4 --seconds 5 --dry-run

# 真实调用
python -m book2real cloud --llm "用一句话介绍纸书智伴"
python -m book2real cloud --image "..." --out .tmp/a.png
python -m book2real cloud --video "..." --out .tmp/a.mp4 --seconds 5 --image-path books/cn01/pages/p03.jpg
```

流水账在 `runs/cloud/ledger.jsonl`（每条：时间/模型/元/用量）。每发一次请求前先查预算，
超了直接报 `预算不足`，不会先花钱再报错。

### 接进业务链路

```bash
# 文生故事：DeepSeek 为每页写画面说明 + 两个问题；--apply 写进 curation.json
python -m book2real story --book books/cn01 --apply
python -m book2real analyze --book books/cn01

# 文生图：补 split_plan 里要求"重新生成"的插图
python -m book2real visuals --book books/cn01

# 文生视频：单页试片（只用于演示/评估，不进正式成片）
python -m book2real clip --book books/cn01 --page p03 --prompt "积木一块块搭高，镜头缓缓推近" --seconds 5
```

> **Q43 边界（2026-11-23 已确认）**：同一任务内分段生成再拼接**不算**二次编辑，只禁人工后期
> 剪辑（片头/转场/BGM/换配音）。因此两条路径都可交付：正式成片走 `python -m book2real film`
> （单趟逐帧渲染，6 本已出），需要单段可重生成时走 `skills/` 的 Seedance 产线（`concat` 拼接）。

## 三、旧实验后端（可留空）

`RECRAFT_API_TOKEN` 等变量仍在 `.env.example` 里，是早期修图实验，官方赛题不需要；
不填不影响 `cloud`、`film`、`reader`。

## 四、注意

- 不要把 key 写进 `curation.json`、`books/`、`docs/`、任何提交物；`scripts/package_submission.py`
  的源码包按 `git ls-files` 打包并排除 `.env*`。
- 把 key 贴进聊天/截图/PR 都算泄露；泄露后立刻在服务商后台轮换。
- 换机器时复制 `.env` 即可，不要把 `.env` 发到公开渠道。
- 现场演示建议先在 `cloud --check` 里确认 key 与模型 id；`ep-…` 与模型名填错都会在
  第一次调用时报 HTTP 400/404，`cloud.py` 会把响应体前 500 字打出来。
