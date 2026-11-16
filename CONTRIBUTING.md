# 协作说明

这是「纸书智伴」赛题的**公开后端仓库**。公开可读；需要直接 push 的队员请让仓库所有者到
GitHub → Settings → Collaborators 邀请（Write 权限）。没有权限的队员可以 Fork + Pull Request。

## 1. 不要提交的东西

- **赛事原始素材**：6 本绘本 PDF、Query 音频、OCR 产物一律不要提交。仓库已忽略 `data/`。
  素材请各自向组委会获取，或由队长私下分发。
- **任何 API key / token / 密码**：MiniMax、DeepSeek、云厂商 key 只放进本机 `.env`；
  `.env`、`*.key`、`*.pem` 已被 `.gitignore` 排除。不要把 key 贴到 issue/PR/聊天记录里。
- **个人机器路径**：脚本里不要写 `C:\Users\xxx\...`；用环境变量（如 `COSYVOICE_HOME`、`COSYVOICE_PY`）。
- **大文件/模型权重**：模型放仓库外，通过环境变量指向。

## 2. 本地启动

```bash
git clone https://github.com/chhaaabbbccc/paperbook-backend.git
cd paperbook-backend
python -m pip install -r requirements.txt
cp .env.example .env        # Windows: copy .env.example .env
# 按需修改 .env（默认 SAPI + 本地 Whisper + 提取式回答）
python -m uvicorn app.main:app --host 0.0.0.0 --port 8790
```

首次使用需要把赛事素材入库：

```bash
python tools/ingest_competition.py --zip "<素材zip路径>"
```

## 3. 分支与提交

- `main` 保持可运行；功能改动开 `feature/<topic>` 分支，提 PR。
- 提交信息用中文或英文均可，建议：`feat: ...` / `fix: ...` / `docs: ...` / `test: ...`。
- 一个 PR 只做一件事；改接口时同步更新 `docs/INTEGRATION.md` 和 `README.md`。
- 不要提交 `data/`、`.env`、`__pycache__`、`pip-cache/`、`.pytest_cache/`。

## 4. 测试

```bash
python -m pytest -q
```

当前 18 个测试覆盖切句、VAD、BM25/意图、跨语言别名、脚本解析、回合与填充音。
改 ASR/TTS/检索逻辑时请补测试；没有 GPU 也能跑这些单测。

## 5. 接口契约

- 前端/视频组看 `docs/INTEGRATION.md`：WebSocket 事件、PCM 格式、`action` 类型、book.json、manifest。
- 延迟设计与调参看 `docs/LATENCY_2S.md`。
- 赛题要求对照看 `docs/REQUIREMENTS_MAPPING.md`。

## 6. 数据与合规

- 赛事素材仅限本赛事开发/评测/演示，不得公开分发或用于赛事外训练。
- 演示/答辩前确认项目说明书中的第三方服务（MiniMax/DeepSeek/模型）披露与儿童内容复核责任人。
- 默认不保存原始音频（`STORE_AUDIO=false`）；调试需要开启时，赛后按通知删除。
