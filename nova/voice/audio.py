"""Utilidades de señal compartidas por la escucha, el doctor y el banco.

Todo lo que entra a un reconocedor pasa por aquí: 16 kHz, mono, float32
en [-1, 1].  Tener un solo sitio donde se decide el formato evita el
fallo clásico de que cada motor reciba el audio tratado de una forma y la
comparación mida el tratamiento en vez del modelo.
"""

from __future__ import annotations

import logging

import numpy as np

log = logging.getLogger("nova.voice.audio")

# 16 kHz es lo que quieren Vosk y Whisper. Capturar a más y bajar aquí es
# mejor que pedirle al driver que capture a 16 kHz: el remuestreo de
# PortAudio en MME es de peor calidad que hacerlo nosotros.
SAMPLE_RATE = 16000

# RMS objetivo al normalizar. 0.05 dejaba picos cómodos por debajo de
# saturación en las pruebas del 26/08 con la voz sintética.
RMS_OBJETIVO = 0.05

# Por debajo de esto no hay señal: es silencio digital, no una sala
# callada. Un micro vivo siempre tiene suelo de ruido por encima.
UMBRAL_SILENCIO = 1e-5


def rms(senal: np.ndarray) -> float:
    if senal.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(senal.astype(np.float64) ** 2)))


def pico(senal: np.ndarray) -> float:
    if senal.size == 0:
        return 0.0
    return float(np.max(np.abs(senal)))


def hay_senal(senal: np.ndarray) -> bool:
    """¿Esto es audio o son ceros?

    Un pico exactamente 0.0 no significa "sala silenciosa": significa que
    el micro está apagado, muteado por hardware o que el dispositivo
    elegido no es el que crees. Distinguirlo importa porque el síntoma
    ("NOVA no me oye") es idéntico al de un modelo malo.
    """
    return pico(senal) > UMBRAL_SILENCIO


def normalizar(senal: np.ndarray, objetivo: float = RMS_OBJETIVO) -> np.ndarray:
    """Lleva la señal a un RMS conocido sin llegar a saturar.

    Un micro con poca ganancia es la mitad del problema de precisión: la
    consonante queda por debajo del ruido de cuantización y el
    reconocedor se queda sin la pista que distingue "abre" de "abren".
    """
    actual = rms(senal)
    if actual <= UMBRAL_SILENCIO:
        return senal
    factor = objetivo / actual
    # Techo: si al subir se satura, la distorsión hace más daño que el
    # nivel bajo que estábamos arreglando.
    tope = pico(senal) or 1e-6
    factor = min(factor, 0.95 / tope)
    return (senal * factor).astype(np.float32)


def a_int16(senal: np.ndarray) -> bytes:
    """Los bytes que espera Vosk (PCM int16 little-endian)."""
    return (np.clip(senal, -1.0, 1.0) * 32767).astype("<i2").tobytes()


def de_int16(datos: bytes) -> np.ndarray:
    """El camino de vuelta: bytes del micro → float32 [-1, 1]."""
    return np.frombuffer(datos, dtype="<i2").astype(np.float32) / 32768.0
