#Requires -Version 5
<#
  End-to-end demo on the official sample book `cn01`:
  read the PDF (if not imported) -> analyze -> film -> reader.
  Usage:  powershell -ExecutionPolicy Bypass -File scripts\run_demo.ps1
#>
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$py = if (Test-Path ".venv\Scripts\python.exe") { ".venv\Scripts\python.exe" } else { "python" }
$env:PYTHONPATH = $root

if (-not (Test-Path "books\cn01\book.json")) {
    & $py -m book2real import-pdf "纸书智伴企业预选赛绘本素材\绘本素材\中文绘本01.pdf" --id cn01
}
& $py -m book2real analyze --book books/cn01
& $py -m book2real film --book books/cn01
& $py -m book2real reader --books books --port 8770 --open
