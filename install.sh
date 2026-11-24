#!/usr/bin/env bash
set -e

APP_NAME="docker-manager"
INSTALL_BIN="$HOME/.local/bin/$APP_NAME"
LEGACY_BIN="$HOME/.local/bin/docker-dashboard.py"
SERVICE_DIR="$HOME/.config/systemd/user"
SERVICE_FILE="$SERVICE_DIR/$APP_NAME.service"
LEGACY_SERVICE="$SERVICE_DIR/docker-dashboard.service"
DESKTOP_DIR="$HOME/.local/share/applications"
DESKTOP_FILE="$DESKTOP_DIR/$APP_NAME.desktop"

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "==> Installing Docker Manager..."

# 1. Create target directories
mkdir -p "$HOME/.local/bin" "$SERVICE_DIR" "$DESKTOP_DIR"

# 2. Copy binary
cp -f "$DIR/docker_manager.py" "$INSTALL_BIN"
chmod +x "$INSTALL_BIN"

# Also maintain symlink for legacy service if needed
ln -sf "$INSTALL_BIN" "$LEGACY_BIN"

# 3. Stop old dashboard service if running
systemctl --user stop docker-dashboard.service 2>/dev/null || true
systemctl --user disable docker-dashboard.service 2>/dev/null || true
rm -f "$LEGACY_SERVICE"

# 4. Install and enable systemd user service
cp -f "$DIR/docker-manager.service" "$SERVICE_FILE"
systemctl --user daemon-reload
systemctl --user enable "$APP_NAME.service"
systemctl --user restart "$APP_NAME.service"

# 5. Install desktop application launcher
cp -f "$DIR/docker-manager.desktop" "$DESKTOP_FILE"
if which update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$DESKTOP_DIR" 2>/dev/null || true
fi

echo ""
echo "========================================================"
echo " ✓ Docker Manager successfully installed and running!"
echo " 👉 Web Interface: http://127.0.0.1:4444"
echo " 👉 Desktop App:   Launch 'Docker Manager' from app menu"
echo "========================================================"
