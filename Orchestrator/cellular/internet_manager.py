#!/usr/bin/env python3
"""
internet_manager.py - Cellular Internet Failover Monitor

PASSIVE monitor for the SIM8260G-M2 data modem. NetworkManager handles the
actual connection lifecycle (autoconnect, retries, routing). This manager:

  1. Polls modem/signal/connection status via mmcli/nmcli (read-only)
  2. Verifies internet connectivity via curl --interface wwan0
  3. Logs events to a circular buffer for the Portal UI
  4. Exposes deep_reconnect() for manual use (radio cycle via AT+CFUN)

It does NOT:
  - Call nmcli connection up/down/modify during normal operation
  - Restart ModemManager
  - Interfere with NM's autoconnect or routing

NM is configured with:
  - connection.autoconnect=yes, autoconnect-retries=0 (infinite)
  - ipv4.route-metric=700 (ethernet 100 wins when present)
  - ipv6.method=disabled (prevents IPv6 timeout killing bearer)
  - rp_filter=2 (loose, for multi-homed connectivity checks)
"""

import asyncio
import json
import re
import time
import traceback
import xml.etree.ElementTree as ET
from collections import deque
from pathlib import Path
from typing import Any, Dict, List, Optional


POLL_INTERVAL = 10.0
EVENT_LOG_MAX = 100
VERIFY_INTERVAL = 60.0  # Re-verify internet every 60s when connected
PROVIDER_DB_PATH = Path("/usr/share/mobile-broadband-provider-info/serviceproviders.xml")


