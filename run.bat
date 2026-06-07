@echo off
REM Resume Tailor - Windows launcher (double-click to start)
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File ".\run.ps1"
pause
