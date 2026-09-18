# Physical Process Acquisition Pipeline

This pipeline turns a pre-defined industrial scene into a reusable
physical-process model for the honeypot.

The intended workflow is:

1. Describe a scene, such as a height-based cargo sorting cell.
2. Let a physical-process-aware planning agent produce components, PLC points,
   Modbus mappings, and a PLC program skeleton.
3. Configure the PLC simulation manually when needed, for example in CODESYS.
4. Drive the PLC through a local Modbus TCP client and capture pcap traffic.
5. Normalize request/response pairs into `ModbusTraceEvent` records.
6. Learn a `LearnedProcessModel` containing response examples, variable
   timelines, and write-effect summaries.
7. Use the learned model as a trace backend, a RAG knowledge source, or
   fine-tuning data for generated protocol replies.

## Implemented MVP

The first concrete scene is `cargo_sorting_height_v1`: a conveyor and turntable
cell that routes low boxes left and high boxes right.

The code is split into four parts:

- `agentic_plc.processes.cargo_sorting.CargoSortingProcessBackend` provides a
  deterministic headless process backend for local acquisition tests.
- `agentic_plc.acquisition.planning` defines planning artifacts:
  `ScenarioAcquisitionPlan`, `SceneComponentPlan`, `PLCPointPlan`, and
  `PLCProgramPlan`.
- `agentic_plc.acquisition.modbus_trace` parses Modbus TCP request/response
  pairs into acquisition trace events.
- `agentic_plc.acquisition.learning` learns a trace-backed process model from
  normalized Modbus interactions.

Run the smoke generator:

```powershell
$env:PYTHONPATH = "src"
python tools\smoke_cargo_sorting_acquisition.py
```

It writes:

```text
records/cargo_sorting_acquisition/acquisition_plan.json
records/cargo_sorting_acquisition/scenario.json
records/cargo_sorting_acquisition/cargo_sorting_height_controller.st
records/cargo_sorting_acquisition/interaction_trace.jsonl
records/cargo_sorting_acquisition/learned_process_model.json
```

## Mapping to the user workflow

### Step 1: scene is given

For the cargo sorter, the scene requirement is:

> Boxes are moved by conveyors to a classifier. Low boxes are routed left and
> high boxes are routed right. The PLC-facing interface uses Modbus TCP.

### Step 2: planning

`build_cargo_sorting_acquisition_plan()` produces:

- physical components: source feeder, entry classifier, turntable diverter,
  left lane, right lane;
- Modbus coils for actuator outputs;
- Modbus discrete inputs for sensors;
- Modbus input registers for diagnostic process state;
- a Structured Text skeleton that can be adapted in CODESYS.

### Step 3: execution and pcap capture

For the current smoke path, `respond_with_register_map()` is used as an
in-process Modbus responder so tests are deterministic and do not require a
live PLC.

For a real CODESYS acquisition run, replace the in-process responder with:

```text
local Modbus TCP client -> CODESYS Modbus server -> PLC program/process simulator
```

Capture traffic with Wireshark or tshark, then export request/response pairs to
the same fields consumed by `parse_modbus_interaction()`.

### Step 4: learning

`learn_process_model()` maps trace responses back to `ScenarioMapping` points.
The learned model stores:

- observed operation counts;
- response examples keyed by function/table/address/count;
- variable timelines decoded into engineering values;
- write-effect summaries showing which variables changed after writes.

### Step 5: honeypot reuse

The model can be used in three ways:

- `LearnedProcessModel.to_trace_backend()` creates a `TraceProcessBackend` for
  deterministic replay.
- `response_examples` can seed a retrieval store for LLM-assisted Modbus reply
  generation.
- `variable_samples` and `write_effects` can become fine-tuning records or
  benchmark oracles for physical/protocol consistency.

The online honeypot should still validate generated replies and world patches
before sending or applying them.
