@echo off
:: 🍪 Cookie-Fooocus — Local Mode (Windows)
:: Provided by CookieHostUK · Coded with Claude AI assistance

set REPO_DIR=%~dp0
set VENV=%REPO_DIR%fooocus_env
set PYTHON=%VENV%\Scripts\python.exe

if not exist "%PYTHON%" (
    echo [ERROR] Not installed. Run install_local.bat first.
    pause
    exit /b 1
)

echo 🍪  Cookie-Fooocus — Local Mode
echo     No auth · Single user · Select presets inside the UI
echo.

call "%VENV%\Scripts\activate.bat"
"%PYTHON%" "%REPO_DIR%entry_with_update.py" %*
