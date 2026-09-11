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
baseline. Public datasets can later be adapted into the same benchmark format.

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
- generated-reply blocking when the protocol state machine denies the request.

The same schema can accept future benchmark adapters:

```text
public dataset trace/pcap
-> protocol decoder
-> ICSEvent sequence
-> expected ProtocolStateMachine status
-> expected PhysicalProcessContext state/reply
-> BenchmarkReport
```

Useful next extensions:

1. Import CIC Modbus 2023 PCAP/log samples into `BenchmarkCase` objects.
2. Map TE/SWaT/HAI process traces into `PhysicalProcessContext` and define
   cross-variable physical invariants.
3. Add S7/ISO-on-TCP or IEC-104 cases once those protocol profiles are
   implemented.
4. Add attacker-agent-driven evaluation inspired by Honeyval, but with ICS
   protocol goals instead of HTTP exploit goals.
