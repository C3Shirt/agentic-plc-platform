# AGENTS.md - Agentic PLC Platform

This repository is the project-owned implementation layer. The sibling Conpot and
MANTIS directories are upstream/reference code and must not be edited unless a task
explicitly calls for an adapter patch.

## Architecture rules

- Keep protocol parsing and responses deterministic.
- Keep the global plant state separate from per-connection and per-actor context.
- An LLM may propose a typed plan but may not emit protocol bytes or mutate state directly.
- Validate every proposed plan before applying it.
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

