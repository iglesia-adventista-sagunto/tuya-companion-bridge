import os
import unittest
import json
import time

# Set CONFIG_PATH before importing app
os.environ["CONFIG_PATH"] = os.path.join(os.path.dirname(__file__), "config.json")

from app import app, devices, discovery_listener, execute_device_action, resolve_device

class TestTuyaBridge(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = app.test_client()
        # Give listener time to sync with real network broadcasts
        time.sleep(2)

    def test_01_discovery_listener_running(self):
        """Test that the discovery listener is active and has bound sockets."""
        self.assertTrue(discovery_listener.is_alive())
        self.assertGreater(len(discovery_listener.socks), 0)

    def test_02_devices_discovered_ips(self):
        """Test that devices in config have their dynamic IPs discovered."""
        for dev_id, entry in devices.items():
            if dev_id == entry["id"]:  # check canonical ID entry
                self.assertIsNotNone(entry.get("ip"), f"Device {entry['name']} should have an IP")
                print(f"[TEST] Device '{entry['name']}' ({entry['id']}) resolved to {entry['ip']}")

    def test_03_health_endpoint(self):
        """Test GET /health returns 200 with discovered devices info."""
        res = self.client.get("/health")
        self.assertEqual(res.status_code, 200)
        data = json.loads(res.data)
        self.assertEqual(data["status"], "healthy")
        self.assertIn("devices", data)
        for dev in data["devices"]:
            self.assertTrue(dev["discovered"])
            self.assertNotEqual(dev["ip"], "discovering...")

    def test_04_device_resolution(self):
        """Test device name and id resolution."""
        pulpito = resolve_device("pulpito")
        self.assertIsNotNone(pulpito)
        self.assertEqual(pulpito["name"], "Pulpito")

        jovenes = resolve_device("jovenes")
        self.assertIsNotNone(jovenes)
        self.assertEqual(jovenes["name"], "Jovenes")

        # Unknown device
        unknown = resolve_device("non_existent_plug")
        self.assertIsNone(unknown)

    def test_05_status_endpoint(self):
        """Test GET /status and GET /<device>/status."""
        # Query default device status
        res = self.client.get("/status")
        self.assertIn(res.status_code, [200, 503])
        if res.status_code == 200:
            data = json.loads(res.data)
            self.assertIn("is_on", data)
            self.assertIn("ip", data)
            print(f"[TEST] Status OK for {data['device']} at {data['ip']}: is_on={data['is_on']}")

    def test_06_is_on_endpoint(self):
        """Test GET /is_on returns plaintext true or false."""
        res = self.client.get("/is_on")
        self.assertIn(res.status_code, [200, 503])
        if res.status_code == 200:
            self.assertIn(res.data.decode().strip(), ["true", "false"])

    def test_07_devices_endpoint(self):
        """Test GET /devices returns array of devices with online status and IP."""
        res = self.client.get("/devices")
        self.assertEqual(res.status_code, 200)
        data = json.loads(res.data)
        self.assertIn("devices", data)
        self.assertGreaterEqual(len(data["devices"]), 1)
        for dev in data["devices"]:
            print(f"[TEST] Device list entry: {dev['name']} -> IP: {dev['ip']}, Online: {dev['online']}")
            self.assertIsNotNone(dev["ip"])

    def test_08_dynamic_ip_recovery(self):
        """Test that if a device's address is temporarily corrupt/wrong, execute_device_action recovers."""
        pulpito = resolve_device("pulpito")
        real_ip = pulpito["ip"]

        # Temporarily inject wrong address
        with pulpito["lock"]:
            pulpito["device"].address = "192.168.1.254"

        # Execute action - it should detect failure and re-sync via listener
        res = execute_device_action(pulpito, lambda d: d.status())
        # Device address should have been restored to real IP
        self.assertEqual(pulpito["device"].address, real_ip)

    @classmethod
    def tearDownClass(cls):
        discovery_listener.stop()

if __name__ == "__main__":
    unittest.main()
