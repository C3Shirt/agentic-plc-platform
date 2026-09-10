# Architecture and implementation order

## Component boundaries

1. **Conpot adapter** receives parsed Modbus/S7/SNMP/HTTP operations and emits an
   `ICSEvent`. Its default response path is deterministic; generated responses
   must pass protocol validation before an adapter sends them.
2. **World model** owns the global PLC and physical-process state. All exposed
   services read from the same revisioned snapshot.
3. **Policy engine** validates protocol writes, agent-proposed deception plans,
   generated protocol replies, and world patches.
4. **Agent controller** classifies actor trajectories and proposes typed
   `AgentProposal` envelopes. A failed or missing agent falls back to deterministic
   protocol behavior.
5. **SSH maintenance gateway** has per-session shell state but reads the same global
   plant state as Conpot.
6. **Telemetry** stores immutable events with connection, actor, protocol, address,
   previous value, new value, result, and world revision.

## First scenario

The first scenario models a small tank, inlet valve, outlet pump, level sensor,
pressure sensor, setpoint, automatic/manual mode, and high-level alarm. The canonical
PDU addresses are defined in `scenarios/tank_pump/scenario.json`.

## Delivery order

### P0 - Baselines

- Run the original Conpot Modbus and HTTP services.
- Save protocol captures and expected responses.
- Record the MANTIS SSH session/event behavior that is worth preserving.

### P1 - Deterministic PLC world

- Complete register-to-state encoding and decoding.
- Add a periodic process tick.
- Add state-transition and invariant tests.
- Connect Modbus reads and writes to the world model.

Gate: a coil or setpoint write causes repeatable sensor and alarm changes.

Current status: the tank-pump register map and a Conpot DataBus adapter exist.
The adapter exposes dynamic register blocks instead of plain lists, because
Conpot's Modbus mediator mutates DataBus block objects directly during writes.

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
- Add an SSH maintenance gateway using deterministic commands first.
- Align asset identity, timestamps, alarms, project files, and register values.

Gate: Modbus, HTTP, and SSH expose the same world revision.

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

### P5 - Evaluation

Compare original Conpot, deterministic world model, and agentic world model using
protocol validity, cross-surface consistency, interaction depth, dwell time,
meaningful state changes, lure progression, honeypot suspicion, and p95 latency.
