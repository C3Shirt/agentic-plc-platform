from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from agentic_plc.agent import (
    ContextBudget,
    LLMConfig,
    OpenAICompatiblePlanner,
    PhysicalProcessContext,
    ProcessContextCompressor,
)
from agentic_plc.contracts.events import ICSEvent, Intent
from agentic_plc.processes import (
    ProcessRegisterMap,
    ScenarioMapping,
    TennesseeEastmanTraceBackend,
)


class ProcessContextCompressorTests(unittest.TestCase):
    def test_keeps_current_request_points_and_exact_register_values(self) -> None:
        context = _process_context()
        event = _read_input_event(address=0, count=3)
        compressed = ProcessContextCompressor(
            ContextBudget(max_points=2, max_events=3)
        ).compress(context, [event])

        payload = compressed.to_prompt_dict()
        selected_ids = {
            point["variable_id"]
            for point in payload["exposed_points"]
        }

        self.assertLessEqual(len(payload["exposed_points"]), 2)
        self.assertIn("xmeas_07", selected_ids)
        self.assertIn("xmeas_08", selected_ids)
        self.assertEqual(payload["request_focus"]["table"], "input_registers")
        self.assertEqual(payload["request_focus"]["register_values"], [7, 80, 90])
        self.assertIn("xmv_10", payload["writable_variable_ids"])
        self.assertEqual(
            payload["compression"]["strategy"],
            "process_aware_context_compression_v1",
        )

    def test_write_surface_is_prioritized_without_losing_allowed_patch_paths(self) -> None:
        context = _process_context()
        event = ICSEvent(
            protocol="modbus",
            session_id="s1",
            source_ip="192.0.2.10",
            actor_id="actor-1",
            intent=Intent.CONTROL_OUTPUT,
            operation="write_single_register",
            transaction_id="18",
            unit_id=1,
            address=5,
            requested_value=420,
            result="observed",
            metadata={"function_code": 6},
        )

        compressed = ProcessContextCompressor(
            ContextBudget(max_points=1)
        ).compress(context, [event])
        payload = compressed.to_prompt_dict()

        self.assertEqual(payload["exposed_points"][0]["variable_id"], "xmv_10")
        self.assertEqual(payload["exposed_points"][0]["encoded_value"], 100)
        self.assertEqual(payload["request_focus"]["table"], "holding_registers")
        self.assertIn("xset_08", payload["writable_variable_ids"])
        self.assertIn("xmv_10", payload["writable_variable_ids"])

    def test_planner_prompt_uses_compressed_process_context(self) -> None:
        context = _process_context()
        event = _read_input_event(address=0, count=3)
        planner = OpenAICompatiblePlanner(
            LLMConfig(
                base_url="https://example.test/v1",
                api_key="dummy",
                model="test-model",
                context_max_points=2,
                context_max_events=2,
            )
        )

        prompt = planner._user_prompt([event], context)
        payload = json.loads(prompt.split("Payload: ", 1)[1])
        process_context = payload["process_context"]

        self.assertEqual(
            process_context["process_card"]["scenario_id"],
            "tennessee_eastman_reactor_separator_cell",
        )
        self.assertLessEqual(len(process_context["exposed_points"]), 2)
        self.assertEqual(
            process_context["request_focus"]["register_values"],
            [7, 80, 90],
        )
        self.assertEqual(payload["allowed_world_patch_paths"], [
            "xset_08",
            "xset_09",
            "xset_10",
            "xset_11",
            "xset_36",
            "xmv_10",
            "xmv_11",
            "xmv_04",
        ])


def _process_context() -> PhysicalProcessContext:
    with tempfile.TemporaryDirectory() as directory:
        trace_dir = Path(directory)
        _write_matrix(trace_dir / "t.dat", [[0.0, 1.0e30, -1.0]])
        _write_matrix(trace_dir / "y.dat", [_row(1.0, 51)])
        _write_matrix(trace_dir / "u.dat", [_row(1.0, 12)])
        _write_matrix(trace_dir / "r.dat", [_row(1.0, 36)])
        backend = TennesseeEastmanTraceBackend.from_directory(trace_dir)

    scenario = ScenarioMapping.from_file(
        Path("scenarios/tennessee_eastman/scenario.json")
    )
    register_map = ProcessRegisterMap(backend, scenario)
    return PhysicalProcessContext(
        backend=backend,
        scenario=scenario,
        register_map=register_map,
    )


def _read_input_event(address: int, count: int) -> ICSEvent:
    return ICSEvent(
        protocol="modbus",
        session_id="s1",
        source_ip="192.0.2.10",
        actor_id="actor-1",
        intent=Intent.READ_PROCESS,
        operation="read_input_registers",
        transaction_id="17",
        unit_id=1,
        address=address,
        count=count,
        result="observed",
        metadata={"function_code": 4},
    )


def _row(start: float, count: int) -> list[float]:
    return [start + offset for offset in range(count)]


def _write_matrix(path: Path, rows: list[list[float]]) -> None:
    path.write_text(
        "\n".join("\t".join(str(value) for value in row) for row in rows) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    unittest.main()
