# Agentic PLC Platform

This is the independent implementation layer for an agentic PLC honeypot. It is
designed to sit beside, rather than inside, the two reference projects:

- `../conpot-main/conpot-main`: ICS protocol data plane.
- `../MANTIS_Terminal_Simulation-main/MANTIS_Terminal_Simulation-main`: reference
  implementation for stateful SSH interaction.

## Initial scope

The first milestone is a tank-pump process shared by Modbus and an HTTP HMI.
The second physical-process target is a Tennessee Eastman trace backend exposed
as a reactor/separator PLC slice. The deterministic path remains the fallback
baseline. The agentic path can add LLM-generated protocol replies and bounded
world-state mutations after validation.

```text
request -> protocol adapter -> normalized event -> policy -> world transition
                                                     |
                           deterministic or validated generated response

normalized events -> asynchronous agent -> AgentProposal envelope
                                      |-> deception_plan -> validator
                                      |-> protocol_reply -> frame validator
                                      |-> world_patch    -> range/schema validator -> world
```

The online protocol path can run without an LLM. If an adapter chooses to use
generated replies online, it should enforce short timeouts and fall back to the
deterministic response path.

## Current implementation

- `TankPumpWorld` is the authoritative deterministic process state.
- `TankPumpRegisterMap` exposes coils, discrete inputs, input registers, and
  holding registers for the tank-pump scenario.
- `ConpotDatabusAdapter` installs list-like dynamic blocks into a Conpot-style
  DataBus. Reads are generated from the current world state, and writes are
  converted back into validated world transitions.
- `create_tank_pump_hmi_server` exposes a minimal HTTP HMI backed by the same
  `TankPumpWorld`.
- `InMemoryEventLog` records accepted and rejected register writes with the
  normalized `ICSEvent` contract.
- `JsonlEventStore` persists normalized events for replay and offline analysis.
- `AgentController` runs a bounded action pass over events. It can use a no-LLM
  rule planner or an OpenAI-compatible planner loaded from `.env`. When supplied
  with `PhysicalProcessContext`, the same generic agent sees the active process
  backend, current snapshot, exposed PLC points, and writable process variables.
- `AgentProposal` supports three validated outputs:
  - `deception_plan`: adjust lures, maintenance notes, and exposed artifacts.
  - `protocol_reply`: generated Modbus TCP response bytes in hex.
  - `world_patch`: bounded mutations to tank level, pressure, mode, alarms, and
    actuator state.
- `install_agentic_modbus_hook` can wrap an initialized Conpot `ModbusServer`
  databank. The hook observes raw Modbus TCP requests, runs the agent runtime,
  sends a validated generated frame when available, and otherwise falls back to
  Conpot's deterministic response path.
  Apply `integrations/conpot/patches/conpot_modbus_request_hook.patch` to Conpot
  to pass real session/source/destination context into this hook.
- `ProcessBackend` is the simulator-neutral backend contract for future physical
  processes. `TraceProcessBackend` replays sampled plant traces, and
  `TennesseeEastmanTraceBackend` adapts TE `t/y/u/r.dat` files without coupling
  protocol adapters to TE-specific columns.
- `ProcessRegisterMap` exposes any scenario-mapped backend as Modbus-style
  register reads/writes.
- `scenarios/tennessee_eastman/scenario.json` maps a bounded TE
  reactor/separator control cell to Modbus-facing points.
- `ProcessContextCompressor` applies Process-Aware Context Compression (PACC)
  before LLM calls. It keeps protocol-critical fields exact, ranks exposed
  process points by request relevance and actor interest, and leaves
  deterministic fallback plus proposal validators unchanged.

Default Conpot DataBus keys:

```text
agenticPlcSlave1Coils
agenticPlcSlave1DiscreteInputs
agenticPlcSlave1InputRegisters
agenticPlcSlave1HoldingRegisters
```

A minimal Conpot template lives under `integrations/conpot/tank_pump`. It is a
starting point for the real deployment adapter, not a copy of the upstream
default template.

## Run the tests

```powershell
python -m pip install -e ".[dev]"
python -m pytest
```

Without installing development dependencies:

```powershell
$env:PYTHONPATH = "src"
python -m unittest discover -s tests -v
```

When Conpot's runtime dependencies are installed, this smoke script checks that
Conpot can load the local template and that a DataBus write updates the world:

```powershell
$env:PYTHONPATH = "src;..\conpot-main\conpot-main"
python tools\smoke_conpot_template.py
```

This smoke script starts a local Conpot Modbus TCP service on an ephemeral port
and drives it with a Modbus client:

```powershell
$env:PYTHONPATH = "src;..\conpot-main\conpot-main"
python tools\smoke_modbus_tcp.py
```

The Tennessee Eastman Conpot template uses the same generic process mapping
stack and can be checked with:

```powershell
$env:PYTHONPATH = "src;..\conpot-main\conpot-main"
python tools\smoke_conpot_te_template.py
python tools\smoke_te_modbus_tcp.py
python tools\smoke_process_aware_modbus_hook.py
```

This smoke script starts Conpot, installs the agentic Modbus databank hook, and
demonstrates a generated Modbus TCP response:

```powershell
$env:PYTHONPATH = "src;..\conpot-main\conpot-main"
python tools\smoke_agentic_modbus_hook.py
```

This smoke script runs Modbus and HTTP HMI against the same world model and
checks cross-surface state consistency:

```powershell
$env:PYTHONPATH = "src;..\conpot-main\conpot-main"
python tools\smoke_cross_surface.py
```

Download and smoke-test the Tennessee Eastman trace backend:

```powershell
$env:PYTHONPATH = "src"
python tools\download_tennessee_eastman.py --idv idv1
python tools\smoke_te_backend.py
python tools\smoke_process_aware_agent.py
python tools\smoke_context_compressor.py
```

The agent smoke uses the no-LLM planner by default and writes sample events to
`records/agent_smoke_events.jsonl`. It also demonstrates accepted world patches
and a local generated Modbus TCP response candidate:

```powershell
python tools\smoke_agent_controller.py
python tools\replay_events.py records\agent_smoke_events.jsonl
```

To call the configured OpenAI-compatible endpoint from `.env`, pass
`--live-llm`. The expected keys are `OpenAIBaseURL` and `APIKey`; optional model
keys are `OpenAIModel`, `OPENAI_MODEL`, or `LLM_MODEL`. `OpenAIBaseURL` can point
to either a chat-completions endpoint root or a `/v1` root; the client tries both
`/chat/completions` and `/v1/chat/completions` when needed. If the endpoint does
not serve the default `gpt-4o-mini` model, set `OpenAIModel` explicitly.
For one-off diagnostics, use `--model <name>` with either smoke script instead
of editing `.env`.

See `docs/architecture.md` for component boundaries and the implementation order.
See `docs/process_backend_design.md` for the generic simulator-backend boundary.
See `docs/process_context_compression.md` for the paper-backed PACC design.
