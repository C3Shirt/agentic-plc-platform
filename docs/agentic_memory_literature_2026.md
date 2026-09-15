# Agentic PLC Honeypot: Snapshot/Memory Literature Notes

Search date: 2026-09-15.

This note records the first-pass literature evidence for positioning the project
as a memory-governed, process-aware ICS honeypot rather than a pure LLM response
generator. The main research claim to develop is:

> An ICS honeypot can expose a protocol-faithful PLC interface while a
> physical-process agent maintains the attacker-observable process world through
> authoritative snapshots, selective memory, and deterministic consistency
> validators.

## Search scope

Searches covered four clusters:

- LLM-enhanced and ICS honeypots.
- Long-term memory for LLM agents.
- Context compression and long-context failure modes.
- LLM world models and rule-aligned simulation agents.

Priority was given to primary sources: IEEE/arXiv, NeurIPS proceedings, ACL
Anthology, ACM/DOI records, OpenReview, and conference pages.

## Papers to cite

| Cluster | Paper | Source status | Why it matters for this project |
| --- | --- | --- | --- |
| LLM + ICS honeypot | Vasilatos et al., `LLMPot: Dynamically Configured LLM-based Honeypot for Industrial Protocol and Physical Process Emulation` | arXiv: https://arxiv.org/abs/2405.05999; IEEE Xplore search result lists EuroS&P 2025 | Closest related work. It motivates industrial protocol and control-logic emulation with LLMs. Our novelty should therefore emphasize long-session cyber-physical consistency and memory governance. |
| LLM honeypot SoK | Bridges et al., `SoK: Honeypots & LLMs, More Than the Sum of Their Parts?` | arXiv: https://arxiv.org/abs/2510.25939 | Useful for related-work framing. It argues the field needs autonomous, self-improving deception systems and gives a taxonomy of LLM-honeypot design/evaluation issues. |
| Agent memory | Xu et al., `A-Mem: Agentic Memory for LLM Agents` | NeurIPS 2025: https://proceedings.neurips.cc/paper_files/paper/2025/hash/19909c36f51abc4856b4560aff3d36d6-Abstract-Conference.html | Supports the idea that memory organization should be dynamic and linked, not just raw event logging or fixed RAG. |
| Agent memory benchmark | Hu et al., `Evaluating Memory in LLM Agents via Incremental Multi-Turn Interactions` | arXiv: https://arxiv.org/abs/2507.05257; search result indicates ICLR 2026 proceedings | Gives four evaluation dimensions for memory agents: accurate retrieval, test-time learning, long-range understanding, and selective forgetting. These map cleanly to honeypot consistency tests. |
| Agent memory survey | Luo et al., `From Storage to Experience: A Survey on the Evolution of LLM Agent Memory Mechanisms` | ACL Findings 2026: https://aclanthology.org/2026.findings-acl.2069/ | Provides a theory arc from storage to reflection to experience. This is a good citation for turning raw attack logs into reusable deception experience. |
| Long-context failure | Liu et al., `Lost in the Middle: How Language Models Use Long Contexts` | TACL/arXiv: https://arxiv.org/abs/2307.03172 | Justifies not putting the entire attack history into the prompt. We need snapshot-first and retrieval/compression-first context. |
| Prompt compression | Jiang et al., `LLMLingua` | EMNLP 2023: https://aclanthology.org/2023.emnlp-main.825/ | Establishes prompt compression as a cost/latency technique. Less process-specific, but useful for motivating bounded context. |
| Long-context compression | Jiang et al., `LongLLMLingua` | ACL 2024: https://aclanthology.org/2024.acl-long.91/ | Directly supports key-information retention under long contexts and position bias. |
| Agent context compression | Kang et al., `ACON: Optimizing Context Compression for Long-horizon LLM Agents` | arXiv: https://arxiv.org/abs/2510.00615; search result confirms ICML 2026 poster/regular listing | Strong match to our compression policy: preserve heterogeneous signals such as state, preconditions, action outcomes, and future decision cues. |
| Believable agents | Park et al., `Generative Agents: Interactive Simulacra of Human Behavior` | UIST 2023/arXiv: https://arxiv.org/abs/2304.03442; DOI: https://doi.org/10.1145/3586183.3606763 | Foundational pattern for observation, memory, reflection, and planning. We borrow the architecture pattern, not the human-social simulation objective. |
| Reflective agents | Shinn et al., `Reflexion: Language Agents with Verbal Reinforcement Learning` | NeurIPS 2023: https://proceedings.neurips.cc/paper_files/paper/2023/hash/1b44b878bb782e6954cd888628510e90-Abstract-Conference.html | Supports using feedback/reflection as episodic memory without model fine-tuning. Useful for future self-improving deception policy. |
| World model alignment | Zhou et al., `WALL-E: World Alignment by NeuroSymbolic Learning improves World Model-based LLM Agents` | NeurIPS 2025: https://papers.nips.cc/paper_files/paper/2025/hash/5e772a13ccba5255331240dcd99aa38b-Abstract-Conference.html | Strong citation for combining LLM world models with symbolic rules. In our case, process constraints and protocol state machines are the symbolic guardrails. |

