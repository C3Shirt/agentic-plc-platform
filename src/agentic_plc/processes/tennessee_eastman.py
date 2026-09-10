from __future__ import annotations

from pathlib import Path
from typing import Any

from .base import ProcessVariable
from .trace import TraceProcessBackend, load_whitespace_matrix

Y_EXTRA_VARIABLES: dict[int, tuple[str, str, str]] = {
    42: ("cost", "Cost", "cents/kmol product"),
    43: ("production_rate_g", "Production Rate G", "kmol G/h"),
    44: ("production_rate_h", "Production Rate H", "kmol H/h"),
    45: ("production_rate_f", "Production Rate F", "kmol F/h"),
    46: ("reactor_partial_pressure_a", "Reactor Partial Pressure A", "kPa"),
    47: ("reactor_partial_pressure_c", "Reactor Partial Pressure C", "kPa"),
    48: ("reactor_partial_pressure_d", "Reactor Partial Pressure D", "kPa"),
    49: ("reactor_partial_pressure_e", "Reactor Partial Pressure E", "kPa"),
    50: ("product_mole_percent_g_true", "Product Mole Percent G True", "%"),
    51: ("product_mole_percent_h_true", "Product Mole Percent H True", "%"),
}

SETPOINT_VARIABLES: dict[int, tuple[str, str, str]] = {
    1: ("a_feed_flow_setpoint", "Setpoint for A feed flow", "kscmh"),
    2: ("d_feed_flow_setpoint", "Setpoint for D feed flow", "kg/h"),
    3: ("e_feed_flow_setpoint", "Setpoint for E feed flow", "kg/h"),
    4: ("ca_feed_flow_setpoint", "Setpoint for C+A feed flow", "kscmh"),
    5: ("purge_rate_setpoint", "Setpoint for purge rate", "kscmh"),
    6: ("separator_underflow_setpoint", "Setpoint for separator underflow", "m3/h"),
    7: ("product_rate_setpoint", "Setpoint for product rate", "m3/h"),
    8: ("reactor_pressure_setpoint", "Setpoint for reactor pressure", "kPa"),
    9: ("reactor_level_setpoint", "Setpoint for reactor level", "%"),
    10: ("separator_level_setpoint", "Setpoint for separator level", "%"),
    11: ("stripper_level_setpoint", "Setpoint for stripper level", "%"),
    12: ("unused_12", "Unused setpoint/control slot 12", ""),
    13: ("operator_product_rate_target", "Operator product rate target", "m3/h"),
    14: ("operator_product_g_target", "Operator target for mole percent G", "%"),
    15: ("unused_15", "Unused setpoint/control slot 15", ""),
    16: ("reactor_feed_ac_ratio_setpoint", "Setpoint for A/(A+C) in feed", "%"),
    17: ("reactor_feed_ac_total_setpoint", "Setpoint for A+C in feed", "%"),
    18: ("unused_18", "Unused setpoint/control slot 18", ""),
    19: ("max_reactor_pressure_override", "Maximum reactor pressure override", "kPa"),
    20: ("separator_coolant_min_override", "Minimum separator coolant valve", "%"),
    21: ("separator_coolant_max_override", "Maximum separator coolant valve", "%"),
    22: ("ratio_1", "Ratio setpoint 1", ""),
    23: ("ratio_2", "Ratio setpoint 2", ""),
    24: ("ratio_3", "Ratio setpoint 3", ""),
    25: ("ratio_4", "Ratio setpoint 4", ""),
    26: ("ratio_5", "Ratio setpoint 5", ""),
    27: ("ratio_6", "Ratio setpoint 6", ""),
    28: ("ratio_7", "Ratio setpoint 7", ""),
    29: ("unused_29", "Unused setpoint/control slot 29", ""),
    30: ("reactor_pressure_override_output", "Reactor pressure override output", ""),
    31: ("production_rate_index_nominal", "Nominal production rate index", ""),
    32: ("production_rate_index_adjusted", "Adjusted production rate index", ""),
    33: ("production_rate_feedback_adjustment", "Production rate feedback adjustment", ""),
    34: ("product_g_feedback_setpoint", "Current setpoint for product G feedback", "%"),
    35: ("eadj", "Eadj value used in controller equations", ""),
    36: ("reactor_temperature_setpoint", "Reactor temperature setpoint", "C"),
}


