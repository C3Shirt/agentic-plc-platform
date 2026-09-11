from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def load_dotenv_values(path: Path | str = ".env") -> dict[str, str]:
    env_path = Path(path)
    if not env_path.exists():
        return {}

    values: dict[str, str] = {}
    with env_path.open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[len("export ") :].strip()
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip()
            if not key:
                continue
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
                value = value[1:-1]
            values[key] = value
    return values


@dataclass(frozen=True, slots=True)
class LLMConfig:
    base_url: str | None
    api_key: str | None
    model: str
    timeout_seconds: float = 15.0
    max_tokens: int = 300
    context_max_points: int = 12
    context_max_events: int = 5

    @property
    def is_configured(self) -> bool:
        return bool(self.base_url and self.api_key)

    @classmethod
    def from_env(
        cls,
        env_file: Path | str = ".env",
        environ: dict[str, str] | None = None,
    ) -> "LLMConfig":
        merged = dict(load_dotenv_values(env_file))
        merged.update(environ or os.environ)

        timeout = merged.get("LLM_TIMEOUT_SECONDS", "15")
        max_tokens = merged.get("LLM_MAX_TOKENS", "300")
        context_max_points = merged.get("LLM_CONTEXT_MAX_POINTS", "12")
        context_max_events = merged.get("LLM_CONTEXT_MAX_EVENTS", "5")
        return cls(
            base_url=first_present(
                merged,
                "OpenAIBaseURL",
                "OPENAI_BASE_URL",
                "LLM_BASE_URL",
            ),
            api_key=first_present(
                merged,
                "APIKey",
                "OPENAI_API_KEY",
                "LLM_API_KEY",
            ),
            model=first_present(
                merged,
                "OpenAIModel",
                "OPENAI_MODEL",
                "LLM_MODEL",
            )
            or "gpt-4o-mini",
            timeout_seconds=float(timeout),
            max_tokens=int(max_tokens),
            context_max_points=int(context_max_points),
            context_max_events=int(context_max_events),
        )


def first_present(values: dict[str, str], *keys: str) -> str | None:
    for key in keys:
        value = values.get(key)
        if value:
            return value
    return None
