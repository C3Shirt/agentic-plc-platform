import json
import threading
import unittest
import urllib.error
import urllib.request

from agentic_plc.adapters import create_tank_pump_hmi_server
from agentic_plc.contracts.events import Intent
from agentic_plc.telemetry import InMemoryEventLog
from agentic_plc.world import TankPumpWorld


class HttpHMITests(unittest.TestCase):
    def test_get_state_returns_world_snapshot_and_logs_event(self) -> None:
        world = TankPumpWorld()
        event_log = InMemoryEventLog()

        with running_hmi(world, event_log) as base_url:
            payload = get_json(f"{base_url}/api/state")

        self.assertEqual(payload["state"]["level_percent"], 50.0)
        self.assertEqual(payload["state"]["revision"], 0)
        self.assertEqual(event_log.list_events()[0].intent, Intent.READ_PROCESS)
        self.assertEqual(event_log.list_events()[0].protocol, "http_hmi")

    def test_post_control_updates_shared_world_and_logs_event(self) -> None:
        world = TankPumpWorld()
        event_log = InMemoryEventLog()

        with running_hmi(world, event_log) as base_url:
            payload = post_json(
                f"{base_url}/api/control",
                {"action": "set_level_setpoint", "value": 70},
            )

        self.assertEqual(payload["state"]["level_setpoint_percent"], 70.0)
        self.assertEqual(world.state.level_setpoint_percent, 70.0)
        self.assertEqual(event_log.list_events()[0].intent, Intent.WRITE_SETPOINT)
        self.assertEqual(event_log.list_events()[0].result, "accepted")

    def test_post_invalid_control_is_rejected(self) -> None:
        world = TankPumpWorld()
        event_log = InMemoryEventLog()

        with running_hmi(world, event_log) as base_url:
            with self.assertRaises(urllib.error.HTTPError) as raised:
                post_json(
                    f"{base_url}/api/control",
                    {"action": "set_level_setpoint", "value": 99},
                )

        self.assertEqual(raised.exception.code, 400)
        raised.exception.close()
        self.assertEqual(world.state.level_setpoint_percent, 65.0)
        self.assertEqual(event_log.list_events()[0].result, "rejected")


class running_hmi:
    def __init__(self, world: TankPumpWorld, event_log: InMemoryEventLog) -> None:
        self.server = create_tank_pump_hmi_server("127.0.0.1", 0, world, event_log)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self) -> str:
        self.thread.start()
        host, port = self.server.server_address
        return f"http://{host}:{port}"

    def __exit__(self, exc_type, exc, tb) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)


def get_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=2) as response:
        return json.loads(response.read().decode("utf-8"))


def post_json(url: str, payload: dict) -> dict:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=2) as response:
        return json.loads(response.read().decode("utf-8"))


if __name__ == "__main__":
    unittest.main()
