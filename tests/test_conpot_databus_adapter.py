import unittest
from typing import Any

from agentic_plc.adapters.conpot_databus import (
    ConpotDatabusAdapter,
    ConpotTankPumpBlock,
    get_shared_tank_pump_runtime,
    reset_shared_tank_pump_runtime,
)
from agentic_plc.telemetry.event_log import InMemoryEventLog
from agentic_plc.world.model import OperatingMode, TankPumpWorld
from agentic_plc.world.registers import RegisterArea, TankPumpRegisterMap


class FakeDatabus:
    def __init__(self) -> None:
        self.values: dict[str, Any] = {}

    def get_value(self, key: str) -> Any:
        return self.values[key]

    def set_value(self, key: str, value: Any) -> None:
        self.values[key] = value


class ConpotLikeDatabus(FakeDatabus):
    def get_value(self, key: str) -> Any:
        item = self.values[key]
        if getattr(item, "get_value", None):
            return item.get_value()
        if hasattr(item, "__call__"):
            return item()
        return item


class ConpotDatabusAdapterTests(unittest.TestCase):
    def test_installs_dynamic_blocks(self) -> None:
        world = TankPumpWorld()
        adapter = ConpotDatabusAdapter(TankPumpRegisterMap(world))
        databus = FakeDatabus()

        adapter.install(databus)

        input_registers = databus.get_value(adapter.binding.input_registers_key)
        self.assertEqual(len(input_registers), 2)
        self.assertEqual(input_registers[0:2], [500, 120])

        world.tick(10)
        self.assertGreater(input_registers[0], 500)

    def test_holding_register_write_updates_world_and_logs_event(self) -> None:
        world = TankPumpWorld()
        event_log = InMemoryEventLog()
        adapter = ConpotDatabusAdapter(TankPumpRegisterMap(world), event_log)
        databus = FakeDatabus()

        adapter.install(databus)
        holding = databus.get_value(adapter.binding.holding_registers_key)
        holding[0] = 700

        events = event_log.list_events()
        self.assertEqual(world.state.level_setpoint_percent, 70.0)
        self.assertEqual(events[-1].result, "accepted")
        self.assertEqual(events[-1].intent.value, "write_setpoint")

    def test_rejected_coil_write_is_logged_and_does_not_mutate_world(self) -> None:
        world = TankPumpWorld()
        event_log = InMemoryEventLog()
        adapter = ConpotDatabusAdapter(TankPumpRegisterMap(world), event_log)
        databus = FakeDatabus()

        adapter.install(databus)
        coils = databus.get_value(adapter.binding.coils_key)
        coils[0] = 1

        events = event_log.list_events()
        self.assertFalse(world.state.outlet_pump_running)
        self.assertEqual(events[-1].result, "rejected")
        self.assertIn("requires manual mode", events[-1].metadata["error"])

    def test_mode_write_then_coil_write_matches_manual_operation(self) -> None:
        world = TankPumpWorld()
        event_log = InMemoryEventLog()
        adapter = ConpotDatabusAdapter(TankPumpRegisterMap(world), event_log)
        databus = FakeDatabus()

        adapter.install(databus)
        holding = databus.get_value(adapter.binding.holding_registers_key)
        coils = databus.get_value(adapter.binding.coils_key)

        holding[1] = 1
        coils[0] = 1

        self.assertEqual(world.state.mode, OperatingMode.MANUAL)
        self.assertTrue(world.state.outlet_pump_running)
        self.assertEqual(len(event_log.list_events()), 2)

    def test_slice_write_matches_multiple_coil_operation(self) -> None:
        world = TankPumpWorld()
        event_log = InMemoryEventLog()
        adapter = ConpotDatabusAdapter(TankPumpRegisterMap(world), event_log)
        databus = FakeDatabus()

        adapter.install(databus)
        holding = databus.get_value(adapter.binding.holding_registers_key)
        coils = databus.get_value(adapter.binding.coils_key)

        holding[1] = 1
        coils[0:2] = [1, 1]

        self.assertEqual(coils[0:2], [1, 1])
        self.assertTrue(world.state.outlet_pump_running)
        self.assertTrue(world.state.inlet_valve_open)
        self.assertEqual(len(event_log.list_events()), 3)

    def test_snapshot_blocks_returns_plain_lists_for_debugging(self) -> None:
        world = TankPumpWorld()
        adapter = ConpotDatabusAdapter(TankPumpRegisterMap(world))

        blocks = adapter.snapshot_blocks()

        self.assertEqual(blocks[adapter.binding.coils_key], [0, 0])
        self.assertEqual(blocks[adapter.binding.holding_registers_key], [650, 2])
        self.assertIn(adapter.binding.input_registers_key, blocks)

    def test_blocks_survive_conpot_style_get_value(self) -> None:
        world = TankPumpWorld()
        adapter = ConpotDatabusAdapter(TankPumpRegisterMap(world))
        databus = ConpotLikeDatabus()

        adapter.install(databus)
        block = databus.get_value(adapter.binding.input_registers_key)

        self.assertEqual(block[0:2], [500, 120])
        self.assertFalse(hasattr(block, "get_value"))
        self.assertFalse(hasattr(block, "__call__"))

    def test_xml_friendly_blocks_share_one_runtime(self) -> None:
        runtime_id = "unit-test-runtime"
        reset_shared_tank_pump_runtime(runtime_id)

        holding = ConpotTankPumpBlock("holding_registers", runtime_id)
        coils = ConpotTankPumpBlock("coils", runtime_id)

        holding[1] = 1
        coils[0] = 1

        runtime = get_shared_tank_pump_runtime(runtime_id)
        self.assertEqual(runtime.world.state.mode, OperatingMode.MANUAL)
        self.assertTrue(runtime.world.state.outlet_pump_running)
        self.assertEqual(coils[0], 1)
        self.assertEqual(len(runtime.event_log.list_events()), 2)


if __name__ == "__main__":
    unittest.main()
