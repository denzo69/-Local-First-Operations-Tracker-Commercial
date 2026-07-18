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

echo Starting JEronAI Operations...
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload

endlocal
