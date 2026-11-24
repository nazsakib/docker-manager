#!/usr/bin/env python3
"""
Lightweight Docker Web Manager for Personal Desktop
- Responsive on all screens (desktop, laptop, mobile, split-screen)
- Light & Dark theme toggle with persistent memory
- Collapsible Project Dropdowns (click dropdown/header to open & close services)
- Individual Container Dropdowns (Options menu for restart, stop, logs, inspect, copy ID)
- Expandable Container Details Drawer
- Smart SVG action icons (Start All, Stop All, Restart All with animated hover)
- Interactive, responsive manual Refresh button with spin feedback & toast confirmation
- Real-time CPU & RAM stats (Total & Per-Container)
"""
import os
import sys
import json
import time
import threading
import urllib.parse
import subprocess
from http.server import HTTPServer, BaseHTTPRequestHandler

PORT = 4444

STATS_CACHE = {}
STATS_TOTAL = {"cpu": "0.0%", "mem": "0 B", "mem_perc": "0%"}

def run_cmd(cmd):
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return res.returncode == 0, res.stdout, res.stderr
    except Exception as e:
        return False, "", str(e)

def parse_bytes(s):
    s = s.strip()
    units = {
        'B': 1, 'kB': 1000, 'MB': 1000**2, 'GB': 1000**3, 'TB': 1000**4,
        'KiB': 1024, 'MiB': 1024**2, 'GiB': 1024**3, 'TiB': 1024**4
    }
    for u in sorted(units.keys(), key=lambda x: -len(x)):
        if s.endswith(u):
            try:
                num = float(s[:-len(u)].strip())
                return num * units[u]
            except Exception:
                pass
    return 0

def format_bytes(b):
    for u in ['B', 'KiB', 'MiB', 'GiB', 'TiB']:
        if b < 1024 or u == 'TiB':
            return f"{b:.2f} {u}" if u not in ('B', 'KiB') else f"{b:.0f} {u}"
        b /= 1024

def stats_worker():
    """Background worker to continuously collect docker stats without blocking the HTTP server."""
    global STATS_CACHE, STATS_TOTAL
    while True:
        try:
            success, stdout, _ = run_cmd(["docker", "stats", "--no-stream", "--format", "json"])
            if success and stdout.strip():
                new_cache = {}
                tot_cpu = 0.0
                tot_mem_bytes = 0
                mem_limit_bytes = 0

                for line in stdout.strip().splitlines():
                    if not line.strip():
                        continue
                    try:
                        s = json.loads(line)
                        cid = s.get("ID", "")[:12]
                        cpu_str = s.get("CPUPerc", "0%").strip()
                        try:
                            tot_cpu += float(cpu_str.rstrip("%"))
                        except Exception:
                            pass

                        mem_usage_str = s.get("MemUsage", "")
                        used_s, _, limit_s = mem_usage_str.partition(" / ")
                        used_b = parse_bytes(used_s)
                        limit_b = parse_bytes(limit_s)
                        tot_mem_bytes += used_b
                        if limit_b > mem_limit_bytes:
                            mem_limit_bytes = limit_b

                        new_cache[cid] = {
                            "cpu": cpu_str,
                            "mem_usage": used_s,
                            "mem_limit": limit_s,
                            "mem_perc": s.get("MemPerc", "0%").strip(),
                            "mem_bytes": used_b
                        }
                    except Exception:
                        pass

                if mem_limit_bytes > 0:
                    tot_perc = (tot_mem_bytes / mem_limit_bytes) * 100
                    tot_mem_str = f"{format_bytes(tot_mem_bytes)} / {format_bytes(mem_limit_bytes)} ({tot_perc:.1f}%)"
                    tot_perc_str = f"{tot_perc:.1f}%"
                else:
                    tot_mem_str = format_bytes(tot_mem_bytes)
                    tot_perc_str = "0%"

                STATS_CACHE = new_cache
                STATS_TOTAL = {
                    "cpu": f"{tot_cpu:.1f}%",
                    "mem": tot_mem_str,
                    "mem_perc": tot_perc_str
                }
        except Exception:
            pass
        time.sleep(2.5)

def get_containers_with_stats():
    cmd = ["docker", "ps", "-a", "--format", "json"]
    success, stdout, stderr = run_cmd(cmd)
    if not success:
        return {"totals": {"running": 0, "stopped": 0, "total": 0, "cpu": "0.0%", "mem": "0 B"}, "containers": []}

    containers = []
    running_count = 0
    stopped_count = 0

    for line in stdout.strip().splitlines():
        if not line.strip():
            continue
        try:
            c = json.loads(line)
            cid = c.get("ID", "")[:12]
            state = c.get("State", "").lower()
            if state == "running":
                running_count += 1
            else:
                stopped_count += 1

            labels = c.get("Labels", "")
            project = "Standalone"
            for part in labels.split(","):
                if part.startswith("com.docker.compose.project="):
                    project = part.split("=", 1)[1].strip()
                    break

            ports_raw = c.get("Ports", "")
            ports = []
            for p in ports_raw.split(","):
                p = p.strip()
                if "->" in p:
                    host_part = p.split("->")[0].strip()
                    if ":" in host_part:
                        port_num = host_part.split(":")[-1]
                        ports.append({"display": p, "port": port_num, "url": f"http://localhost:{port_num}"})
                elif p:
                    ports.append({"display": p, "port": None, "url": None})

            # Match stats from cache
            c_stat = STATS_CACHE.get(cid, {})
            cpu_val = c_stat.get("cpu", "0.0%") if state == "running" else "--"
            mem_val = c_stat.get("mem_usage", "0 B") if state == "running" else "--"
            mem_perc = c_stat.get("mem_perc", "0%") if state == "running" else "--"
            mem_bytes = c_stat.get("mem_bytes", 0) if state == "running" else 0

            containers.append({
                "id": cid,
                "name": c.get("Names", "").lstrip("/"),
                "image": c.get("Image", ""),
                "state": state,
                "status": c.get("Status", ""),
                "command": c.get("Command", ""),
                "mounts": c.get("Mounts", ""),
                "networks": c.get("Networks", ""),
                "ports": ports,
                "project": project,
                "created": c.get("RunningFor", "") or c.get("CreatedAt", ""),
                "cpu": cpu_val,
                "mem": mem_val,
                "mem_perc": mem_perc,
                "mem_bytes": mem_bytes
            })
        except Exception:
            continue

    return {
        "totals": {
            "running": running_count,
            "stopped": stopped_count,
            "total": len(containers),
            "cpu": STATS_TOTAL.get("cpu", "0.0%"),
            "mem": STATS_TOTAL.get("mem", "0 B"),
            "mem_perc": STATS_TOTAL.get("mem_perc", "0%")
        },
        "containers": containers
    }

