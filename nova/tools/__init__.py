"""Herramientas de NOVA — todo lo que puede *hacer*, no solo decir."""

from __future__ import annotations

from ..config import CONFIG
from . import apps, audio, files, memory, recordatorios, system, web
from .registry import (
    PendingConfirmation,
    Risk,
    Tool,
    ToolRegistry,
    ToolResult,
)


def build_registry(confirm_policy: str | None = None) -> ToolRegistry:
    """Registro con todas las herramientas listas para usar."""
    reg = ToolRegistry(confirm_policy or CONFIG.confirm_policy)
    system.register(reg)
    apps.register(reg)
    files.register(reg)
    memory.register(reg)
    audio.register(reg)
    recordatorios.register(reg)
    web.register(reg)
    return reg


__all__ = [
    "PendingConfirmation",
    "Risk",
    "Tool",
    "ToolRegistry",
    "ToolResult",
    "build_registry",
]
