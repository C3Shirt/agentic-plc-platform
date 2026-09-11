# Architecture and implementation order

## Component boundaries

1. **Conpot adapter** receives parsed Modbus/S7/SNMP/HTTP operations and emits an
   `ICSEvent`. Its default response path is deterministic; generated responses
   must pass protocol validation before an adapter sends them.
2. **World model / Process backend** owns the global PLC and physical-process
   state. All exposed services read from the same revisioned snapshot.
   Backend-specific physics are hidden behind the generic `ProcessBackend`
   contract.
3. **Policy engine** validates protocol writes, agent-proposed deception plans,
   generated protocol replies, and world patches.
4. **Process-aware agent controller** classifies actor trajectories and proposes
   typed `AgentProposal` envelopes from normalized events plus the active
   physical-process context. A failed or missing agent falls back to
   deterministic protocol behavior.
5. **Protocol interaction state** keeps per-actor, per-protocol multi-turn state
   such as address probing, register mapping, write attempts, and write-effect
   verification. This borrows the stateful interaction idea from MANTIS without
   adding an SSH service.
6. **Protocol state machines** keep per-session protocol legality separate from
   attacker intent and plant state. They answer whether the next protocol
   transition is allowed, anomalous-but-servable, or denied before generated
   protocol bytes can be sent.
7. **Telemetry** stores immutable events with connection, actor, protocol, address,
   previous value, new value, result, and world revision.

## Protocol interaction state

The runtime keeps actor/session dynamics separate from plant state. The
`ProtocolIntentTracker` observes normalized `ICSEvent` records and enriches each
stored event with an `interaction_phase`, such as:

- `process_monitoring`
- `register_mapping`
- `address_probing`
- `write_attempt`
- `effect_verification`

This is the MANTIS-inspired part of the design: the agent reasons over a
multi-turn interaction trajectory. The implementation remains protocol-facing
and does not add an SSH service. A `ProcessAwareResponsePolicy` can wrap any
base planner and add bounded deception plans while protocol replies and world
patches still pass through their validators.

The `ProtocolStateMachineRegistry` is a separate layer. It enriches each event
with `protocol_fsm_allowed`, `protocol_fsm_status`, and a reason code before the
intent tracker runs. This distinction matters because a protocol transition can
be illegal even when the attacker's long-term intent is clear. For example,
future OPC UA, S7, DNP3, or IEC-104 profiles may require a handshake, activated
session, sequence number, or select-before-operate transition before reads,
writes, or commands are servable.

## First scenario

The first scenario models a small tank, inlet valve, outlet pump, level sensor,
pressure sensor, setpoint, automatic/manual mode, and high-level alarm. The
canonical PDU addresses are defined in `scenarios/tank_pump/scenario.json`.

The second scenario uses the Tennessee Eastman Challenge Process as an external
trace backend. It exposes only a reactor/separator PLC slice through
`scenarios/tennessee_eastman/scenario.json`; TE-specific columns stay inside the
process backend and scenario mapping.

## Delivery order

### P0 - Baselines

- Run the original Conpot Modbus and HTTP services.
- Save protocol captures and expected responses.
- Record the MANTIS stateful-interaction behavior that is worth preserving at
  the design level, not its SSH-specific surface.

### P1 - Deterministic PLC world

- Complete register-to-state encoding and decoding.
- Add a periodic process tick.
- Add state-transition and invariant tests.
- Connect Modbus reads and writes to the world model.

Gate: a coil or setpoint write causes repeatable sensor and alarm changes.

Current status: the tank-pump register map and a Conpot DataBus adapter exist.
The adapter exposes dynamic register blocks instead of plain lists, because
Conpot's Modbus mediator mutates DataBus block objects directly during writes.
An optional `AgenticModbusDatabank` hook can wrap Conpot's initialized Modbus
databank and replace a deterministic response with a validated generated Modbus
TCP frame.

Current status: a generic process-backend layer exists for future physical
processes. `TraceProcessBackend` replays sampled traces and
`TennesseeEastmanTraceBackend` adapts TE IDV `t/y/u/r.dat` files into canonical
measurement, manipulated-variable, and setpoint variables. `ProcessRegisterMap`
turns scenario-mapped process variables into Modbus-style blocks, and
`integrations/conpot/tennessee_eastman` loads that mapping through Conpot
DataBus function values.

### P2 - Unified telemetry