HTML_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Docker Manager</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;600&family=Plus+Jakarta+Sans:wght@400;500;600;700&display=swap" rel="stylesheet">
  <style>
    :root {
      --accent: #0284c7;
      --accent-hover: #0369a1;
      --accent-glow: rgba(2, 132, 199, 0.18);
      --success: #10b981;
      --success-bg: rgba(16, 185, 129, 0.12);
      --danger: #ef4444;
      --danger-bg: rgba(239, 68, 68, 0.12);
    }

    /* Dark Theme */
    body.dark-theme {
      --bg: #090d16;
      --header-bg: rgba(17, 24, 39, 0.94);
      --card-bg: #111827;
      --card-header: rgba(15, 23, 42, 0.9);
      --card-header-hover: rgba(30, 41, 59, 0.9);
      --card-border: #1f293d;
      --row-border: rgba(31, 41, 61, 0.7);
      --row-hover: rgba(30, 41, 59, 0.5);
      --details-bg: #0c1220;
      --text: #f1f5f9;
      --text-muted: #94a3b8;
      --text-dim: #64748b;
      --pill-bg: rgba(15, 23, 42, 0.7);
      --btn-bg: #1f2937;
      --btn-hover: #374151;
      --port-bg: #1e293b;
      --modal-bg: #0f172a;
      --modal-body-bg: #020617;
      --dropdown-bg: #162032;
      --dropdown-border: #29354d;
      --dropdown-hover: #1e293b;
    }

    /* Light Theme */
    body.light-theme {
      --bg: #f8fafc;
      --header-bg: rgba(255, 255, 255, 0.96);
      --card-bg: #ffffff;
      --card-header: #f8fafc;
      --card-header-hover: #f1f5f9;
      --card-border: #e2e8f0;
      --row-border: #f1f5f9;
      --row-hover: #f8fafc;
      --details-bg: #f8fafc;
      --text: #0f172a;
      --text-muted: #475569;
      --text-dim: #64748b;
      --pill-bg: #f1f5f9;
      --btn-bg: #f1f5f9;
      --btn-hover: #e2e8f0;
      --port-bg: #f1f5f9;
      --modal-bg: #ffffff;
      --modal-body-bg: #0f172a;
      --dropdown-bg: #ffffff;
      --dropdown-border: #cbd5e1;
      --dropdown-hover: #f1f5f9;
    }

    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      background-color: var(--bg);
      color: var(--text);
      font-family: 'Plus Jakarta Sans', system-ui, sans-serif;
      min-height: 100vh;
      display: flex;
      flex-direction: column;
      transition: background-color 0.2s ease, color 0.2s ease;
    }

    header {
      background: var(--header-bg);
      backdrop-filter: blur(14px);
      border-bottom: 1px solid var(--card-border);
      position: sticky;
      top: 0;
      z-index: 40;
      padding: 12px 24px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      flex-wrap: wrap;
    }
    .brand {
      display: flex;
      align-items: center;
      gap: 10px;
    }
    .brand-icon {
      width: 34px;
      height: 34px;
      background: linear-gradient(135deg, #0ea5e9, #2563eb);
      border-radius: 9px;
      display: flex;
      align-items: center;
      justify-content: center;
      box-shadow: 0 4px 12px rgba(14, 165, 233, 0.3);
      flex-shrink: 0;
    }
    .brand h1 {
      font-size: 17px;
      font-weight: 700;
      letter-spacing: -0.02em;
      white-space: nowrap;
    }
    .brand .badge {
      font-size: 11px;
      background: var(--pill-bg);
      color: var(--accent);
      padding: 3px 8px;
      border-radius: 9999px;
      border: 1px solid var(--card-border);
      font-weight: 600;
      white-space: nowrap;
    }

    .stats-bar {
      display: flex;
      gap: 10px;
      align-items: center;
      flex-wrap: wrap;
    }
    .stat-pill {
      font-size: 12px;
      color: var(--text-muted);
      display: inline-flex;
      align-items: center;
      gap: 6px;
      background: var(--pill-bg);
      padding: 5px 10px;
      border-radius: 8px;
      border: 1px solid var(--card-border);
      white-space: nowrap;
    }
    .stat-pill strong {
      color: var(--text);
      font-weight: 600;
    }
    .stat-pill-metric strong {
      color: var(--accent);
    }
    .dot {
      width: 8px;
      height: 8px;
      border-radius: 50%;
      flex-shrink: 0;
    }
    .dot-green { background: var(--success); box-shadow: 0 0 8px var(--success); }
    .dot-gray { background: var(--text-dim); }

    .header-actions {
      display: flex;
      gap: 8px;
      align-items: center;
      flex-wrap: wrap;
    }
    button {
      cursor: pointer;
      font-family: inherit;
      border: none;
      outline: none;
      transition: all 0.15s ease;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      gap: 6px;
      font-weight: 600;
    }
    .btn {
      padding: 7px 12px;
      border-radius: 8px;
      font-size: 12px;
    }
    .btn-secondary {
      background: var(--btn-bg);
      color: var(--text);
      border: 1px solid var(--card-border);
    }
    .btn-secondary:hover { background: var(--btn-hover); }
    .btn-secondary:active { transform: scale(0.97); }

    .btn-theme {
      width: 32px;
      height: 32px;
      padding: 0;
      border-radius: 8px;
      background: var(--btn-bg);
      color: var(--text);
      border: 1px solid var(--card-border);
      display: inline-flex;
      align-items: center;
      justify-content: center;
      flex-shrink: 0;
    }
    .btn-theme:hover { background: var(--btn-hover); }
    .btn-danger-outline {
      background: transparent;
      color: #f87171;
      border: 1px solid rgba(239, 68, 68, 0.3);
    }
    .btn-danger-outline:hover { background: var(--danger-bg); }

    .spin-anim {
      animation: spin 0.7s cubic-bezier(0.4, 0, 0.2, 1) infinite;
    }
    @keyframes spin {
      from { transform: rotate(0deg); }
      to { transform: rotate(360deg); }
    }

    main {
      flex: 1;
      max-width: 1400px;
      width: 100%;
      margin: 0 auto;
      padding: 20px 24px 60px;
    }

    /* Collapsible Project Section */
    .project-section {
      margin-bottom: 20px;
      background: var(--card-bg);
      border: 1px solid var(--card-border);
      border-radius: 14px;
      overflow: visible;
      box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.05);
      transition: border-color 0.15s ease;
    }
    .project-header {
      padding: 12px 18px;
      background: var(--card-header);
      border-bottom: 1px solid var(--card-border);
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 12px;
      flex-wrap: wrap;
      cursor: pointer;
      user-select: none;
      transition: background 0.15s ease;
      border-radius: 14px 14px 0 0;
    }
    .project-section.collapsed .project-header {
      border-bottom: none;
      border-radius: 14px;
    }
    .project-header:hover {
      background: var(--card-header-hover);
    }
    .project-title-wrap {
      display: flex;
      align-items: center;
      gap: 10px;
    }
    .chevron-box {
      width: 24px;
      height: 24px;
      display: flex;
      align-items: center;
      justify-content: center;
      border-radius: 6px;
      background: var(--pill-bg);
      border: 1px solid var(--card-border);
      flex-shrink: 0;
    }
    .chevron-icon {
      width: 15px;
      height: 15px;
      color: var(--accent);
      transition: transform 0.22s cubic-bezier(0.4, 0, 0.2, 1);
    }
    .project-section.collapsed .chevron-icon {
      transform: rotate(-90deg);
      color: var(--text-dim);
    }
    .project-title h2 {
      font-size: 14px;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.04em;
    }
    .project-dropdown-badge {
      font-size: 11px;
      color: var(--text-muted);
      background: var(--pill-bg);
      padding: 3px 9px;
      border-radius: 6px;
      border: 1px solid var(--card-border);
      display: inline-flex;
      align-items: center;
      gap: 5px;
      font-weight: 600;
      cursor: pointer;
      transition: all 0.15s ease;
    }
    .project-dropdown-badge:hover {
      color: var(--accent);
      border-color: var(--accent);
    }
    .project-meta {
      font-size: 12px;
      color: var(--text-dim);
      display: flex;
      gap: 8px;
      align-items: center;
    }
    .project-actions {
      display: flex;
      gap: 6px;
      flex-wrap: wrap;
    }

    /* Container List */
    .container-list {
      display: flex;
      flex-direction: column;
    }
    .project-section.collapsed .container-list {
      display: none;
    }

    /* Smart Action Button Styling */
    .btn-action-smart {
      padding: 5px 11px;
      border-radius: 6px;
      font-size: 11.5px;
      display: inline-flex;
      align-items: center;
      gap: 6px;
      font-weight: 600;
      transition: all 0.15s ease;
    }
    .btn-action-smart:active { transform: scale(0.96); }

    .btn-start-smart {
      color: #10b981;
      background: rgba(16, 185, 129, 0.1);
      border: 1px solid rgba(16, 185, 129, 0.28);
    }
    .btn-start-smart:hover {
      background: rgba(16, 185, 129, 0.22);
      border-color: #10b981;
    }
    .btn-start-smart:hover svg { transform: scale(1.15); transition: transform 0.2s ease; }

    .btn-stop-smart {
      color: #ef4444;
      background: rgba(239, 68, 68, 0.1);
      border: 1px solid rgba(239, 68, 68, 0.28);
    }
    .btn-stop-smart:hover {
      background: rgba(239, 68, 68, 0.22);
      border-color: #ef4444;
    }
    .btn-stop-smart:hover svg { transform: scale(1.15); transition: transform 0.2s ease; }

    .btn-restart-smart {
      color: var(--accent);
      background: var(--accent-glow);
      border: 1px solid rgba(2, 132, 199, 0.35);
    }
    .btn-restart-smart:hover {
      background: rgba(2, 132, 199, 0.28);
      border-color: var(--accent);
    }
    .btn-restart-smart:hover .restart-smart-icon {
      transform: rotate(180deg);
      transition: transform 0.4s cubic-bezier(0.4, 0, 0.2, 1);
    }

    /* Container Row: Adaptive & Fully Responsive */
    .container-item {
      border-bottom: 1px solid var(--row-border);
      position: relative;
    }
    .container-item:last-child { border-bottom: none; }
    
    .container-row {
      padding: 13px 18px;
      display: grid;
      grid-template-columns: minmax(180px, 1.4fr) 95px 85px 130px minmax(130px, 1fr) auto;
      align-items: center;
      gap: 14px;
      transition: background 0.12s ease;
      cursor: pointer;
    }
    .container-row:hover { background: var(--row-hover); }

    .col-name {
      display: flex;
      align-items: center;
      gap: 10px;
      min-width: 0;
    }
    .container-toggle-icon {
      color: var(--text-dim);
      transition: transform 0.15s ease;
      flex-shrink: 0;
    }
    .container-item.open .container-toggle-icon {
      transform: rotate(90deg);
      color: var(--accent);
    }
    .container-title {
      font-weight: 600;
      font-size: 13.5px;
      color: var(--text);
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }
    .container-image {
      font-size: 11px;
      color: var(--text-dim);
      font-family: 'JetBrains Mono', monospace;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
      margin-top: 2px;
    }
    .col-status {
      display: flex;
      align-items: center;
    }
    .status-badge {
      display: inline-flex;
      align-items: center;
      gap: 4px;
      font-size: 11px;
      font-weight: 600;
      padding: 2px 8px;
      border-radius: 9999px;
      white-space: nowrap;
    }
    .status-running {
      background: var(--success-bg);
      color: #10b981;
      border: 1px solid rgba(16, 185, 129, 0.25);
    }
    .status-stopped {
      background: rgba(148, 163, 184, 0.12);
      color: var(--text-muted);
      border: 1px solid rgba(148, 163, 184, 0.2);
    }
    .col-metric {
      font-family: 'JetBrains Mono', monospace;
      font-size: 12px;
      min-width: 0;
    }
    .metric-value {
      font-weight: 600;
      color: var(--text);
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }
    .metric-sub {
      font-size: 10px;
      color: var(--text-dim);
      margin-top: 2px;
      white-space: nowrap;
    }
    .col-ports {
      display: flex;
      flex-wrap: wrap;
      gap: 5px;
      min-width: 0;
    }
    .port-link {
      font-family: 'JetBrains Mono', monospace;
      font-size: 11px;
      background: var(--port-bg);
      color: var(--accent);
      padding: 3px 8px;
      border-radius: 6px;
      text-decoration: none;
      display: inline-flex;
      align-items: center;
      gap: 4px;
      border: 1px solid var(--card-border);
      transition: all 0.15s ease;
      white-space: nowrap;
    }
    .port-link:hover {
      border-color: var(--accent);
      background: var(--accent-glow);
    }
    .col-actions {
      display: flex;
      justify-content: flex-end;
      gap: 6px;
      flex-shrink: 0;
      align-items: center;
    }
    .btn-action {
      padding: 5px 9px;
      border-radius: 6px;
      font-size: 11.5px;
      background: var(--btn-bg);
      color: var(--text);
      border: 1px solid var(--card-border);
      white-space: nowrap;
    }
    .btn-action:hover { background: var(--btn-hover); }

    /* Dropdown Options for Individual Containers */
    .dropdown-wrapper {
      position: relative;
      display: inline-block;
    }
    .btn-dropdown-toggle {
      padding: 5px 9px;
      border-radius: 6px;
      font-size: 11.5px;
      background: var(--btn-bg);
      color: var(--text);
      border: 1px solid var(--card-border);
      display: inline-flex;
      align-items: center;
      gap: 4px;
    }
    .btn-dropdown-toggle:hover {
      background: var(--btn-hover);
      color: var(--accent);
      border-color: var(--accent);
    }
    .dropdown-menu {
      display: none;
      position: absolute;
      right: 0;
      top: calc(100% + 5px);
      background: var(--dropdown-bg);
      border: 1px solid var(--dropdown-border);
      border-radius: 9px;
      box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.35), 0 8px 10px -6px rgba(0, 0, 0, 0.25);
      min-width: 185px;
      z-index: 60;
      padding: 5px;
    }
    .dropdown-menu.show {
      display: block;
      animation: fadeInDown 0.15s ease;
    }
    @keyframes fadeInDown {
      from { opacity: 0; transform: translateY(-4px); }
      to { opacity: 1; transform: translateY(0); }
    }
    .dropdown-item {
      padding: 7px 11px;
      font-size: 11.5px;
      color: var(--text);
      border-radius: 6px;
      cursor: pointer;
      display: flex;
      align-items: center;
      gap: 8px;
      transition: background 0.12s ease;
      white-space: nowrap;
    }
    .dropdown-item:hover {
      background: var(--dropdown-hover);
      color: var(--accent);
    }
    .dropdown-item svg {
      width: 14px;
      height: 14px;
      flex-shrink: 0;
    }
    .dropdown-divider {
      height: 1px;
      background: var(--card-border);
      margin: 4px 0;
    }

    /* Expandable Container Details Drawer */
    .container-details {
      display: none;
      background: var(--details-bg);
      padding: 14px 20px;
      border-top: 1px dashed var(--card-border);
      font-size: 12px;
    }
    .container-item.open .container-details {
      display: block;
    }
    .details-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      gap: 12px;
    }
    .detail-item {
      display: flex;
      flex-direction: column;
      gap: 3px;
    }
    .detail-label {
      font-size: 10.5px;
      text-transform: uppercase;
      letter-spacing: 0.05em;
      color: var(--text-dim);
      font-weight: 600;
    }
    .detail-value {
      font-family: 'JetBrains Mono', monospace;
      color: var(--text);
      word-break: break-all;
    }

    /* Responsive Breakpoints */
    @media (max-width: 1024px) {
      .container-row {
        grid-template-columns: minmax(160px, 1fr) 90px 75px 120px auto;
      }
      .col-ports { display: none; }
    }

    @media (max-width: 768px) {
      header { padding: 10px 16px; }
      main { padding: 14px 14px 40px; }
      .container-row {
        display: flex;
        flex-direction: column;
        align-items: stretch;
        gap: 12px;
        padding: 14px 16px;
      }
      .row-top {
        display: flex;
        justify-content: space-between;
        align-items: center;
        width: 100%;
      }
      .row-metrics-bar {
        display: flex;
        justify-content: space-between;
        background: var(--pill-bg);
        padding: 8px 12px;
        border-radius: 8px;
        border: 1px solid var(--card-border);
      }
      .col-ports-mobile {
        display: flex;
        flex-wrap: wrap;
        gap: 5px;
      }
      .col-actions {
        justify-content: space-between;
        width: 100%;
        padding-top: 4px;
      }
      .col-actions .btn-action, .col-actions .dropdown-wrapper {
        flex: 1;
      }
      .col-actions .btn-dropdown-toggle {
        width: 100%;
        justify-content: center;
      }
    }

    /* Modal for Logs */
    .modal-overlay {
      position: fixed;
      top: 0; left: 0; right: 0; bottom: 0;
      background: rgba(0, 0, 0, 0.75);
      backdrop-filter: blur(4px);
      z-index: 100;
      display: none;
      align-items: center;
      justify-content: center;
      padding: 16px;
    }
    .modal-overlay.open { display: flex; }
    .modal {
      background: var(--modal-bg);
      border: 1px solid var(--card-border);
      border-radius: 14px;
      width: 100%;
      max-width: 900px;
      max-height: 80vh;
      display: flex;
      flex-direction: column;
      box-shadow: 0 25px 50px -12px rgba(0, 0, 0, 0.5);
    }
    .modal-header {
      padding: 14px 18px;
      border-bottom: 1px solid var(--card-border);
      display: flex;
      justify-content: space-between;
      align-items: center;
    }
    .modal-header h3 { font-size: 14px; font-weight: 600; }
    .modal-body {
      padding: 14px 18px;
      flex: 1;
      overflow-y: auto;
      background: var(--modal-body-bg);
      font-family: 'JetBrains Mono', monospace;
      font-size: 12px;
      color: #cbd5e1;
      white-space: pre-wrap;
      word-break: break-all;
      min-height: 320px;
      line-height: 1.6;
    }
    .toast {
      position: fixed;
      bottom: 24px;
      right: 24px;
      background: var(--card-bg);
      border: 1px solid var(--accent);
      color: var(--text);
      padding: 11px 18px;
      border-radius: 9px;
      font-size: 12.5px;
      font-weight: 500;
      box-shadow: 0 12px 30px rgba(0,0,0,0.35);
      display: none;
      z-index: 200;
      animation: toastSlide 0.2s ease;
    }
    @keyframes toastSlide {
      from { transform: translateY(10px); opacity: 0; }
      to { transform: translateY(0); opacity: 1; }
    }
  </style>
