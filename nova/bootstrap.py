"""Lo que hay que cargar ANTES de que PyQt5 entre en el proceso.

**Este módulo no puede importar PyQt5, ni nada que lo importe.**  Ni
`nova.app`, ni `nova.ui`.  Si algún día lo hace, NOVA volverá a morir con
un *segmentation fault* al arrancar.

Qué pasa, medido el 26/08 en un script mínimo:

    cargar Whisper → importar PyQt5            ✓ funciona
    importar PyQt5 → cargar Whisper            ✗ segmentation fault

Y no hace falta ni crear la QApplication: **basta con el import**.  El
fallo cae dentro de `WhisperModel.__init__`, sin excepción de Python y
sin traceback (aparece con `python -X faulthandler`), así que desde el
síntoma no hay forma de llegar a la causa.

Como `nova/app.py` importa PyQt5 en su cabecera, el orden sólo se puede
garantizar desde fuera: `run.py` llama aquí primero y sólo después
importa la aplicación.

El precio es que el orbe tarda unos segundos en salir. Se paga a gusto:
NOVA4 tardaba 42 s en ser capaz de oír, y encima sin decirlo.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .config import CONFIG
from .voice.cuda import precargar_runtime
from .voice.transcriptor import Transcriptor

log = logging.getLogger("nova.bootstrap")


# ── Argumentos y logging ─────────────────────────────────────────────
#
# Viven aquí y no en `app.py` por el mismo motivo que todo lo demás de
# este módulo: `run.py` los necesita ANTES de importar la aplicación, y
# la aplicación arrastra PyQt5.


def parsear_argumentos(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="nova",
        description="NOVA — asistente de escritorio local por voz.",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Escribe el detalle completo (audio, parciales, tiempos) al fichero de log.",
    )
    return parser.parse_args(argv)


def configurar_logging(debug: bool = False, destino: Path | None = None) -> None:
    """Consola limpia, fichero detallado.

    Cuando el audio falla es en directo y no se puede reproducir: o quedó
    escrito, o no hay diagnóstico posible. Por eso el fichero se lleva
    TODO (incluidos los parciales de la etapa 1, que son DEBUG) mientras
    la consola se queda en INFO — si no, la consola es ilegible justo
    cuando más falta hace mirarla.

    El fichero rota: en NOVA4 era un `FileHandler` a secas y crecía sin
    techo. Con `--debug` cada frase deja varias líneas, así que sin
    rotación esto se come el disco en sesiones largas.
    """
    # La consola de Windows llega en cp1252 y los mensajes llevan flechas
    # y comillas españolas. Sin esto, `logging` revienta al escribir —
    # "Logging error: 'charmap' codec can't encode character"— y encima
    # se pierde justo la línea que explicaba lo que estaba pasando.
    for flujo in (sys.stdout, sys.stderr):
        if hasattr(flujo, "reconfigure"):
            try:
                flujo.reconfigure(encoding="utf-8", errors="replace")
            except Exception:  # noqa: BLE001
                pass

    raiz = logging.getLogger()
    raiz.setLevel(logging.DEBUG if debug else logging.INFO)
    for viejo in list(raiz.handlers):
        raiz.removeHandler(viejo)

    formato = logging.Formatter(
        "%(asctime)s [%(name)s] %(levelname)s: %(message)s", datefmt="%H:%M:%S"
    )

    consola = logging.StreamHandler(sys.stdout)
    consola.setLevel(logging.INFO)
    consola.setFormatter(formato)
    raiz.addHandler(consola)

    ruta = destino or CONFIG.log_file
    ruta.parent.mkdir(parents=True, exist_ok=True)
    fichero = RotatingFileHandler(
        ruta, maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    fichero.setLevel(logging.DEBUG if debug else logging.INFO)
    fichero.setFormatter(formato)
    raiz.addHandler(fichero)

    # faster_whisper habla muchísimo en DEBUG ("Processing segment at...")
    # y tapa lo nuestro justo cuando hace falta leerlo.
    for ruidoso in ("httpx", "httpcore", "urllib3", "comtypes", "faster_whisper"):
        logging.getLogger(ruidoso).setLevel(logging.WARNING)

    if debug:
        log.info("Modo depuración: el detalle va a %s", ruta)


def preparar_transcriptor(anunciar: bool = True) -> Transcriptor:
    """Construye y calienta la etapa 2. Devuelve siempre un transcriptor.

    Si falla, devuelve uno vacío en vez de reventar: NOVA arranca igual y
    lo dice por el orbe y por el log. Quedarse sin oído no debe impedir
    ver que está viva.
    """
    precargar_runtime()
    if anunciar:
        print(f"Preparando el oído de NOVA ({CONFIG.whisper_model})...", flush=True)

    t0 = time.monotonic()
    transcriptor = Transcriptor(
        modelo=CONFIG.whisper_model,
        compute_type=CONFIG.whisper_compute,
        device=CONFIG.whisper_device,
        vosk_model=CONFIG.vosk_model,
    )
    if transcriptor.cargar():
        transcriptor.precalentar()
        log.info("Etapa 2 lista en %.1fs: %s", time.monotonic() - t0, transcriptor.motor)
        if anunciar:
            print(f"  listo en {time.monotonic() - t0:.1f}s — {transcriptor.motor}", flush=True)
    else:
        log.error("Sin transcriptor: %s", transcriptor.error)
        if anunciar:
            print(f"  ✗ {transcriptor.error}", flush=True)
    return transcriptor
