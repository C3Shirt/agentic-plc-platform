import unittest

from agentic_plc.telemetry import ActorCorrelator, SessionContext


class ActorCorrelationTests(unittest.TestCase):
    def test_same_source_ip_maps_to_same_actor_across_sessions(self) -> None:
        correlator = ActorCorrelator()

        first = correlator.actor_id_for(
            SessionContext(
                protocol="modbus",
                session_id="s1",
                source_ip="192.0.2.10",
                source_port=50100,
            )
        )
        second = correlator.actor_id_for(
            SessionContext(
                protocol="ssh",
                session_id="s2",
                source_ip="192.0.2.10",
                source_port=50101,
            )
        )

        self.assertEqual(first, second)
        self.assertTrue(first.startswith("actor:"))

    def test_unknown_source_ip_maps_to_unknown_actor(self) -> None:
        actor_id = ActorCorrelator().actor_id_for(
            SessionContext(
                protocol="modbus",
                session_id="s1",
                source_ip="0.0.0.0",
            )
        )

        self.assertEqual(actor_id, "actor:unknown")


if __name__ == "__main__":
    unittest.main()
