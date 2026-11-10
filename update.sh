#!/usr/bin/env bash
set -e

INSTALL_DIR="${INSTALL_DIR:-/opt/tuya-companion-bridge}"

echo "=========================================="
echo " Updating Tuya Companion Bridge Service"
echo "=========================================="

if [ -d "$INSTALL_DIR" ]; then
    cd "$INSTALL_DIR"
else
    DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
    cd "$DIR"
    INSTALL_DIR="$DIR"
fi

echo "Working directory: $INSTALL_DIR"

# 1. Pull latest code if git repo
if [ -d ".git" ]; then
    echo "Pulling latest changes from git..."
    git fetch --all --prune
    git reset --hard origin/$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "main") || git pull
else
    echo "Notice: $INSTALL_DIR is not a git repo. Skipping git pull."
fi

# 2. Update Python dependencies in virtualenv
if [ -d "venv" ]; then
    echo "Updating Python virtual environment dependencies..."
    ./venv/bin/pip install --upgrade pip --quiet
    if [ -f "requirements.txt" ]; then
        ./venv/bin/pip install -r requirements.txt --quiet
    fi
fi

# 3. Restart systemd service if installed
if systemctl list-unit-files | grep -q "tuya-bridge.service"; then
    echo "Restarting tuya-bridge.service..."
    systemctl daemon-reload
    systemctl restart tuya-bridge.service
    echo "Service status:"
    systemctl --no-pager status tuya-bridge.service
else
    echo "Notice: tuya-bridge.service not installed in systemd."
fi

echo "=========================================="
echo " Tuya Companion Bridge update complete!"
echo "=========================================="
