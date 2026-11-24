# Docker Manager

A lightweight, zero-dependency local web dashboard and desktop control panel for Docker on Linux. 

Designed as a high-performance, native replacement for Docker Desktop that runs on **~15 MB RAM** and **0.0% idle CPU**.

---

## Overview

Docker Desktop on Linux often incurs heavy memory overhead and background virtualization layers. **Docker Manager** communicates directly with your native Linux Docker daemon over standard CLI commands, providing a desktop UI without the bloat.

### Highlights
- **Ultra Lightweight:** Written in pure Python 3 using standard library modules (`http.server`, `subprocess`, `threading`). No Node.js runtime, no npm dependencies, no electron overhead.
- **Compose Stack Aware:** Automatically detects and groups containers by Docker Compose project (`immich`, `bhni`, `standalone`).
- **Collapsible Groups:** One-click dropdowns to expand or collapse services per project, with state preserved in local storage.
- **Batch & Individual Controls:** Start All, Stop All, or Restart All across an entire stack, or control containers individually.
- **Container Options Dropdown:** Quick actions on every container for Start, Stop, Restart, live logs, technical inspection, and port shortcuts.
- **Live Metrics Telemetry:** Real-time CPU and memory metrics per container and system-wide totals, updated continuously via a non-blocking background thread.
- **Integrated Log Viewer:** View real-time container output in an embedded modal window.
- **Port Launcher:** Clickable host port links that open your web services directly in your default browser.
- **Dark & Light Modes:** Built-in theme switcher with persistent preferences.
- **Safe Docker Pruning:** One-click cleanup button to purge unused containers and dangling build caches.
- **Systemd User Integration:** Runs seamlessly in the background as a user-level service that requires no root permissions.

---

## Requirements

- Linux operating system
- Python 3.8+ (standard library only)
- Docker Engine / Docker CLI (user should be in the `docker` group)

---

## Quick Installation

Clone this repository and run the installer:

```bash
git clone https://github.com/nazsakib/docker-manager.git
cd docker-manager
chmod +x install.sh
./install.sh
```

The installer will:
1. Place the binary at `~/.local/bin/docker-manager`
2. Configure and enable a systemd user service (`docker-manager.service`)
3. Install a desktop launcher entry in your application menu

---

## Usage

### Accessing the Web Dashboard
Once installed, open your browser and navigate to:
```text
http://127.0.0.1:4444
```

### Launching as a Desktop Application
Search for **Docker Manager** in your desktop application launcher (GNOME, KDE, XFCE). It will open as an app window.

### Managing the Service

Check service status:
```bash
systemctl --user status docker-manager
```

Restart the service:
```bash
systemctl --user restart docker-manager
```

Stop the service:
```bash
systemctl --user stop docker-manager
```

View service logs:
```bash
journalctl --user -u docker-manager -f
```

---

## Project Structure

```text
docker-manager/
├── docker_manager.py           # Core application with embedded web interface
├── docker-manager.service      # Systemd user unit definition
├── docker-manager.desktop      # Desktop application launcher
├── install.sh                  # Automated installer
├── uninstall.sh                # Clean uninstallation script
├── .gitignore                  # Git exclusions
└── README.md                   # Documentation
```

---

## Uninstallation

To remove Docker Manager and its background service:

```bash
./uninstall.sh
```
