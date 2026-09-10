import unittest

from agentic_plc.adapters import (
    AgenticModbusDatabank,
    ModbusHookContext,
    event_from_modbus_tcp_request,
    install_agentic_modbus_hook,
)
from agentic_plc.agent import AgentRuntime, RuleBasedDeceptionPlanner
from agentic_plc.contracts.events import Intent
from agentic_plc.telemetry import InMemoryEventLog
from agentic_plc.world import TankPumpWorld


class FakeDatabank:
    def __init__(self) -> None:
        self.calls = 0

    def handle_request(self, query, request: bytes, mode: str):
        self.calls += 1
        return b"fallback", {"response": b"fallback", "mode": mode}


class FakeServer:
    def __init__(self) -> None:
        self._databank = FakeDatabank()


class FakeDecoratedServer:
    def __init__(self) -> None:
        self.wrapped = FakeServer()


class AgenticModbusDatabankTests(unittest.TestCase):
    def test_event_from_read_holding_register_request(self) -> None:
        event = event_from_modbus_tcp_request(
            read_holding_register_request(transaction_id=17, unit_id=1),
            context=ModbusHookContext(
                session_id="s1",
                source_ip="192.0.2.10",
                actor_id="actor-1",
                source_port=50200,
            ),
        )

        self.assertEqual(event.intent, Intent.READ_PROCESS)
        self.assertEqual(event.operation, "read_holding_registers")
        self.assertEqual(event.transaction_id, "17")
        self.assertEqual(event.unit_id, 1)
        self.assertEqual(event.address, 0)
        self.assertEqual(event.count, 2)

    def test_agentic_databank_returns_generated_frame(self) -> None:
        inner = FakeDatabank()
        event_log = InMemoryEventLog()
        runtime = AgentRuntime(
            world=TankPumpWorld(),
            planner=RuleBasedDeceptionPlanner(),
            event_store=event_log,
        )
        databank = AgenticModbusDatabank(inner, runtime)

        response, logdata = databank.handle_request(
            query=None,
            request=read_holding_register_request(transaction_id=17, unit_id=1),
            mode="tcp",
        )

        self.assertEqual(response.hex(), "00110000000701030401f40078")
        self.assertEqual(logdata["agentic_generated"], True)
        self.assertEqual(inner.calls, 0)
        self.assertEqual(event_log.list_events()[0].intent, Intent.READ_PROCESS)

    def test_agentic_databank_falls_back_without_generated_frame(self) -> None:
        inner = FakeDatabank()
        runtime = AgentRuntime(world=TankPumpWorld(), event_store=InMemoryEventLog())
        databank = AgenticModbusDatabank(inner, runtime)

        response, logdata = databank.handle_request(
            query=None,
            request=write_single_register_request(transaction_id=18, unit_id=1),
            mode="tcp",
        )

        self.assertEqual(response, b"fallback")
        self.assertEqual(logdata["mode"], "tcp")
        self.assertEqual(inner.calls, 1)

    def test_install_agentic_modbus_hook_wraps_server_databank(self) -> None:
        server = FakeServer()
        runtime = AgentRuntime(world=TankPumpWorld())

        wrapped = install_agentic_modbus_hook(server, runtime)

        self.assertIs(server._databank, wrapped)
        self.assertIsInstance(server._databank, AgenticModbusDatabank)

    def test_install_agentic_modbus_hook_wraps_conpot_decorated_server(self) -> None:
        server = FakeDecoratedServer()
        runtime = AgentRuntime(world=TankPumpWorld())

        wrapped = install_agentic_modbus_hook(server, runtime)

        self.assertIs(server.wrapped._databank, wrapped)
        self.assertFalse(hasattr(server, "_databank"))


def read_holding_register_request(transaction_id: int, unit_id: int) -> bytes:
    return _frame(transaction_id, unit_id, bytes.fromhex("03 00 00 00 02"))


def write_single_register_request(transaction_id: int, unit_id: int) -> bytes:
    return _frame(transaction_id, unit_id, bytes.fromhex("06 00 00 02 bc"))


def _frame(transaction_id: int, unit_id: int, pdu: bytes) -> bytes:
    length = 1 + len(pdu)
    raw = bytearray()
    raw.extend(transaction_id.to_bytes(2, "big"))
    raw.extend((0).to_bytes(2, "big"))
    raw.extend(length.to_bytes(2, "big"))
    raw.append(unit_id)
    raw.extend(pdu)
    return bytes(raw)


if __name__ == "__main__":
    unittest.main()
