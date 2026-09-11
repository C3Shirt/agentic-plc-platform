# Generic process backend design

The honeypot should not bind a PLC protocol implementation to one physical
process simulator. The intended boundary is:

```text
ProtocolAdapter -> ScenarioMapping -> ProcessBackend -> PhysicalProcessContext -> AgentRuntime
```

## Contracts

- `ProcessBackend` owns simulator state, snapshots, ticks, reads, and bounded
  writes.
- `ScenarioMapping` selects the PLC-facing slice: protocol table, address,
  scaling, tag, access mode, and process variable id.
- `ProcessRegisterMap` turns a scenario-mapped backend into Modbus-style
  register reads/writes. It is generic and should work for TE, water-treatment,
  power-grid, or building-automation backends if they expose the same contract.
  Its write-preview path decodes protocol values into engineering values without
  mutating the backend, which lets generated write acknowledgements depend on a
  separately validated process-state patch.
- `ProtocolAdapter` remains protocol-specific. Modbus, HTTP HMI, S7, OPC UA,
  DNP3, BACnet, or EtherNet/IP should all read/write through mappings rather
  than through simulator-specific column numbers.
- `AgentRuntime` can observe protocol events and propose generated replies or
  world patches. The backend remains the state authority after validation.
- `PhysicalProcessContext` is what makes the agent generic. It describes the
  active process type, current snapshot, PLC-facing points, scaling, access mode,
  and writable variable ids. The agent should infer behavior from this context
  instead of relying on TE-specific or tank-pump-specific code paths.

## Backend families

The first implementation includes two backend styles:

1. native deterministic model, currently `TankPumpWorld`;
2. trace replay, currently `TraceProcessBackend` and
   `TennesseeEastmanTraceBackend`.

The same contract can later support:

- Python simulators;
- FMU/Modelica wrappers;
- MATLAB/Simulink co-simulation wrappers;
- containerized process simulators;
- remote RPC simulators;
- recorded plant historian traces.

## Tennessee Eastman integration

The Tennessee Eastman backend is intentionally a replay backend. It reads the
UW TE IDV files (`t.dat`, `y.dat`, `u.dat`, `r.dat`) into canonical variables:

- `xmeas_01` through `xmeas_51` for measured and extra output variables;
- `xmv_01` through `xmv_12` for manipulated variables;
- `xset_01` through `xset_36` for decentralized-controller setpoints and
  related controller variables.

The first scenario,
`scenarios/tennessee_eastman/scenario.json`, exposes only a reactor/separator
PLC slice:

- reactor pressure, level, and temperature;
- separator temperature, level, and pressure;
- stripper level and temperature;
- production rate of G;
- reactor/separator/stripper setpoints;
- reactor coolant, condenser coolant, and feed-valve outputs.

This is a deliberately partial PLC view of the full TE plant. An attacker sees a
coherent industrial control cell, while the backend can still advance against the
larger process trace.

## Generic process-aware agent

The agent should be described as process-aware, not TE-aware. TE is only one
loaded backend. At runtime the planner receives:

- recent normalized `ICSEvent` records;
- a `PhysicalProcessContext` with `process_id`, backend name, scenario id, PLC
  area, snapshot revision, simulation time, exposed process points, and writable
  variable ids;
- optional protocol/register mapping helpers for deterministic fallback or
  validation.

For read requests, a planner can generate protocol replies from the current
snapshot and scenario mapping. For state changes, patches are accepted only when
they target writable backend variables and pass backend bounds.

For Modbus write requests, the default process-aware path is:

```text
request write -> scenario decode -> world_patch proposal -> patch validator
              -> backend override -> generated write ACK
```

If the decode or patch validation fails, the generated ACK is withheld and the
protocol adapter can use its deterministic fallback.

## Runtime write semantics for traces

Trace files are immutable. If a protocol write targets a writable manipulated
variable or setpoint, `TraceProcessBackend.write()` stores a bounded runtime
override. The snapshot returns the override while preserving the original trace.
This gives the honeypot a credible state mutation path without corrupting source
data. A future live simulator backend can replace this with true plant-dynamics
feedback.
