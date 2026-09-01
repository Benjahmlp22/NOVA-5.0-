"""Arranca NOVA.

    python run.py
    python run.py --debug     # detalle completo al fichero de log

El orden de estas líneas IMPORTA y no es estilo. La etapa 2 de voz tiene
que construirse antes de que PyQt5 entre en el proceso, o NOVA muere con
un segmentation fault sin traceback — ni siquiera hace falta crear la
QApplication, basta con el import. Por eso `nova.app` se importa DENTRO
del bloque de abajo y no arriba. El porqué medido está en
`nova/bootstrap.py`.

Requisitos: Ollama abierto con el modelo descargado y el modelo de voz
de Vosk disponible (ver README).
"""

from __future__ import annotations

import sys

from nova.bootstrap import (
    activar_forense,
    configurar_logging,
    parsear_argumentos,
    preparar_transcriptor,
)

if __name__ == "__main__":
    args = parsear_argumentos(sys.argv[1:])
    configurar_logging(debug=args.debug)
    # Antes que nada lo demás: si NOVA se va a morir sin traceback, esto
    # es lo único que lo va a contar. Ver `nova/forense.py`.
    activar_forense()

    transcriptor = preparar_transcriptor()

    # Sólo AHORA: este import arrastra PyQt5.
    from nova.app import run

    sys.exit(run(args, transcriptor))
