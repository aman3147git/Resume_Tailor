# Resume Tailor - Windows launcher
# Creates a .venv on first run, installs deps, then launches the Streamlit app.

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

if (-not (Test-Path ".venv")) {
    Write-Host "Creating virtual environment..." -ForegroundColor Cyan
    python -m venv .venv
}

Write-Host "Activating virtual environment..." -ForegroundColor Cyan
. .\.venv\Scripts\Activate.ps1

Write-Host "Installing/updating dependencies..." -ForegroundColor Cyan
python -m pip install --upgrade pip | Out-Null
pip install -r requirements.txt

Write-Host "Starting Resume Tailor..." -ForegroundColor Green
streamlit run app.py
