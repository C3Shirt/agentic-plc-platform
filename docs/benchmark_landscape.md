# Benchmark landscape for ICS honeypot consistency evaluation

Research date: 2026-09-11.

## Finding

I did not find a public benchmark that directly evaluates the combination we
need:

```text
industrial-protocol state-machine consistency
+ physical-process state consistency
+ generated honeypot response consistency
+ LLM/agent fallback safety
```

There are strong adjacent resources, but they mostly evaluate IDS/anomaly
detection, honeypot camouflage, or non-ICS LLM honeypots. Therefore the project
now includes a local synthetic benchmark generator as the first reproducible
baseline, plus a CIC Modbus/tshark CSV importer for adapting public Modbus
traffic into the same format without adding a heavy PCAP parsing dependency.

## Adjacent public resources

| Resource | What it gives us | Gap for our benchmark |
| --- | --- | --- |
| SWaT | Water-treatment process data, network traffic, normal/attack labels, sensor/actuator values. Official iTrust page reports 11 days of operation with 7 normal days and 4 attack days, 51 sensors/actuators, and 41 attacks for SWaT.A1. | Excellent process/security dataset, but not a honeypot response benchmark and not designed to score generated protocol replies. |
| WaDi | Water-distribution process data. Official iTrust page reports 16 days of operation, 123 sensors/actuators, and 15 attacks. | Strong physical-process dataset, but not a protocol dialogue benchmark for interactive honeypots. |
| BATADAL | Water-distribution attack-detection competition with long normal and attack-containing simulated datasets. | Useful for process anomaly baselines; lacks interactive protocol state and honeypot response scoring. |
| HAI | HIL-based augmented ICS dataset with coupled turbine/boiler/FESTO water-treatment processes; public GitHub versions and HAICon competitions. | Strong time-series benchmark, but mostly IDS/anomaly-detection oriented. |
| CIC Modbus 2023 | PCAPs and attack logs from a simulated substation Modbus network; includes attacks such as reconnaissance, query flooding, false data injection, stacking Modbus frames, brute-force write, and replay. | Very useful source of Modbus attack traffic, but it does not score whether a honeypot maintains physical-process read/write consistency while replying. |
| ICS-Flow | Network packets, flow records, and process-variable logs from simulated ICS components, plus ICSFlowGenerator. | Good multimodal IDS dataset; not a generative honeypot dialogue benchmark. |
| LLMPot | Closest LLM+ICS honeypot paper found; focuses on LLM-based industrial protocol and physical process emulation. | The paper motivates our direction, but I did not find a reusable public benchmark format for protocol-FSM/physical consistency scoring. |
| Honeyval | LLM-powered honeypot evaluation framework with attacker agents and verifiable exploit goals. | Useful evaluation philosophy, but it targets HTTP honeypots, not ICS protocols or physical-process backends. |
| HoneyPLC | High-interaction PLC honeypot; evaluated with reconnaissance tools, Shodan Honeyscore, Siemens tools, PLCScan, and Internet deployment. | Useful realism/camouflage baseline, but not a protocol+physics consistency benchmark for generated replies. |

## Sources

- SWaT official dataset page:
  https://www.sutd.edu.sg/itrust/itrust-labs/datasets/dataset-characteristics/swat/
- WaDi official dataset page:
  https://www.sutd.edu.sg/itrust/itrust-labs/datasets/dataset-characteristics/wadi/
- BATADAL official iTrust page:
  https://www.sutd.edu.sg/itrust/itrust-labs/datasets/dataset-characteristics/batadal/
- HAI GitHub dataset:
  https://github.com/icsdataset/hai
- HAI 1.0 CSET/USENIX paper page:
  https://www.usenix.org/conference/cset20/presentation/shin
- CIC Modbus 2023:
  https://www.unb.ca/cic/datasets/modbus-2023.html
- ICS-Flow paper:
  https://arxiv.org/abs/2305.09678
- LLMPot paper:
  https://arxiv.org/abs/2405.05999
- Honeyval paper:
  https://arxiv.org/abs/2605.29963
- HoneyPLC source:
  https://github.com/sefcom/honeyplc
- ICS dataset meta-review:
  https://arxiv.org/abs/2608.24757

## Local benchmark direction

The current local benchmark is intentionally synthetic and reproducible. It
tests the runtime we already own instead of depending on private testbeds or
licensed standards. The default cases cover:

- valid Modbus read scans;
- anomalous transaction-id reuse;
- write-then-read-back physical consistency;
- protocol-valid but physically invalid writes;
- snapshot-revision governance across a multi-step actor session;
- actor-memory and context-compression retention for touched PLC points;
- generated-reply blocking when the protocol state machine denies the request.
- declarative physical invariants over process snapshots and generated replies.
- optional formula-process dynamics where a write is followed by a backend tick
  and a readback from the evolved snapshot.

The same schema can accept future benchmark adapters:

```text
public dataset trace/pcap
-> protocol decoder
-> ICSEvent sequence
-> expected ProtocolStateMachine status
-> expected PhysicalProcessContext state/reply
-> benchmark JSON artifact
-> BenchmarkReport
```

`BenchmarkReport.check_group_counts` now aggregates passing/failing checks into
paper-facing groups:

