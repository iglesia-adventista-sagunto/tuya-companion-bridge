import json
import logging
import os
import threading
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

for dev in devices_list:
    dev_id = dev["id"]
    name = dev.get("name", dev_id)
    ip = dev["ip"]
    key = dev["key"]
    version = float(dev.get("version", 3.4))

    outlet = tinytuya.OutletDevice(dev_id, ip, key)
    outlet.set_version(version)
    # Tuya devices time out idle TCP sockets after ~30s.
    # Disabling persistent socket keeps connections fresh and resilient.
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

@app.route("/health", methods=["GET"])
@app.route("/", methods=["GET"])
def health():
    dev_summary = [
        {"name": d.get("name"), "id": d["id"], "ip": d["ip"]}
        for d in devices_list
    ]
    return jsonify({
        "status": "healthy",
        "service": "Tuya Companion Bridge",
        "default_device": default_device_entry.get("name"),
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
        with entry["lock"]:
            st = entry["device"].status()
        err = st.get("Error")
        metrics = extract_metrics(st) if not err else None
        results.append({
            "name": d.get("name"),
            "id": d["id"],
            "ip": d["ip"],
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

    with entry["lock"]:
        st = entry["device"].status()

    if "Error" in st:
        logger.error(f"Failed to query {entry['name']}: {st}")
        return jsonify({
            "error": "Failed to communicate with device",
            "details": st.get("Error"),
            "device": entry["name"]
        }), 503

    metrics = extract_metrics(st)
    return jsonify({
        "device": entry["name"],
        "id": entry["id"],
        **metrics
    })

@app.route("/is_on", methods=["GET"])
@app.route("/<device_ref>/is_on", methods=["GET"])
def get_is_on(device_ref=None):
    entry = resolve_device(device_ref)
    if not entry:
        return "false", 404

    with entry["lock"]:
        st = entry["device"].status()

    if "Error" in st:
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

    with entry["lock"]:
        res = entry["device"].turn_on()

    if isinstance(res, dict) and "Error" in res:
        return jsonify({"error": res.get("Error"), "device": entry["name"]}), 503

    logger.info(f"Turned ON: {entry['name']}")
    return jsonify({"action": "on", "device": entry["name"], "result": res})

@app.route("/off", methods=["GET", "POST"])
@app.route("/<device_ref>/off", methods=["GET", "POST"])
def turn_off(device_ref=None):
    entry = resolve_device(device_ref)
    if not entry:
        return jsonify({"error": f"Device '{device_ref}' not found"}), 404

    with entry["lock"]:
        res = entry["device"].turn_off()

    if isinstance(res, dict) and "Error" in res:
        return jsonify({"error": res.get("Error"), "device": entry["name"]}), 503

    logger.info(f"Turned OFF: {entry['name']}")
    return jsonify({"action": "off", "device": entry["name"], "result": res})

@app.route("/toggle", methods=["GET", "POST"])
@app.route("/<device_ref>/toggle", methods=["GET", "POST"])
def toggle(device_ref=None):
    entry = resolve_device(device_ref)
    if not entry:
        return jsonify({"error": f"Device '{device_ref}' not found"}), 404

    with entry["lock"]:
        st = entry["device"].status()
        if "Error" in st:
            return jsonify({"error": st.get("Error"), "device": entry["name"]}), 503

        dps = st.get("dps", {})
        current_state = dps.get("1", False)
        new_state = not current_state

        if new_state:
            res = entry["device"].turn_on()
        else:
            res = entry["device"].turn_off()

    logger.info(f"Toggled {entry['name']} -> {new_state}")
    return jsonify({
        "action": "toggle",
        "device": entry["name"],
        "previous_state": current_state,
        "new_state": new_state,
        "result": res
    })

if __name__ == "__main__":
    logger.info(f"Starting Tuya Companion Bridge on port {port}...")
    app.run(host="0.0.0.0", port=port)
