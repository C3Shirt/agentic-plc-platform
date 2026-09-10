# Agentic PLC Platform

This is the independent implementation layer for an agentic PLC honeypot. It is
designed to sit beside, rather than inside, the two reference projects:

- `../conpot-main/conpot-main`: ICS protocol data plane.
- `../MANTIS_Terminal_Simulation-main/MANTIS_Terminal_Simulation-main`: reference
  implementation for stateful SSH interaction.

## Initial scope

The first milestone is a deterministic tank-pump process shared by Modbus and an
HTTP HMI. SSH and LLM-driven deception planning are added only after the world
model, event contract, and protocol consistency tests pass.

```text
request -> protocol adapter -> normalized event -> policy -> world transition
                                                     |
                                      deterministic protocol response

normalized events -> asynchronous agent -> typed deception plan -> validator
```

The online protocol path never waits for an LLM.

## Current implementation

- `TankPumpWorld` is the authoritative deterministic process state.
- `TankPumpRegisterMap` exposes coils, discrete inputs, input registers, and
  holding registers for the tank-pump scenario.
- `ConpotDatabusAdapter` installs list-like dynamic blocks into a Conpot-style
  DataBus. Reads are generated from the current world state, and writes are
  converted back into validated world transitions.
- `InMemoryEventLog` records accepted and rejected register writes with the
  normalized `ICSEvent` contract.

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

See `docs/architecture.md` for component boundaries and the implementation order.
