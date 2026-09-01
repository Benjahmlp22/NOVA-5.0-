"""Lo que tiene que quedar escrito cuando NOVA muere sin decir nada.

El 01/09 NOVA se cerró seis veces seguidas, siempre en el mismo punto:
justo después de entender una orden.  No dejó traceback en `nova.log`, y
se dio por hecho que era un crash nativo.  **No lo era**: el Visor de
sucesos de Windows no registró NI UN `Application Error` de `python.exe`
ese día — los dos que hay (Qt5Core y MSVCP140) son del 26/08.  Un crash
nativo de verdad siempre deja evento; una muerte sin evento, no.

Quedan dos formas de morir en silencio, y este módulo cubre las dos:

**Una excepción de Python en un slot de Qt.**  PyQt5 no la propaga: se
la pasa a `sys.excepthook` y acto seguido llama a `qFatal()`, que aborta
el proceso.  Como el `excepthook` por defecto escribe en `stderr` y NOVA
se lanza en segundo plano, ese traceback no lo lee nadie.  Aquí se
sustituye por uno que escribe al log, que es donde se va a mirar.

**Un fallo por debajo de Python** (CUDA, PortAudio, Qt).  Para eso está
`faulthandler`: engancha SIGSEGV/SIGABRT/SIGFPE y vuelca la pila de
*todos* los hilos antes de que el proceso se vaya.  Sin él lo único que
queda es un log que se corta a media frase, que es exactamente lo que
pasó.

El fichero se abre una vez y se guarda en un global: `faulthandler`
escribe sobre el descriptor, no sobre el objeto, así que si Python
recolecta el fichero el volcado se pierde justo cuando hace falta.
"""

from __future__ import annotations

import faulthandler
import logging
import os
import sys
import threading
import traceback
from datetime import datetime
from pathlib import Path
from types import TracebackType

log = logging.getLogger("nova.forense")

_fichero = None  # ver docstring: NO puede ser local


def _apuntar(cabecera: str, texto: str) -> None:
    """Deja constancia en el log y en el fichero de volcados.

    En los dos sitios y no en uno: el log es donde se mira, pero cuando
    el proceso se está muriendo el `logging` puede no llegar a vaciar su
    búfer, y el fichero de volcados se escribe sin búfer.
    """
    log.error("%s\n%s", cabecera, texto)
    if _fichero is not None:
        try:
            _fichero.write(f"\n--- {datetime.now():%Y-%m-%d %H:%M:%S} {cabecera}\n{texto}\n")
            _fichero.flush()
        except Exception:  # noqa: BLE001
            pass


def _al_fallar(tipo: type[BaseException], valor: BaseException,
               pila: TracebackType | None) -> None:
    """Sustituye a `sys.excepthook`. Ver el docstring del módulo.

    KeyboardInterrupt se deja pasar tal cual: Ctrl+C no es un fallo y no
    tiene por qué ensuciar el log.
    """
    if issubclass(tipo, KeyboardInterrupt):
        sys.__excepthook__(tipo, valor, pila)
        return
    _apuntar(
        "excepción sin capturar (si vino de un slot de Qt, el proceso muere aquí)",
        "".join(traceback.format_exception(tipo, valor, pila)),
    )


def _al_fallar_hilo(args) -> None:  # noqa: ANN001
    """Lo mismo para los hilos: sin esto, un hilo que revienta se calla."""
    if issubclass(args.exc_type, SystemExit):
        return
    _apuntar(
        f"excepción sin capturar en el hilo {args.thread.name if args.thread else '?'}",
        "".join(traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback)),
    )


def activar(destino: Path) -> Path:
    """Engancha los tres avisos. Se llama ANTES de importar PyQt5.

    Devuelve la ruta del fichero de volcados para poder anunciarla.
    """
    global _fichero

    destino.parent.mkdir(parents=True, exist_ok=True)
    _fichero = destino.open("a", encoding="utf-8", buffering=1)
    _fichero.write(
        f"\n===== arranque {datetime.now():%Y-%m-%d %H:%M:%S} · pid {os.getpid()} =====\n"
    )
    faulthandler.enable(file=_fichero, all_threads=True)
    sys.excepthook = _al_fallar
    threading.excepthook = _al_fallar_hilo
    return destino
