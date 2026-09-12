from __future__ import annotations

import unittest

from agentic_plc.contracts.events import ICSEvent, Intent
from agentic_plc.evaluation import (
    InvariantComparison,
    ProcessInvariant,
    ProcessInvariantEvaluator,
    ProcessInvariantKind,
    TrendDirection,
    create_benchmark_process_context,
)
from agentic_plc.agent.process_context import PhysicalProcessContext
from agentic_plc.processes import ProcessVariable, TraceProcessBackend


class PhysicalInvariantTests(unittest.TestCase):
    def test_variable_equals_and_bounds_invariants_pass(self) -> None:
        context = create_benchmark_process_context()
        context.backend.write("level_sp", 70.0)
        snapshot = context.snapshot()
        evaluator = ProcessInvariantEvaluator()

        results = evaluator.evaluate_many(
            (
                ProcessInvariant(
                    invariant_id="level_sp_eq_70",
                    kind=ProcessInvariantKind.VARIABLE_EQUALS,
                    variable_id="level_sp",
                    value=70.0,
                ),
                ProcessInvariant(
                    invariant_id="level_sp_in_bounds",
                    kind=ProcessInvariantKind.VARIABLE_BETWEEN,
                    variable_id="level_sp",
                ),
            ),
            context=context,
            before_snapshot=snapshot,
            after_snapshot=snapshot,
            event=_read_holding_event(),
            reply_values=None,
        )

        self.assertTrue(all(result.passed for result in results))

    def test_relation_invariant_detects_violation(self) -> None:
        context = create_benchmark_process_context()
        snapshot = context.snapshot()
        evaluator = ProcessInvariantEvaluator()

        result = evaluator.evaluate(
            ProcessInvariant(
                invariant_id="level_pct_ge_sp",
                kind=ProcessInvariantKind.RELATION,
                left_variable="level_pct",
                operator=InvariantComparison.GE,
                right_variable="level_sp",
            ),
            context=context,
            before_snapshot=snapshot,
            after_snapshot=snapshot,
            event=_read_holding_event(),
            reply_values=None,
        )

        self.assertFalse(result.passed)
        self.assertEqual(result.reason, "relation_violated")

    def test_trend_and_moves_toward_invariants(self) -> None:
        context = _moving_level_context()
        before = context.snapshot()
        context.backend.tick(1.0)
        after = context.snapshot()
        evaluator = ProcessInvariantEvaluator()

        trend = evaluator.evaluate(
            ProcessInvariant(
                invariant_id="level_pct_increases",
                kind=ProcessInvariantKind.TREND,
                variable_id="level_pct",
                direction=TrendDirection.INCREASE,
            ),
            context=context,
            before_snapshot=before,
            after_snapshot=after,
            event=_read_holding_event(),
            reply_values=None,
        )
        moves_toward = evaluator.evaluate(
            ProcessInvariant(
                invariant_id="level_pct_moves_toward_sp",
                kind=ProcessInvariantKind.MOVES_TOWARD,
                variable_id="level_pct",
                target_variable="level_sp",
            ),
            context=context,
            before_snapshot=before,
            after_snapshot=after,
            event=_read_holding_event(),
            reply_values=None,
        )

        self.assertTrue(trend.passed)
        self.assertTrue(moves_toward.passed)

    def test_reply_matches_process_snapshot_invariant(self) -> None:
        context = create_benchmark_process_context()
        evaluator = ProcessInvariantEvaluator()
        event = _read_holding_event()

        passing = evaluator.evaluate(
            ProcessInvariant(
                invariant_id="reply_matches_sp",
                kind=ProcessInvariantKind.REPLY_MATCHES_PROCESS_SNAPSHOT,
            ),
            context=context,
            before_snapshot=context.snapshot(),
            after_snapshot=context.snapshot(),
            event=event,
            reply_values=(500,),
        )
        failing = evaluator.evaluate(
            ProcessInvariant(
                invariant_id="reply_mismatches_sp",
                kind=ProcessInvariantKind.REPLY_MATCHES_PROCESS_SNAPSHOT,
            ),
            context=context,
            before_snapshot=context.snapshot(),
            after_snapshot=context.snapshot(),
            event=event,
            reply_values=(501,),
        )

        self.assertTrue(passing.passed)
        self.assertFalse(failing.passed)
        self.assertEqual(failing.reason, "reply_process_snapshot_mismatch")

    def test_invariant_round_trip(self) -> None:
        invariant = ProcessInvariant(
            invariant_id="pressure_bound",
            kind=ProcessInvariantKind.VARIABLE_BETWEEN,
            variable_id="pressure",
            minimum=0.0,
            maximum=10.0,
            tolerance=0.01,
            description="Pressure should stay in range.",
            metadata={"source": "test"},
        )

        loaded = ProcessInvariant.from_dict(invariant.to_dict())

        self.assertEqual(loaded, invariant)


def _read_holding_event() -> ICSEvent:
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


def _moving_level_context() -> PhysicalProcessContext:
    return PhysicalProcessContext(
        backend=TraceProcessBackend(
            process_id="moving_level",
            name="moving_level_trace",
            time_seconds=[0.0, 1.0],
            variables=[
                ProcessVariable(
                    variable_id="level_sp",
                    name="Level setpoint",
                    role="setpoint",
                    minimum=0.0,
                    maximum=100.0,
                    writable=True,
                ),
                ProcessVariable(
                    variable_id="level_pct",
                    name="Level measurement",
                    role="measurement",
                    minimum=0.0,
                    maximum=100.0,
                    writable=False,
                ),
            ],
            series={
                "level_sp": (70.0, 70.0),
                "level_pct": (48.0, 55.0),
            },
        )
    )


if __name__ == "__main__":
    unittest.main()