class TennesseeEastmanTraceBackend(TraceProcessBackend):
    """TE decentralized-control trace replay backend.

    This class adapts the University of Washington Tennessee Eastman IDV data
    into the generic TraceProcessBackend contract. It does not expose Modbus or
    PLC addresses directly; scenario JSON decides which process variables a
    honeypot PLC presents to an attacker.
    """

    @classmethod
    def from_directory(
        cls,
        trace_dir: str | Path,
        *,
        tables_dir: str | Path | None = None,
        idv: str = "idv1",
        loop: bool = False,
    ) -> TennesseeEastmanTraceBackend:
        trace_path = Path(trace_dir)
        y_rows = load_whitespace_matrix(trace_path / "y.dat")
        u_rows = load_whitespace_matrix(trace_path / "u.dat")
        r_rows = load_whitespace_matrix(trace_path / "r.dat")
        t_rows = load_whitespace_matrix(trace_path / "t.dat")

        _require_column_count("y.dat", y_rows, 51)
        _require_column_count("u.dat", u_rows, 12)
        _require_column_count("r.dat", r_rows, 36)
        _require_column_count("t.dat", t_rows, 1)
        if not (len(y_rows) == len(u_rows) == len(r_rows) == len(t_rows)):
            raise ValueError("TE y/u/r/t row counts must match")

        tables_path = Path(tables_dir) if tables_dir else None
        variables = [
            *_measurement_variables(tables_path),
            *_manipulated_variables(tables_path),
            *_setpoint_variables(),
        ]
        series = _build_series("xmeas", y_rows, 51)
        series.update(_build_series("xmv", u_rows, 12))
        series.update(_build_series("xset", r_rows, 36))
        time_seconds = [row[0] * 3600.0 for row in t_rows]

        return cls(
            process_id=f"tennessee_eastman_{idv}",
            name="tennessee_eastman_trace",
            time_seconds=time_seconds,
            variables=variables,
            series=series,
            metadata={
                "simulator_family": "tennessee_eastman",
                "trace_id": idv,
                "trace_dir": str(trace_path),
                "tables_dir": str(tables_path) if tables_path else None,
                "source": "University of Washington Tennessee Eastman Challenge Archive",
            },
            loop=loop,
        )

    @classmethod
    def from_asset_root(
        cls,
        asset_root: str | Path,
        *,
        idv: str = "idv1",
        loop: bool = False,
    ) -> TennesseeEastmanTraceBackend:
        root = Path(asset_root)
        trace_dir = root / "extracted" / idv
        tables_dir = root / "extracted" / "tables" / "tables"
        return cls.from_directory(
            trace_dir,
            tables_dir=tables_dir if tables_dir.exists() else None,
            idv=idv,
            loop=loop,
        )


def _measurement_variables(tables_dir: Path | None) -> list[ProcessVariable]:
    table_names = _load_table_names(tables_dir / "table3.txt") if tables_dir else {}
    variables: list[ProcessVariable] = []
    for index in range(1, 42):
        name, unit = table_names.get(index, (f"XMEAS {index}", ""))
        variables.append(
            ProcessVariable(
                variable_id=f"xmeas_{index:02d}",
                name=name,
                role="measurement",
                unit=unit or None,
                metadata={"te_signal": f"XMEAS({index})"},
            )
        )
    for index in range(42, 52):
        _, name, unit = Y_EXTRA_VARIABLES[index]
        variables.append(
            ProcessVariable(
                variable_id=f"xmeas_{index:02d}",
                name=name,
                role="measurement",
                unit=unit,
                metadata={"te_signal": f"Y_EXTRA({index})"},
            )
        )
    return variables


def _manipulated_variables(tables_dir: Path | None) -> list[ProcessVariable]:
    table_names = _load_table_names(tables_dir / "table4.txt") if tables_dir else {}
    variables: list[ProcessVariable] = []
    for index in range(1, 13):
        name, unit = table_names.get(index, (f"XMV {index}", "%"))
        variables.append(
            ProcessVariable(
                variable_id=f"xmv_{index:02d}",
                name=name,
                role="manipulated_variable",
                unit=unit or None,
                minimum=0.0,
                maximum=100.0,
                writable=True,
                metadata={"te_signal": f"XMV({index})"},
            )
        )
    return variables


def _setpoint_variables() -> list[ProcessVariable]:
    variables: list[ProcessVariable] = []
    for index in range(1, 37):
        _, name, unit = SETPOINT_VARIABLES[index]
        variables.append(
            ProcessVariable(
                variable_id=f"xset_{index:02d}",
                name=name,
                role="setpoint",
                unit=unit or None,
                writable=True,
                metadata={"te_signal": f"R({index})"},
            )
        )
    return variables


def _load_table_names(path: Path) -> dict[int, tuple[str, str]]:
    if not path.exists():
        return {}
    result: dict[int, tuple[str, str]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        parts = line.rstrip().split("\t")
        if len(parts) < 3:
            continue
        try:
            index = int(parts[0].strip())
        except ValueError:
            continue
        result[index] = (parts[1].strip(), parts[2].strip())
    return result


def _build_series(prefix: str, rows: list[list[float]], column_count: int) -> dict[str, tuple[float, ...]]:
    series: dict[str, tuple[float, ...]] = {}
    for column_index in range(column_count):
        variable_id = f"{prefix}_{column_index + 1:02d}"
        series[variable_id] = tuple(row[column_index] for row in rows)
    return series


def _require_column_count(name: str, rows: list[list[float]], minimum: int) -> None:
    for row_index, row in enumerate(rows, 1):
        if len(row) < minimum:
            raise ValueError(f"{name}:{row_index} has {len(row)} columns; expected {minimum}")