</head>
<body class="dark-theme">
  <header>
    <div class="brand">
      <div class="brand-icon">
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#ffffff" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M4 14.899A7 7 0 1 1 15.71 8h1.79a4.5 4.5 0 0 1 2.5 8.242"/>
          <path d="M12 12v9"/>
          <path d="m8 17 4 4 4-4"/>
        </svg>
      </div>
      <div>
        <h1>Docker Manager</h1>
      </div>
      <span class="badge">Native Linux</span>
    </div>

    <div class="stats-bar">
      <div class="stat-pill"><span class="dot dot-green"></span> <strong id="stat-running">0</strong> running</div>
      <div class="stat-pill"><span class="dot dot-gray"></span> <strong id="stat-stopped">0</strong> stopped</div>
      
      <!-- Live Metrics -->
      <div class="stat-pill stat-pill-metric" title="Total container CPU consumption">
        ⚡ CPU: <strong id="stat-cpu">0.0%</strong>
      </div>
      <div class="stat-pill stat-pill-metric" title="Total container RAM consumption">
        🧠 RAM: <strong id="stat-mem">0 B</strong>
      </div>
    </div>

    <div class="header-actions">
      <!-- Light/Dark Toggle -->
      <button class="btn-theme" id="theme-btn" onclick="toggleTheme()" title="Toggle Light / Dark mode">
        <svg id="theme-icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <circle cx="12" cy="12" r="5"/><path d="M12 1v2M12 21v2M4.2 4.2l1.4 1.4M18.4 18.4l1.4 1.4M1 12h2M21 12h2M4.2 19.8l1.4-1.4M18.4 5.6l1.4-1.4"/>
        </svg>
      </button>

      <!-- Working Interactive Refresh Button -->
      <button class="btn btn-secondary" id="refresh-btn" onclick="manualRefresh()" title="Refresh container status and stats">
        <svg id="refresh-icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
          <path d="M21 12a9 9 0 0 0-9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/>
          <path d="M3 3v5h5"/>
          <path d="M3 12a9 9 0 0 0 9 9 9.75 9.75 0 0 0 6.74-2.74L21 16"/>
          <path d="M16 21h5v-5"/>
        </svg>
        <span id="refresh-label">Refresh</span>
      </button>

      <button class="btn btn-danger-outline" onclick="pruneSystem()">
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 6h18"/><path d="M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6"/><path d="M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2"/></svg>
        Clean Cache
      </button>
    </div>
  </header>

  <main id="app-main">
    <div style="text-align: center; padding: 60px; color: var(--text-muted);">
      Loading containers and real-time stats...
    </div>
  </main>

  <div class="modal-overlay" id="logs-modal">
    <div class="modal">
      <div class="modal-header">
        <h3 id="logs-title">Container Logs</h3>
        <button class="btn btn-secondary btn-sm" onclick="closeLogs()">Close</button>
      </div>
      <div class="modal-body" id="logs-content">Loading logs...</div>
    </div>
  </div>

  <div class="toast" id="toast"></div>

  <script>
    // State of collapsed projects and open details
    let collapsedProjects = JSON.parse(localStorage.getItem('docker-collapsed-projects') || '{}');
    let openContainers = {};
    let activeDropdownId = null;
    let isRefreshing = false;

    // Theme Management
    function initTheme() {
      const savedTheme = localStorage.getItem('docker-theme') || 'dark';
      setTheme(savedTheme);
    }

    function toggleTheme() {
      const current = document.body.classList.contains('light-theme') ? 'light' : 'dark';
      const next = current === 'light' ? 'dark' : 'light';
      setTheme(next);
      localStorage.setItem('docker-theme', next);
    }

    function setTheme(theme) {
      const icon = document.getElementById('theme-icon');
      if (theme === 'light') {
        document.body.classList.remove('dark-theme');
        document.body.classList.add('light-theme');
        icon.innerHTML = '<path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z"/>';
      } else {
        document.body.classList.remove('light-theme');
        document.body.classList.add('dark-theme');
        icon.innerHTML = '<circle cx="12" cy="12" r="5"/><path d="M12 1v2M12 21v2M4.2 4.2l1.4 1.4M18.4 18.4l1.4 1.4M1 12h2M21 12h2M4.2 19.8l1.4-1.4M18.4 5.6l1.4-1.4"/>';
      }
    }

    initTheme();

    let toastTimer = null;
    function showToast(msg) {
      const t = document.getElementById('toast');
      t.innerText = msg;
      t.style.display = 'block';
      if (toastTimer) clearTimeout(toastTimer);
      toastTimer = setTimeout(() => { t.style.display = 'none'; }, 2800);
    }

    // Working manual refresh with animation & feedback
    async function manualRefresh() {
      if (isRefreshing) return;
      isRefreshing = true;
      const icon = document.getElementById('refresh-icon');
      const label = document.getElementById('refresh-label');
      if (icon) icon.classList.add('spin-anim');
      if (label) label.innerText = 'Refreshing...';

      try {
        const data = await fetchContainers(true);
        const running = data?.totals?.running ?? 0;
        const total = data?.totals?.total ?? 0;
        showToast(`✓ Refreshed: ${running} running of ${total} containers`);
      } catch (e) {
        showToast('Refresh failed: could not connect to Docker');
      } finally {
        setTimeout(() => {
          if (icon) icon.classList.remove('spin-anim');
          if (label) label.innerText = 'Refresh';
          isRefreshing = false;
        }, 550);
      }
    }

    // Dropdown options management for individual containers
    function toggleContainerDropdown(id, e) {
      if (e) e.stopPropagation();
      const menu = document.getElementById(id);
      const isAlreadyOpen = menu && menu.classList.contains('show');
      closeAllDropdowns();
      if (!isAlreadyOpen && menu) {
        menu.classList.add('show');
        activeDropdownId = id;
      }
    }

    function closeAllDropdowns() {
      document.querySelectorAll('.dropdown-menu.show').forEach(m => m.classList.remove('show'));
      activeDropdownId = null;
    }

    window.addEventListener('click', (e) => {
      if (!e.target.closest('.dropdown-wrapper')) {
        closeAllDropdowns();
      }
    });

    function copyToClipboard(text, msg) {
      navigator.clipboard.writeText(text).then(() => {
        showToast(msg || 'Copied to clipboard!');
      }).catch(() => {
        showToast('Failed to copy');
      });
    }

    async function fetchContainers(forceRender = false) {
      try {
        const res = await fetch('/api/containers');
        const data = await res.json();
        // If user currently has a dropdown menu open and it's not a manual refresh, do not disturb the DOM
        if (activeDropdownId && !forceRender) {
          updateHeaderStats(data.totals);
        } else {
          renderContainers(data);
        }
        return data;
      } catch (e) {
        showToast('Error loading containers');
        return null;
      }
    }

    function updateHeaderStats(totals) {
      if (!totals) return;
      document.getElementById('stat-running').innerText = totals.running || 0;
      document.getElementById('stat-stopped').innerText = totals.stopped || 0;
      document.getElementById('stat-cpu').innerText = totals.cpu || '0.0%';
      document.getElementById('stat-mem').innerText = totals.mem || '0 B';
    }

    function toggleProjectCollapse(project) {
      collapsedProjects[project] = !collapsedProjects[project];
      localStorage.setItem('docker-collapsed-projects', JSON.stringify(collapsedProjects));
      const section = document.getElementById(`project-sec-${project}`);
      if (section) {
        if (collapsedProjects[project]) {
          section.classList.add('collapsed');
        } else {
          section.classList.remove('collapsed');
        }
      }
    }

    function toggleContainerDetails(cid, e) {
      if (e && (e.target.closest('button') || e.target.closest('a') || e.target.closest('.dropdown-wrapper'))) return;
      openContainers[cid] = !openContainers[cid];
      const item = document.getElementById(`container-item-${cid}`);
      if (item) {
        if (openContainers[cid]) {
          item.classList.add('open');
        } else {
          item.classList.remove('open');
        }
      }
    }

    function renderContainers(data) {
      const totals = data.totals || {};
      const containers = data.containers || [];
      updateHeaderStats(totals);

      // Group by project
      const groups = {};
      containers.forEach(c => {
        const p = c.project || 'Standalone';
        if (!groups[p]) groups[p] = { list: [], cpuTotal: 0, memTotalBytes: 0 };
        groups[p].list.push(c);
        if (c.state === 'running' && c.cpu && c.cpu !== '--') {
          groups[p].cpuTotal += parseFloat(c.cpu.replace('%', '')) || 0;
        }
        if (c.state === 'running' && c.mem_bytes) {
          groups[p].memTotalBytes += c.mem_bytes;
        }
      });

      const main = document.getElementById('app-main');
      if (containers.length === 0) {
        main.innerHTML = '<div style="text-align: center; padding: 80px; color: var(--text-muted);">No containers found.</div>';
        return;
      }

      function formatBytes(bytes) {
        if (!bytes) return '0 B';
        const u = ['B', 'KiB', 'MiB', 'GiB'];
        let i = 0;
        while (bytes >= 1024 && i < u.length - 1) {
          bytes /= 1024;
          i++;
        }
        return bytes.toFixed(1) + ' ' + u[i];
      }

      const isMobile = window.innerWidth <= 768;

      let html = '';
      for (const [project, group] of Object.entries(groups)) {
        const projCpu = group.cpuTotal.toFixed(1) + '%';
        const projMem = formatBytes(group.memTotalBytes);
        const isCollapsed = !!collapsedProjects[project];
        const collapsedClass = isCollapsed ? 'collapsed' : '';

        html += `
          <div class="project-section ${collapsedClass}" id="project-sec-${project}">
            <div class="project-header" onclick="toggleProjectCollapse('${project}')" title="Click to open or close ${project} services">
              <div class="project-title-wrap">
                <!-- Dropdown Chevron Button -->
                <div class="chevron-box">
                  <svg class="chevron-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
                    <polyline points="6 9 12 15 18 9"></polyline>
                  </svg>
                </div>
                <div class="project-title">
                  <h2>${project}</h2>
                </div>
                <!-- Interactive Dropdown Badge (Clicking opens/closes services) -->
                <span class="project-dropdown-badge" title="Click to toggle services">
                  <span>${group.list.length} services</span>
                  <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="6 9 12 15 18 9"/></svg>
                </span>
              </div>

              <div class="project-meta">
                <span>CPU: <strong>${projCpu}</strong></span>
                <span>•</span>
                <span>RAM: <strong>${projMem}</strong></span>
              </div>

              <div class="project-actions" onclick="event.stopPropagation()">
                <!-- Smart Start All Button -->
                <button class="btn-action-smart btn-start-smart" onclick="projectAction('${project}', 'start')" title="Start all ${project} services">
                  <svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor"><polygon points="6 3 20 12 6 21 6 3"/></svg>
                  <span>Start All</span>
                </button>

                <!-- Smart Stop All Button -->
                <button class="btn-action-smart btn-stop-smart" onclick="projectAction('${project}', 'stop')" title="Stop all ${project} services">
                  <svg width="11" height="11" viewBox="0 0 24 24" fill="currentColor"><rect x="5" y="5" width="14" height="14" rx="2"/></svg>
                  <span>Stop All</span>
                </button>

                <!-- Smart Restart All Icon & Button -->
                <button class="btn-action-smart btn-restart-smart" onclick="projectAction('${project}', 'restart')" title="Restart all ${project} services">
                  <svg class="restart-smart-icon" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
                    <path d="M21 12a9 9 0 0 0-9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/>
                    <path d="M3 3v5h5"/>
                    <path d="M3 12a9 9 0 0 0 9 9 9.75 9.75 0 0 0 6.74-2.74L21 16"/>
                    <path d="M16 21h5v-5"/>
                  </svg>
                  <span>Restart All</span>
                </button>
              </div>
            </div>

            <!-- Services Underneath (Opened / Closed via dropdown) -->
            <div class="container-list">
        `;

        group.list.forEach(c => {
          const isRunning = c.state === 'running';
          const statusClass = isRunning ? 'status-running' : 'status-stopped';
          const statusDot = isRunning ? 'dot-green' : 'dot-gray';
          const isOpen = !!openContainers[c.id];
          const openClass = isOpen ? 'open' : '';

          let portsHtml = '';
          c.ports.forEach(p => {
            if (p.url) {
              portsHtml += `<a href="${p.url}" target="_blank" class="port-link">🔗 ${p.display}</a>`;
            } else if (p.display) {
              portsHtml += `<span class="port-link" style="color:var(--text-dim); border-color:transparent;">${p.display}</span>`;
            }
          });

          const dropdownHtml = `
            <div class="dropdown-wrapper" onclick="event.stopPropagation()">
              <button class="btn-dropdown-toggle" onclick="toggleContainerDropdown('drop-${c.id}', event)" title="Options for ${c.name}">
                <span>Options</span>
                <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="6 9 12 15 18 9"/></svg>
              </button>
              <div class="dropdown-menu" id="drop-${c.id}">
                ${isRunning
                  ? `<div class="dropdown-item" onclick="containerAction('${c.id}', 'stop'); closeAllDropdowns();">
                       <svg viewBox="0 0 24 24" fill="currentColor"><rect x="5" y="5" width="14" height="14" rx="2"/></svg>
                       <span>Stop Container</span>
                     </div>`
                  : `<div class="dropdown-item" onclick="containerAction('${c.id}', 'start'); closeAllDropdowns();">
                       <svg viewBox="0 0 24 24" fill="currentColor"><polygon points="6 3 20 12 6 21 6 3"/></svg>
                       <span>Start Container</span>
                     </div>`
                }
                <div class="dropdown-item" onclick="containerAction('${c.id}', 'restart'); closeAllDropdowns();">
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 12a9 9 0 0 0-9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/><path d="M3 3v5h5"/><path d="M3 12a9 9 0 0 0 9 9 9.75 9.75 0 0 0 6.74-2.74L21 16"/><path d="M16 21h5v-5"/></svg>
                  <span>Restart Container</span>
                </div>
                <div class="dropdown-item" onclick="viewLogs('${c.id}', '${c.name}'); closeAllDropdowns();">
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/></svg>
                  <span>View Live Logs</span>
                </div>
                <div class="dropdown-divider"></div>
                <div class="dropdown-item" onclick="toggleContainerDetails('${c.id}'); closeAllDropdowns();">
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/></svg>
                  <span>Container Details</span>
                </div>
                <div class="dropdown-item" onclick="copyToClipboard('${c.id}', 'Container ID copied: ${c.id}'); closeAllDropdowns();">
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>
                  <span>Copy ID (${c.id.substring(0, 8)})</span>
                </div>
                ${c.ports.length && c.ports[0].url ? `
                <div class="dropdown-item" onclick="window.open('${c.ports[0].url}', '_blank'); closeAllDropdowns();">
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg>
                  <span>Open Port: ${c.ports[0].port}</span>
                </div>` : ''}
              </div>
            </div>
          `;

          if (isMobile) {
            // Mobile Card View
            html += `
              <div class="container-item ${openClass}" id="container-item-${c.id}">
                <div class="container-row" onclick="toggleContainerDetails('${c.id}', event)">
                  <div class="row-top">
                    <div class="col-name">
                      <span class="dot ${statusDot}"></span>
                      <div style="min-width:0;">
                        <div class="container-title" title="${c.name}">${c.name}</div>
                        <div class="container-image" title="${c.image}">${c.image}</div>
                      </div>
                    </div>
                    <span class="status-badge ${statusClass}">${c.state.toUpperCase()}</span>
                  </div>

                  <div class="row-metrics-bar">
                    <div class="col-metric">
                      <span class="metric-sub">CPU: </span><span class="metric-value">${c.cpu}</span>
                    </div>
                    <div class="col-metric">
                      <span class="metric-sub">RAM: </span><span class="metric-value">${c.mem}</span>
                    </div>
                  </div>

                  ${portsHtml ? `<div class="col-ports-mobile">${portsHtml}</div>` : ''}

                  <div class="col-actions" onclick="event.stopPropagation()">
                    ${isRunning
                      ? `<button class="btn btn-action btn-stop" onclick="containerAction('${c.id}', 'stop')">Stop</button>`
                      : `<button class="btn btn-action btn-start" onclick="containerAction('${c.id}', 'start')">Start</button>`
                    }
                    <button class="btn btn-action btn-restart" onclick="containerAction('${c.id}', 'restart')">Restart</button>
                    ${dropdownHtml}
                  </div>
                </div>

                <!-- Expandable Details -->
                <div class="container-details">
                  <div class="details-grid">
                    <div class="detail-item"><span class="detail-label">ID</span><span class="detail-value">${c.id}</span></div>
                    <div class="detail-item"><span class="detail-label">Created</span><span class="detail-value">${c.created}</span></div>
                    <div class="detail-item"><span class="detail-label">Status</span><span class="detail-value">${c.status}</span></div>
                    <div class="detail-item"><span class="detail-label">Command</span><span class="detail-value">${c.command || 'None'}</span></div>
                  </div>
                </div>
              </div>
            `;
          } else {
            // Desktop Grid View
            html += `
              <div class="container-item ${openClass}" id="container-item-${c.id}">
                <div class="container-row" onclick="toggleContainerDetails('${c.id}', event)" title="Click to view details">
                  <div class="col-name">
                    <svg class="container-toggle-icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="9 18 15 12 9 6"></polyline></svg>
                    <span class="dot ${statusDot}"></span>
                    <div style="min-width:0;">
                      <div class="container-title" title="${c.name}">${c.name}</div>
                      <div class="container-image" title="${c.image}">${c.image}</div>
                    </div>
                  </div>

                  <div class="col-status">
                    <span class="status-badge ${statusClass}">
                      ${c.state.toUpperCase()}
                    </span>
                  </div>

                  <div class="col-metric">
                    <div class="metric-value">${c.cpu}</div>
                    <div class="metric-sub">CPU</div>
                  </div>

                  <div class="col-metric">
                    <div class="metric-value">${c.mem}</div>
                    <div class="metric-sub">${c.mem_perc !== '--' ? c.mem_perc + ' system' : 'Memory'}</div>
                  </div>

                  <div class="col-ports">
                    ${portsHtml || '<span style="font-size:11px; color:var(--text-dim);">-</span>'}
                  </div>

                  <div class="col-actions" onclick="event.stopPropagation()">
                    ${isRunning
                      ? `<button class="btn btn-action btn-stop" onclick="containerAction('${c.id}', 'stop')">Stop</button>`
                      : `<button class="btn btn-action btn-start" onclick="containerAction('${c.id}', 'start')">Start</button>`
                    }
                    <button class="btn btn-action btn-restart" onclick="containerAction('${c.id}', 'restart')">Restart</button>
                    ${dropdownHtml}
                  </div>
                </div>

                <!-- Expandable Details -->
                <div class="container-details">
                  <div class="details-grid">
                    <div class="detail-item"><span class="detail-label">ID</span><span class="detail-value">${c.id}</span></div>
                    <div class="detail-item"><span class="detail-label">Created</span><span class="detail-value">${c.created}</span></div>
                    <div class="detail-item"><span class="detail-label">Status</span><span class="detail-value">${c.status}</span></div>
                    <div class="detail-item"><span class="detail-label">Command</span><span class="detail-value">${c.command || 'None'}</span></div>
                  </div>
                </div>
              </div>
            `;
          }
        });

        html += `
            </div>
          </div>
        `;
      }

      main.innerHTML = html;
    }

    async function containerAction(id, action) {
      showToast(`${action.toUpperCase()}ing container...`);
      try {
        const res = await fetch(`/api/container/${id}/${action}`, { method: 'POST' });
        const data = await res.json();
        if (data.success) {
          showToast(`Container ${action}ed`);
          fetchContainers(true);
        } else {
          showToast('Failed: ' + (data.error || 'Unknown error'));
        }
      } catch (e) {
        showToast('Request failed');
      }
    }

    async function projectAction(project, action) {
      showToast(`${action.toUpperCase()}ing ${project}...`);
      try {
        const res = await fetch(`/api/project/${encodeURIComponent(project)}/${action}`, { method: 'POST' });
        const data = await res.json();
        showToast(`${project} ${action} complete`);
        fetchContainers(true);
      } catch (e) {
        showToast('Request failed');
      }
    }

    async function viewLogs(id, name) {
      document.getElementById('logs-title').innerText = `Logs: ${name}`;
      document.getElementById('logs-content').innerText = 'Loading logs...';
      document.getElementById('logs-modal').classList.add('open');

      try {
        const res = await fetch(`/api/logs/${id}`);
        const data = await res.json();
        document.getElementById('logs-content').innerText = data.logs || 'No logs available.';
        const body = document.getElementById('logs-content');
        body.scrollTop = body.scrollHeight;
      } catch (e) {
        document.getElementById('logs-content').innerText = 'Failed to load logs.';
      }
    }

    function closeLogs() {
      document.getElementById('logs-modal').classList.remove('open');
    }

    async function pruneSystem() {
      if (!confirm('Clean unused Docker caches and stopped containers?')) return;
      showToast('Pruning Docker caches...');
      try {
        const res = await fetch('/api/prune', { method: 'POST' });
        const data = await res.json();
        showToast('Cleaned up unused Docker resources!');
        fetchContainers(true);
      } catch (e) {
        showToast('Prune failed');
      }
    }

    window.addEventListener('resize', () => {
      fetchContainers(true);
    });

    fetchContainers(true);
    setInterval(() => {
      fetchContainers(false);
    }, 3500);
  </script>
