from __future__ import annotations

import json
import unittest

from agentic_plc.evaluation import (
    ConsistencyBenchmarkRunner,
    benchmark_cases_from_payload,
    benchmark_cases_to_payload,
    build_default_modbus_consistency_cases,
    build_formula_process_consistency_cases,
    create_benchmark_process_context,
    create_formula_benchmark_process_context,
)


class ConsistencyBenchmarkTests(unittest.TestCase):
    def test_default_cases_cover_protocol_and_physical_consistency(self) -> None:
        cases = build_default_modbus_consistency_cases()

        self.assertEqual(len(cases), 6)
        tags = {tag for case in cases for tag in case.tags}
        self.assertIn("protocol_fsm", tags)
        self.assertIn("physical_consistency", tags)
        self.assertIn("generated_reply_gate", tags)
        self.assertIn("snapshot_consistency", tags)
        self.assertIn("memory_consistency", tags)

    def test_runner_passes_default_modbus_consistency_benchmark(self) -> None:
        report = ConsistencyBenchmarkRunner().run()

        self.assertTrue(report.passed)
        self.assertEqual(report.total_steps, 13)
        self.assertEqual(
            report.status_counts(),
            {"allowed": 11, "anomalous": 1, "denied": 1},
        )
        self.assertEqual(report.check_group_counts()["snapshot_consistency"]["passed"], 6)
        self.assertEqual(report.check_group_counts()["memory_consistency"]["passed"], 12)

    def test_write_then_readback_checks_physical_and_reply_values(self) -> None:
        case = next(
            case
            for case in build_default_modbus_consistency_cases()
            if case.case_id == "modbus_write_then_readback"
        )

        results = ConsistencyBenchmarkRunner().run_case(case)

        self.assertTrue(all(result.passed for result in results))
        self.assertEqual(results[0].process_values["level_sp"], 70.0)
        self.assertEqual(results[0].world_patch_count, 1)
        self.assertEqual(results[1].reply_values, (700,))
        self.assertTrue(results[0].process_invariant_results)
        self.assertTrue(
            all(result.passed for result in results[0].process_invariant_results)
        )

    def test_protocol_denial_blocks_forced_generated_reply(self) -> None:
        case = next(
            case
            for case in build_default_modbus_consistency_cases()
            if case.case_id == "modbus_denied_transition_blocks_forced_reply"
        )

        result = ConsistencyBenchmarkRunner().run_case(case)[0]

        self.assertTrue(result.passed)
        self.assertEqual(result.protocol_status, "denied")
        self.assertFalse(result.forced_reply_accepted)
        assert result.forced_reply_error is not None
        self.assertIn("protocol state machine", result.forced_reply_error)

    def test_snapshot_memory_governance_case_tracks_context_and_revisions(self) -> None:
        case = next(
            case
            for case in build_default_modbus_consistency_cases()
            if case.case_id == "modbus_snapshot_memory_governance"
        )

        results = ConsistencyBenchmarkRunner().run_case(case)

        self.assertTrue(all(result.passed for result in results))
        self.assertEqual([result.snapshot_revision_delta for result in results], [0, 1, 1, 0])
        self.assertEqual(results[1].patch_base_revisions, (0,))
        self.assertEqual(results[2].patch_base_revisions, (1,))
        assert results[-1].memory_result is not None
        self.assertEqual(results[-1].memory_result.actor_event_count, 4)
        self.assertIn("level_sp", results[-1].memory_result.touched_variables)
        self.assertIn("pump_cmd", results[-1].memory_result.touched_variables)
        self.assertIn("level_sp", results[-1].memory_result.selected_variables)

    def test_report_is_json_serializable(self) -> None:
        report = ConsistencyBenchmarkRunner().run()

        encoded = json.dumps(report.to_dict(), sort_keys=True)

        self.assertIn("modbus_write_then_readback", encoded)

    def test_default_cases_round_trip_with_process_invariants(self) -> None:
        cases = build_default_modbus_consistency_cases()
        loaded = benchmark_cases_from_payload(benchmark_cases_to_payload(cases))

        report = ConsistencyBenchmarkRunner().run(loaded)

        self.assertTrue(report.passed)
        invariant_checks = [
            check_name
            for results in report.cases.values()
            for result in results
            for check_name in result.checks
            if check_name.startswith("process_invariant:")
        ]
        self.assertIn(
            "process_invariant:read_reply_matches_process_snapshot",
            invariant_checks,
        )

    def test_benchmark_process_context_starts_from_expected_state(self) -> None:
        context = create_benchmark_process_context()

        self.assertEqual(context.backend.read("level_sp"), 50.0)
        self.assertEqual(context.values_for_modbus_event(_read_holding_event()), [500])

    def test_formula_process_cases_cover_dynamic_backend(self) -> None:
        cases = build_formula_process_consistency_cases()

        report = ConsistencyBenchmarkRunner(
            process_context_factory=create_formula_benchmark_process_context,
        ).run(cases)

        self.assertTrue(report.passed)
        self.assertEqual(report.total_steps, 2)
        results = report.cases["formula_process_write_then_dynamics"]
        self.assertEqual(results[0].snapshot_revision_delta, 2)
        self.assertEqual(results[0].patch_base_revisions, (0,))
        self.assertAlmostEqual(results[0].process_values["level_pct"], 53.9)
        self.assertEqual(results[1].reply_values, (539,))


def _read_holding_event():
    from agentic_plc.contracts.events import ICSEvent, Intent

    return ICSEvent(
        protocol="modbus",
        session_id="s1",
        source_ip="192.0.2.10",
        actor_id="actor-1",
        intent=Intent.READ_PROCESS,
        operation="read_holding_registers",
        transaction_id="1",
        unit_id=1,
        address=0,
        count=1,
        metadata={"function_code": 3},
    )


if __name__ == "__main__":
    unittest.main()
