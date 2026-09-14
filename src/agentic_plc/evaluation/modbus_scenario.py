from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


MODBUS_TABLE_FUNCTION_CODES: dict[str, int] = {
    "coils": 1,
    "discrete_inputs": 2,
    "holding_registers": 3,
    "input_registers": 4,
}


@dataclass(frozen=True, slots=True)
class ScenarioModbusPoint:
    """One Modbus-facing point loaded from a process scenario file."""

    variable_id: str
    table: str
    address: int
    access: str
    scale: float = 1.0
    data_type: str = "uint16"
    tag: str | None = None
    description: str = ""

    @property
    def function_code(self) -> int:
        return MODBUS_TABLE_FUNCTION_CODES[self.table]

    @property
    def writable(self) -> bool:
        return "write" in self.access.lower()


def load_scenario_payload(source: str | Path | Mapping[str, Any] | object) -> dict[str, Any]:
    """Load a process scenario from a path, mapping, or ScenarioMapping-like object."""

    if isinstance(source, (str, Path)):
        return json.loads(Path(source).read_text(encoding="utf-8"))
    if isinstance(source, Mapping):
        return dict(source)
    to_dict = getattr(source, "to_dict", None)
    if callable(to_dict):
        payload = to_dict()
        if isinstance(payload, Mapping):
            return dict(payload)
    raise TypeError("scenario source must be a path, mapping, or ScenarioMapping")


def scenario_id_from_source(source: str | Path | Mapping[str, Any] | object) -> str:
    payload = load_scenario_payload(source)
    scenario_id = str(payload.get("scenario_id") or "").strip()
    if scenario_id:
        return scenario_id
    if isinstance(source, (str, Path)):
        return Path(source).stem
    return "scenario"


def modbus_points_from_scenario(
    source: str | Path | Mapping[str, Any] | object,
) -> tuple[ScenarioModbusPoint, ...]:
    """Extract Modbus points from both modern `points[]` and legacy `register_map` scenarios."""

    payload = load_scenario_payload(source)
    points = _points_from_modern_scenario(payload)
    if points:
        return points
    return _points_from_legacy_register_map(payload)


def _points_from_modern_scenario(
    payload: Mapping[str, Any],
) -> tuple[ScenarioModbusPoint, ...]:
    raw_points = payload.get("points")
    if not isinstance(raw_points, list):
        return ()

    points: list[ScenarioModbusPoint] = []
    for raw_point in raw_points:
        if not isinstance(raw_point, Mapping):
            continue
        protocol = str(raw_point.get("protocol", "modbus")).lower()
        if protocol != "modbus":
            continue
        table = str(raw_point.get("table", "")).strip()
        if table not in MODBUS_TABLE_FUNCTION_CODES:
            continue
        variable_id = str(raw_point.get("variable_id", "")).strip()
        if not variable_id:
            continue
        points.append(
            ScenarioModbusPoint(
                variable_id=variable_id,
                table=table,
                address=int(raw_point["address"]),
                access=str(raw_point.get("access", "read")),
                scale=float(raw_point.get("scale", 1.0)),
                data_type=str(raw_point.get("data_type", "uint16")),
                tag=_optional_text(raw_point.get("tag")),
                description=str(raw_point.get("description", "")),
            )
        )
    return tuple(sorted(points, key=lambda point: (point.function_code, point.address)))


def _points_from_legacy_register_map(
    payload: Mapping[str, Any],
) -> tuple[ScenarioModbusPoint, ...]:
    register_map = payload.get("register_map")
    if not isinstance(register_map, Mapping):
        return ()

    points: list[ScenarioModbusPoint] = []
    for table, raw_block in register_map.items():
        table_name = str(table)
        if table_name not in MODBUS_TABLE_FUNCTION_CODES:
            continue
        if not isinstance(raw_block, Mapping):
            continue
        for raw_address, raw_variable in raw_block.items():
            variable_id = str(raw_variable).strip()
            if not variable_id:
                continue
            points.append(
                ScenarioModbusPoint(
                    variable_id=variable_id,
                    table=table_name,
                    address=int(raw_address),
                    access=_legacy_access(table_name),
                    scale=_infer_scale(variable_id),
                    data_type=_legacy_data_type(table_name),
                )
            )
    return tuple(sorted(points, key=lambda point: (point.function_code, point.address)))


def _legacy_access(table: str) -> str:
    if table in {"coils", "holding_registers"}:
        return "read_write"
    return "read"


def _legacy_data_type(table: str) -> str:
    if table in {"coils", "discrete_inputs"}:
        return "bool"
    return "uint16"


def _infer_scale(variable_id: str) -> float:
    normalized = variable_id.lower()
    if normalized.endswith("_x100"):
        return 100.0
    if normalized.endswith("_x10"):
        return 10.0
    return 1.0


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