## Design implications

### 1. Do not compete with LLMPot only on protocol generation

LLMPot already makes the case that LLMs can help generate industrial-protocol
honeypot behavior. Our stronger angle is:

- long-running interaction consistency,
- attacker-specific memory,
- protocol-state and physical-state co-validation,
- generic physical-process backend abstraction.

### 2. Treat snapshot as the authoritative working memory

The LLM should not be the source of truth. It should receive a compact
snapshot, propose a typed `WorldPatch`, and let deterministic validators decide
whether that proposal can mutate the process backend.

This suggests three memory layers:

- `Hot snapshot`: current process values, revision, writable paths, bounds, and
  protocol mappings.
- `Episodic attack memory`: actor/session history, recently touched addresses,
  write/read-back behavior, repeated probing, and inferred attack phase.
- `Semantic/rule memory`: process type, invariants, equations, causal
  relationships, register-map meaning, and protocol state-machine constraints.

### 3. Compression must preserve state-changing evidence

For ICS honeypots, the information that cannot be lost during compression is
not just "semantically important text". It includes:

- exact transaction id and unit id,
- function code and protocol state,
- touched address ranges,
- decoded engineering values,
- snapshot revision,
- previous attacker-visible values,
- invariant boundaries and writable/read-only distinctions.

This is why the current deterministic `ProcessContextCompressor` should remain
structured and auditable before we add any learned or LLM-based compressor.

### 4. The physical-process agent can be fidelity-adjustable

The backend may be a TE trace, an equation-based process, or an LLM-assisted
agent that maintains a plausible attacker-observable world. The paper should be
careful to claim attacker-observable consistency, not full high-fidelity process
simulation unless the backend actually provides it.

## Candidate paper positioning

Possible title direction:

- `Memory-Governed Cyber-Physical Deception for Agentic ICS Honeypots`
- `Snapshot-Consistent Agentic PLC Honeypots`
- `Process-Aware Context and Memory Management for LLM-Assisted ICS Honeypots`

Possible central contribution:

1. A generic `PhysicalProcessProvider` abstraction that decouples protocol
   interaction from physical-process implementation.
2. A snapshot-governed agent architecture where LLMs can generate protocol
   replies and process-state patches only through validated envelopes.
3. A process-aware context compression method that preserves protocol-critical,
   physical-critical, and attacker-memory-critical fields.
4. A benchmark for protocol-state consistency, physical-state consistency,
   cross-session memory consistency, and context/cost efficiency.

## Open citation verification tasks

- Confirm final IEEE metadata and DOI for LLMPot's EuroS&P 2025 version.
- Confirm final ICLR 2026 metadata for MemoryAgentBench if citing as
  conference paper rather than arXiv.
- Confirm final ICML 2026 metadata for ACON before camera-ready writing.
- Add venue-specific BibTeX once paper target is selected.
