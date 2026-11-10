import json
import logging
import os
import select
import socket
import threading
import time
from flask import Flask, jsonify, request
import tinytuya

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s in %(module)s: %(message)s"
)
logger = logging.getLogger("tuya-bridge")

CONFIG_PATH = os.environ.get("CONFIG_PATH", "config.json")

def load_config():
    if not os.path.exists(CONFIG_PATH):
        raise FileNotFoundError(f"Configuration file not found at: {CONFIG_PATH}")
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

config = load_config()
port = config.get("port", 5000)
devices_list = config.get("devices", [])

if not devices_list:
    raise ValueError("No devices defined in config.json")

# Map devices by normalized name and ID
devices = {}
device_locks = {}

class TuyaDiscoveryListener(threading.Thread):
    """
    Background listener for Tuya UDP broadcast announcements (ports 6666 and 6667).
    Continuously detects dynamic IP address changes and keeps device configurations in sync.
    """
    def __init__(self, on_device_discovered=None):
        super().__init__(daemon=True, name="TuyaDiscoveryListener")
        self.on_device_discovered = on_device_discovered
        self.discovered = {}
        self.events = {}
        self.lock = threading.Lock()
        self.running = True
        self.socks = []

    def run(self):
        for p in (tinytuya.UDPPORT, tinytuya.UDPPORTS):
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                if hasattr(socket, "SO_REUSEPORT"):
                    try:
                        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
                    except Exception:
                        pass
                s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                s.bind(("", p))
                s.setblocking(False)
                self.socks.append(s)
                logger.info(f"UDP discovery listener bound to port {p}")
            except Exception as e:
                logger.warning(f"Could not bind to UDP port {p} for discovery: {e}")

        if not self.socks:
            logger.error("No UDP sockets bound for discovery listener.")
            return

        while self.running:
            try:
                readable, _, _ = select.select(self.socks, [], [], 1.0)
                for s in readable:
                    data, addr = s.recvfrom(4096)
                    self._handle_packet(data, addr)
            except Exception as e:
                if self.running:
                    logger.debug(f"Discovery listener loop error: {e}")

    def _handle_packet(self, data, addr):
        try:
            payload = tinytuya.decrypt_udp(data)
            if not payload:
                return
            data_dict = json.loads(payload)
            gw_id = data_dict.get("gwId")
            if not gw_id:
                return
            ip = data_dict.get("ip") or addr[0]
            version = data_dict.get("version", "3.3")

            now = time.time()
            with self.lock:
                self.discovered[gw_id] = {
                    "ip": ip,
                    "version": version,
                    "timestamp": now,
                    "data": data_dict
                }
                if gw_id in self.events:
                    self.events[gw_id].set()

            if self.on_device_discovered:
                self.on_device_discovered(gw_id, ip, version, data_dict)
        except Exception:
            pass

    def get_ip(self, dev_id):
        with self.lock:
            info = self.discovered.get(dev_id)
            if info:
                return info.get("ip")
            return None

    def wait_for_device(self, dev_id, timeout=6.0, force_fresh=False):
        now = time.time()
        with self.lock:
            existing = self.discovered.get(dev_id)
            if existing and not force_fresh and (now - existing["timestamp"] < 30):
                return existing["ip"]
            if dev_id not in self.events:
                self.events[dev_id] = threading.Event()
            else:
                self.events[dev_id].clear()
            evt = self.events[dev_id]

        if evt.wait(timeout=timeout):
            with self.lock:
                return self.discovered.get(dev_id, {}).get("ip")
        return None

    def stop(self):
        self.running = False
        for s in self.socks:
            try:
                s.close()
            except Exception:
                pass


def on_device_discovered(gw_id, ip, version, data_dict):
    entry = devices.get(gw_id)
    if not entry:
        return
    with entry["lock"]:
        old_ip = entry.get("ip")
        if old_ip != ip or entry["device"].address != ip:
            logger.info(f"Discovered IP update for '{entry['name']}' ({gw_id}): {old_ip or 'None'} -> {ip}")
            entry["ip"] = ip
            entry["device"].address = ip


