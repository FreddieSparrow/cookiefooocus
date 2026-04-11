@echo off
:: ─────────────────────────────────────────────────────────────────────────────
:: 🍪 Cookie-Fooocus — LOCAL MODE Installer (Windows)
:: Provided by CookieHostUK · Coded with Claude AI assistance
::
:: Single-user local mode. No login, no passwords.
:: Select presets (Realistic, Anime, etc.) inside the web UI.
:: ─────────────────────────────────────────────────────────────────────────────

setlocal enabledelayedexpansion
set REPO_DIR=%~dp0
set VENV=%REPO_DIR%fooocus_env

echo.
echo  ╔══════════════════════════════════════════════════════╗
echo  ║   🍪  Cookie-Fooocus — LOCAL MODE Installer         ║
echo  ║   Single-user · No passwords · No auth              ║
echo  ║   Provided by CookieHostUK                          ║
echo  ╚══════════════════════════════════════════════════════╝
echo.

:: Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found. Install Python 3.10+ from https://python.org
    pause
    exit /b 1
)

for /f "tokens=2" %%v in ('python --version 2^>^&1') do set PV=%%v
echo [OK] Python %PV% found.

:: Create venv
if exist "%VENV%" (
    echo [!] Virtual environment already exists — skipping creation.
) else (
    echo [..] Creating virtual environment...
    python -m venv "%VENV%"
    echo [OK] Environment created.
)

:: Activate and install
call "%VENV%\Scripts\activate.bat"
echo [..] Upgrading pip...
pip install --upgrade pip --quiet
echo [..] Installing core requirements...
pip install -r "%REPO_DIR%requirements_versions.txt" --quiet
echo [..] Installing optional extras...
pip install rapidfuzz transformers pillow psutil --quiet

echo.
echo  ╔══════════════════════════════════════════════════════╗
echo  ║   ✅  Installation complete!                        ║
echo  ╚══════════════════════════════════════════════════════╝
echo.
echo   To start: double-click run_local.bat
echo   Or run:   run_local.bat
echo.
echo   Select Realistic, Anime or other presets inside the web UI.
echo.
pause
