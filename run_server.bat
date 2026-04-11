@echo off
:: 🍪 Cookie-Fooocus — Server Mode (Windows)
:: Provided by CookieHostUK · Coded with Claude AI assistance

set REPO_DIR=%~dp0
set VENV=%REPO_DIR%fooocus_env
set PYTHON=%VENV%\Scripts\python.exe

if not exist "%PYTHON%" (
    echo [ERROR] Not installed. Run install_server.bat first.
    pause
    exit /b 1
)

if not exist "%REPO_DIR%auth.json" (
    echo [WARNING] No auth.json found — using default credentials: admin / changeme123
    echo          Create auth.json before exposing this to the internet!
    echo.
)

echo 🍪  Cookie-Fooocus — Server Mode
echo     Auth: enabled  Roles: admin/user  Listen: all interfaces
echo.

call "%VENV%\Scripts\activate.bat"
"%PYTHON%" "%REPO_DIR%entry_with_update.py" --server --listen %*
