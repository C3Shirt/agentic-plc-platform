# AGENTS.md - Agentic PLC Platform

This repository is the project-owned implementation layer. The sibling Conpot and
MANTIS directories are upstream/reference code and must not be edited unless a task
explicitly calls for an adapter patch.

## Architecture rules

- Keep deterministic protocol behavior as the fallback and regression baseline.
- Keep the global plant state separate from per-connection and per-actor context.
- An LLM may generate protocol response bytes and world-model patches, but only
  through the typed `AgentProposal` envelope.
- Validate every generated protocol reply, deception plan, and world patch before
  applying or sending it.
- Preserve a no-LLM fallback path for every online interaction.
- Do not connect the honeypot to a real PLC or production network.

## Development

```powershell
python -m pip install -e ".[dev]"
python -m pytest
```

Use `src/agentic_plc/contracts` for cross-component schemas, `world` for the
deterministic process model, `policy` for validation, and `adapters` for narrow
integration with exposed services.
