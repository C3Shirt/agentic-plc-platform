from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from agentic_plc.contracts.events import ICSEvent, Intent
from agentic_plc.telemetry.event_log import EventSink, InMemoryEventLog
from agentic_plc.world.model import TankPumpWorld
from agentic_plc.world.registers import (
    RegisterAccessError,
    RegisterArea,
    TankPumpRegisterMap,
)


class DatabusLike(Protocol):
    def get_value(self, key: str) -> Any:
        raise NotImplementedError

    def set_value(self, key: str, value: Any) -> None:
        raise NotImplementedError


@dataclass(frozen=True, slots=True)
class ConpotDatabusBinding:
    coils_key: str = "agenticPlcSlave1Coils"
    discrete_inputs_key: str = "agenticPlcSlave1DiscreteInputs"
    input_registers_key: str = "agenticPlcSlave1InputRegisters"
    holding_registers_key: str = "agenticPlcSlave1HoldingRegisters"

    def key_for(self, area: RegisterArea) -> str:
        return {
            RegisterArea.COILS: self.coils_key,
            RegisterArea.DISCRETE_INPUTS: self.discrete_inputs_key,
            RegisterArea.INPUT_REGISTERS: self.input_registers_key,
            RegisterArea.HOLDING_REGISTERS: self.holding_registers_key,
        }[area]


@dataclass(frozen=True, slots=True)
class ConpotWriteContext:
    session_id: str = "conpot-databus"
    source_ip: str = "0.0.0.0"
    actor_id: str | None = None
    source_port: int | None = None
    unit_id: int | None = 1


@dataclass(slots=True)
class SharedTankPumpRuntime:
    world: TankPumpWorld
    register_map: TankPumpRegisterMap
    event_log: InMemoryEventLog


_SHARED_RUNTIMES: dict[str, SharedTankPumpRuntime] = {}


def get_shared_tank_pump_runtime(runtime_id: str = "tank_pump_v1") -> SharedTankPumpRuntime:
    runtime = _SHARED_RUNTIMES.get(runtime_id)
    if runtime is None:
        world = TankPumpWorld()
        runtime = SharedTankPumpRuntime(
            world=world,
            register_map=TankPumpRegisterMap(world),
            event_log=InMemoryEventLog(),
        )
        _SHARED_RUNTIMES[runtime_id] = runtime
    return runtime


def reset_shared_tank_pump_runtime(runtime_id: str = "tank_pump_v1") -> SharedTankPumpRuntime:
    _SHARED_RUNTIMES.pop(runtime_id, None)
    return get_shared_tank_pump_runtime(runtime_id)


