@echo off
REM ─────────────────────────────────────────────────────────────────────────
REM WatchTower — offline installer for airgapped Windows systems
REM Run this once on the target machine after copying the pdu-dashboard folder.
REM Requires Python 3.11+ already installed.
REM ─────────────────────────────────────────────────────────────────────────

echo.
echo  WatchTower — Offline Install
echo  ════════════════════════════
echo.

REM Check Python is available
python --version >nul 2>&1
if errorlevel 1 (
    echo  ERROR: Python not found. Install Python 3.11+ and add it to PATH.
    pause
    exit /b 1
)

echo  Python found:
python --version

echo.
echo  Installing Python packages from local wheels...
python -m pip install --no-index --find-links=vendor\wheels -r requirements.txt

if errorlevel 1 (
    echo.
    echo  ERROR: Package installation failed. Check the output above.
    pause
    exit /b 1
)

echo.
echo  Creating data directory...
if not exist data mkdir data

echo.
echo  Running database migration...
python migrate.py

echo.
echo  ════════════════════════════════════════════════════════
echo  Install complete.
echo  Start the app with:  python run.py
echo  Then open:           http://127.0.0.1:8000
echo  ════════════════════════════════════════════════════════
echo.
pause
