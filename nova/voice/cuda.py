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

    # Y ADEMÁS al PATH. No es cinturón y tirantes: hacen falta las dos.
    #
    # `os.add_dll_directory` basta para lo que carga Python (ctypes, los
    # .pyd), y con él `ctranslate2` importa bien y hasta responde
    # `get_cuda_device_count() == 1`. Pero CTranslate2 carga cuBLAS y
    # cuDNN desde su propio C++, con un `LoadLibrary` a secas que no mira
    # los directorios registrados — y revienta en la PRIMERA
    # transcripción, no al importar, con "Library cublas64_12.dll is not
    # found or cannot be loaded".
    #
    # Comprobado el 26/08: con add_dll_directory a solas, el modelo carga
    # en 2.4 s y falla al transcribir; añadiendo el PATH, transcribe.
    if añadidas:
        os.environ["PATH"] = os.pathsep.join(
            [str(c) for c in añadidas] + [os.environ.get("PATH", "")]
        )

    if añadidas:
        log.debug("DLL de CUDA registradas: %s", ", ".join(c.name for c in añadidas))
    else:
        log.debug("no encontré DLL de CUDA instaladas por pip")

    _preparado = True
    return añadidas


def precargar_runtime() -> bool:
    """Importa CTranslate2 pronto. NO basta por sí solo — ver abajo.

    El modelo de Whisper hay que **construirlo antes de que exista la
    QApplication**. Si no, NOVA muere con un *segmentation fault* limpio:
    sin traceback de Python, sin excepción, sin nada que apunte a la
    causa. Reproducido el 26/08 en un script mínimo con sólo PyQt5 y
    faster-whisper:

        cargar Whisper → crear QApplication            ✓ funciona
        crear QApplication → cargar Whisper            ✗ segfault
        crear QApplication → cargar en otro hilo       ✗ segfault

    O sea que no es cosa de hilos, es el orden. Qt inicializa su propio
    estado de carga de DLL y después de eso construir el modelo revienta
    el proceso.

    Con un QApplication vacío y sin bucle de eventos, adelantar sólo este
    import parecía suficiente — y en la app real, con widgets y
    `exec_()` corriendo, seguía cayendo dentro de `WhisperModel.__init__`
    (faulthandler dixit). Así que el import se adelanta igual, pero quien
    de verdad evita el fallo es cargar el transcriptor entero en `run()`
    antes de montar Qt.
    """
    preparar_dlls()
    try:
        import ctranslate2  # noqa: F401

        log.debug("CTranslate2 precargado antes de Qt")
        return True
    except Exception:  # noqa: BLE001
        log.debug("faster-whisper no está instalado; nada que precargar", exc_info=True)
        return False


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
