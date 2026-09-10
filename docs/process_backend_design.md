# Generic process backend design

The honeypot should not bind a PLC protocol implementation to one physical
process simulator. The intended boundary is:

```text
ProtocolAdapter -> ScenarioMapping -> ProcessBackend -> AgentRuntime
```

## Contracts

- `ProcessBackend` owns simulator state, snapshots, ticks, reads, and bounded
  writes.
- `ScenarioMapping` selects the PLC-facing slice: protocol table, address,
  scaling, tag, access mode, and process variable id.
- `ProcessRegisterMap` turns a scenario-mapped backend into Modbus-style
  register reads/writes. It is generic and should work for TE, water-treatment,
  power-grid, or building-automation backends if they expose the same contract.
- `ProtocolAdapter` remains protocol-specific. Modbus, HTTP HMI, SSH
  maintenance, S7, OPC UA, or DNP3 should all read/write through mappings rather
  than through simulator-specific column numbers.
- `AgentRuntime` can observe protocol events and propose generated replies or
  world patches. The backend remains the state authority after validation.

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

## Runtime write semantics for traces

Trace files are immutable. If a protocol write targets a writable manipulated
variable or setpoint, `TraceProcessBackend.write()` stores a bounded runtime
override. The snapshot returns the override while preserving the original trace.
This gives the honeypot a credible state mutation path without corrupting source
data. A future live simulator backend can replace this with true plant-dynamics
feedback.
