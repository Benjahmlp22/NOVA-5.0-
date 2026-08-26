"""Interfaz de NOVA: panel abajo a la derecha, colapsable al orbe."""

from .actividad import describir
from .glow import GlowBorder
from .interfaz import Interfaz
from .orb import Orb
from .panel import Panel

__all__ = ["GlowBorder", "Interfaz", "Orb", "Panel", "describir"]
