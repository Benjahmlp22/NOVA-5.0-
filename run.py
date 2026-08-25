"""Arranca NOVA.

    python run.py
    python run.py --debug     # detalle completo al fichero de log

Requisitos: Ollama abierto con el modelo descargado y el modelo de voz
de Vosk disponible (ver README).
"""

from __future__ import annotations

import sys

from nova.app import run

if __name__ == "__main__":
    sys.exit(run(sys.argv[1:]))