class WorldRegisterBlock:
    """List-like register block that Conpot can store in its DataBus."""

    def __init__(
        self,
        register_map: TankPumpRegisterMap,
        area: RegisterArea,
        event_sink: EventSink | None = None,
        context: ConpotWriteContext | None = None,
    ) -> None:
        self._register_map = register_map
        self._area = area
        self._event_sink = event_sink
        self._context = context or ConpotWriteContext()

    def __len__(self) -> int:
        return self._register_map.block_size(self._area)

    def __getitem__(self, index: int | slice) -> int | list[int]:
        block = self._register_map.encode_blocks()[self._area]
        return block[index]

    def __setitem__(self, index: int | slice, value: Any) -> None:
        if isinstance(index, slice):
            start, stop, step = index.indices(len(self))
            if step != 1:
                raise RegisterAccessError("stepped register writes are not supported")
            values = self._coerce_sequence(value)
            if len(values) != stop - start:
                raise RegisterAccessError("slice assignment length mismatch")
            for offset, cell_value in enumerate(values):
                self._write_cell(start + offset, cell_value)
            return
        self._write_cell(index, value)

    def to_list(self) -> list[int]:
        return self._register_map.encode_blocks()[self._area]

    def _write_cell(self, address: int, value: Any) -> None:
        previous_value = self._read_previous(address)
        try:
            write = self._register_map.write(self._area, address, value)
        except ValueError as exc:
            self._record_event(
                address=address,
                previous_value=previous_value,
                requested_value=value,
                resulting_value=previous_value,
                result="rejected",
                error=str(exc),
            )
            return

        self._record_event(
            address=address,
            previous_value=write.previous_value,
            requested_value=write.requested_value,
            resulting_value=write.resulting_value,
            result="accepted",
            world_revision=write.world_revision,
        )

    def _read_previous(self, address: int) -> int | None:
        try:
            return self._register_map.read(self._area, address)[0]
        except ValueError:
            return None

    def _record_event(
        self,
        address: int,
        previous_value: Any,
        requested_value: Any,
        resulting_value: Any,
        result: str,
        error: str | None = None,
        world_revision: int | None = None,
    ) -> None:
        if self._event_sink is None:
            return
        metadata: dict[str, Any] = {"register_area": self._area.value}
        if error:
            metadata["error"] = error
        self._event_sink.append(
            ICSEvent(
                protocol="modbus",
                session_id=self._context.session_id,
                source_ip=self._context.source_ip,
                actor_id=self._context.actor_id,
                source_port=self._context.source_port,
                unit_id=self._context.unit_id,
                intent=self._intent_for(address),
                operation=f"write_{self._area.value}",
                address=address,
                count=1,
                previous_value=previous_value,
                requested_value=requested_value,
                resulting_value=resulting_value,
                result=result,
                world_revision=world_revision
                if world_revision is not None
                else self._register_map.world.state.revision,
                metadata=metadata,
            )
        )

    def _intent_for(self, address: int) -> Intent:
        if self._area is RegisterArea.HOLDING_REGISTERS and address == 0:
            return Intent.WRITE_SETPOINT
        if self._area in {RegisterArea.COILS, RegisterArea.HOLDING_REGISTERS}:
            return Intent.CONTROL_OUTPUT
        return Intent.UNSUPPORTED_OPERATION

    def _coerce_sequence(self, value: Any) -> Sequence[Any]:
        if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray, str)):
            return value
        raise RegisterAccessError("slice assignment requires a sequence")


class ConpotDatabusAdapter:
    """Installs dynamic register blocks into a Conpot-compatible DataBus."""

    def __init__(
        self,
        register_map: TankPumpRegisterMap,
        event_sink: EventSink | None = None,
        binding: ConpotDatabusBinding | None = None,
        context: ConpotWriteContext | None = None,
    ) -> None:
        self._register_map = register_map
        self._event_sink = event_sink
        self._binding = binding or ConpotDatabusBinding()
        self._context = context or ConpotWriteContext()

    @property
    def binding(self) -> ConpotDatabusBinding:
        return self._binding

    def install(self, databus: DatabusLike) -> None:
        for area in RegisterArea:
            databus.set_value(self._binding.key_for(area), self.block(area))

    def block(self, area: RegisterArea | str) -> WorldRegisterBlock:
        return WorldRegisterBlock(
            register_map=self._register_map,
            area=RegisterArea(area),
            event_sink=self._event_sink,
            context=self._context,
        )

    def snapshot_blocks(self) -> dict[str, list[int]]:
        blocks = self._register_map.encode_blocks()
        return {
            self._binding.key_for(area): list(values)
            for area, values in blocks.items()
        }


class ConpotTankPumpBlock(WorldRegisterBlock):
    """XML-friendly DataBus value for Conpot templates."""

    def __init__(
        self,
        area: str,
        runtime_id: str = "tank_pump_v1",
        unit_id: int = 1,
    ) -> None:
        runtime = get_shared_tank_pump_runtime(runtime_id)
        super().__init__(
            register_map=runtime.register_map,
            area=RegisterArea(area),
            event_sink=runtime.event_log,
            context=ConpotWriteContext(
                session_id=f"conpot-{runtime_id}",
                source_ip="0.0.0.0",
                unit_id=int(unit_id),
            ),
        )
