# Process-Aware Context Compression

This document defines Process-Aware Context Compression (PACC), the prompt
compression layer for the generic agentic PLC honeypot. PACC is intentionally
not Tennessee-Eastman-specific. It compresses the active `PhysicalProcessContext`
for any backend that implements `ProcessBackend` and any PLC slice that is
declared through `ScenarioMapping`.

## Why compression is needed

The agent can eventually see several kinds of context at once:

- current protocol request and transaction identifiers;
- current physical-process snapshot;
- protocol-facing PLC point mappings;
- recent attacker/session behavior;
- deception state and exposed maintenance artifacts;
- backend-specific metadata such as TE trace identifiers or future simulator
  state.

Passing all of this as raw text is expensive and risky. More importantly, it can
make the model miss critical fields. `Lost in the Middle` shows that relevant
information placed inside long contexts may be underused by LLMs even when the
context window is technically large. The honeypot also cannot use lossy prose
summaries for register values, addresses, transaction ids, units, bounds, or
write permissions because those fields are required for protocol validation.

## Theoretical grounding

PACC uses structured, task-aware compression rather than free-form
summarization.

1. **Information bottleneck.** The information bottleneck principle frames
   compression as keeping information about the relevant target while discarding
   irrelevant input detail. In this system, the relevant target is not general
   conversation quality; it is a valid `AgentProposal`: protocol reply bytes,
   bounded world patch, or validated deception plan.
2. **Belief compression for partially observable control.** The honeypot only
   observes attacker interactions and a partial plant snapshot. Belief
   compression work on POMDPs supports representing high-dimensional uncertain
   state through lower-dimensional features that are sufficient for planning in
   encountered regions of state space.
3. **Predictive state representations.** PSR work motivates retaining histories
   of actions and observations that predict future observations. PACC therefore
   keeps recent exact protocol events and touched PLC points instead of only the
   latest snapshot.
4. **Prompt compression and long-context retrieval.** LLMLingua and
   LongLLMLingua motivate budgeted key-information retention under long prompts;
   RAG motivates explicit retrieval/memory instead of relying only on parametric
   model memory.
5. **Agent-specific context compression.** ACON motivates optimizing compressed
   histories and observations for long-horizon agents, where state, action
   outcomes, preconditions, and future decision cues must survive compression.
   For this project, those cues are protocol transaction fields, touched
   registers, decoded engineering values, writable surfaces, and snapshot
   revisions.
6. **Agent memory and action loops.** ReAct and Reflexion motivate separating
   reasoning/action payloads from compact episodic memory. A-Mem and recent
   agent-memory surveys further motivate dynamically organizing interaction
   memory instead of treating it as append-only logs. PACC keeps an actor memory
   summary, but generated actions still pass through typed platform validators.

## Compression contract

PACC takes:

```text
events + PhysicalProcessContext -> CompressedProcessContext
```

The output has five sections:

- `process_card`: stable identity and snapshot metadata, including process id,
  backend name, scenario id, PLC area, protocol, revision, simulation time, and
  variable counts.
- `request_focus`: latest protocol intent, function code, table, address,
  count, transaction id, unit id, requested value, and exact register values
  when available.
- `exposed_points`: a ranked subset of PLC-facing variables with exact numeric
  value, unit, scale, access mode, Modbus table/address, writable flag, encoded
  register value, and selection reason codes.
- `writable_variable_ids`: all writable process variables exposed by the
  scenario. This list is not truncated by `max_points`, because world-patch
  validation depends on the full allowed path set.
- `actor_memory`: deterministic episodic memory for the current actor/source,
  including intent counts, recently touched PLC points, and the last exact
  protocol events. When `ProtocolIntentTracker` has enriched runtime events,
  this section also carries interaction-phase fields such as register mapping,
  write attempt, or write-effect verification.

## Ranking policy

The current deterministic ranker scores each exposed point by:

```text
score(v) =
  current protocol address hit
+ recent actor interest in the same point
+ writable/control-surface relevance
+ read/write role relevance
+ proximity to declared physical bounds
```

This is deliberately simple and auditable. It gives the LLM a dense, structured
view of the variables most likely to affect the current response while retaining
fallback access to the authoritative backend and register map.

## Safety properties

- PACC does not mutate the backend.
- PACC does not bypass `AgentProposal`, protocol reply validation, deception
  plan validation, or process/world patch validation.
- PACC preserves exact protocol-critical fields. It may drop background points,
  but it does not paraphrase register values or write permissions.
- Deterministic planner behavior remains available when no LLM is configured or
  when the LLM times out.
- A future embedding/RAG memory layer must retrieve into the same structured
  contract, not directly into unconstrained protocol bytes.

## First implementation

Code:

- `agentic_plc.agent.context_compressor.ProcessContextCompressor`
- `agentic_plc.agent.process_memory.summarize_actor_process_memory`

Configuration:

- `LLM_CONTEXT_MAX_POINTS`, default `12`
- `LLM_CONTEXT_MAX_EVENTS`, default `5`

Smoke:

```powershell
$env:PYTHONPATH = "src"
python tools\smoke_context_compressor.py
```

## References

- Tishby, Pereira, and Bialek. *The Information Bottleneck Method*.
  https://arxiv.org/abs/physics/0004057
- Roy, Gordon, and Thrun. *Finding Approximate POMDP Solutions Through Belief
  Compression*. Journal of Artificial Intelligence Research, 2005.
  https://arxiv.org/abs/1107.0053
- Singh, James, and Rudary. *Predictive State Representations: A New Theory for
  Modeling Dynamical Systems*. UAI, 2004. https://arxiv.org/abs/1207.4167
- Liu et al. *Lost in the Middle: How Language Models Use Long Contexts*.
  Transactions of the Association for Computational Linguistics, 2024.
  https://arxiv.org/abs/2307.03172
- Jiang et al. *LLMLingua: Compressing Prompts for Accelerated Inference of
  Large Language Models*. 2023. https://arxiv.org/abs/2310.05736
- Jiang et al. *LongLLMLingua: Accelerating and Enhancing LLMs in Long Context
  Scenarios via Prompt Compression*. 2023. https://arxiv.org/abs/2310.06839
- Kang et al. *ACON: Optimizing Context Compression for Long-horizon LLM
  Agents*. ICML 2026/arXiv. https://arxiv.org/abs/2510.00615
- Lewis et al. *Retrieval-Augmented Generation for Knowledge-Intensive NLP
  Tasks*. NeurIPS, 2020. https://arxiv.org/abs/2005.11401
- Yao et al. *ReAct: Synergizing Reasoning and Acting in Language Models*.
  ICLR, 2023. https://arxiv.org/abs/2210.03629
- Shinn et al. *Reflexion: Language Agents with Verbal Reinforcement Learning*.
  NeurIPS, 2023. https://arxiv.org/abs/2303.11366
- Xu et al. *A-Mem: Agentic Memory for LLM Agents*. NeurIPS, 2025.
  https://proceedings.neurips.cc/paper_files/paper/2025/hash/19909c36f51abc4856b4560aff3d36d6-Abstract-Conference.html
- Luo et al. *From Storage to Experience: A Survey on the Evolution of LLM
  Agent Memory Mechanisms*. Findings of ACL, 2026.
  https://aclanthology.org/2026.findings-acl.2069/
