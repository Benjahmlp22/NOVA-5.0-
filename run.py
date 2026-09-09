"""Arranca Qt y el wake word; Whisper se construye en un proceso sin Qt."""


from __future__ import annotations

import multiprocessing
import os
import sys

from nova.bootstrap import (
    activar_forense,
    configurar_logging,
    parsear_argumentos,
    preparar_transcriptor_diferido,
)

if __name__ == "__main__":
    multiprocessing.freeze_support()
    from nova.config import ROOT
    os.chdir(ROOT)  # HKCU Run no define el directorio de trabajo.
    args = parsear_argumentos(sys.argv[1:])
    configurar_logging(debug=args.debug)
    # Antes que nada lo demás: si NOVA se va a morir sin traceback, esto
    # es lo único que lo va a contar. Ver `nova/forense.py`.
    activar_forense()

    transcriptor = preparar_transcriptor_diferido()

    # Sólo AHORA: este import arrastra PyQt5.
    from nova.app import run

    sys.exit(run(args, transcriptor))
