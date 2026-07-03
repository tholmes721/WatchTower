#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# WatchTower — offline installer for airgapped Linux/macOS systems
# Run once on the target machine after copying the pdu-dashboard folder.
# Requires Python 3.11+ already installed.
# ─────────────────────────────────────────────────────────────────────────────
set -e

echo ""
echo " WatchTower — Offline Install"
echo " ════════════════════════════"
echo ""

# Check Python
if ! command -v python3 &>/dev/null; then
    echo " ERROR: python3 not found. Install Python 3.11+ first."
    exit 1
fi

echo " Python found: $(python3 --version)"
echo ""

# Install from local wheels
echo " Installing Python packages from local wheels..."
python3 -m pip install --no-index --find-links=vendor/wheels -r requirements.txt

# Create data dir
echo ""
echo " Creating data directory..."
mkdir -p data

# Run migration
echo ""
echo " Running database migration..."
python3 migrate.py

echo ""
echo " ════════════════════════════════════════════════════════"
echo " Install complete."
echo " Start the app with:  python3 run.py"
echo " Then open:           http://127.0.0.1:8000"
echo " ════════════════════════════════════════════════════════"
echo ""
