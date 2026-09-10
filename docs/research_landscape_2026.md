# Research landscape and improvement roadmap

Date: 2026-09-10

This note summarizes the current public research landscape around ICS
honeypots, LLM-powered honeypots, and LLM agents, then maps it to concrete
improvements for this repository.

## Sources reviewed

- Conpot: https://raw.githubusercontent.com/mushorg/conpot/master/README.md
- HoneyPLC: https://raw.githubusercontent.com/sefcom/honeyplc/master/README.md
- MiniCPS: https://arxiv.org/abs/1507.04860 and
  https://raw.githubusercontent.com/scy-phy/minicps/master/README.md
- HoneyICS: https://dl.acm.org/doi/10.1145/3600160.3604984
- ICSLure: https://arxiv.org/abs/2509.04080
- Time-to-Lie: https://arxiv.org/abs/2410.17731
- LLMPot: https://arxiv.org/abs/2405.05999
- HoneyGPT: https://arxiv.org/abs/2406.01882
- LLMHoney: https://arxiv.org/abs/2509.01463
- HoneyLLM: https://link.springer.com/book/10.1007/978-981-97-8801-9
- VelLMes: https://arxiv.org/abs/2510.06975
- LLM-agent evaluation survey: https://arxiv.org/abs/2503.16416
- LLM-agent planning survey: https://arxiv.org/abs/2402.02716
- WebArena: https://arxiv.org/abs/2307.13854
- OSWorld: https://arxiv.org/abs/2404.07972
- SWE-agent / Agent-Computer Interfaces:
  https://arxiv.org/abs/2405.15793

## Landscape

Existing ICS honeypots split into three practical families.

Conpot is template-driven and useful as a protocol-facing ICS data plane, but
its low-interaction style and static data make it easier to fingerprint during
longer sessions. HoneyPLC moves closer to high interaction by simulating
multiple PLC profiles, S7comm behavior, hardware information, and ladder-logic
upload/download capture. HoneyICS and ICSLure push the state of the art toward
physics-aware honeynets: coherent plant behavior, richer monitoring, and
multiple OT components rather than one isolated PLC.

ICS simulation/testbed projects such as MiniCPS, ICSSIM, and OpenPLC are not
honeypots by themselves, but they show what a credible research environment
needs: reproducible topology, network emulation, industrial protocols,
programmable controller behavior, and a physical-process API.

Recent work also shows that ICS honeypots are fingerprintable. Time-to-Lie uses
ICMP TTL behavior to identify many exposed ICS honeypots with minimal traffic.
For this project, that means application realism is not enough; network stack,
service banners, timing, TTL, open-port set, and protocol behavior must be
coherent.

LLM honeypot work is moving quickly but is still mostly shell-centric. HoneyGPT
uses LLM prompting to improve terminal-honeypot flexibility, interaction depth,
and engagement. LLMHoney combines deterministic dictionary/VFS handling for
common commands with LLM output for novel commands. HoneyLLM and VelLMes extend
the same idea toward medium/high-interaction deception and multi-service
emulation. LLMPot is the most relevant to this project because it explicitly
targets industrial protocols and physical-process emulation using LLMs.

The agent literature adds three useful lessons. First, agent quality depends on
interfaces: SWE-agent shows that purpose-built tool interfaces can materially
change agent behavior. Second, realistic environments matter: WebArena and
OSWorld show that agents look stronger on toy tasks than in executable,
stateful, long-horizon environments. Third, evaluation must measure cost,
safety, robustness, and fine-grained failure modes, not only task success.

## Design position for this project

The strongest research position is not "LLM writes arbitrary bytes directly to
the socket." That is brittle, slow, hard to validate, and likely to hallucinate
protocol or process behavior.

The stronger position is:

Agentic PLC Honeypot = deterministic cyber-physical world + real protocol data
plane + cross-surface deception state + validated agent-generated actions.

In this design, Conpot handles the protocol surface, the world model enforces
process invariants, SSH/HMI/Modbus share the same revisioned state, and the LLM
agent only proposes typed `AgentProposal` envelopes. These envelopes may include
deception plans, generated Modbus TCP response frames, and bounded world-state
patches, but each path has a validator before it can affect the honeypot.

This gives the project a clean distinction from prior work:

- Compared with Conpot: higher interaction through physical process state,
  telemetry, and adaptive cross-surface narratives.
- Compared with HoneyPLC: cheaper and more configurable, while still borrowing
  the idea of PLC profiles and uploaded logic capture.
- Compared with HoneyICS/ICSLure: less hardware realism, but easier deployment
  and a clearer agentic deception layer.
- Compared with HoneyGPT/LLMHoney: not limited to shell output; the shell,
  Modbus, HMI, alarms, maintenance files, and plant behavior all agree.
- Compared with LLMPot: LLMs may generate protocol responses, but generated
  frames are constrained by protocol parsers, request correlation, world-model
  invariants, and deterministic fallback.