- `protocol_state`: protocol-FSM expectations;
- `generated_action`: generated reply/world-patch gating;
- `physical_process`: expected values and physical invariants;
- `snapshot_consistency`: snapshot revision deltas and patch base-revision
  preconditions;
- `memory_consistency`: compressed context selection, actor event counts, and
  touched-variable retention.

Useful next extensions:

1. Use `tools/import_cic_modbus_benchmark.py` to convert CIC Modbus 2023
   packet CSV and attack logs into `BenchmarkCase` objects.
2. Map TE/SWaT/HAI process traces into `PhysicalProcessContext` and define
   cross-variable physical invariants.
3. Add S7/ISO-on-TCP or IEC-104 cases once those protocol profiles are
   implemented.
4. Add attacker-agent-driven evaluation inspired by Honeyval, but with ICS
   protocol goals instead of HTTP exploit goals.

## CIC Modbus CSV import path

The CIC Modbus 2023 data is useful because it contains Modbus PCAPs and attack
logs, but the PCAP files can be large and the project should not depend on one
packet parser. The implemented importer therefore accepts a normalized CSV,
typically exported from tshark:

```powershell
$env:PYTHONPATH = "src"
python tools\import_cic_modbus_benchmark.py --print-tshark sample.pcap records\cic_modbus_packets.csv
```

The generated command exports fields such as transaction id, unit id, function
code, address, count/value fields, IP/port context, and raw TCP payload. The
actual import then preserves CIC attack-log labels when available:

```powershell
python tools\import_cic_modbus_benchmark.py `
  --packets records\cic_modbus_packets.csv `
  --attack-log records\cic_modbus_attack_log.csv `
  --run `
  --output records\cic_modbus_benchmark.json
```

Imported or hand-authored JSON artifacts can be replayed independently:

```powershell
python tools\run_consistency_benchmark.py `
  --input records\cic_modbus_benchmark.json `
  --output records\cic_modbus_report.json
```

What this gives us now:

- real public Modbus traffic converted into `BenchmarkCase` / `BenchmarkStep`;
- protocol-FSM expectations for allowed, anomalous, and denied transitions;
- attack labels stored in `ICSEvent.metadata` for later per-attack analysis;
- a reproducible JSON artifact that can be replayed by our benchmark runner.
- compatibility with the same physical invariant DSL used by synthetic cases.

What it intentionally does not claim:

- no physical-process ground truth is inferred from CIC Modbus packets alone;
- no LLM-generated response quality score is assigned without a mapped process
  backend and expected read/write effects;
- no raw PCAP parsing dependency is required inside the platform.

## Physical invariant DSL

The benchmark schema now supports optional per-step `process_invariants`. These
rules are evaluated against the process snapshot before and after a benchmark
step, plus the generated protocol reply when one exists. This moves the
benchmark beyond fixed expected values while keeping every check explicit and
auditable.

Supported invariant kinds:

- `variable_equals`: a process variable must equal a value within tolerance;
- `variable_between`: a process variable must stay inside explicit or declared
  backend bounds;
- `relation`: one process variable must satisfy `lt`, `le`, `eq`, `ge`, or `gt`
  relative to another variable or scalar value;
- `trend`: a variable must increase, decrease, or stay stable between the
  before/after snapshots of one step;
- `moves_toward`: a measurement must move closer to a target variable;
- `reply_matches_process_snapshot`: generated Modbus read values must equal the
  current process snapshot encoded through the active register map.

Example:

```json
{
  "invariant_id": "read_reply_matches_process_snapshot",
  "kind": "reply_matches_process_snapshot",
  "description": "Generated Modbus read values must be encoded from the current physical-process snapshot."
}
```

## Snapshot and memory checks

The benchmark schema also supports per-step snapshot/memory expectations:

- `expected_snapshot_revision_delta`: expected change in the authoritative
  process snapshot revision after the step;
- `expected_patch_base_revision_matches_before`: whether accepted
  `WorldPatch.metadata.base_revision` must match the pre-step snapshot;
- `expected_context_selected_variables`: variables that must survive
  process-aware context compression for the current request;
- `expected_actor_memory_event_count`: expected number of same-actor events in
  compact memory;
- `expected_actor_memory_touched_variables`: process variables that the actor
  memory must retain as previously touched.

These checks are designed for the paper's core question: whether an agentic ICS
honeypot can stay consistent over long protocol sessions without sending the
LLM the full raw interaction history.

## Formula-process dynamics

`FormulaProcessBackend` adds a middle ground between static trace replay and
high-fidelity numerical simulation. Benchmark steps can now specify
`tick_seconds_after` so a write can be followed by a process tick before
physical invariants are evaluated. This supports cases such as:

```text
write level setpoint -> apply validated WorldPatch -> tick formula backend
                     -> check level moves toward setpoint
                     -> read input register from evolved snapshot
```

The helper `build_formula_process_consistency_cases()` creates this dynamic
case. It uses the same `BenchmarkCase`/`BenchmarkReport` schema, which means
formula backends, TE traces, and future physical-process agents can be compared
with the same consistency metrics.

CLI example:

```powershell
$env:PYTHONPATH = "src"
python tools\generate_consistency_benchmark.py --suite formula --cases-only --output records\formula_consistency_cases.json
python tools\run_consistency_benchmark.py --process-backend formula --input records\formula_consistency_cases.json --output records\formula_consistency_report.json
```
