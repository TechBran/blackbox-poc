#!/home/ai-black-box-fc/Desktop/blackbox_poc./blackbox_poc/Orchestrator/venv/bin/python3
"""
BlackBox Overseer - System Monitor Server
Real-time system stats with efficient caching.
"""
import http.server
import socketserver
import json
import psutil
import platform
import os
import time
import subprocess
from datetime import datetime
from urllib.parse import parse_qs, urlparse

PORT = 8067
DIRECTORY = os.path.dirname(os.path.abspath(__file__))

# Cache for stats (avoid recalculating on every request)
_stats_cache = {}
_cache_time = 0
CACHE_TTL = 0.5  # Cache for 500ms

def get_system_stats():
    """Collect system stats with caching."""
    global _stats_cache, _cache_time

    now = time.time()
    if now - _cache_time < CACHE_TTL and _stats_cache:
        return _stats_cache

    try:
        # CPU - interval=None uses cached value from last call (fast)
        cpu_percent = psutil.cpu_percent(interval=None)
        cpu_per_core = psutil.cpu_percent(percpu=True)
        cpu_freq = psutil.cpu_freq()

        # Memory
        mem = psutil.virtual_memory()

        # Disk
        disk = psutil.disk_usage('/')
        io = psutil.disk_io_counters()

        # Network
        net = psutil.net_io_counters()

        # Temperature (Linux specific)
        temps = {}
        try:
            if hasattr(psutil, "sensors_temperatures"):
                temps = psutil.sensors_temperatures()
        except:
            pass

        # Processes - only get memory, skip cpu_percent (which causes blocking)
        processes = []
        for proc in psutil.process_iter(['pid', 'name', 'memory_percent']):
            try:
                info = proc.info
                info['cpu_percent'] = 0  # Skip CPU to avoid blocking
                processes.append(info)
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                pass

        # Sort by memory percent
        processes.sort(key=lambda x: x.get('memory_percent', 0), reverse=True)
        top_processes = processes[:5]

        _stats_cache = {
            "timestamp": datetime.now().isoformat(),
            "system": {
                "hostname": platform.node(),
                "os": f"{platform.system()} {platform.release()}",
                "uptime": int(now - psutil.boot_time())
            },
            "cpu": {
                "total": cpu_percent,
                "cores": cpu_per_core,
                "freq": cpu_freq.current if cpu_freq else 0,
                "count": psutil.cpu_count()
            },
            "memory": {
                "total": mem.total,
                "available": mem.available,
                "percent": mem.percent,
                "used": mem.used
            },
            "disk": {
                "total": disk.total,
                "free": disk.free,
                "percent": disk.percent,
                "read_bytes": io.read_bytes if io else 0,
                "write_bytes": io.write_bytes if io else 0
            },
            "network": {
                "bytes_sent": net.bytes_sent,
                "bytes_recv": net.bytes_recv
            },
            "sensors": temps,
            "processes": top_processes
        }
        _cache_time = now

    except Exception as e:
        print(f"[Stats] Error: {e}")
        if not _stats_cache:
            _stats_cache = {"error": str(e)}

    return _stats_cache


def get_blackbox_logs(lines=200):
    """Fetch BlackBox service logs from journalctl."""
    try:
        result = subprocess.run(
            ['journalctl', '-u', 'blackbox.service', '-n', str(lines), '--no-pager'],
            capture_output=True,
            text=True,
            timeout=10
        )
        return {
            "success": True,
            "logs": result.stdout,
            "lines": lines,
            "source": "blackbox.service"
        }
    except subprocess.TimeoutExpired:
        return {"success": False, "error": "Timeout fetching logs"}
    except Exception as e:
        return {"success": False, "error": str(e)}


def get_monitor_logs(lines=200):
    """Fetch System Monitor local logs."""
    log_file = os.path.join(DIRECTORY, "monitor.log")
    try:
        if not os.path.exists(log_file):
            return {"success": True, "logs": "(No log file yet)", "lines": 0, "source": "monitor.log"}

        with open(log_file, 'r') as f:
            all_lines = f.readlines()

        # Get last N lines
        recent_lines = all_lines[-lines:] if len(all_lines) > lines else all_lines
        return {
            "success": True,
            "logs": ''.join(recent_lines),
            "lines": len(recent_lines),
            "total_lines": len(all_lines),
            "source": "monitor.log"
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


class MonitorHandler(http.server.SimpleHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=DIRECTORY, **kwargs)

    def log_message(self, format, *args):
        # Suppress logging for /api/stats
        if '/api/stats' not in str(args):
            super().log_message(format, *args)

    def end_headers(self):
        self.send_header('Cache-Control', 'no-store, no-cache, must-revalidate')
        self.send_header('Access-Control-Allow-Origin', '*')
        super().end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        if path == '/api/stats':
            stats = get_system_stats()
            content = json.dumps(stats).encode()

            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', len(content))
            self.end_headers()
            self.wfile.write(content)
            return

        if path == '/api/logs/blackbox':
            lines = int(query.get('lines', [200])[0])
            logs = get_blackbox_logs(lines)
            content = json.dumps(logs).encode()

            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', len(content))
            self.end_headers()
            self.wfile.write(content)
            return

        if path == '/api/logs/monitor':
            lines = int(query.get('lines', [200])[0])
            logs = get_monitor_logs(lines)
            content = json.dumps(logs).encode()

            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', len(content))
            self.end_headers()
            self.wfile.write(content)
            return

        return super().do_GET()

if __name__ == "__main__":
    os.chdir(DIRECTORY)

    # Initial CPU call to establish baseline (first call always returns 0)
    psutil.cpu_percent(interval=None)

    socketserver.TCPServer.allow_reuse_address = True
    print(f"Starting BlackBox Overseer on port {PORT}...")
    try:
        with socketserver.TCPServer(("", PORT), MonitorHandler) as httpd:
            print(f"Serving at http://localhost:{PORT}")
            httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping server...")
    except Exception as e:
        print(f"Error: {e}")
