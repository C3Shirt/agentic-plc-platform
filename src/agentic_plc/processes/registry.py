from __future__ import annotations

from collections.abc import Callable, Mapping

from .base import ProcessBackend

BackendFactory = Callable[..., ProcessBackend]


class ProcessBackendRegistry:
    """Small registry used to keep simulator selection out of protocol code."""

    def __init__(self) -> None:
        self._factories: dict[str, BackendFactory] = {}

    def register(self, backend_type: str, factory: BackendFactory) -> None:
        if not backend_type:
            raise ValueError("backend_type must not be empty")
        if backend_type in self._factories:
            raise ValueError(f"backend already registered: {backend_type}")
        self._factories[backend_type] = factory

    def create(self, backend_type: str, **kwargs: object) -> ProcessBackend:
        try:
            factory = self._factories[backend_type]
        except KeyError as exc:
            raise KeyError(f"unknown process backend: {backend_type}") from exc
        return factory(**kwargs)

    @property
    def factories(self) -> Mapping[str, BackendFactory]:
        return dict(self._factories)
