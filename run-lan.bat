@echo off
setlocal

cd /d %~dp0

if not exist .venv (
    echo Creating virtual environment...
    py -m venv .venv
)

call .venv\Scripts\activate

python -m pip install --upgrade pip
pip install -r requirements.txt

if not exist data mkdir data
if not exist backups mkdir backups

set PYTHONPATH=%CD%

echo Applying database migrations...
python -m app.migration_bootstrap
if errorlevel 1 (
    echo Database migration failed.
    exit /b 1
)

echo Starting JEronAI Operations for LAN access...
echo.
echo Open this app on this computer:
echo   http://127.0.0.1:8000
echo.
echo Open this app from another device on the same Wi-Fi/LAN using this computer's local IP:
echo   http://YOUR_LOCAL_IP:8000
echo.
echo Keep this only on trusted local networks. Do not expose it directly to the public internet.
echo.

python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

endlocal
