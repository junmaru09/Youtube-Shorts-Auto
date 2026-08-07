"""Video generation backends. Registered by name so settings.yaml can swap them."""

from __future__ import annotations

from .base import VideoBackend

_REGISTRY: dict[str, type[VideoBackend]] = {}


def register(name: str, backend_cls: type[VideoBackend]) -> None:
    _REGISTRY[name] = backend_cls


def get_backend(name: str) -> VideoBackend:
    if name not in _REGISTRY:
        _load_builtin(name)
    if name not in _REGISTRY:
        raise KeyError(f"unknown video backend: {name!r} (known: {sorted(_REGISTRY)})")
    return _REGISTRY[name]()


def _load_builtin(name: str) -> None:
    """Import on demand so `ideate` doesn't need the video SDK installed."""
    if name == "veo":
        from .veo import VeoBackend

        register("veo", VeoBackend)
    elif name == "fake":
        from .fake import FakeBackend

        register("fake", FakeBackend)


__all__ = ["VideoBackend", "get_backend", "register"]
