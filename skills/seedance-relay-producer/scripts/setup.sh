#!/usr/bin/env bash
set -euo pipefail

echo "=== [seedance-relay-producer] Linux/macOS 环境初始化 ==="
python3 --version

if [ ! -d ".venv" ]; then
  echo "创建虚拟环境 .venv ..."
  python3 -m venv .venv
fi

source .venv/bin/activate
python -m pip install --upgrade pip

# 依据虚拟环境解释器版本选择依赖曲线：3.13+ 无 cp313 wheel 的钉版包改用区间约束文件
PY_VER="$(python -c 'import sys;print(sys.version_info[0]*100+sys.version_info[1])')"
REQ_FILE="requirements.txt"
if [ "${PY_VER}" -ge 313 ] && [ -f "requirements-py313.txt" ]; then
  REQ_FILE="requirements-py313.txt"
  echo "检测到 Python 3.13+，改用 3.13 兼容依赖曲线 ${REQ_FILE}"
fi
echo "安装锁定版本依赖 ${REQ_FILE} ..."
pip install -r "${REQ_FILE}"

if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "WARNING: 未检测到 ffmpeg / ffprobe，qc.py、concat.py、cleanup.py 将无法运行。" >&2
fi

if [ ! -f ".env" ] && [ -f ".env.example" ]; then
  cp .env.example .env
  echo "已生成 .env 模板，请填入真实的 ARK_API_KEY。"
fi

if [ ! -f "config.yaml" ] && [ -f "config.example.yaml" ]; then
  cp config.example.yaml config.yaml
  echo "已生成 config.yaml 配置文件。"
fi

echo "运行环境与依赖自检..."
python scripts/preflight.py --check-env
echo "=== 初始化完成 ==="
