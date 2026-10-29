# Tuya Companion Bridge

Local REST HTTP bridge to control Tuya Smart Plugs from Bitfocus Companion or HTTP automation clients without cloud dependency.

---

## Files

```text
tuya-companion-bridge/
├── app.py                  # REST API server
├── config.template.json    # Device template configuration
├── Dockerfile              # Container definition
├── docker-compose.yml      # Docker Compose definition
├── requirements.txt        # Dependencies
├── tuya-bridge.service     # Systemd unit file
├── start.bat               # Windows start script
├── start.sh                # Linux start script
├── .gitignore              # Git ignore rules
└── README.md               # Documentation
```

---

## Setup & Configuration

1. Create `config.json` from the template:
   ```bash
   cp config.template.json config.json
   ```

2. Edit `config.json` with your device details:
   ```json
   {
     "port": 5000,
     "devices": [
       {
         "name": "desk-lamp",
         "id": "tuya_device_id_here",
         "ip": "192.168.1.50",
         "key": "tuya_local_key_here",
         "version": "3.3"
       }
     ]
   }
   ```

Supported protocol versions are usually `3.3` or `3.4`. Set a static DHCP reservation on your router so device IP addresses remain constant.

> Note: `config.json` contains local private keys and device IDs; it is ignored by git to prevent leaking secrets.

---

## Deployment

### Docker Compose

1. Copy this folder to your server:
   ```bash
   cd tuya-companion-bridge
   docker compose up -d --build
   ```

2. Verify:
   ```bash
   curl http://<server-ip>:5000/health
   ```

If Docker cannot reach your local subnet, set `network_mode: host` in `docker-compose.yml`.

---

### Windows Native

Run directly with Python:

1. Create a virtual environment and install dependencies:
   ```cmd
   python -m venv venv
   venv\Scripts\pip install -r requirements.txt
   ```

2. Start the bridge:
   ```cmd
   venv\Scripts\python app.py
   ```

Or double-click `start.bat`.

---

### Linux Systemd Service

1. Copy project files to `/opt/tuya-companion-bridge`:
   ```bash
   sudo cp -r tuya-companion-bridge /opt/tuya-companion-bridge
   cd /opt/tuya-companion-bridge
   ```

2. Create virtual environment and install packages:
   ```bash
   sudo apt update && sudo apt install -y python3 python3-venv python3-pip
   sudo python3 -m venv venv
   sudo ./venv/bin/pip install -r requirements.txt
   ```

3. Enable and start service:
   ```bash
   sudo cp tuya-bridge.service /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable --now tuya-bridge
   ```

4. Check status:
   ```bash
   sudo systemctl status tuya-bridge
   ```

---

## API Reference

### Default Device

| Action | Method | URL | Description |
| :--- | :--- | :--- | :--- |
| Toggle | GET / POST | `http://<server-ip>:5000/toggle` | Switch state |
| Turn ON | GET / POST | `http://<server-ip>:5000/on` | Power on |
| Turn OFF | GET / POST | `http://<server-ip>:5000/off` | Power off |
| Status | GET | `http://<server-ip>:5000/status` | Current state and telemetry |

### Named Device

| Action | Method | URL |
| :--- | :--- | :--- |
| Toggle | GET / POST | `http://<server-ip>:5000/<device-name>/toggle` |
| Turn ON | GET / POST | `http://<server-ip>:5000/<device-name>/on` |
| Turn OFF | GET / POST | `http://<server-ip>:5000/<device-name>/off` |
| Status | GET | `http://<server-ip>:5000/<device-name>/status` |

---

## Bitfocus Companion Setup

1. Open Companion web UI at `http://<companion-ip>:8000`.
2. Under **Connections**, add **Generic HTTP Requests**.
3. Under **Buttons**, select an empty button:
   - Set button text to `Toggle`.
   - Under **Press Actions**, add **generic-http: HTTP GET**.
   - Set URL:
     ```text
     http://<server-ip>:5000/toggle
     ```
4. Press the button to test.