async def _run_cmd(cmd: List[str], timeout: float = 15.0) -> tuple:
    """Run a subprocess, return (returncode, stdout, stderr)."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return (
            proc.returncode,
            stdout.decode("utf-8", errors="replace").strip(),
            stderr.decode("utf-8", errors="replace").strip(),
        )
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except Exception:
            pass
        return (-1, "", "timeout")
    except FileNotFoundError:
        return (-1, "", f"command not found: {cmd[0]}")
    except Exception as e:
        return (-1, "", str(e))


async def _run_cmd_json(cmd: List[str], timeout: float = 15.0) -> Optional[dict]:
    """Run a subprocess expecting JSON output, return parsed dict or None."""
    rc, stdout, stderr = await _run_cmd(cmd, timeout)
    if rc != 0 or not stdout:
        return None
    try:
        return json.loads(stdout)
    except json.JSONDecodeError:
        return None


class CellularInternetManager:
    """
    Passive monitor for cellular internet failover.

    Polls status, verifies connectivity, logs events. Does NOT manage
    the connection — NetworkManager handles that via autoconnect.
    """

    def __init__(self, connection_name: str = "5G-Internet"):
        self._connection_name = connection_name
        self._task: Optional[asyncio.Task] = None
        self._running = False

        # State (read-only from NM/MM)
        self._state = "unknown"  # unknown, disconnected, connecting, connected
        self._modem_index: Optional[int] = None
        self._modem_path: Optional[str] = None
        self._connected_since: Optional[float] = None

        # Cached info
        self._modem_info: Dict[str, Any] = {}
        self._signal_info: Dict[str, Any] = {}
        self._connection_info: Dict[str, Any] = {}
        self._routing_info: Dict[str, Any] = {}
        self._signal_poll_setup = False

        # Internet verification
        self._internet_verified = False
        self._last_verify_time = 0.0

        # Event log
        self._event_log: deque = deque(maxlen=EVENT_LOG_MAX)

    # =========================================================================
    # Lifecycle
    # =========================================================================

    async def start(self):
        if self._running:
            return
        self._running = True
        self._log_event("info", "Cellular internet monitor started (passive mode)")
        self._task = asyncio.create_task(self._monitor_loop())
        print("[CELL-INET] Monitor started (passive — NM manages connection)")

    async def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        self._log_event("info", "Cellular internet monitor stopped")
        print("[CELL-INET] Monitor stopped")

    # =========================================================================
    # Read-Only Status Gathering
    # =========================================================================

    async def discover_modem(self) -> Optional[int]:
        """Find the modem index via mmcli -L -J."""
        data = await _run_cmd_json(["mmcli", "-L", "-J"])
        if not data:
            self._modem_index = None
            self._modem_path = None
            return None

        modems = data.get("modem-list") or []
        if not modems:
            self._modem_index = None
            self._modem_path = None
            self._signal_poll_setup = False
            return None

        path = modems[0]
        match = re.search(r"/Modem/(\d+)$", path)
        if match:
            new_idx = int(match.group(1))
            if new_idx != self._modem_index or not self._signal_poll_setup:
                await _run_cmd(["mmcli", "-m", str(new_idx), "--signal-setup=10"])
                self._signal_poll_setup = True
            self._modem_index = new_idx
            self._modem_path = path
            return self._modem_index

        self._modem_index = None
        self._modem_path = None
        self._signal_poll_setup = False
        return None

    async def get_modem_info(self) -> Dict[str, Any]:
        if self._modem_index is None:
            self._modem_info = {}
            return {}

        data = await _run_cmd_json(["mmcli", "-m", str(self._modem_index), "-J"])
        if not data:
            self._modem_info = {}
            return {}

        modem = data.get("modem", {})
        generic = modem.get("generic", {})
        three_gpp = modem.get("3gpp", {})

        info = {
            "manufacturer": generic.get("manufacturer", ""),
            "model": generic.get("model", ""),
            "revision": generic.get("revision", ""),
            "equipment_id": generic.get("equipment-identifier", ""),
            "state": generic.get("state", ""),
            "power_state": generic.get("power-state", ""),
            "access_technologies": generic.get("access-technologies", []),
            "signal_quality": generic.get("signal-quality", {}).get("value", 0),
            "operator_code": three_gpp.get("operator-code", ""),
            "operator_name": three_gpp.get("operator-name", ""),
            "registration_state": three_gpp.get("registration-state", ""),
            "mcc": three_gpp.get("operator-code", "")[:3] if three_gpp.get("operator-code") else "",
            "mnc": three_gpp.get("operator-code", "")[3:] if three_gpp.get("operator-code") else "",
        }
        self._modem_info = info
        return info

    async def get_signal_details(self) -> Dict[str, Any]:
        if self._modem_index is None:
            return {}

        data = await _run_cmd_json(["mmcli", "-m", str(self._modem_index), "--signal-get", "-J"])
        if not data:
            return {}

        def _clean(val):
            if val is None or val == "--":
                return "--"
            try:
                return "--" if float(val) <= -32000 else val
            except (ValueError, TypeError):
                return val

        signal = data.get("modem", {}).get("signal", {})
        result = {}
        lte = signal.get("lte", {})
        if lte:
            result["lte"] = {
                "rssi": _clean(lte.get("rssi", "--")),
                "rsrp": _clean(lte.get("rsrp", "--")),
                "rsrq": _clean(lte.get("rsrq", "--")),
                "snr": _clean(lte.get("s/n", "--")),
            }
        nr5g = signal.get("5g", {})
        if nr5g:
            result["nr5g"] = {
                "rsrp": _clean(nr5g.get("rsrp", "--")),
                "rsrq": _clean(nr5g.get("rsrq", "--")),
                "snr": _clean(nr5g.get("s/n", "--")),
            }
        self._signal_info = result
        return result

    async def get_connection_status(self) -> Dict[str, Any]:
        rc, stdout, stderr = await _run_cmd(
            ["nmcli", "-t", "-f", "GENERAL.STATE,IP4.ADDRESS,IP4.GATEWAY,IP4.DNS,connection.autoconnect",
             "connection", "show", self._connection_name]
        )
        if rc != 0:
            self._connection_info = {"active": False}
            return self._connection_info

        info: Dict[str, Any] = {"active": False}
        for line in stdout.splitlines():
            if ":" in line:
                key, _, val = line.partition(":")
                key, val = key.strip(), val.strip()
                if "STATE" in key:
                    info["state"] = val
                    info["active"] = "activated" in val.lower()
                elif "ADDRESS" in key:
                    info["ip"] = val
                elif "GATEWAY" in key:
                    info["gateway"] = val
                elif "DNS" in key:
                    info.setdefault("dns", []).append(val)
                elif "autoconnect" in key.lower():
                    info["autoconnect"] = val

        rc2, stdout2, _ = await _run_cmd(
            ["nmcli", "-t", "-f", "gsm.apn,ipv4.route-metric",
             "connection", "show", self._connection_name]
        )
        if rc2 == 0:
            for line in stdout2.splitlines():
                if ":" in line:
                    key, _, val = line.partition(":")
                    if "apn" in key.lower():
                        info["apn"] = val
                    elif "route-metric" in key.lower():
                        info["metric"] = val

        self._connection_info = info
        return info

    async def get_routing_status(self) -> Dict[str, Any]:
        rc, stdout, _ = await _run_cmd(["ip", "route", "show", "default"])
        if rc != 0:
            return {}

        routes = []
        for line in stdout.splitlines():
            parts = line.split()
            route: Dict[str, str] = {}
            for i, p in enumerate(parts):
                if p == "dev" and i + 1 < len(parts):
                    route["interface"] = parts[i + 1]
                elif p == "metric" and i + 1 < len(parts):
                    route["metric"] = parts[i + 1]
                elif p == "via" and i + 1 < len(parts):
                    route["gateway"] = parts[i + 1]
            if route:
                routes.append(route)

        routes.sort(key=lambda r: int(r.get("metric", "9999")))
        result = {
            "routes": routes,
            "primary_interface": routes[0].get("interface", "") if routes else "",
            "cellular_active": any(r.get("interface", "").startswith("wwan") for r in routes),
        }
        self._routing_info = result
        return result

    # =========================================================================
    # Internet Verification (read-only — curl with SO_BINDTODEVICE)
    # =========================================================================

    async def verify_internet(self) -> bool:
        """Verify connectivity through wwan0 using curl --interface (SO_BINDTODEVICE)."""
        rc, stdout, _ = await _run_cmd(
            ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code} %{time_total}",
             "--interface", "wwan0", "--connect-timeout", "5", "--max-time", "10",
             "http://connectivitycheck.gstatic.com/generate_204"],
            timeout=15.0
        )
        self._last_verify_time = time.time()
        if rc == 0 and stdout.startswith("204"):
            parts = stdout.split()
            try:
                latency_ms = f"{float(parts[1]) * 1000:.0f}ms"
            except (ValueError, IndexError):
                latency_ms = "?"
            if not self._internet_verified:
                self._log_event("info", f"Internet verified ({latency_ms})")
            self._internet_verified = True
            return True
        else:
            if self._internet_verified:
                self._log_event("error", f"Internet verification failed (rc={rc})")
            self._internet_verified = False
            return False

    # =========================================================================
    # APN Auto-Detection (used by deep_reconnect only)
    # =========================================================================

    def auto_detect_apn(self, mcc: str, mnc: str) -> Optional[str]:
        if not PROVIDER_DB_PATH.exists():
            return None
        try:
            tree = ET.parse(str(PROVIDER_DB_PATH))
            root = tree.getroot()
            for country in root.iter("country"):
                for provider in country.iter("provider"):
                    for gsm in provider.iter("gsm"):
                        match = False
                        for nid in gsm.iter("network-id"):
                            if nid.get("mcc") == mcc and nid.get("mnc") == mnc:
                                match = True
                                break
                        if not match:
                            continue
                        candidates = []
                        for apn_elem in gsm.iter("apn"):
                            apn_value = apn_elem.get("value", "")
                            usage = apn_elem.find("usage")
                            utype = usage.get("type", "") if usage is not None else ""
                            apn_lower = apn_value.lower()
                            if "ims" in apn_lower or utype == "wap":
                                continue
                            score = 10 if "internet" in apn_lower else 0
                            score += 5 if "internet" in utype else 0
                            candidates.append((score, apn_value))
                        if candidates:
                            candidates.sort(key=lambda x: -x[0])
                            return candidates[0][1]
        except Exception as e:
            print(f"[CELL-INET] APN lookup error: {e}")
        return None

    # =========================================================================
    # Manual Actions (exposed via API, not called by monitor loop)
    # =========================================================================

    async def deep_reconnect(self) -> Dict[str, Any]:
        """Software equivalent of physical unplug/replug.

        Cycles the radio via mmcli disable/enable for a fresh carrier session.
        Only called manually via API — the monitor loop NEVER calls this.
        """
        self._log_event("info", "Deep reconnect — cycling radio for fresh carrier session")
        self._internet_verified = False

        idx = await self.discover_modem()
        if idx is None:
            self._log_event("error", "No modem for deep reconnect")
            return {"ok": False, "error": "No modem found"}

        # Bring NM connection down
        await _run_cmd(["nmcli", "connection", "down", self._connection_name])
        self._connected_since = None

        # Tear down bearers + radio cycle
        self._log_event("info", "Disconnecting bearers...")
        await _run_cmd(["mmcli", "-m", str(idx), "--simple-disconnect"], timeout=10.0)

        self._log_event("info", "Radio OFF...")
        await _run_cmd(["mmcli", "-m", str(idx), "--disable"], timeout=10.0)
        await asyncio.sleep(3)

        self._log_event("info", "Radio ON...")
        await _run_cmd(["mmcli", "-m", str(idx), "--enable"], timeout=10.0)
        await asyncio.sleep(10)

        # Let NM autoconnect bring it back up
        self._log_event("info", "Waiting for NM autoconnect...")

        # Wait up to 30s for NM to reconnect
        for _ in range(6):
            await asyncio.sleep(5)
            await self.get_connection_status()
            if self._connection_info.get("active"):
                await asyncio.sleep(3)
                ok = await self.verify_internet()
                self._log_event("connected" if ok else "info",
                    f"Deep reconnect complete — internet {'verified' if ok else 'unverified'}")
                return {"ok": True, "internet_verified": ok}

        self._log_event("error", "Deep reconnect — NM did not autoconnect within 30s")
        return {"ok": False, "error": "NM did not autoconnect after radio cycle"}

    async def run_speed_test(self) -> Dict[str, Any]:
        results: Dict[str, Any] = {"download_mbps": None, "upload_mbps": None, "latency_ms": None}

        rc, stdout, _ = await _run_cmd(
            ["curl", "-s", "-o", "/dev/null", "-w", "%{time_connect}",
             "--interface", "wwan0", "--connect-timeout", "5", "--max-time", "10",
             "http://connectivitycheck.gstatic.com/generate_204"],
            timeout=15.0
        )
        if rc == 0 and stdout:
            try:
                results["latency_ms"] = round(float(stdout) * 1000, 1)
            except ValueError:
                pass

        rc, stdout, _ = await _run_cmd(
            ["curl", "-s", "-o", "/dev/null", "-w", "%{speed_download}",
             "--interface", "wwan0", "--connect-timeout", "10", "--max-time", "15",
             "http://speedtest.tele2.net/10MB.zip"],
            timeout=20.0
        )
        if rc == 0 and stdout:
            try:
                results["download_mbps"] = round(float(stdout) * 8 / 1_000_000, 2)
            except ValueError:
                pass

        rc, stdout, _ = await _run_cmd(
            ["curl", "-s", "-o", "/dev/null", "-w", "%{speed_upload}",
             "--interface", "wwan0", "--connect-timeout", "10", "--max-time", "15",
             "-T", "/dev/zero", "http://speedtest.tele2.net/upload.php"],
            timeout=20.0
        )
        if rc == 0 and stdout:
            try:
                results["upload_mbps"] = round(float(stdout) * 8 / 1_000_000, 2)
            except ValueError:
                pass

        self._log_event("info", f"Speed: {results['download_mbps']} down, {results['upload_mbps']} up, {results['latency_ms']}ms")
        return results

    async def send_at_command(self, command: str) -> Dict[str, Any]:
        """Send a raw AT command via mmcli --command."""
        if self._modem_index is None:
            await self.discover_modem()
        if self._modem_index is None:
            return {"ok": False, "error": "No modem"}

        rc, stdout, stderr = await _run_cmd(
            ["mmcli", "-m", str(self._modem_index), "--command", command],
            timeout=10.0
        )
        response = stdout if rc == 0 else (stderr or "Command failed")
        self._log_event("info", f"AT: {command} -> {response[:80]}")
        return {"ok": rc == 0, "command": command, "response": response}

    # =========================================================================
    # Full Status
    # =========================================================================

    async def get_full_status(self) -> Dict[str, Any]:
        return {
            "state": self._state,
            "modem": self._modem_info,
            "modem_index": self._modem_index,
            "signal": self._signal_info,
            "connection": self._connection_info,
            "routing": self._routing_info,
            "auto_reconnect": True,  # NM handles this now
            "connection_name": self._connection_name,
            "connected_since": self._connected_since,
            "uptime_seconds": (time.time() - self._connected_since) if self._connected_since else None,
            "internet_verified": self._internet_verified,
            "modem_initializing": False,
            "unstable_count": 0,
            "backoff": {
                "active": False,
                "seconds": 0,
                "remaining": 0,
                "consecutive_failures": 0,
                "throttle_detected": False,
            },
        }

    def get_event_log(self, limit: int = 50) -> List[Dict[str, Any]]:
        events = list(self._event_log)
        events.reverse()
        return events[:limit]

    # =========================================================================
    # Passive Monitor Loop
    # =========================================================================

    async def _monitor_loop(self):
        """Background loop — read-only polling, no connection management."""
        while self._running:
            try:
                await self.discover_modem()

                if self._modem_index is not None:
                    await self.get_modem_info()
                    await self.get_signal_details()
                    await self.get_connection_status()
                    await self.get_routing_status()

                    conn_active = self._connection_info.get("active", False)

                    if conn_active:
                        if self._state != "connected":
                            self._state = "connected"
                            self._connected_since = time.time()
                            operator = self._modem_info.get("operator_name", "?")
                            ip = self._connection_info.get("ip", "?")
                            self._log_event("connected", f"{operator} — {ip}")

                        # Periodic internet verification
                        now = time.time()
                        if now - self._last_verify_time > VERIFY_INTERVAL:
                            await self.verify_internet()
                    else:
                        if self._state == "connected":
                            self._state = "disconnected"
                            self._connected_since = None
                            self._internet_verified = False
                            self._log_event("disconnected", "Connection went down")
                        elif self._state == "unknown":
                            self._state = "disconnected"

                else:
                    # No modem
                    if self._state not in ("disconnected", "unknown"):
                        self._state = "disconnected"
                        self._connected_since = None
                        self._modem_info = {}
                        self._signal_info = {}
                        self._connection_info = {}
                        self._internet_verified = False
                        self._log_event("disconnected", "Modem not found")

            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"[CELL-INET] Monitor error: {e}")
                traceback.print_exc()

            try:
                await asyncio.sleep(POLL_INTERVAL)
            except asyncio.CancelledError:
                break

    # =========================================================================
    # Event Logging
    # =========================================================================

    def _log_event(self, event_type: str, detail: str = ""):
        self._event_log.append({
            "time": time.time(),
            "type": event_type,
            "detail": detail,
        })
        print(f"[CELL-INET] [{event_type}] {detail}")


# =============================================================================
# Module-level singleton
# =============================================================================

_manager: Optional[CellularInternetManager] = None


def get_internet_manager() -> Optional[CellularInternetManager]:
    return _manager


async def start_internet_manager() -> CellularInternetManager:
    global _manager
    if _manager is None:
        from Orchestrator.config import CELLULAR_INTERNET_CONNECTION
        _manager = CellularInternetManager(
            connection_name=CELLULAR_INTERNET_CONNECTION,
        )
    if not _manager._running:
        await _manager.start()
    return _manager


async def stop_internet_manager():
    global _manager
    if _manager:
        await _manager.stop()
        _manager = None
