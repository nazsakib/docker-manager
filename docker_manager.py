#!/usr/bin/env python3
"""
Docker Manager - Lightweight Local Docker Web Control Panel
- Zero-overhead native Linux Docker management (< 20 MB RAM, 0% CPU)
- 4 High-Value Features:
  1. Interactive Web Terminal & Desktop Terminal Launcher (Exec Shell)
  2. Images Manager (Disk Space telemetry, In-Use indicators, Delete & Prune)
  3. Volumes & Mounts Explorer (Host mountpoints & attached containers)
  4. Real-time Search Filter Bar + Environment Variables & Network Inspector
- Collapsible Project Groups, Smart Action Icons, Light/Dark themes
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
STATS_TOTAL = {"cpu": "0.0%", "mem": "0 B", "mem_full": "0 B", "mem_perc": "0%"}

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
                    tot_mem_str = f"{format_bytes(tot_mem_bytes)} ({tot_perc:.1f}%)"
                    tot_mem_full = f"{format_bytes(tot_mem_bytes)} / {format_bytes(mem_limit_bytes)} ({tot_perc:.1f}%)"
                    tot_perc_str = f"{tot_perc:.1f}%"
                else:
                    tot_mem_str = format_bytes(tot_mem_bytes)
                    tot_mem_full = format_bytes(tot_mem_bytes)
                    tot_perc_str = "0%"

                STATS_CACHE = new_cache
                STATS_TOTAL = {
                    "cpu": f"{tot_cpu:.1f}%",
                    "mem": tot_mem_str,
                    "mem_full": tot_mem_full,
                    "mem_perc": tot_perc_str
                }
        except Exception:
            pass
        time.sleep(2.5)

def get_containers_with_stats():
    cmd = ["docker", "ps", "-a", "--format", "json"]
    success, stdout, stderr = run_cmd(cmd)
    if not success:
        return {"totals": {"running": 0, "stopped": 0, "total": 0, "cpu": "0.0%", "mem": "0 B", "mem_full": "0 B"}, "containers": []}

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

    # Match stats and calculate stack status
    stacks = {}
    for c in containers:
        p = c.get("project", "")
        if p and p != "Standalone":
            if p not in stacks:
                stacks[p] = {"running": 0, "stopped": 0, "total": 0}
            stacks[p]["total"] += 1
            if c.get("state") == "running":
                stacks[p]["running"] += 1
            else:
                stacks[p]["stopped"] += 1
        else:
            cname = c.get("name", "unknown")
            s_name = f"standalone_{cname}"
            stacks[s_name] = {
                "running": 1 if c.get("state") == "running" else 0,
                "stopped": 0 if c.get("state") == "running" else 1,
                "total": 1
            }

    running_stacks = sum(1 for p, s in stacks.items() if s["running"] > 0)
    stopped_stacks = sum(1 for p, s in stacks.items() if s["running"] == 0)

    return {
        "totals": {
            "running": running_stacks,
            "stopped": stopped_stacks,
            "running_containers": running_count,
            "stopped_containers": stopped_count,
            "total_containers": len(containers),
            "total_stacks": len(stacks),
            "cpu": STATS_TOTAL.get("cpu", "0.0%"),
            "mem": STATS_TOTAL.get("mem", "0 B"),
            "mem_full": STATS_TOTAL.get("mem_full", "0 B"),
            "mem_perc": STATS_TOTAL.get("mem_perc", "0%")
        },
        "containers": containers
    }

def get_images():
    """Retrieve all local docker images with in-use status and container attribution."""
    res = subprocess.run(["docker", "images", "--format", "json"], capture_output=True, text=True)
    c_res = subprocess.run(["docker", "ps", "-a", "--format", "json"], capture_output=True, text=True)
    
    active_images_map = {}
    for line in c_res.stdout.strip().splitlines():
        if line.strip():
            try:
                c = json.loads(line)
                c_name = c.get("Names", "").lstrip("/")
                img = c.get("Image", "")
                if img not in active_images_map:
                    active_images_map[img] = []
                active_images_map[img].append(c_name)
            except Exception:
                pass

    images = []
    tot_size_bytes = 0
    for line in res.stdout.strip().splitlines():
        if not line.strip():
            continue
        try:
            img = json.loads(line)
            repo = img.get("Repository", "")
            tag = img.get("Tag", "")
            full_name = f"{repo}:{tag}" if tag and tag != "<none>" else repo
            img_id = img.get("ID", "")[:12]
            size_str = img.get("Size", "")
            tot_size_bytes += parse_bytes(size_str)
            
            using_containers = []
            for target, cnames in active_images_map.items():
                if target in (full_name, repo, img_id) or (img_id in target):
                    using_containers.extend(cnames)
            using_containers = sorted(list(set(using_containers)))

            images.append({
                "id": img_id,
                "repository": repo,
                "tag": tag,
                "full_name": full_name,
                "size": size_str,
                "created": img.get("CreatedSince", "") or img.get("CreatedAt", ""),
                "in_use": len(using_containers) > 0,
                "containers": using_containers
            })
        except Exception:
            continue

    return {
        "count": len(images),
        "total_size": format_bytes(tot_size_bytes),
        "images": images
    }

def get_volumes():
    """Retrieve all local docker volumes with container mount associations."""
    res = subprocess.run(["docker", "volume", "ls", "--format", "json"], capture_output=True, text=True)
    c_res = subprocess.run(["docker", "ps", "-a", "--format", "json"], capture_output=True, text=True)
    
    vol_containers_map = {}
    for line in c_res.stdout.strip().splitlines():
        if line.strip():
            try:
                c = json.loads(line)
                c_name = c.get("Names", "").lstrip("/")
                mounts_str = c.get("Mounts", "")
                for m in mounts_str.split(","):
                    m = m.strip()
                    if m:
                        if m not in vol_containers_map:
                            vol_containers_map[m] = []
                        vol_containers_map[m].append(c_name)
            except Exception:
                pass

    volumes = []
    for line in res.stdout.strip().splitlines():
        if not line.strip():
            continue
        try:
            v = json.loads(line)
            name = v.get("Name", "")
            using = vol_containers_map.get(name, [])
            volumes.append({
                "name": name,
                "driver": v.get("Driver", "local"),
                "mountpoint": v.get("Mountpoint", ""),
                "labels": v.get("Labels", ""),
                "in_use": len(using) > 0,
                "containers": using
            })
        except Exception:
            continue

    return {
        "count": len(volumes),
        "volumes": volumes
    }

def get_container_inspect(cid):
    """Retrieve environment variables, networking, and mounts for a container."""
    res = subprocess.run(["docker", "inspect", cid], capture_output=True, text=True)
    if res.returncode != 0 or not res.stdout.strip():
        return None
    try:
        data = json.loads(res.stdout)[0]
        config = data.get("Config", {})
        env_pairs = []
        for e in config.get("Env", []):
            if "=" in e:
                k, v = e.split("=", 1)
                env_pairs.append({"key": k, "value": v})
            else:
                env_pairs.append({"key": e, "value": ""})

        networks = []
        for net_name, net_data in data.get("NetworkSettings", {}).get("Networks", {}).items():
            networks.append({
                "name": net_name,
                "ip": net_data.get("IPAddress", "") or "Host / None",
                "gateway": net_data.get("Gateway", "") or "-",
                "mac": net_data.get("MacAddress", "") or "-"
            })

        mounts = []
        for m in data.get("Mounts", []):
            mounts.append({
                "type": m.get("Type", "bind"),
                "name": m.get("Name", ""),
                "source": m.get("Source", ""),
                "destination": m.get("Destination", ""),
                "rw": m.get("RW", True)
            })

        return {
            "id": data.get("Id", "")[:12],
            "name": data.get("Name", "").lstrip("/"),
            "image": config.get("Image", ""),
            "command": " ".join(config.get("Cmd", [])) if config.get("Cmd") else "None",
            "created": data.get("Created", "")[:19].replace("T", " "),
            "status": data.get("State", {}).get("Status", ""),
            "running": data.get("State", {}).get("Running", False),
            "restart_policy": data.get("HostConfig", {}).get("RestartPolicy", {}).get("Name", "no"),
            "env": env_pairs,
            "networks": networks,
            "mounts": mounts
        }
    except Exception as e:
        return {"error": str(e)}

def exec_container_cmd(cid, cmd_str):
    """Execute a command inside a running container and return stdout/stderr."""
    chk = subprocess.run(["docker", "inspect", "-f", "{{.State.Running}}", cid], capture_output=True, text=True)
    if chk.stdout.strip() != "true":
        return False, "", "Container is not running. Please start the container first."
    res = subprocess.run(["docker", "exec", cid, "sh", "-c", cmd_str], capture_output=True, text=True, timeout=15)
    return res.returncode == 0, res.stdout, res.stderr

def launch_container_terminal(cid):
    """Launch an interactive desktop terminal attached to the container."""
    terms = [
        ["ghostty", "-e", "docker", "exec", "-it", cid, "/bin/sh"],
        ["gnome-terminal", "--", "docker", "exec", "-it", cid, "/bin/sh"],
        ["x-terminal-emulator", "-e", "docker", "exec", "-it", cid, "/bin/sh"]
    ]
    for t in terms:
        try:
            if subprocess.run(["which", t[0]], capture_output=True).returncode == 0:
                env = os.environ.copy()
                if "DISPLAY" not in env:
                    env["DISPLAY"] = ":0"
                subprocess.Popen(t, env=env)
                return True, t[0]
        except Exception:
            pass
    return False, "No desktop terminal emulator found"

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
      --warning: #f59e0b;
      --warning-bg: rgba(245, 158, 11, 0.12);
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
      --input-bg: #0f172a;
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
      --input-bg: #f1f5f9;
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

    /* Structured Adaptive Header */
    header {
      background: var(--header-bg);
      backdrop-filter: blur(14px);
      border-bottom: 1px solid var(--card-border);
      position: sticky;
      top: 0;
      z-index: 40;
      padding: 10px 24px;
      display: flex;
      flex-direction: column;
      gap: 10px;
    }
    .header-main-row {
      display: flex;
      align-items: center;
      justify-content: space-between;
      width: 100%;
      gap: 14px;
    }
    .brand {
      display: flex;
      align-items: center;
      gap: 10px;
      flex-shrink: 0;
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

    .header-actions {
      display: flex;
      gap: 8px;
      align-items: center;
      flex-shrink: 0;
    }

    .stats-bar {
      display: flex;
      gap: 8px;
      align-items: center;
      flex-wrap: wrap;
      padding-top: 6px;
      border-top: 1px dashed var(--card-border);
    }
    .stat-pill {
      font-size: 12px;
      color: var(--text-muted);
      display: inline-flex;
      align-items: center;
      gap: 6px;
      background: var(--pill-bg);
      padding: 5px 11px;
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

    /* Sub-Header: Search & Navigation Tabs */
    .controls-strip {
      background: var(--card-bg);
      border-bottom: 1px solid var(--card-border);
      padding: 10px 24px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      flex-wrap: wrap;
    }
    .nav-tabs {
      display: flex;
      gap: 6px;
      align-items: center;
    }
    .nav-tab {
      padding: 6px 14px;
      border-radius: 8px;
      font-size: 12.5px;
      color: var(--text-muted);
      background: transparent;
      border: 1px solid transparent;
      display: inline-flex;
      align-items: center;
      gap: 7px;
    }
    .nav-tab:hover {
      background: var(--pill-bg);
      color: var(--text);
    }
    .nav-tab.active {
      background: var(--pill-bg);
      color: var(--accent);
      border-color: var(--card-border);
      font-weight: 700;
    }
    .tab-icon {
      flex-shrink: 0;
      opacity: 0.85;
      transition: all 0.15s ease;
    }
    .nav-tab:hover .tab-icon {
      opacity: 1;
    }
    .nav-tab.active .tab-icon {
      opacity: 1;
      stroke: var(--accent);
    }
    .tab-badge {
      font-size: 10.5px;
      padding: 1px 6px;
      border-radius: 9999px;
      background: var(--btn-bg);
      border: 1px solid var(--card-border);
      color: var(--text-dim);
    }
    .nav-tab.active .tab-badge {
      background: var(--accent-glow);
      color: var(--accent);
      border-color: var(--accent);
    }

    .search-box {
      position: relative;
      flex: 1;
      max-width: 380px;
      min-width: 220px;
    }
    .search-input {
      width: 100%;
      padding: 7px 12px 7px 32px;
      font-size: 12px;
      font-family: inherit;
      border-radius: 8px;
      background: var(--input-bg);
      border: 1px solid var(--card-border);
      color: var(--text);
      outline: none;
      transition: all 0.15s ease;
    }
    .search-input:focus {
      border-color: var(--accent);
      box-shadow: 0 0 0 3px var(--accent-glow);
    }
    .search-icon {
      position: absolute;
      left: 10px;
      top: 50%;
      transform: translateY(-50%);
      color: var(--text-dim);
      pointer-events: none;
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
      min-width: 195px;
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
      padding: 16px 20px;
      border-top: 1px dashed var(--card-border);
      font-size: 12px;
    }
    .container-item.open .container-details {
      display: block;
    }
    .details-subtabs {
      display: flex;
      gap: 6px;
      border-bottom: 1px solid var(--card-border);
      padding-bottom: 10px;
      margin-bottom: 14px;
    }
    .details-subtab {
      padding: 4px 10px;
      border-radius: 6px;
      font-size: 11px;
      background: transparent;
      color: var(--text-muted);
    }
    .details-subtab.active {
      background: var(--pill-bg);
      color: var(--accent);
      border: 1px solid var(--card-border);
      font-weight: 600;
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

    /* Environment Variables Table */
    .env-table-wrap {
      max-height: 280px;
      overflow-y: auto;
      border: 1px solid var(--card-border);
      border-radius: 8px;
      background: var(--card-bg);
    }
    .env-table {
      width: 100%;
      border-collapse: collapse;
      font-family: 'JetBrains Mono', monospace;
      font-size: 11.5px;
    }
    .env-table th, .env-table td {
      padding: 6px 12px;
      text-align: left;
      border-bottom: 1px solid var(--row-border);
    }
    .env-table th {
      background: var(--card-header);
      color: var(--text-muted);
      font-size: 10.5px;
      text-transform: uppercase;
      position: sticky;
      top: 0;
    }
    .env-key {
      color: var(--accent);
      font-weight: 600;
      width: 35%;
      word-break: break-all;
    }
    .env-val {
      color: var(--text);
      word-break: break-all;
    }

    /* Images & Volumes Views */
    .view-panel {
      display: none;
    }
    .view-panel.active {
      display: block;
    }
    .panel-top-bar {
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 16px;
      padding: 10px 16px;
      background: var(--card-bg);
      border: 1px solid var(--card-border);
      border-radius: 10px;
      flex-wrap: wrap;
      gap: 10px;
    }
    .panel-meta {
      font-size: 12.5px;
      color: var(--text-muted);
      display: flex;
      gap: 14px;
      align-items: center;
    }
    .panel-meta strong {
      color: var(--text);
    }

    .data-table-card {
      background: var(--card-bg);
      border: 1px solid var(--card-border);
      border-radius: 12px;
      overflow-x: auto;
      -webkit-overflow-scrolling: touch;
      box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.05);
    }
    .data-table {
      width: 100%;
      min-width: 650px;
      border-collapse: collapse;
      font-size: 12px;
    }
    .data-table th, .data-table td {
      padding: 12px 16px;
      text-align: left;
      border-bottom: 1px solid var(--row-border);
      vertical-align: middle;
    }
    .data-table th {
      background: var(--card-header);
      color: var(--text-dim);
      font-size: 11px;
      text-transform: uppercase;
      letter-spacing: 0.04em;
      white-space: nowrap;
    }
    .data-table tr:hover {
      background: var(--row-hover);
    }
    .badge-in-use {
      font-size: 10.5px;
      padding: 3px 8px;
      border-radius: 9999px;
      background: var(--success-bg);
      color: #10b981;
      border: 1px solid rgba(16, 185, 129, 0.25);
      font-weight: 600;
      display: inline-flex;
      align-items: center;
      white-space: nowrap;
      line-height: 1.2;
    }
    .badge-unused {
      font-size: 10.5px;
      padding: 3px 8px;
      border-radius: 9999px;
      background: var(--warning-bg);
      color: var(--warning);
      border: 1px solid rgba(245, 158, 11, 0.25);
      font-weight: 600;
      display: inline-flex;
      align-items: center;
      white-space: nowrap;
      line-height: 1.2;
    }

    /* Terminal Console Modal */
    .terminal-modal-body {
      padding: 0;
      background: #050811;
      display: flex;
      flex-direction: column;
      height: 480px;
    }
    .terminal-chips {
      display: flex;
      gap: 6px;
      padding: 8px 14px;
      background: #0a0e1a;
      border-bottom: 1px solid #1e293b;
      overflow-x: auto;
    }
    .chip {
      padding: 3px 8px;
      border-radius: 4px;
      background: #1e293b;
      color: #94a3b8;
      font-size: 11px;
      font-family: 'JetBrains Mono', monospace;
      cursor: pointer;
      white-space: nowrap;
    }
    .chip:hover {
      background: var(--accent);
      color: #ffffff;
    }
    .terminal-screen {
      flex: 1;
      overflow-y: auto;
      padding: 14px;
      font-family: 'JetBrains Mono', monospace;
      font-size: 12px;
      color: #a5f3fc;
      line-height: 1.5;
      white-space: pre-wrap;
      word-break: break-all;
    }
    .terminal-prompt-bar {
      display: flex;
      align-items: center;
      gap: 8px;
      padding: 8px 12px;
      background: #090d16;
      border-top: 1px solid #1e293b;
    }
    .terminal-prompt-label {
      font-family: 'JetBrains Mono', monospace;
      font-size: 12px;
      color: #10b981;
      white-space: nowrap;
      font-weight: 600;
    }
    .terminal-input {
      flex: 1;
      background: transparent;
      border: none;
      outline: none;
      color: #ffffff;
      font-family: 'JetBrains Mono', monospace;
      font-size: 12.5px;
    }

    /* Responsive Breakpoints & Clean Layout */
    @media (max-width: 1119px) {
      header {
        padding: 10px 16px;
        gap: 10px;
      }
      .stats-bar {
        width: 100%;
        display: flex;
        justify-content: space-between;
        gap: 8px;
      }
      .stat-pill {
        flex: 1 1 auto;
        justify-content: center;
      }
    }

    @media (max-width: 850px) {
      .container-row {
        grid-template-columns: minmax(160px, 1fr) 90px 75px 120px auto;
      }
      .col-ports { display: none; }
      .project-header {
        flex-direction: column;
        align-items: stretch;
        gap: 10px;
        padding: 12px 14px;
      }
      .project-title-wrap {
        width: 100%;
        justify-content: space-between;
      }
      .project-meta {
        font-size: 11.5px;
      }
      .project-actions {
        width: 100%;
        display: grid;
        grid-template-columns: repeat(3, 1fr);
        gap: 6px;
      }
      .btn-action-smart {
        justify-content: center;
        padding: 6px 6px;
        font-size: 11px;
      }
    }

    @media (max-width: 640px) {
      header { padding: 8px 12px; }
      .controls-strip { padding: 8px 12px; }
      .search-box { max-width: 100%; }
      main { padding: 12px 12px 40px; }
      .brand h1 { font-size: 15px; }
      .brand .badge { display: none; }
      .stats-bar {
        display: grid;
        grid-template-columns: repeat(2, 1fr);
        gap: 6px;
      }
      .btn-clean-label { display: none; }
      .btn-danger-outline { padding: 6px 9px; }
      .btn-danger-outline::after { content: "Clean"; }
      .panel-top-bar {
        flex-direction: column;
        align-items: stretch;
        gap: 8px;
      }
      .panel-top-bar .btn {
        width: 100%;
        justify-content: center;
      }
      .data-table th, .data-table td {
        padding: 10px 12px;
      }
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

    /* Modal for Logs & Details */
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
      max-height: 85vh;
      display: flex;
      flex-direction: column;
      box-shadow: 0 25px 50px -12px rgba(0, 0, 0, 0.5);
      overflow: hidden;
    }
    .modal-header {
      padding: 12px 18px;
      border-bottom: 1px solid var(--card-border);
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 10px;
    }
    .modal-header h3 { font-size: 13.5px; font-weight: 600; }
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
  <!-- Top Application Bar -->
  <header>
    <div class="header-main-row">
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

      <div class="header-actions">
        <!-- Light/Dark Toggle -->
        <button class="btn-theme" id="theme-btn" onclick="toggleTheme()" title="Toggle Light / Dark mode">
          <svg id="theme-icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <circle cx="12" cy="12" r="5"/><path d="M12 1v2M12 21v2M4.2 4.2l1.4 1.4M18.4 18.4l1.4 1.4M1 12h2M21 12h2M4.2 19.8l1.4-1.4M18.4 5.6l1.4-1.4"/>
          </svg>
        </button>

        <!-- Interactive Refresh Button -->
        <button class="btn btn-secondary" id="refresh-btn" onclick="manualRefresh()" title="Refresh container status and stats">
          <svg id="refresh-icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
            <path d="M21 12a9 9 0 0 0-9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/>
            <path d="M3 3v5h5"/>
            <path d="M3 12a9 9 0 0 0 9 9 9.75 9.75 0 0 0 6.74-2.74L21 16"/>
            <path d="M16 21h5v-5"/>
          </svg>
          <span id="refresh-label">Refresh</span>
        </button>

        <button class="btn btn-danger-outline" onclick="pruneSystem()" title="Clean unused Docker caches and stopped containers">
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 6h18"/><path d="M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6"/><path d="M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2"/></svg>
          <span class="btn-clean-label">Clean Cache</span>
        </button>
      </div>
    </div>

    <!-- Live Telemetry Status Bar -->
    <div class="stats-bar">
      <div class="stat-pill" id="stat-pill-running" title="Running stacks / services"><span class="dot dot-green"></span> <strong id="stat-running">0</strong> running</div>
      <div class="stat-pill" id="stat-pill-stopped" title="Stopped stacks / services"><span class="dot dot-gray"></span> <strong id="stat-stopped">0</strong> stopped</div>
      
      <!-- Live Metrics -->
      <div class="stat-pill stat-pill-metric" title="Total container CPU consumption">
        ⚡ CPU: <strong id="stat-cpu">0.0%</strong>
      </div>
      <div class="stat-pill stat-pill-metric" id="stat-mem-pill" title="Total container RAM consumption">
        🧠 RAM: <strong id="stat-mem">0 B</strong>
      </div>
    </div>
  </header>

  <!-- Navigation Tabs & Live Search Strip -->
  <div class="controls-strip">
    <div class="nav-tabs">
      <button class="nav-tab active" id="tab-btn-containers" onclick="switchTab('containers')">
        <svg class="tab-icon" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/>
          <polyline points="3.27 6.96 12 12.01 20.73 6.96"/>
          <line x1="12" y1="22.08" x2="12" y2="12"/>
        </svg>
        <span>Containers</span>
        <span class="tab-badge" id="tab-count-containers">0</span>
      </button>

      <button class="nav-tab" id="tab-btn-images" onclick="switchTab('images')">
        <svg class="tab-icon" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <polygon points="12 2 2 7 12 12 22 7 12 2"/>
          <polyline points="2 17 12 22 22 17"/>
          <polyline points="2 12 12 17 22 12"/>
        </svg>
        <span>Images</span>
        <span class="tab-badge" id="tab-count-images">0</span>
      </button>

      <button class="nav-tab" id="tab-btn-volumes" onclick="switchTab('volumes')">
        <svg class="tab-icon" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <ellipse cx="12" cy="5" rx="9" ry="3"/>
          <path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5"/>
          <path d="M3 12c0 1.66 4 3 9 3s9-1.34 9-3"/>
        </svg>
        <span>Volumes</span>
        <span class="tab-badge" id="tab-count-volumes">0</span>
      </button>
    </div>

    <!-- Quick Search / Filter Bar -->
    <div class="search-box">
      <svg class="search-icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/></svg>
      <input type="text" id="global-search" class="search-input" placeholder="Filter containers, images, ports, stacks... (/)" oninput="filterCurrentView(this.value)">
    </div>
  </div>

  <main>
    <!-- View 1: Containers Panel -->
    <div class="view-panel active" id="panel-containers">
      <div id="containers-container">
        <div style="text-align: center; padding: 60px; color: var(--text-muted);">
          Loading containers and real-time stats...
        </div>
      </div>
    </div>

    <!-- View 2: Images Panel -->
    <div class="view-panel" id="panel-images">
      <div class="panel-top-bar">
        <div class="panel-meta">
          <span>Total Images: <strong id="img-meta-count">0</strong></span>
          <span>•</span>
          <span>Total Disk Size: <strong id="img-meta-size">0 B</strong></span>
        </div>
        <button class="btn btn-secondary" onclick="pruneImages()" title="Prune dangling / unused images">
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 6h18"/><path d="M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6"/><path d="M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2"/></svg>
          Prune Unused Images
        </button>
      </div>

      <div class="data-table-card">
        <table class="data-table">
          <thead>
            <tr>
              <th>Repository & Tag</th>
              <th>Image ID</th>
              <th>Size</th>
              <th>Created</th>
              <th>Status</th>
              <th style="text-align: right;">Action</th>
            </tr>
          </thead>
          <tbody id="images-tbody">
            <tr><td colspan="6" style="text-align:center; padding: 30px; color: var(--text-dim);">Loading images...</td></tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- View 3: Volumes Panel -->
    <div class="view-panel" id="panel-volumes">
      <div class="panel-top-bar">
        <div class="panel-meta">
          <span>Total Volumes: <strong id="vol-meta-count">0</strong></span>
        </div>
      </div>

      <div class="data-table-card">
        <table class="data-table">
          <thead>
            <tr>
              <th>Volume Name</th>
              <th>Driver</th>
              <th>Mountpoint (Host)</th>
              <th>Used By Container</th>
              <th style="text-align: right;">Action</th>
            </tr>
          </thead>
          <tbody id="volumes-tbody">
            <tr><td colspan="5" style="text-align:center; padding: 30px; color: var(--text-dim);">Loading volumes...</td></tr>
          </tbody>
        </table>
      </div>
    </div>
  </main>

  <!-- Modal for Logs -->
  <div class="modal-overlay" id="logs-modal">
    <div class="modal">
      <div class="modal-header">
        <h3 id="logs-title">Container Logs</h3>
        <button class="btn btn-secondary btn-sm" onclick="closeLogs()">Close</button>
      </div>
      <div class="modal-body" id="logs-content">Loading logs...</div>
    </div>
  </div>

  <!-- Modal for In-Browser Exec Terminal -->
  <div class="modal-overlay" id="terminal-modal">
    <div class="modal" style="max-width: 820px;">
      <div class="modal-header">
        <div style="display:flex; align-items:center; gap:8px;">
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="4 17 10 11 4 5"/><line x1="12" y1="19" x2="20" y2="19"/></svg>
          <h3 id="terminal-modal-title">Exec Terminal</h3>
        </div>
        <div style="display:flex; gap:6px;">
          <button class="btn btn-secondary btn-sm" onclick="launchDesktopTerminal()" title="Launch full interactive session in Ghostty / GNOME Terminal">
            🖥️ Desktop Terminal
          </button>
          <button class="btn btn-secondary btn-sm" onclick="closeTerminal()">Close</button>
        </div>
      </div>

      <div class="terminal-modal-body">
        <div class="terminal-chips">
          <span style="color:#64748b; font-size:10px; align-self:center; font-weight:600;">QUICK:</span>
          <span class="chip" onclick="quickTerminal('ls -la')">ls -la</span>
          <span class="chip" onclick="quickTerminal('env')">env</span>
          <span class="chip" onclick="quickTerminal('df -h')">df -h</span>
          <span class="chip" onclick="quickTerminal('ps aux')">ps aux</span>
          <span class="chip" onclick="quickTerminal('whoami')">whoami</span>
          <span class="chip" onclick="clearTerminalScreen()">clear</span>
        </div>
        <div class="terminal-screen" id="terminal-screen">Type a command below to execute inside the container...&#10;</div>
        <div class="terminal-prompt-bar">
          <span class="terminal-prompt-label" id="terminal-prompt-label">root@container:/#</span>
          <input type="text" class="terminal-input" id="terminal-input" placeholder="Type command (e.g. ls, env, cat file)..." onkeydown="handleTerminalKey(event)">
          <button class="btn btn-secondary btn-sm" onclick="sendTerminalCommand()">Run</button>
        </div>
      </div>
    </div>
  </div>

  <div class="toast" id="toast"></div>

  <script>
    // State Management
    let currentTab = 'containers';
    let cachedContainers = [];
    let cachedImages = [];
    let cachedVolumes = [];
    let searchQuery = '';
    let collapsedProjects = JSON.parse(localStorage.getItem('docker-collapsed-projects') || '{}');
    let openContainers = {};
    let containerInspectCache = {};
    let activeDropdownId = null;
    let isRefreshing = false;
    let activeTerminalCid = null;
    let activeTerminalName = null;
    let terminalHistory = [];
    let historyIdx = -1;

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

    // Tab Switching
    function switchTab(tab) {
      currentTab = tab;
      document.querySelectorAll('.nav-tab').forEach(t => t.classList.remove('active'));
      document.querySelectorAll('.view-panel').forEach(p => p.classList.remove('active'));
      
      const btn = document.getElementById(`tab-btn-${tab}`);
      const panel = document.getElementById(`panel-${tab}`);
      if (btn) btn.classList.add('active');
      if (panel) panel.classList.add('active');

      if (tab === 'images') fetchImages();
      if (tab === 'volumes') fetchVolumes();
    }

    // Global Search / Live Filtering
    function filterCurrentView(q) {
      searchQuery = (q || '').trim().toLowerCase();
      if (currentTab === 'containers') renderContainers({ containers: cachedContainers });
      if (currentTab === 'images') renderImages(cachedImages);
      if (currentTab === 'volumes') renderVolumes(cachedVolumes);
    }

    window.addEventListener('keydown', (e) => {
      if (e.key === '/' && document.activeElement.tagName !== 'INPUT') {
        e.preventDefault();
        const s = document.getElementById('global-search');
        if (s) { s.focus(); s.select(); }
      }
      if (e.key === 'Escape') {
        closeAllDropdowns();
        closeLogs();
        closeTerminal();
      }
    });

    // Refresh Logic
    async function manualRefresh() {
      if (isRefreshing) return;
      isRefreshing = true;
      const icon = document.getElementById('refresh-icon');
      const label = document.getElementById('refresh-label');
      if (icon) icon.classList.add('spin-anim');
      if (label) label.innerText = 'Refreshing...';

      try {
        if (currentTab === 'containers') await fetchContainers(true);
        if (currentTab === 'images') await fetchImages();
        if (currentTab === 'volumes') await fetchVolumes();
        showToast('✓ Updated');
      } catch (e) {
        showToast('Refresh failed');
      } finally {
        setTimeout(() => {
          if (icon) icon.classList.remove('spin-anim');
          if (label) label.innerText = 'Refresh';
          isRefreshing = false;
        }, 550);
      }
    }

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

    // Fetch Containers
    async function fetchContainers(forceRender = false) {
      try {
        const res = await fetch('/api/containers');
        const data = await res.json();
        cachedContainers = data.containers || [];
        document.getElementById('tab-count-containers').innerText = cachedContainers.length;

        if (data.totals) {
          updateHeaderStats(data.totals);
        }

        if (activeDropdownId && !forceRender) {
          // Dropdown is open, keep DOM stable
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
      const rElem = document.getElementById('stat-running');
      const sElem = document.getElementById('stat-stopped');
      if (rElem) rElem.innerText = totals.running !== undefined ? totals.running : 0;
      if (sElem) sElem.innerText = totals.stopped !== undefined ? totals.stopped : 0;

      const rPill = document.getElementById('stat-pill-running');
      const sPill = document.getElementById('stat-pill-stopped');
      if (rPill && totals.running_containers !== undefined) {
        rPill.title = `${totals.running} stack(s) running (${totals.running_containers} containers)`;
      }
      if (sPill && totals.stopped_containers !== undefined) {
        sPill.title = `${totals.stopped} stack(s) stopped (${totals.stopped_containers} containers)`;
      }

      const cpuElem = document.getElementById('stat-cpu');
      if (cpuElem) cpuElem.innerText = totals.cpu || '0.0%';

      const memElem = document.getElementById('stat-mem');
      if (memElem) {
        memElem.innerText = totals.mem || '0 B';
        const pill = document.getElementById('stat-mem-pill');
        if (pill && totals.mem_full) {
          pill.title = `Total container RAM: ${totals.mem_full}`;
        }
      }
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

    async function toggleContainerDetails(cid, e, defaultTab = 'overview') {
      if (e && (e.target.closest('button') || e.target.closest('a') || e.target.closest('.dropdown-wrapper'))) return;
      openContainers[cid] = !openContainers[cid];
      const item = document.getElementById(`container-item-${cid}`);
      if (item) {
        if (openContainers[cid]) {
          item.classList.add('open');
          // Fetch inspect data if not cached
          if (!containerInspectCache[cid]) {
            try {
              const res = await fetch(`/api/container/${cid}/inspect`);
              containerInspectCache[cid] = await res.json();
              populateInspectDrawer(cid, defaultTab);
            } catch (err) {}
          } else {
            populateInspectDrawer(cid, defaultTab);
          }
        } else {
          item.classList.remove('open');
        }
      }
    }

    function switchDrawerSubtab(cid, tab) {
      document.querySelectorAll(`#details-${cid} .details-subtab`).forEach(b => b.classList.remove('active'));
      const activeBtn = document.getElementById(`subtab-btn-${cid}-${tab}`);
      if (activeBtn) activeBtn.classList.add('active');
      populateInspectDrawer(cid, tab);
    }

    function populateInspectDrawer(cid, tab = 'overview') {
      const drawer = document.getElementById(`details-body-${cid}`);
      if (!drawer) return;
      const data = containerInspectCache[cid];
      if (!data) return;

      if (tab === 'overview') {
        drawer.innerHTML = `
          <div class="details-grid">
            <div class="detail-item"><span class="detail-label">Container ID</span><span class="detail-value">${data.id}</span></div>
            <div class="detail-item"><span class="detail-label">Created At</span><span class="detail-value">${data.created}</span></div>
            <div class="detail-item"><span class="detail-label">Status</span><span class="detail-value">${data.status}</span></div>
            <div class="detail-item"><span class="detail-label">Restart Policy</span><span class="detail-value">${data.restart_policy}</span></div>
            <div class="detail-item" style="grid-column: 1 / -1;"><span class="detail-label">Command</span><span class="detail-value">${data.command}</span></div>
          </div>
        `;
      } else if (tab === 'env') {
        const envList = data.env || [];
        if (envList.length === 0) {
          drawer.innerHTML = '<div style="color:var(--text-dim); padding: 12px;">No environment variables defined.</div>';
          return;
        }
        let rows = '';
        envList.forEach(e => {
          rows += `
            <tr>
              <td class="env-key">${e.key}</td>
              <td class="env-val">
                <span>${e.value}</span>
                <button class="btn btn-sm" style="padding:2px 6px; font-size:10px; margin-left:6px;" onclick="copyToClipboard('${e.value.replace(/'/g, "\\'")}', 'Copied ${e.key}!')">Copy</button>
              </td>
            </tr>
          `;
        });
        drawer.innerHTML = `
          <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:8px;">
            <span style="font-size:11px; color:var(--text-dim);">${envList.length} Variables</span>
            <button class="btn btn-secondary btn-sm" onclick="copyAllEnv('${cid}')">📋 Copy All</button>
          </div>
          <div class="env-table-wrap">
            <table class="env-table">
              <thead><tr><th>Variable</th><th>Value</th></tr></thead>
              <tbody>${rows}</tbody>
            </table>
          </div>
        `;
      } else if (tab === 'net') {
        let netHtml = '';
        (data.networks || []).forEach(n => {
          netHtml += `
            <div class="detail-item">
              <span class="detail-label">Network: ${n.name}</span>
              <span class="detail-value">IP: ${n.ip} | Gateway: ${n.gateway}</span>
            </div>
          `;
        });
        let mountHtml = '';
        (data.mounts || []).forEach(m => {
          mountHtml += `
            <div style="font-family:'JetBrains Mono', monospace; font-size:11px; padding: 4px 0; border-bottom: 1px solid var(--row-border);">
              <span style="color:var(--accent); font-weight:600;">[${m.type.toUpperCase()}]</span> 
              <span>${m.source || m.name}</span> &rarr; 
              <span style="color:var(--text-muted);">${m.destination}</span>
            </div>
          `;
        });
        drawer.innerHTML = `
          <div style="display:flex; flex-direction:column; gap:14px;">
            <div class="details-grid">${netHtml || '<div>No networks attached</div>'}</div>
            <div>
              <div class="detail-label" style="margin-bottom:6px;">Storage Mounts & Volumes (${(data.mounts || []).length})</div>
              <div style="background:var(--card-bg); border:1px solid var(--card-border); border-radius:8px; padding:10px;">
                ${mountHtml || 'No mounts'}
              </div>
            </div>
          </div>
        `;
      }
    }

    function copyAllEnv(cid) {
      const data = containerInspectCache[cid];
      if (!data || !data.env) return;
      const text = data.env.map(e => `${e.key}=${e.value}`).join('\\n');
      copyToClipboard(text, 'All environment variables copied!');
    }

    // Render Containers List
    function renderContainers(data) {
      let containers = data.containers || cachedContainers;
      if (searchQuery) {
        containers = containers.filter(c => 
          c.name.toLowerCase().includes(searchQuery) ||
          c.image.toLowerCase().includes(searchQuery) ||
          c.project.toLowerCase().includes(searchQuery) ||
          (c.ports && c.ports.some(p => (p.display || '').toLowerCase().includes(searchQuery)))
        );
      }

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

      const containerElem = document.getElementById('containers-container');
      if (containers.length === 0) {
        containerElem.innerHTML = `
          <div style="text-align: center; padding: 70px; color: var(--text-muted);">
            ${searchQuery ? `No containers match "${searchQuery}".` : 'No containers found.'}
          </div>
        `;
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

      const isMobile = window.innerWidth <= 850;

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
                <!-- Interactive Dropdown Badge -->
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
                <div class="dropdown-item" onclick="openTerminalModal('${c.id}', '${c.name}'); closeAllDropdowns();">
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="4 17 10 11 4 5"/><line x1="12" y1="19" x2="20" y2="19"/></svg>
                  <span>Exec Terminal</span>
                </div>
                <div class="dropdown-item" onclick="viewLogs('${c.id}', '${c.name}'); closeAllDropdowns();">
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/></svg>
                  <span>View Live Logs</span>
                </div>
                <div class="dropdown-divider"></div>
                <div class="dropdown-item" onclick="toggleContainerDetails('${c.id}', null, 'env'); closeAllDropdowns();">
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="11" width="18" height="11" rx="2" ry="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg>
                  <span>Inspect & Env Vars</span>
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

          const drawerHtml = `
            <div class="container-details" id="details-${c.id}">
              <div class="details-subtabs">
                <button class="details-subtab active" id="subtab-btn-${c.id}-overview" onclick="switchDrawerSubtab('${c.id}', 'overview')">Overview</button>
                <button class="details-subtab" id="subtab-btn-${c.id}-env" onclick="switchDrawerSubtab('${c.id}', 'env')">Environment Variables</button>
                <button class="details-subtab" id="subtab-btn-${c.id}-net" onclick="switchDrawerSubtab('${c.id}', 'net')">Network & Mounts</button>
              </div>
              <div id="details-body-${c.id}">
                <div class="details-grid">
                  <div class="detail-item"><span class="detail-label">ID</span><span class="detail-value">${c.id}</span></div>
                  <div class="detail-item"><span class="detail-label">Created</span><span class="detail-value">${c.created}</span></div>
                  <div class="detail-item"><span class="detail-label">Status</span><span class="detail-value">${c.status}</span></div>
                  <div class="detail-item"><span class="detail-label">Command</span><span class="detail-value">${c.command || 'None'}</span></div>
                </div>
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
                ${drawerHtml}
              </div>
            `;
          } else {
            // Desktop Grid View
            html += `
              <div class="container-item ${openClass}" id="container-item-${c.id}">
                <div class="container-row" onclick="toggleContainerDetails('${c.id}', event)" title="Click to view details and env vars">
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
                ${drawerHtml}
              </div>
            `;
          }
        });

        html += `
            </div>
          </div>
        `;
      }

      containerElem.innerHTML = html;
    }

    // Images Panel Fetch & Render
    async function fetchImages() {
      try {
        const res = await fetch('/api/images');
        const data = await res.json();
        cachedImages = data.images || [];
        document.getElementById('tab-count-images').innerText = data.count || 0;
        document.getElementById('img-meta-count').innerText = data.count || 0;
        document.getElementById('img-meta-size').innerText = data.total_size || '0 B';
        renderImages(cachedImages);
      } catch (err) {
        showToast('Failed to load images');
      }
    }

    function renderImages(images) {
      const tbody = document.getElementById('images-tbody');
      let filtered = images;
      if (searchQuery) {
        filtered = images.filter(img => 
          img.repository.toLowerCase().includes(searchQuery) ||
          img.tag.toLowerCase().includes(searchQuery) ||
          img.id.toLowerCase().includes(searchQuery)
        );
      }
      if (filtered.length === 0) {
        tbody.innerHTML = '<tr><td colspan="6" style="text-align:center; padding:30px; color:var(--text-dim);">No images found.</td></tr>';
        return;
      }
      let html = '';
      filtered.forEach(img => {
        const statusHtml = (img.containers && img.containers.length) 
          ? `<div style="display:flex; flex-wrap:wrap; gap:4px;">${img.containers.map(c => `<span class="badge-in-use">${c}</span>`).join('')}</div>`
          : (img.in_use ? `<span class="badge-in-use">In Use</span>` : `<span class="badge-unused">Unused</span>`);

        html += `
          <tr>
            <td>
              <div style="font-weight:600; color:var(--text);">${img.repository}</div>
              <div style="font-size:11px; color:var(--text-dim); font-family:'JetBrains Mono';">${img.tag}</div>
            </td>
            <td><code style="color:var(--accent); font-size:11.5px;">${img.id}</code></td>
            <td><strong>${img.size}</strong></td>
            <td style="color:var(--text-dim);">${img.created}</td>
            <td>${statusHtml}</td>
            <td style="text-align: right;">
              <button class="btn btn-danger-outline btn-sm" onclick="deleteImage('${img.id}', '${img.full_name}')" title="Delete image">Delete</button>
            </td>
          </tr>
        `;
      });
      tbody.innerHTML = html;
    }

    async function deleteImage(id, name) {
      if (!confirm(`Delete image ${name} (${id})?`)) return;
      showToast('Deleting image...');
      try {
        const res = await fetch('/api/image/delete', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ id: id })
        });
        const data = await res.json();
        if (data.success) {
          showToast('Image deleted');
          fetchImages();
        } else {
          showToast('Failed: ' + (data.error || 'Image may be in use'));
        }
      } catch (e) {
        showToast('Request failed');
      }
    }

    async function pruneImages() {
      if (!confirm('Prune all unused and dangling images to reclaim disk space?')) return;
      showToast('Pruning unused images...');
      try {
        const res = await fetch('/api/images/prune', { method: 'POST' });
        const data = await res.json();
        showToast('Unused images pruned!');
        fetchImages();
      } catch (e) {
        showToast('Prune failed');
      }
    }

    // Volumes Panel Fetch & Render
    async function fetchVolumes() {
      try {
        const res = await fetch('/api/volumes');
        const data = await res.json();
        cachedVolumes = data.volumes || [];
        document.getElementById('tab-count-volumes').innerText = data.count || 0;
        document.getElementById('vol-meta-count').innerText = data.count || 0;
        renderVolumes(cachedVolumes);
      } catch (err) {
        showToast('Failed to load volumes');
      }
    }

    function renderVolumes(volumes) {
      const tbody = document.getElementById('volumes-tbody');
      let filtered = volumes;
      if (searchQuery) {
        filtered = volumes.filter(v => 
          v.name.toLowerCase().includes(searchQuery) ||
          v.mountpoint.toLowerCase().includes(searchQuery) ||
          (v.containers && v.containers.some(c => c.toLowerCase().includes(searchQuery)))
        );
      }
      if (filtered.length === 0) {
        tbody.innerHTML = '<tr><td colspan="5" style="text-align:center; padding:30px; color:var(--text-dim);">No volumes found.</td></tr>';
        return;
      }
      let html = '';
      filtered.forEach(v => {
        const usingHtml = (v.containers && v.containers.length) 
          ? `<div style="display:flex; flex-wrap:wrap; gap:4px;">${v.containers.map(c => `<span class="badge-in-use">${c}</span>`).join('')}</div>`
          : (v.in_use ? `<span class="badge-in-use">In Use</span>` : `<span class="badge-unused">Unused</span>`);

        html += `
          <tr>
            <td>
              <div style="font-weight:600; font-family:'JetBrains Mono'; color:var(--text);">${v.name}</div>
            </td>
            <td><span class="tab-badge">${v.driver}</span></td>
            <td style="color:var(--text-dim); font-size:11px; font-family:'JetBrains Mono'; word-break:break-all;">${v.mountpoint}</td>
            <td>${usingHtml}</td>
            <td style="text-align: right;">
              <button class="btn btn-danger-outline btn-sm" onclick="deleteVolume('${v.name}')" title="Delete volume">Delete</button>
            </td>
          </tr>
        `;
      });
      tbody.innerHTML = html;
    }

    async function deleteVolume(name) {
      if (!confirm(`Delete volume ${name}? WARNING: Any data inside this volume will be permanently removed.`)) return;
      showToast('Deleting volume...');
      try {
        const res = await fetch('/api/volume/delete', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ name: name })
        });
        const data = await res.json();
        if (data.success) {
          showToast('Volume deleted');
          fetchVolumes();
        } else {
          showToast('Failed: ' + (data.error || 'Volume may be attached to a container'));
        }
      } catch (e) {
        showToast('Request failed');
      }
    }

    // In-Browser Exec Terminal Logic
    function openTerminalModal(cid, name) {
      activeTerminalCid = cid;
      activeTerminalName = name;
      document.getElementById('terminal-modal-title').innerText = `Exec Terminal: ${name}`;
      document.getElementById('terminal-prompt-label').innerText = `root@${name}:/#`;
      document.getElementById('terminal-screen').innerText = `Connected to container '${name}' (${cid}). Type any command or click shortcuts.\\n\\n`;
      document.getElementById('terminal-modal').classList.add('open');
      const input = document.getElementById('terminal-input');
      input.value = '';
      input.focus();
    }

    function closeTerminal() {
      document.getElementById('terminal-modal').classList.remove('open');
      activeTerminalCid = null;
      activeTerminalName = null;
    }

    function clearTerminalScreen() {
      document.getElementById('terminal-screen').innerText = `Console cleared for ${activeTerminalName}.\\n`;
    }

    function quickTerminal(cmd) {
      const input = document.getElementById('terminal-input');
      input.value = cmd;
      sendTerminalCommand();
    }

    function handleTerminalKey(e) {
      if (e.key === 'Enter') {
        sendTerminalCommand();
      } else if (e.key === 'ArrowUp') {
        if (terminalHistory.length && historyIdx > 0) {
          historyIdx--;
          document.getElementById('terminal-input').value = terminalHistory[historyIdx];
        }
      } else if (e.key === 'ArrowDown') {
        if (historyIdx < terminalHistory.length - 1) {
          historyIdx++;
          document.getElementById('terminal-input').value = terminalHistory[historyIdx];
        } else {
          historyIdx = terminalHistory.length;
          document.getElementById('terminal-input').value = '';
        }
      }
    }

    async function sendTerminalCommand() {
      const input = document.getElementById('terminal-input');
      const cmd = (input.value || '').trim();
      if (!cmd || !activeTerminalCid) return;

      terminalHistory.push(cmd);
      historyIdx = terminalHistory.length;
      input.value = '';

      const screen = document.getElementById('terminal-screen');
      screen.innerText += `\\nroot@${activeTerminalName}:/# ${cmd}\\n`;
      screen.scrollTop = screen.scrollHeight;

      try {
        const res = await fetch(`/api/container/${activeTerminalCid}/exec`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ command: cmd })
        });
        const data = await res.json();
        if (data.stdout) screen.innerText += data.stdout;
        if (data.stderr) screen.innerText += `\\n[stderr] ${data.stderr}`;
        if (!data.stdout && !data.stderr) screen.innerText += '[Done with exit code 0]\\n';
      } catch (err) {
        screen.innerText += `\\n[Error executing command]: ${err.message}\\n`;
      }
      screen.scrollTop = screen.scrollHeight;
    }

    async function launchDesktopTerminal() {
      if (!activeTerminalCid) return;
      showToast('Launching desktop terminal...');
      try {
        const res = await fetch(`/api/container/${activeTerminalCid}/terminal`, { method: 'POST' });
        const data = await res.json();
        if (data.success) {
          showToast(`Launched in ${data.terminal}!`);
        } else {
          showToast('Failed to launch: ' + (data.error || 'No terminal found'));
        }
      } catch (err) {
        showToast('Launch failed');
      }
    }

    // Container Actions
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
      if (currentTab === 'containers') renderContainers({ containers: cachedContainers });
    });

    // Initial Fetch & Auto Refresh
    async function initDashboard() {
      await fetchContainers(true);
      fetchImages();
      fetchVolumes();
    }
    initDashboard();

    setInterval(() => {
      if (currentTab === 'containers') fetchContainers(false);
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

        elif path == "/api/images":
            data = get_images()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(data).encode("utf-8"))

        elif path == "/api/volumes":
            data = get_volumes()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(data).encode("utf-8"))

        elif path.startswith("/api/container/") and path.endswith("/inspect"):
            parts = path.strip("/").split("/")
            if len(parts) == 4:
                cid = parts[2]
                inspect_data = get_container_inspect(cid)
                if inspect_data:
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps(inspect_data).encode("utf-8"))
                    return

            self.send_response(404)
            self.end_headers()

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

        # Read JSON body if available
        content_length = int(self.headers.get("Content-Length", 0))
        body = {}
        if content_length > 0:
            try:
                body = json.loads(self.rfile.read(content_length).decode("utf-8"))
            except Exception:
                pass

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

                elif action == "exec":
                    cmd_str = body.get("command", "")
                    success, stdout, stderr = exec_container_cmd(container_id, cmd_str)
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"success": success, "stdout": stdout, "stderr": stderr}).encode("utf-8"))
                    return

                elif action == "terminal":
                    success, term_name = launch_container_terminal(container_id)
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"success": success, "terminal": term_name}).encode("utf-8"))
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

        elif path == "/api/image/delete":
            img_id = body.get("id", "")
            success, stdout, stderr = run_cmd(["docker", "rmi", img_id])
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"success": success, "error": stderr}).encode("utf-8"))
            return

        elif path == "/api/images/prune":
            success, stdout, stderr = run_cmd(["docker", "image", "prune", "-f"])
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"success": success, "output": stdout}).encode("utf-8"))
            return

        elif path == "/api/volume/delete":
            vol_name = body.get("name", "")
            success, stdout, stderr = run_cmd(["docker", "volume", "rm", vol_name])
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"success": success, "error": stderr}).encode("utf-8"))
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
