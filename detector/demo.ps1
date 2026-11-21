# HTCCIP bot detector: live terminal demo (no UI).
# Usage (PowerShell, repo root):  .\detector\demo.ps1  [-Python path\to\python.exe]
param([string]$Python = "python")
$env:PYTHONIOENCODING = "utf-8"; $env:PYTHONWARNINGS = "ignore"
Set-Location (Split-Path $PSScriptRoot -Parent)
function Step($title) { Write-Host ""; Write-Host ("=" * 90) -ForegroundColor DarkCyan; Write-Host "  $title" -ForegroundColor Cyan; Write-Host ("=" * 90) -ForegroundColor DarkCyan }

Step "HTCCIP bot detector: one question, is this account a bot?"
Write-Host "  Model: LightGBM + relational graph network, trained on Cresci-2017 (10,197 human-labelled accounts)."
Write-Host "  Accounts below are from the HELD-OUT test split: the model never saw them during training."

Step "1/3  A bot (social spambot), with its true label"
& $Python -m detector explain 1070386470 --data cresci2017 --split test --model-dir detector/model

Step "2/3  A real person, with its true label"
& $Python -m detector explain 11086752 --data cresci2017 --split test --model-dir detector/model

Step "3/3  How good is it? (detector/REPORT.md)"
Write-Host "  Held-out accounts (2,039):         F1 0.996  (95% CI 0.993-0.998), 9 false alarms, 9 missed bots"
Write-Host "  Bot types NEVER seen in training:  F1 0.972 - 0.997  (fake followers, 3 social-spambot waves, traditional spam)"
Write-Host "  Calibration error:                 0.6 points  (a '80%' means ~80 of 100 such accounts were bots)"
Write-Host ""
Write-Host "  Live API: POST https://hybrid-threat-coordinated-campaign.onrender.com/api/bot/score  and  /api/bot/account" -ForegroundColor Green
