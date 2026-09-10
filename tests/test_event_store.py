import tempfile
import unittest
from pathlib import Path

from agentic_plc.contracts.events import ICSEvent, Intent
from agentic_plc.telemetry import JsonlEventStore, event_from_dict, event_to_dict
from agentic_plc.telemetry.replay import summarize_events


class JsonlEventStoreTests(unittest.TestCase):
    def test_round_trip_event(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = JsonlEventStore(Path(directory) / "events.jsonl")
            event = ICSEvent(
                protocol="modbus",
                session_id="s1",
                source_ip="192.0.2.10",
                actor_id="actor-1",
                intent=Intent.WRITE_SETPOINT,
                operation="write_holding_registers",
                address=0,
                requested_value=700,
                resulting_value=700,
                result="accepted",
                world_revision=4,
            )

            store.append(event)

            loaded = store.list_events()
            self.assertEqual(len(loaded), 1)
            self.assertEqual(loaded[0].intent, Intent.WRITE_SETPOINT)
            self.assertEqual(loaded[0].requested_value, 700)

    def test_serialization_ignores_unknown_fields(self) -> None:
        event = ICSEvent(
            protocol="ssh",
            session_id="s1",
            source_ip="192.0.2.20",
            intent=Intent.AUTH_ATTEMPT,
            operation="password_auth",
        )

        payload = event_to_dict(event)
        payload["unknown"] = "ignored"

        loaded = event_from_dict(payload)
        self.assertEqual(loaded.intent, Intent.AUTH_ATTEMPT)
        self.assertEqual(loaded.protocol, "ssh")

    def test_replay_summary_counts_sessions_and_intents(self) -> None:
        events = [
            ICSEvent(
                protocol="modbus",
                session_id="s1",
                source_ip="192.0.2.10",
                actor_id="actor-1",
                intent=Intent.WRITE_SETPOINT,
                operation="write",
                result="accepted",
                world_revision=1,
            ),
            ICSEvent(
                protocol="ssh",
                session_id="s2",
                source_ip="192.0.2.10",
                actor_id="actor-1",
                intent=Intent.AUTH_ATTEMPT,
                operation="auth",
                result="observed",
                world_revision=2,
            ),
        ]

        summary = summarize_events(events)

        self.assertEqual(summary.total_events, 2)
        self.assertEqual(summary.sessions, 2)
        self.assertEqual(summary.actors, 1)
        self.assertEqual(summary.protocols, {"modbus": 1, "ssh": 1})
        self.assertEqual(summary.latest_world_revision, 2)


if __name__ == "__main__":
    unittest.main()
