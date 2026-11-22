$ErrorActionPreference = "Stop"
Write-Host "=== [seedance-relay-producer] Windows PowerShell 环境初始化 ==="

python --version

if (-not (Test-Path ".venv")) {
    Write-Host "创建虚拟环境 .venv ..."
    python -m venv .venv
}

.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip

# 依据虚拟环境解释器版本选择依赖曲线：3.13+ 无 cp313 wheel 的钉版包改用区间约束文件
$pyVer = (python -c "import sys;print(sys.version_info[0]*100+sys.version_info[1])").Trim()
$reqFile = "requirements.txt"
if (([int]$pyVer -ge 313) -and (Test-Path "requirements-py313.txt")) {
    $reqFile = "requirements-py313.txt"
    Write-Host "检测到 Python 3.13+，改用 3.13 兼容依赖曲线 $reqFile"
}
Write-Host "安装锁定版本依赖 $reqFile ..."
pip install -r $reqFile

if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
    Write-Warning "未检测到 ffmpeg / ffprobe：qc.py、concat.py、cleanup.py 将无法运行。请安装后重开终端（如 winget install Gyan.FFmpeg）。"
}

if ((-not (Test-Path ".env")) -and (Test-Path ".env.example")) {
    Copy-Item ".env.example" ".env"
    Write-Host "已生成 .env 模板，请填入真实的 ARK_API_KEY。"
}

if ((-not (Test-Path "config.yaml")) -and (Test-Path "config.example.yaml")) {
    Copy-Item "config.example.yaml" "config.yaml"
    Write-Host "已生成 config.yaml 配置文件。"
}

Write-Host "运行环境与依赖自检..."
python scripts/preflight.py --check-env
Write-Host "=== 初始化完成 ==="
