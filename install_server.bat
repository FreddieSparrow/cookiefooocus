@echo off
:: ─────────────────────────────────────────────────────────────────────────────
:: 🍪 Cookie-Fooocus — SERVER MODE Installer (Windows)
:: Provided by CookieHostUK · Coded with Claude AI assistance
::
:: Multi-user server mode.
:: PBKDF2 auth · Admin/User roles · Session tokens · Audit logs
::
:: After install:
::   1. Edit auth.json (copy from auth.json.example)
::   2. Run: run_server.bat
:: ─────────────────────────────────────────────────────────────────────────────

setlocal enabledelayedexpansion
set REPO_DIR=%~dp0
set VENV=%REPO_DIR%fooocus_env

echo.
echo  ╔══════════════════════════════════════════════════════╗
echo  ║   🍪  Cookie-Fooocus — SERVER MODE Installer        ║
echo  ║   Multi-user · PBKDF2 auth · Role-based access      ║
echo  ║   Provided by CookieHostUK                          ║
echo  ╚══════════════════════════════════════════════════════╝
echo.
echo   Default credentials:  admin / changeme123
echo   CHANGE BEFORE EXPOSING TO THE INTERNET
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

:: Create auth.json if missing
if not exist "%REPO_DIR%auth.json" (
    if exist "%REPO_DIR%auth.json.example" (
        copy "%REPO_DIR%auth.json.example" "%REPO_DIR%auth.json" >nul
        echo [!] Created auth.json from example — EDIT IT NOW.
    ) else (
        echo [{"user":"admin","pass":"changeme123","role":"admin"}] > "%REPO_DIR%auth.json"
        echo [!] Created auth.json with DEFAULT credentials.
        echo     Username: admin   Password: changeme123
        echo     CHANGE BEFORE STARTING THE SERVER.
    )
) else (
    echo [OK] auth.json already exists.
)

echo.
echo  ╔══════════════════════════════════════════════════════╗
echo  ║   ✅  Server Mode installation complete!            ║
echo  ╚══════════════════════════════════════════════════════╝
echo.
echo   Next steps:
echo     1. Edit auth.json — change the admin password
echo     2. Run: run_server.bat
echo.
echo   WARNING: Default password is changeme123
echo            Change it before exposing to the internet.
echo.
pause
