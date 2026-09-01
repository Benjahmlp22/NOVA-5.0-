"""Plugins: cómo la gente personaliza su NOVA.

Un plugin es una carpeta con un `plugin.json` y, si hace falta, un
`plugin.py`. Puede cambiar la personalidad de NOVA, su voz, las frases
que usa, y —si trae código— añadir cosas nuevas que sabe hacer.

Léete `manifiesto.py` antes de tocar nada: ahí está explicado lo que
este sistema SÍ garantiza y lo que no.
"""

from __future__ import annotations

from .carga import cargar, cargar_activos
from .gestor import Gestor
from .manifiesto import DELICADOS, PERMISOS, Plugin
from .revision import Revision, revisar

__all__ = [
    "DELICADOS",
    "PERMISOS",
    "Gestor",
    "Plugin",
    "Revision",
    "cargar",
    "cargar_activos",
    "revisar",
]
