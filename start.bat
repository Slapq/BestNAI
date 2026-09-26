@echo off
chcp 65001 >nul
title BestNAI

cd /d "%~dp0"

if not exist ".env" (
    copy ".env.example" ".env" >nul
    echo [!] Please edit .env first, then re-run
    notepad .env
    pause
    exit /b
)

echo [*] Starting BestNAI backend...
start "BestNAI Backend" cmd /k "cd /d %~dp0 && python run.py"

timeout /t 3 /nobreak >nul

echo [*] Opening frontend...
start "" http://127.0.0.1:8010/

echo [OK] BestNAI started
echo     Open http://127.0.0.1:8010/ in browser
