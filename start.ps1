# FunClip startup script
$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectRoot

Write-Host "Starting FunClip..." -ForegroundColor Cyan
& ".venv\Scripts\python.exe" "funclip/launch.py" $args
