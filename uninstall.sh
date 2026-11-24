#!/usr/bin/env bash
set -e

APP_NAME="docker-manager"
INSTALL_BIN="$HOME/.local/bin/$APP_NAME"
LEGACY_BIN="$HOME/.local/bin/docker-dashboard.py"
SERVICE_FILE="$HOME/.config/systemd/user/$APP_NAME.service"
LEGACY_SERVICE="$HOME/.config/systemd/user/docker-dashboard.service"
DESKTOP_FILE="$HOME/.local/share/applications/$APP_NAME.desktop"

echo "==> Uninstalling Docker Manager..."

systemctl --user stop "$APP_NAME.service" 2>/dev/null || true
systemctl --user disable "$APP_NAME.service" 2>/dev/null || true
systemctl --user stop docker-dashboard.service 2>/dev/null || true
systemctl --user disable docker-dashboard.service 2>/dev/null || true

rm -f "$SERVICE_FILE" "$LEGACY_SERVICE"
systemctl --user daemon-reload

rm -f "$INSTALL_BIN" "$LEGACY_BIN" "$DESKTOP_FILE"

echo "✓ Docker Manager has been uninstalled."
