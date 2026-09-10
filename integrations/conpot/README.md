# Conpot integration

`tank_pump/` contains a minimal Conpot template that references this package from
DataBus `function` values. Conpot must run with this repository's `src`
directory on `PYTHONPATH`.

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

Smoke check after installing Conpot dependencies:

```powershell
$env:PYTHONPATH = "src;..\conpot-main\conpot-main"
python tools\smoke_conpot_template.py
python tools\smoke_modbus_tcp.py
```
