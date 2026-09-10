import unittest

from agentic_plc.world.model import OperatingMode, TankPumpWorld
from agentic_plc.world.registers import (
    RegisterAccessError,
    RegisterArea,
    TankPumpRegisterMap,
)


class TankPumpRegisterMapTests(unittest.TestCase):
    def test_reads_encode_process_state(self) -> None:
        world = TankPumpWorld()
        registers = TankPumpRegisterMap(world)

        self.assertEqual(registers.read(RegisterArea.INPUT_REGISTERS, 0, 2), [500, 120])
        self.assertEqual(registers.read(RegisterArea.HOLDING_REGISTERS, 0, 2), [650, 2])

    def test_setpoint_write_updates_world(self) -> None:
        world = TankPumpWorld()
        registers = TankPumpRegisterMap(world)

        write = registers.write(RegisterArea.HOLDING_REGISTERS, 0, 700)

        self.assertEqual(world.state.level_setpoint_percent, 70.0)
        self.assertEqual(write.previous_value, 650)
        self.assertEqual(write.resulting_value, 700)

    def test_mode_write_enables_manual_coil_control(self) -> None:
        world = TankPumpWorld()
        registers = TankPumpRegisterMap(world)

        registers.write(RegisterArea.HOLDING_REGISTERS, 1, 1)
        registers.write(RegisterArea.COILS, 0, 1)

        self.assertEqual(world.state.mode, OperatingMode.MANUAL)
        self.assertTrue(world.state.outlet_pump_running)

    def test_read_only_register_write_is_rejected(self) -> None:
        world = TankPumpWorld()
        registers = TankPumpRegisterMap(world)

        with self.assertRaises(RegisterAccessError):
            registers.write(RegisterArea.INPUT_REGISTERS, 0, 999)

    def test_unmapped_range_is_rejected(self) -> None:
        world = TankPumpWorld()
        registers = TankPumpRegisterMap(world)

        with self.assertRaises(RegisterAccessError):
            registers.read(RegisterArea.HOLDING_REGISTERS, 1, 2)


if __name__ == "__main__":
    unittest.main()
