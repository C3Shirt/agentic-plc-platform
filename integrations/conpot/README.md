# Conpot integration

`tank_pump/` and `tennessee_eastman/` contain minimal Conpot templates that
reference this package from DataBus `function` values. Conpot must run with this
repository's `src` directory on `PYTHONPATH`.

The template exposes slave id `1` with these zero-based Modbus blocks:

```text
coils:             0 pump, 1 inlet valve
discrete inputs:   0 high-level alarm
input registers:   0 level_percent_x10, 1 pressure_bar_x100
holding registers: 0 setpoint_percent_x10, 1 operating_mode_code
```

Conpot stores dynamic `ConpotTankPumpBlock` objects in the DataBus. This is
intentional: Conpot's Modbus mediator writes to block objects directly, so plain
lists cannot enforce world-model validation or telemetry.

## Tennessee Eastman template

`tennessee_eastman/` exposes a reactor/separator PLC slice over the generic
`ProcessBackend` stack:

```text
input registers:   0 reactor pressure, 1 reactor level, 2 reactor temp,
                   3 separator temp, 4 separator level, 5 separator pressure,
                   6 stripper level, 7 stripper temp, 8 production rate G
holding registers: 0 reactor pressure SP, 1 reactor level SP,
                   2 separator level SP, 3 stripper level SP,
                   4 reactor temp SP, 5 reactor coolant valve,
                   6 condenser coolant valve, 7 A+C feed valve
```

The template uses `ConpotTennesseeEastmanBlock`, which loads
`scenarios/tennessee_eastman/scenario.json` and
`external/tennessee_eastman/extracted/idv1`. Run
`python tools\download_tennessee_eastman.py` before starting this template.

## Agentic request hook

Generated Modbus replies need connection-level session context. The local Conpot
checkout has therefore been extended with a small generic request hook in:

```text
../conpot-main/conpot-main/conpot/protocols/modbus/modbus_server.py
```

The hook does not import this project. It only exposes `set_request_hook()` and
passes `query`, raw `request`, `mode`, and a context dictionary containing the
real Conpot session id, source endpoint, and destination endpoint. This project
then installs `AgenticModbusDatabank` through `install_agentic_modbus_hook()`.

For a clean Conpot checkout, apply:

```powershell
cd ..\conpot-main\conpot-main
git apply ..\..\agentic-plc-platform\integrations\conpot\patches\conpot_modbus_request_hook.patch
```

Smoke check after installing Conpot dependencies:

```powershell
$env:PYTHONPATH = "src;..\conpot-main\conpot-main"
python tools\smoke_conpot_template.py
python tools\smoke_modbus_tcp.py
python tools\smoke_agentic_modbus_hook.py
python tools\smoke_conpot_te_template.py
python tools\smoke_te_modbus_tcp.py
```
