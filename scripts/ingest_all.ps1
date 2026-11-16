# 素材入库 + 阅读器包重建（一条命令跑完）
# 用法：powershell -File scripts/ingest_all.ps1 [-Materials "..\纸书智伴企业预选赛绘本素材"]
param(
    [string]$Materials = "纸书智伴企业预选赛绘本素材",
    [switch]$Force
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$materials = if (Test-Path (Join-Path $root $Materials)) { Join-Path $root $Materials } else { $Materials }

Push-Location (Join-Path $root "backend")
try {
    $args = @("tools/ingest_competition.py", "--path", $materials)
    if ($Force) { $args += "--force" }
    py @args
}
finally {
    Pop-Location
}

Push-Location $root
try {
    py scripts/sync_reader.py
}
finally {
    Pop-Location
}