- Create a unique session for every connection.
- Correlate sessions into a separate actor identifier.
- Record normalized reads, writes, invalid addresses, retries, and outcomes.

Gate: one interaction can be reconstructed from immutable events.

Current status: normalized events can be persisted to JSONL and replayed into a
summary. Connection-level session generation and actor correlation remain to be
added at the protocol adapter layer.

### P3 - Cross-surface consistency

- Add the HTTP HMI.
- Align asset identity, timestamps, alarms, historian-like values, and register
  values across protocol-facing surfaces.

Gate: Modbus, HTTP, and future ICS protocol adapters expose the same world
revision.

Current status: a minimal HTTP HMI exists and is backed by the same
`TankPumpWorld` as the Conpot Modbus template. The cross-surface smoke writes a
setpoint through Modbus, reads it through HMI, then writes through HMI and reads
the updated holding register through Modbus.

### P4 - Agent controller with generated replies and bounded state mutation

- Add trajectory classification.
- Add asynchronous `AgentProposal` generation.
- Validate every plan with `DeceptionPlanValidator`.
- Validate generated Modbus TCP replies with `ProtocolReplyValidator`.
- Apply LLM-generated world-model patches through `WorldPatchApplier`.
- Add caching, timeouts, and a no-agent fallback.

Gate: disabling or timing out the agent does not change protocol conformance.

Current status: a synchronous `AgentController` can run a no-LLM planner or an
OpenAI-compatible planner configured from `.env`. The controller validates
deception plans, generated Modbus TCP reply frames, and bounded world patches. If
constructed with a `TankPumpWorld`, it applies accepted world patches and records
the before/after snapshots in the decision object.

The controller can also receive a `PhysicalProcessContext`. In that mode the
agent sees the active backend type, snapshot, PLC area, exposed protocol points,
and writable process-variable ids. Rule-based fallback replies can therefore be
generated from TE or any future scenario-mapped process backend, not from a
tank-specific constant response.

The first Conpot integration point is `install_agentic_modbus_hook`. With the
local Conpot request-hook patch applied, it registers a hook through
`ModbusServer.set_request_hook()` and receives real session/source/destination
context from the connection handler. Without that Conpot patch, it can still
fall back to wrapping the decorated `ModbusServer.wrapped._databank` object, but
that fallback lacks true connection-level context.

Current status: `ProtocolIntentTracker` now enriches runtime events with
protocol interaction phases, and `ProcessAwareResponsePolicy` can add bounded
deception plans for address probing, register mapping, write attempts, and
write-effect verification without changing the deterministic protocol fallback.

Current status: generated Modbus TCP responses now have builders for read-bit
responses, read-register responses, single-write echoes, multiple-write echoes,
and exception responses. `ProtocolReplyValidator` checks that generated response
frames match the latest request's transaction id, unit id, function code, read
byte count, and write echo fields before an adapter can send them.
LLM planners may return either exact `payload_hex` or structured Modbus reply
fields; structured replies are converted to bytes before the same validator is
applied.

Current status: protocol writes against a scenario-mapped physical process can
now be translated into a process-variable `world_patch` before a generated
Modbus write acknowledgement is released. A successful write ACK is withheld
unless the paired world patch is accepted, preserving read-back consistency and
keeping deterministic Conpot fallback available for unmapped or rejected writes.

Current status: a generic `ProtocolStateMachine` interface and registry now run
inside `AgentRuntime.observe()`. The first concrete profile is Modbus TCP, which
models Modbus as a session-light protocol while checking transaction id, unit
id, function-code/operation consistency, request shape, and suspicious
transaction-id reuse. `ProtocolReplyValidator` rejects generated replies for
events whose protocol transition was denied by the state machine.

### P5 - Evaluation

Compare original Conpot, deterministic world model, and agentic world model using
protocol validity, cross-surface consistency, interaction depth, dwell time,
meaningful state changes, lure progression, honeypot suspicion, and p95 latency.

Current status: a first local consistency benchmark exists under
`agentic_plc.evaluation`. It generates reproducible Modbus TCP interaction
cases and scores protocol FSM status, generated reply behavior, world-patch
application, and process read-back consistency. The benchmark is synthetic by
design; public datasets such as SWaT, WaDi, HAI, BATADAL, CIC Modbus 2023, and
ICS-Flow can later be imported into the same case/report schema.
