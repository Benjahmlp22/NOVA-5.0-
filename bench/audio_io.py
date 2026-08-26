"""Leer audio a 16 kHz mono, igual para los dos reconocedores.

Vosk quiere int16 a 16 kHz; faster-whisper quiere float32 a 16 kHz. Si
cada uno recibe el audio remuestreado de una forma distinta, la
comparación mide el remuestreo tanto como el modelo — y el que sale
perdiendo es siempre el que tuvo peor suerte con el filtro.

Se usa PyAV, que ya entra con faster-whisper, en vez de escribir un
remuestreador a mano con numpy: un remuestreo sin filtro anti-aliasing
mete alias en las frecuencias donde vive la consonante, que es justo lo
que un reconocedor necesita para distinguir "abre" de "abren".
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

SAMPLE_RATE = 16000


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
    entero = np.concatenate(trozos).astype(np.float32) / 32768.0
    return entero


def a_int16(senal: np.ndarray) -> bytes:
    """Los bytes que espera Vosk (PCM int16 little-endian)."""
    recortada = np.clip(senal, -1.0, 1.0)
    return (recortada * 32767).astype("<i2").tobytes()


def rms(senal: np.ndarray) -> float:
    if senal.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(senal.astype(np.float64) ** 2)))


def normalizar(senal: np.ndarray, objetivo_rms: float = 0.05) -> np.ndarray:
    """Sube el nivel a un RMS conocido sin llegar a saturar.

    Un micro con poca ganancia es la mitad del problema de precisión: el
    reconocedor recibe una señal donde la consonante queda por debajo del
    ruido de cuantización. Igualar el nivel antes de reconocer es gratis
    y quita esa variable de en medio.
    """
    actual = rms(senal)
    if actual <= 1e-6:
        return senal
    factor = objetivo_rms / actual
    # Techo: si al subir se satura, la distorsión hace más daño que el
    # nivel bajo que estábamos arreglando.
    pico = float(np.max(np.abs(senal))) or 1e-6
    factor = min(factor, 0.95 / pico)
    return (senal * factor).astype(np.float32)
