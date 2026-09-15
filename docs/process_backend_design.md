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

The first implementation includes three backend styles:

1. native deterministic model, currently `TankPumpWorld`;
2. trace replay, currently `TraceProcessBackend` and
   `TennesseeEastmanTraceBackend`;
3. equation-driven process slices, currently `FormulaProcessBackend`.
4. backend wrappers, currently `AgenticProcessBackend`, which lets a generic
   physical-process agent evolve any backend through snapshot-governed internal
   patches.

The same contract can later support:

- Python simulators;
- FMU/Modelica wrappers;
- MATLAB/Simulink co-simulation wrappers;
- containerized process simulators;
- remote RPC simulators;
- recorded plant historian traces.
- LLM-assisted physical-process agents that propose formula/constraint updates
  through the same snapshot-governed patch path.

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

## Formula process backend

`FormulaProcessBackend` fills the gap between trace replay and high-fidelity
external simulation. It maintains a mutable process state and updates selected
variables with safe arithmetic expressions over the pre-tick state and `dt`.
For example, a tank-level slice can expose:

```text
level_pct(t + dt) =
  level_pct(t) + dt * (0.2 * (level_sp(t) - level_pct(t)) + 1.5 * pump_cmd(t))
```

The implementation deliberately avoids arbitrary Python `eval`. Expressions are
parsed through a whitelist of numeric operators, comparisons, conditionals, and
safe math functions. Outputs are finite-number checked and clamped to declared
engineering bounds.

This backend does not claim high-fidelity plant simulation. Its purpose is to
maintain attacker-observable cyber-physical consistency when a real simulator is
unavailable or unnecessary. It is also the natural bridge to a future
`physical-process agent`: the agent can propose or select equations and
parameters, while the platform still owns snapshots, bounds, register mappings,
and patch validation.

The first formula scenario is
`scenarios/formula_tank/scenario.json`. The optional benchmark case
`build_formula_process_consistency_cases()` checks that a setpoint write updates
the authoritative snapshot, one formula tick moves the measurement toward the
new setpoint, and the next Modbus read encodes the evolved snapshot.

## Agentic physical-process wrapper

`AgenticProcessBackend` is the first implementation of the more general
physical-process-agent idea. It wraps an arbitrary `ProcessBackend`, captures a
revisioned snapshot, asks a `PhysicalProcessAgentPolicy` for a typed
`WorldPatch`, and applies that patch through
`ProcessSnapshotManager.apply_internal()`.

This internal path is deliberately separate from attacker-facing writes:

- external protocol/LLM patches still use `ProcessSnapshotManager.apply()` and
  can only target variables declared as writable;
- internal process dynamics use `apply_internal()` and require the backend to
  expose `write_internal()`, so the simulator/agent can evolve read-only
  measurements while retaining process id, backend name, base revision, numeric
  bounds, duplicate-path, TTL, and operation-count checks.

The current baseline policy is `SetpointTrackingProcessAgentPolicy`. It is not
intended as a high-fidelity plant simulator. It gives us a deterministic and
auditable process-agent baseline: move one or more measurements toward exposed
setpoints, optionally influenced by actuator variables and bounded by maximum
per-second deltas. A future LLM policy should implement the same
`propose(context) -> WorldPatch | None` contract, using the snapshot, exposed
variables, recent memory entries, and scenario metadata as its inputs.

The wrapper also keeps compact physical-process-agent memory entries such as
base ticks and accepted internal patches. This is the engineering hook for the
paper's memory/snapshot contribution: the process world is not a raw prompt log;
it is a revisioned state with auditable internal and external mutation channels.

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
they target writable backend variables, pass backend bounds, and satisfy
snapshot preconditions such as process id, backend name, and base revision.

For Modbus write requests, the default process-aware path is:

```text
request write -> scenario decode -> world_patch proposal -> patch validator
              -> backend override -> generated write ACK
```

If the decode or patch validation fails, the generated ACK is withheld and the
protocol adapter can use its deterministic fallback.

## Configuration material needed over time

No extra material is required for the current generic MVP: the platform can run
with synthetic formula scenarios, TE trace slices, and deterministic
setpoint-tracking process-agent rules.

For a stronger demo or paper experiment, the following materials are useful:

- PLC point table: protocol, address/table/object id, tag, engineering unit,
  scale, read/write access, and canonical process variable id;
- process semantics: which tags are measurements, setpoints, manipulated
  variables, alarms, modes, counters, or interlocks;
- normal operating envelope: min/max bounds, typical ranges, safe ramp rates,
  alarm thresholds, and physically impossible combinations;
- process topology: which measurements depend on which actuators, feeds,
  controllers, tanks, reactors, pumps, valves, or breakers;
- protocol behavior target: supported function codes/services, authentication
  or session state if any, exception behavior, timing profile, and device
  identification strings;
- scenario narrative: plant type, PLC area, vendor/device persona, operator/HMI
  labels, and likely attacker-observable asset inventory;
- optional traces or simulator hooks: historian CSV, TE/SWaT/HAI-like logs,
  FMU/Modelica/MATLAB/Python simulator wrapper, or hand-written formula rules.

These materials should be added as scenario/configuration files rather than
hard-coded protocol logic, so the same honeypot can later impersonate a
chemical, water-treatment, power-grid, or building-automation slice.

## Runtime write semantics for traces

Trace files are immutable. If a protocol write targets a writable manipulated
variable or setpoint, `TraceProcessBackend.write()` stores a bounded runtime
override. The snapshot returns the override while preserving the original trace.
This gives the honeypot a credible state mutation path without corrupting source
data. A formula backend can replace the replay override with lightweight
attacker-observable dynamics, and a future live simulator backend can replace
both with true plant-dynamics feedback.
