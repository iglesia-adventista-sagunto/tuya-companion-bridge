#!/usr/bin/env bash
set -e

# Tuya Companion Bridge - LXC Automated Installer & Update Linker
# Compatible with Debian/Ubuntu LXCs (Proxmox VE / standalone LXC)

if [ "$EUID" -ne 0 ]; then
  echo "Error: Please run as root (sudo bash install-lxc.sh)"
  exit 1
fi

TARGET_DIR="/opt/tuya-companion-bridge"
SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"

echo "=========================================================="
echo " Installing Tuya Companion Bridge in LXC"
echo "=========================================================="

# 1. Install prerequisites
echo "1. Checking system packages..."
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip git curl >/dev/null

# 2. Deploy files to target directory
echo "2. Setting up $TARGET_DIR..."
mkdir -p "$TARGET_DIR"

if [ "$SOURCE_DIR" != "$TARGET_DIR" ]; then
    echo "Copying application files to $TARGET_DIR..."
    cp -r "$SOURCE_DIR"/* "$TARGET_DIR"/ 2>/dev/null || true
    if [ -d "$SOURCE_DIR/.git" ]; then
        cp -r "$SOURCE_DIR/.git" "$TARGET_DIR"/ 2>/dev/null || true
    fi
fi

cd "$TARGET_DIR"

# Ensure scripts have execute permission
chmod +x start.sh update.sh 2>/dev/null || true

# 3. Create Virtualenv and install Python dependencies
echo "3. Setting up Python virtual environment..."
if [ ! -d "venv" ]; then
    python3 -m venv venv
fi
./venv/bin/pip install --upgrade pip --quiet
./venv/bin/pip install -r requirements.txt --quiet

# 4. Configuration template check
if [ ! -f "config.json" ]; then
    echo "Creating config.json from template..."
    cp config.template.json config.json
    echo "IMPORTANT: Remember to edit $TARGET_DIR/config.json with your device credentials."
fi

# 5. Install systemd service
echo "4. Registering systemd service..."
cp tuya-bridge.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now tuya-bridge.service

# 6. Link to standard LXC 'update' command
echo "5. Linking to LXC 'update' command..."
UPDATE_BIN="/usr/local/bin/update"
HOOK_SNIPPET="
# === Tuya Companion Bridge Update Hook ===
if [ -f /opt/tuya-companion-bridge/update.sh ]; then
    bash /opt/tuya-companion-bridge/update.sh
fi
"

if [ -f "$UPDATE_BIN" ]; then
    if ! grep -q "tuya-companion-bridge" "$UPDATE_BIN"; then
        echo "Appending update hook to existing $UPDATE_BIN..."
        echo "$HOOK_SNIPPET" >> "$UPDATE_BIN"
    else
        echo "Update hook already present in $UPDATE_BIN."
    fi
else
    echo "Creating standard $UPDATE_BIN with update hook..."
    cat <<'EOF' > "$UPDATE_BIN"
#!/usr/bin/env bash
set -e
echo "=== System Update (APT) ==="
apt-get update && apt-get upgrade -y

# === Tuya Companion Bridge Update Hook ===
if [ -f /opt/tuya-companion-bridge/update.sh ]; then
    bash /opt/tuya-companion-bridge/update.sh
fi
EOF
    chmod +x "$UPDATE_BIN"
fi

echo "=========================================================="
echo " Installation Complete!"
echo " Service Status:"
systemctl --no-pager status tuya-bridge.service
echo ""
echo " You can update the service at any time by running:"
echo "   update"
echo " or:"
echo "   /opt/tuya-companion-bridge/update.sh"
echo "=========================================================="
