import unittest

from agentic_plc.contracts.events import DeceptionPlan
from agentic_plc.policy.plan_validator import DeceptionPlanValidator
from agentic_plc.world.model import ControlAction, OperatingMode, TankPumpWorld


class TankPumpWorldTests(unittest.TestCase):
    def test_auto_mode_fills_toward_setpoint(self) -> None:
        world = TankPumpWorld()
        initial_level = world.state.level_percent

        world.tick(10)

        self.assertTrue(world.state.inlet_valve_open)
        self.assertGreater(world.state.level_percent, initial_level)
        self.assertEqual(world.state.revision, 1)

    def test_manual_output_requires_manual_mode(self) -> None:
        world = TankPumpWorld()

        with self.assertRaises(ValueError):
            world.apply_control(ControlAction.SET_OUTLET_PUMP, True)

        world.apply_control(ControlAction.SET_MODE, OperatingMode.MANUAL.value)
        world.apply_control(ControlAction.SET_OUTLET_PUMP, True)
        self.assertTrue(world.state.outlet_pump_running)

    def test_out_of_range_setpoint_is_rejected(self) -> None:
        world = TankPumpWorld()

        with self.assertRaises(ValueError):
            world.apply_control(ControlAction.SET_LEVEL_SETPOINT, 99)


class DeceptionPlanValidatorTests(unittest.TestCase):
    def test_known_action_is_accepted(self) -> None:
        plan = DeceptionPlan(
            actor_id="actor-1",
            action="publish_maintenance_note",
            target="WO-2048",
            reason="Repeated setpoint reads",
        )
        DeceptionPlanValidator().validate(plan)

    def test_direct_state_mutation_is_rejected(self) -> None:
        plan = DeceptionPlan(
            actor_id="actor-1",
            action="write_arbitrary_register",
            target="holding:0",
            reason="Unvalidated model proposal",
        )

        with self.assertRaises(ValueError):
            DeceptionPlanValidator().validate(plan)


if __name__ == "__main__":
    unittest.main()