discovery_listener = TuyaDiscoveryListener(on_device_discovered=on_device_discovered)
discovery_listener.start()

# Initialize devices
for dev in devices_list:
    dev_id = dev["id"]
    name = dev.get("name", dev_id)
    raw_ip = dev.get("ip")
    # Support dynamic discovery if ip is missing, empty, or "auto"
    ip = None if (not raw_ip or str(raw_ip).strip().lower() in ("auto", "none", "")) else str(raw_ip).strip()
    key = dev["key"]
    version = float(dev.get("version", 3.4))

    # Use placeholder "127.0.0.1" if dynamic IP so tinytuya doesn't invoke internal find_device()
    outlet = tinytuya.OutletDevice(dev_id, ip or "127.0.0.1", key)
    outlet.set_version(version)
    outlet.set_socketPersistent(False)
    outlet.set_socketTimeout(3)

    lock = threading.Lock()
    entry = {
        "name": name,
        "id": dev_id,
        "ip": ip,
        "device": outlet,
        "lock": lock
    }

    devices[dev_id] = entry
    devices[name.lower()] = entry
    device_locks[dev_id] = lock

# Startup auto-discovery: allow a brief window for devices with missing or dynamic IPs
unresolved = [d["id"] for d in devices_list if not devices[d["id"]]["ip"]]
if unresolved:
    logger.info(f"Resolving dynamic IPs for {len(unresolved)} device(s) via UDP discovery...")
    wait_until = time.time() + 5.0
    while time.time() < wait_until:
        unresolved = [d["id"] for d in devices_list if not devices[d["id"]]["ip"]]
        if not unresolved:
            break
        time.sleep(0.3)

# Give a quick 1-2 second sync window for all devices in case static IPs in config have drifted
time.sleep(1.5)

# Log configured devices status
for d in devices_list:
    dev_entry = devices[d["id"]]
    logger.info(f"Registered device '{dev_entry['name']}' [{dev_entry['id']}] -> IP: {dev_entry['ip'] or 'Unresolved (will discover on request)'}")

# Default device (first in config)
default_device_entry = devices_list[0]
default_id = default_device_entry["id"]

app = Flask(__name__)

def resolve_device(target=None):
    if not target:
        return devices.get(default_id)
    target_clean = target.strip().lower()
    return devices.get(target_clean) or devices.get(target.strip())

def extract_metrics(status_dict):
    dps = status_dict.get("dps", {})
    # DPS 1 is standard relay switch
    is_on = dps.get("1", False)
    # Voltage (DPS 20) is typically in tenths of a Volt (e.g. 2378 -> 237.8V)
    voltage_raw = dps.get("20", 0)
    voltage = (voltage_raw / 10.0) if voltage_raw else 0.0
    # Current (DPS 18) in mA
    current_ma = dps.get("18", 0)
    # Power (DPS 19) in tenths of a Watt (e.g. 153 -> 15.3W)
    power_raw = dps.get("19", 0)
    power_w = (power_raw / 10.0) if power_raw else 0.0

    return {
        "is_on": is_on,
        "voltage_v": voltage,
        "current_ma": current_ma,
        "power_w": power_w,
        "dps": dps
    }