</body>
</html>
"""

class DockerHandler(BaseHTTPRequestHandler):
    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        path = url.path

        if path == "/" or path == "/index.html":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(HTML_PAGE.encode("utf-8"))

        elif path == "/api/containers":
            data = get_containers_with_stats()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(data).encode("utf-8"))

        elif path.startswith("/api/logs/"):
            container_id = path.split("/api/logs/")[1]
            success, stdout, stderr = run_cmd(["docker", "logs", "--tail", "200", container_id])
            logs = stdout + stderr
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"success": success, "logs": logs}).encode("utf-8"))

        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        url = urllib.parse.urlparse(self.path)
        path = url.path

        if path.startswith("/api/container/"):
            parts = path.strip("/").split("/")
            if len(parts) == 4:
                container_id = parts[2]
                action = parts[3]
                if action in ("start", "stop", "restart"):
                    success, stdout, stderr = run_cmd(["docker", action, container_id])
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"success": success, "error": stderr}).encode("utf-8"))
                    return

        elif path.startswith("/api/project/"):
            parts = path.strip("/").split("/")
            if len(parts) == 4:
                project = urllib.parse.unquote(parts[2])
                action = parts[3]
                if action in ("start", "stop", "restart"):
                    data = get_containers_with_stats()
                    matching = [c["id"] for c in data.get("containers", []) if c["project"].lower() == project.lower()]
                    successes = []
                    for cid in matching:
                        s, _, _ = run_cmd(["docker", action, cid])
                        successes.append(s)
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"success": all(successes)}).encode("utf-8"))
                    return

        elif path == "/api/prune":
            success, stdout, stderr = run_cmd(["docker", "system", "prune", "-f"])
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"success": success, "output": stdout}).encode("utf-8"))
            return

        self.send_response(400)
        self.end_headers()

    def log_message(self, format, *args):
        pass

def main():
    t = threading.Thread(target=stats_worker, daemon=True)
    t.start()

    server = HTTPServer(("127.0.0.1", PORT), DockerHandler)
    print(f"Docker Dashboard running at http://127.0.0.1:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass

if __name__ == "__main__":
    main()
