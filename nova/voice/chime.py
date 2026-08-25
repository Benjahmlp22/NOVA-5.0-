"""Sonido de activación — dos tonos ascendentes generados al vuelo.

Sin archivos de audio que distribuir: se sintetiza al importar y suena
en ~0.2 s.  Es la confirmación de que NOVA te oyó, antes incluso de que
el modelo empiece a pensar.
"""

from __future__ import annotations

import logging
import threading

log = logging.getLogger("nova.voice.chime")

_SR = 44100
_DUR = 0.22


def _generar():
    try:
        import numpy as np
    except ImportError:
        return None

    mitad = int(_SR * _DUR / 2)
    t = np.linspace(0, _DUR / 2, mitad, endpoint=False)

    # C6 → E6 con un armónico suave encima: limpio, corto, nada intrusivo.
    tono1 = np.sin(2 * np.pi * 1047 * t) * 0.32 + np.sin(2 * np.pi * 2094 * t) * 0.08
    tono2 = np.sin(2 * np.pi * 1319 * t) * 0.32 + np.sin(2 * np.pi * 2638 * t) * 0.08
    onda = np.concatenate([tono1, tono2])

    # Envolvente: sin fundido, un clic seco delata el corte de la onda.
    fade = int(len(onda) * 0.18)
    env = np.ones(len(onda))
    env[:fade] = np.linspace(0, 1, fade)
    env[-fade:] = np.linspace(1, 0, fade)
    return (onda * env).astype("float32")


_AUDIO = _generar()


def play() -> None:
    """Suena el chime sin bloquear a quien llama."""
    if _AUDIO is None:
        return

    def _run() -> None:
        try:
            import sounddevice as sd

            sd.play(_AUDIO, _SR, blocking=True)
        except Exception:
            log.debug("no pude reproducir el chime", exc_info=True)

    threading.Thread(target=_run, daemon=True, name="chime").start()