def execute_device_action(entry, action_fn):
    """
    Executes action_fn(entry['device']) holding entry['lock'].
    If the device IP is not resolved yet, or if the action fails (e.g. dynamic IP changed),
    attempts dynamic discovery and retries once.
    """
    with entry["lock"]:
        # If no IP resolved yet, check cache or wait briefly for discovery
        if not entry.get("ip"):
            cached_ip = discovery_listener.get_ip(entry["id"])
            if cached_ip:
                entry["ip"] = cached_ip
                entry["device"].address = cached_ip
            else:
                resolved_ip = discovery_listener.wait_for_device(entry["id"], timeout=5.0)
                if resolved_ip:
                    entry["ip"] = resolved_ip
                    entry["device"].address = resolved_ip
                else:
                    return {"Error": "Device IP not discovered yet (device may be offline)"}

        # First attempt
        try:
            res = action_fn(entry["device"])
        except Exception as e:
            res = {"Error": str(e)}

        is_error = isinstance(res, dict) and "Error" in res
        if is_error:
            logger.warning(
                f"Communication failed for '{entry['name']}' at {entry.get('ip')}: {res.get('Error')}. "
                f"Checking for updated IP..."
            )
            # Check if listener already has a newer or different IP
            latest_ip = discovery_listener.get_ip(entry["id"])
            if latest_ip and (latest_ip != entry.get("ip") or entry["device"].address != latest_ip):
                logger.info(f"Syncing discovered IP for '{entry['name']}': {entry.get('ip')} -> {latest_ip}. Retrying...")
                entry["ip"] = latest_ip
                entry["device"].address = latest_ip
                try:
                    res = action_fn(entry["device"])
                    return res
                except Exception as e:
                    res = {"Error": str(e)}

            # Otherwise wait for a fresh UDP announcement in case the device just obtained a new IP
            fresh_ip = discovery_listener.wait_for_device(entry["id"], timeout=5.0, force_fresh=True)
            if fresh_ip and (fresh_ip != entry.get("ip") or entry["device"].address != fresh_ip):
                logger.info(f"Dynamic IP changed for '{entry['name']}': {entry.get('ip')} -> {fresh_ip}. Retrying...")
                entry["ip"] = fresh_ip
                entry["device"].address = fresh_ip
                try:
                    res = action_fn(entry["device"])
                except Exception as e:
                    res = {"Error": str(e)}
            elif fresh_ip:
                # Retry once on existing IP
                try:
                    res = action_fn(entry["device"])
                except Exception as e:
                    res = {"Error": str(e)}

        return res

@app.route("/health", methods=["GET"])
@app.route("/", methods=["GET"])
def health():
    dev_summary = [
        {
            "name": devices[d["id"]]["name"],
            "id": d["id"],
            "ip": devices[d["id"]]["ip"] or "discovering...",
            "discovered": bool(devices[d["id"]]["ip"])
        }
        for d in devices_list
    ]
    return jsonify({
        "status": "healthy",
        "service": "Tuya Companion Bridge",
        "default_device": devices[default_id]["name"],
        "devices": dev_summary,
        "endpoints": {
            "GET /status": "Status of default device",
            "GET /is_on": "Plaintext true/false state of default device",
            "GET,POST /on": "Turn ON default device",
            "GET,POST /off": "Turn OFF default device",
            "GET,POST /toggle": "Toggle default device",
            "GET /<device>/status": "Status of specific device (by name or id)",
            "GET /<device>/is_on": "Plaintext true/false state of specific device",
            "GET,POST /<device>/on": "Turn ON specific device",
            "GET,POST /<device>/off": "Turn OFF specific device",
            "GET,POST /<device>/toggle": "Toggle specific device"
        }
    })

@app.route("/devices", methods=["GET"])
def list_devices():
    results = []
    for d in devices_list:
        entry = devices[d["id"]]
        st = execute_device_action(entry, lambda dev: dev.status())
        err = st.get("Error") if isinstance(st, dict) else None
        metrics = extract_metrics(st) if (isinstance(st, dict) and not err) else None
        results.append({
            "name": entry["name"],
            "id": entry["id"],
            "ip": entry.get("ip"),
            "online": err is None,
            "error": err,
            "metrics": metrics
        })
    return jsonify({"devices": results})

