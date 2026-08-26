"""Que CTranslate2 encuentre cuBLAS y cuDNN en Windows.

faster-whisper corre sobre CTranslate2, que en GPU necesita las DLL de
cuBLAS y cuDNN.  Instaladas con pip (`nvidia-cublas-cu12`,
`nvidia-cudnn-cu12`) esas DLL acaban en
`site-packages/nvidia/<lib>/bin`, que **no** está en el PATH de Windows.

Y desde Python 3.8 tampoco vale con meterlas en `PATH`: la carga de DLL
nativas sólo mira los directorios registrados con `os.add_dll_directory`.
Sin esto, importar `ctranslate2` con GPU falla con un
"Could not locate cudnn_ops64_9.dll" que no dice de dónde viene.

Se llama antes de importar `faster_whisper`, y es idempotente.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

log = logging.getLogger("nova.voice.cuda")

_preparado = False


def _directorios_nvidia() -> list[Path]:
    """Carpetas `bin` de los paquetes nvidia-*-cu12 instalados con pip."""
    try:
        import nvidia
    except ImportError:
        return []

    raices = [Path(p) for p in getattr(nvidia, "__path__", [])]
    salida: list[Path] = []
    for raiz in raices:
        if not raiz.is_dir():
            continue
        for lib in sorted(raiz.iterdir()):
            binario = lib / "bin"
            if binario.is_dir():
                salida.append(binario)
    return salida


def preparar_dlls() -> list[Path]:
    """Registra las carpetas de DLL de CUDA. Devuelve las que añadió."""
    global _preparado
    if _preparado or sys.platform != "win32":
        return []

    añadidas: list[Path] = []
    for carpeta in _directorios_nvidia():
        try:
            os.add_dll_directory(str(carpeta))
        except OSError:
            log.debug("no pude registrar %s", carpeta, exc_info=True)
            continue
        añadidas.append(carpeta)

    if añadidas:
        log.debug("DLL de CUDA registradas: %s", ", ".join(c.name for c in añadidas))
    else:
        log.debug("no encontré DLL de CUDA instaladas por pip")

    _preparado = True
    return añadidas


def hay_gpu() -> bool:
    """¿Hay una GPU que CTranslate2 pueda usar de verdad?

    No basta con que exista la tarjeta: si faltan las DLL o la versión de
    cuDNN no cuadra, `ctranslate2` importa bien y revienta al crear el
    modelo. Preguntarle a él directamente es la única respuesta fiable.
    """
    preparar_dlls()
    try:
        import ctranslate2

        return ctranslate2.get_cuda_device_count() > 0
    except Exception:
        log.debug("CUDA no disponible para CTranslate2", exc_info=True)
        return False
