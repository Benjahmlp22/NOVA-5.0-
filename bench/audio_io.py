"""Leer ficheros de audio a 16 kHz mono para el banco de pruebas.

El tratamiento de la señal (normalizar, convertir, medir nivel) vive en
`nova/voice/audio.py`, que es lo que usa NOVA de verdad: si el banco
tratara el audio de otra forma que la app, mediría otra cosa que la app.
Aquí sólo queda leer del disco, que es lo único que la app no hace.

Se usa PyAV (entra con faster-whisper) en vez de un remuestreador casero
con numpy: sin filtro anti-aliasing se meten alias justo en las
frecuencias donde vive la consonante, que es lo que el reconocedor
necesita para distinguir "abre" de "abren".
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from nova.voice.audio import SAMPLE_RATE, a_int16, de_int16, normalizar, pico, rms

__all__ = ["SAMPLE_RATE", "a_int16", "de_int16", "leer_mono_16k", "normalizar", "pico", "rms"]


def leer_mono_16k(ruta: Path) -> np.ndarray:
    """Devuelve la señal en float32 [-1, 1], mono, a 16 kHz."""
    import av

    with av.open(str(ruta)) as contenedor:
        flujo = contenedor.streams.audio[0]
        remuestreador = av.audio.resampler.AudioResampler(
            format="s16", layout="mono", rate=SAMPLE_RATE
        )
        trozos: list[np.ndarray] = []
        for marco in contenedor.decode(flujo):
            for salida in remuestreador.resample(marco):
                trozos.append(salida.to_ndarray().reshape(-1))
        # El remuestreador guarda muestras en su búfer interno: hay que
        # vaciarlo o se pierde la última fracción de segundo, que en una
        # orden corta puede ser la palabra entera.
        for salida in remuestreador.resample(None):
            trozos.append(salida.to_ndarray().reshape(-1))

    if not trozos:
        return np.zeros(0, dtype=np.float32)
    return np.concatenate(trozos).astype(np.float32) / 32768.0