@app.route("/status", methods=["GET"])
@app.route("/<device_ref>/status", methods=["GET"])
def get_status(device_ref=None):
    entry = resolve_device(device_ref)
    if not entry:
        return jsonify({"error": f"Device '{device_ref}' not found"}), 404

    st = execute_device_action(entry, lambda dev: dev.status())

    if not isinstance(st, dict) or "Error" in st:
        err_msg = st.get("Error") if isinstance(st, dict) else str(st)
        logger.error(f"Failed to query {entry['name']}: {st}")
        return jsonify({
            "error": "Failed to communicate with device",
            "details": err_msg,
            "device": entry["name"],
            "ip": entry.get("ip")
        }), 503

    metrics = extract_metrics(st)
    return jsonify({
        "device": entry["name"],
        "id": entry["id"],
        "ip": entry.get("ip"),
        **metrics
    })

@app.route("/is_on", methods=["GET"])
@app.route("/<device_ref>/is_on", methods=["GET"])
def get_is_on(device_ref=None):
    entry = resolve_device(device_ref)
    if not entry:
        return "false", 404

    st = execute_device_action(entry, lambda dev: dev.status())

    if not isinstance(st, dict) or "Error" in st:
        logger.error(f"Failed to query {entry['name']}: {st}")
        return "false", 503

    dps = st.get("dps", {})
    is_on = dps.get("1", False)
    return ("true" if is_on else "false"), 200, {"Content-Type": "text/plain; charset=utf-8"}

@app.route("/on", methods=["GET", "POST"])
@app.route("/<device_ref>/on", methods=["GET", "POST"])
def turn_on(device_ref=None):
    entry = resolve_device(device_ref)
    if not entry:
        return jsonify({"error": f"Device '{device_ref}' not found"}), 404

    res = execute_device_action(entry, lambda dev: dev.turn_on())

    if isinstance(res, dict) and "Error" in res:
        return jsonify({"error": res.get("Error"), "device": entry["name"], "ip": entry.get("ip")}), 503

    logger.info(f"Turned ON: {entry['name']} ({entry.get('ip')})")
    return jsonify({"action": "on", "device": entry["name"], "result": res})

@app.route("/off", methods=["GET", "POST"])
@app.route("/<device_ref>/off", methods=["GET", "POST"])
def turn_off(device_ref=None):
    entry = resolve_device(device_ref)
    if not entry:
        return jsonify({"error": f"Device '{device_ref}' not found"}), 404

    res = execute_device_action(entry, lambda dev: dev.turn_off())

    if isinstance(res, dict) and "Error" in res:
        return jsonify({"error": res.get("Error"), "device": entry["name"], "ip": entry.get("ip")}), 503

    logger.info(f"Turned OFF: {entry['name']} ({entry.get('ip')})")
    return jsonify({"action": "off", "device": entry["name"], "result": res})

@app.route("/toggle", methods=["GET", "POST"])
@app.route("/<device_ref>/toggle", methods=["GET", "POST"])
def toggle(device_ref=None):
    entry = resolve_device(device_ref)
    if not entry:
        return jsonify({"error": f"Device '{device_ref}' not found"}), 404

    def _toggle(dev):
        st = dev.status()
        if isinstance(st, dict) and "Error" in st:
            return st
        dps = st.get("dps", {})
        current_state = dps.get("1", False)
        new_state = not current_state
        if new_state:
            action_res = dev.turn_on()
        else:
            action_res = dev.turn_off()
        return {
            "action": "toggle",
            "device": entry["name"],
            "previous_state": current_state,
            "new_state": new_state,
            "result": action_res
        }

    res = execute_device_action(entry, _toggle)

    if isinstance(res, dict) and "Error" in res:
        return jsonify({"error": res.get("Error"), "device": entry["name"], "ip": entry.get("ip")}), 503

    logger.info(f"Toggled {entry['name']} ({entry.get('ip')}) -> {res.get('new_state')}")
    return jsonify(res)

if __name__ == "__main__":
    logger.info(f"Starting Tuya Companion Bridge on port {port}...")
    try:
        app.run(host="0.0.0.0", port=port)
    finally:
        discovery_listener.stop()
