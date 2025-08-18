param([int]$Port=5000, [string]$Host="127.0.0.1")
$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot
$python = "python"
try { py -3 --version | Out-Null; $python = "py -3" } catch {}
if (!(Test-Path ".venv")) { & $python -m venv .venv }
& ".\.venv\Scripts\Activate.ps1"
python -m pip install --upgrade pip
pip install -r requirements.txt
if (!(Test-Path ".env") -and (Test-Path ".env.example")) { Copy-Item ".env.example" ".env" -Force }
$env:FLASK_HOST = $Host
$env:FLASK_PORT = "$Port"
python run.py