## Improvement roadmap

P2 should turn telemetry into a first-class subsystem. Add a durable JSONL or
SQLite event store, per-connection session IDs, actor correlation, normalized
read/write/invalid-address events, and replay tests. The agent should consume
this event stream, not protocol internals.

P3 should add cross-surface consistency. Build an HTTP HMI and SSH maintenance
gateway that expose the same world revision as Modbus. The SSH side can borrow
MANTIS-style command classification, deterministic handlers, and session
memory, but command output must come from the shared ICS world and curated
plant artifacts.

P4 should implement a bounded agent controller. The agent should classify actor
trajectory, select from approved fault scenarios, publish maintenance notes, and
decide which existing artifacts to expose. When it generates protocol bytes or
world-model mutations, those outputs must pass `ProtocolReplyValidator` and
`WorldPatchApplier`. The current `AgentProposal` envelope is the right boundary
to extend.

P5 should add scenario generation as an offline workflow. LLMs can draft
register maps, plant narratives, HMI labels, historian snippets, maintenance
logs, and likely attacker goals. A compiler/validator should turn those drafts
into deterministic scenarios with invariant tests.

P6 should add fingerprint-hardening and evaluation. Test TTL/personality
alignment, banner consistency, timing distributions, unsupported-function
responses, Modbus exception behavior, and cross-protocol contradictions.
Compare three variants: stock Conpot, deterministic world-model Conpot, and
agentic cross-surface honeypot.

## Evaluation metrics

- Protocol validity: legal responses, exception codes, malformed input handling.
- Process validity: no impossible plant states; alarms and interlocks obey rules.
- Cross-surface consistency: Modbus, HTTP, and SSH show the same world revision.
- Interaction depth: command count, session duration, state-changing actions.
- Deception quality: human or model-assisted suspicion score and contradiction
  count.
- Intelligence value: unique TTPs, uploaded code/files, tool fingerprints,
  repeated actor behavior.
- Operational cost: p50/p95 response latency, LLM calls per session, token cost,
  memory footprint.
- Safety: no direct LLM mutation, no outbound attack enablement, no real PLC or
  production-network connectivity.

## 2026 refresh: how the literature changes the next implementation steps

The recent LLM-honeypot literature points to a hybrid architecture rather than a
pure LLM emulator. HoneyGPT and LLMHoney show the value of mixing deterministic
fast paths with LLM-generated outputs for novel attacker actions. LLMPot shows
that industrial-protocol and process emulation can be configured with LLM
assistance, but the hard part is still validation and physical consistency. The
2026 SoK argues that the field is moving toward autonomous, self-improving
deception systems, while also emphasizing detection vectors and evaluation
quality.

The current implementation now matches that direction:

- `AgenticModbusDatabank` wraps Conpot's response path and can replace a
  deterministic response with a validated generated Modbus TCP frame.
- `AgentRuntime` consumes normalized events and applies accepted `world_patch`
  actions against the shared `TankPumpWorld`.
- `ProtocolReplyValidator` and `WorldPatchApplier` keep generated outputs within
  protocol and process boundaries.

The agent literature adds four concrete design requirements for the next phase.

1. Build a narrow agent-computer interface. SWE-agent's main lesson is that
   interface design matters. Our agent should not receive raw internal objects;
   it should receive tools such as `read_world_snapshot`, `build_modbus_reply`,
   `propose_world_patch`, `select_lure_artifact`, and `record_actor_note`.
2. Use executable, state-based evaluation. WebArena and OSWorld are useful
   because success is checked against environment state, not only text
   similarity. Our benchmark should define attacker objectives, run Modbus/SSH/HMI
   sessions, then grade final world state, transcript consistency, and suspicion
   signals.
3. Add episodic memory with feedback. Reflexion suggests storing compact
   reflections after each session. For honeypots, the reflection should be
   structured: actor fingerprint, commands/protocol functions tried, likely goal,
   generated-action failures, and which lure progressed the interaction.
4. Add a scenario/skill library. Voyager's useful idea is not Minecraft itself;
   it is the growing library of reusable skills. Here that maps to reusable
   industrial scenarios: tank tuning, pump fault, historian mismatch,
   maintenance window, firmware backup, ladder-logic upload, and alarm recovery.

## Immediate engineering tasks

1. Add an actor/session correlation module.
2. Add an HTTP HMI backed by `TankPumpWorld`.
3. Port the useful MANTIS SSH patterns into an ICS maintenance gateway.
4. Add an agent tool interface instead of free-form planner prompts.
5. Add a scenario/skill library and deterministic scenario compiler.
6. Add a benchmark harness comparing stock Conpot, deterministic, and agentic
   modes.
7. Add fingerprint-hardening checks for TTL, banners, timing distributions,
   exception behavior, and cross-surface contradictions.
