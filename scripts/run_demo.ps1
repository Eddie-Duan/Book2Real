#Requires -Version 5
<#
  End-to-end demo: sample book -> film -> player.
  Usage:  powershell -ExecutionPolicy Bypass -File scripts\run_demo.ps1
#>
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$py = if (Test-Path ".venv\Scripts\python.exe") { ".venv\Scripts\python.exe" } else { "python" }
$env:PYTHONPATH = $root

& $py -m book2real sample --out books
if (-not (Test-Path "books\moon_rabbit\pages")) { throw "sample book was not generated" }

& $py -m book2real run --book books/moon_rabbit --out runs --jobs 2
& $py -m book2real ui --runs runs --port 8765 --open
