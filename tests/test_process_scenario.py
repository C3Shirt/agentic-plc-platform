from __future__ import annotations

from pathlib import Path
import unittest

from agentic_plc.processes import ScenarioMapping


class ScenarioMappingTests(unittest.TestCase):
    def test_loads_tennessee_eastman_scenario(self) -> None:
        scenario = ScenarioMapping.from_file(
            Path("scenarios/tennessee_eastman/scenario.json")
        )

        self.assertEqual(
            scenario.scenario_id, "tennessee_eastman_reactor_separator_cell"
        )
        self.assertEqual(scenario.backend_type, "tennessee_eastman_trace")
        modbus_points = scenario.variables_for_protocol("modbus")
        self.assertGreaterEqual(len(modbus_points), 10)
        self.assertIn("xmeas_07", {point.variable_id for point in modbus_points})
        self.assertIn("xset_09", {point.variable_id for point in modbus_points})


if __name__ == "__main__":
    unittest.main()
