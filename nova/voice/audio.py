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


# ── ¿Esto suena a voz? ───────────────────────────────────────────────
#
# Que haya señal y que el nivel sea correcto NO garantiza que sirva. El
# 26/08 el corpus entero se grabó con nivel perfecto (RMS 0.015-0.026,
# picos sanos) y los dos reconocedores devolvieron basura: el 67-81% de
# la energía del tramo hablado estaba por debajo de 300 Hz, contra un
# 17-32% en audio que sí se transcribe. Nada avisó hasta las 20 frases.

# La banda donde vive la inteligibilidad. El teléfono lleva un siglo
# usando 300-3400 Hz porque es donde están los formantes que distinguen
# una palabra de otra.
BANDA_VOZ = (300, 3400)

# Por debajo de esto, el tramo hablado es retumbe con algo de voz dentro.
#
# Medido sobre 20 frases de cada tipo el 26/08:
#
#     audio que se transcribe bien   min 34.8%   mediana 53.0%   max 82.7%
#     audio que dio basura           min 14.2%   mediana 21.2%   max 66.2%
#
# Las colas se solapan, así que esto es un olor, no un veredicto: una
# frase suelta por debajo del umbral puede ser normal. Lo que no es
# normal es que la MEDIANA de una sesión entera caiga aquí — de ahí que
# el grabador avise también al final, que es la señal fuerte.
FRACCION_VOZ_MINIMA = 0.30


def fraccion_en_banda_de_voz(senal: np.ndarray, sr: int = SAMPLE_RATE) -> float:
    """Qué parte de la energía cae en 300-3400 Hz."""
    if senal.size < 256:
        return 0.0
    ventana = senal * np.hanning(senal.size)
    espectro = np.abs(np.fft.rfft(ventana)) ** 2
    freqs = np.fft.rfftfreq(senal.size, 1 / sr)
    total = float(espectro.sum())
    if total <= 0:
        return 0.0
    dentro = (freqs >= BANDA_VOZ[0]) & (freqs < BANDA_VOZ[1])
    return float(espectro[dentro].sum() / total)


def tramo_hablado(senal: np.ndarray, sr: int = SAMPLE_RATE) -> np.ndarray:
    """Recorta el silencio de los extremos.

    Medir las bandas sobre el fichero entero no vale: el silencio pesa
    más que la voz y su ruido de baja frecuencia domina el espectro. Es
    un error que ya cometí una vez analizando esto.
    """
    bloque = max(1, sr // 20)  # 50 ms
    if senal.size < bloque * 2:
        return senal
    niveles = np.array([
        np.sqrt(np.mean(senal[i:i + bloque] ** 2))
        for i in range(0, senal.size - bloque, bloque)
    ])
    if niveles.max() <= 0:
        return senal
    activos = np.where(niveles >= niveles.max() * 0.25)[0]
    if activos.size == 0:
        return senal
    return senal[activos[0] * bloque:(activos[-1] + 1) * bloque]


def diagnostico_de_voz(senal: np.ndarray, sr: int = SAMPLE_RATE) -> str:
    """Devuelve el problema encontrado, o "" si el audio tiene buena pinta."""
    if senal.size == 0:
        return "no hay audio"
    if not hay_senal(senal):
        return "silencio digital: el micro no entrega nada"

    voz = tramo_hablado(senal, sr)
    fraccion = fraccion_en_banda_de_voz(voz, sr)
    if fraccion < FRACCION_VOZ_MINIMA:
        return (
            f"sólo el {fraccion * 100:.0f}% de la energía está en la banda de voz "
            f"({BANDA_VOZ[0]}-{BANDA_VOZ[1]} Hz); suena a retumbe, no a habla"
        )
    if rms(voz) < 0.005:
        return "nivel demasiado bajo: la consonante se pierde bajo el ruido"
    return ""


# ── Captura y remuestreo ─────────────────────────────────────────────
#
# WASAPI en modo compartido SÓLO abre el micro a su tasa nativa: pedirle
# 16 kHz da "Invalid sample rate [PaErrorCode -9997]". MME y DirectSound
# sí aceptan 16 kHz... porque remuestrean ellos por dentro, y ese es
# justo el remuestreo de mala calidad del que queremos escapar.
#
# Así que se captura a la tasa del dispositivo y se baja aquí.


def tasa_de_captura(device: int | None = None) -> int:
    """A qué tasa se puede abrir de verdad ese micrófono.

    Se prefiere la NATIVA aunque el driver acepte 16 kHz: bajar nosotros
    con un filtro decente es mejor que dejárselo a PortAudio sobre MME.
    """
    import sounddevice as sd

    indice = device if device is not None else sd.default.device[0]
    info = sd.query_devices(indice, "input")
    nativa = int(info["default_samplerate"])

    for tasa in (nativa, SAMPLE_RATE):
        try:
            sd.check_input_settings(device=device, samplerate=tasa, channels=1, dtype="int16")
            return tasa
        except Exception:  # noqa: BLE001 — cualquier fallo significa "esta no"
            continue
    return nativa


def _fir_paso_bajo(corte: float, taps: int) -> np.ndarray:
    """Sinc enventanado con Hamming. `corte` va normalizado a la tasa de origen."""
    n = np.arange(taps) - (taps - 1) / 2
    h = np.sinc(2 * corte * n) * np.hamming(taps)
    return (h / h.sum()).astype(np.float32)


def _decimar(senal: np.ndarray, factor: int) -> np.ndarray:
    """Baja por un factor entero, filtrando antes.

    Sin el filtro, todo lo que hay por encima de la nueva Nyquist se
    dobla hacia abajo y aparece como ruido tonal en mitad de la voz. Es
    el error clásico de "coger una muestra de cada tres", y hace más daño
    en la consonante — la /s/ y la /f/ viven donde más alias hay — que
    es justo lo que el reconocedor necesita para distinguir palabras.
    """
    # Corte al 90% de la nueva Nyquist: deja sitio a la caída del filtro.
    h = _fir_paso_bajo(0.45 / factor, taps=32 * factor + 1)
    filtrada = np.convolve(senal, h, mode="same")
    return filtrada[::factor].astype(np.float32)


def _remuestrear_pyav(senal: np.ndarray, origen: int, destino: int) -> np.ndarray:
    """Para relaciones no enteras (44100 → 16000), con el resampler de PyAV."""
    import av

    marco = av.AudioFrame.from_ndarray(
        (np.clip(senal, -1, 1) * 32767).astype("<i2").reshape(1, -1),
        format="s16",
        layout="mono",
    )
    marco.sample_rate = origen
    remuestreador = av.audio.resampler.AudioResampler(
        format="s16", layout="mono", rate=destino
    )
    trozos = [s.to_ndarray().reshape(-1) for s in remuestreador.resample(marco)]
    # Vaciar el búfer interno o se pierde la última fracción de segundo.
    trozos += [s.to_ndarray().reshape(-1) for s in remuestreador.resample(None)]
    if not trozos:
        return np.zeros(0, dtype=np.float32)
    return (np.concatenate(trozos).astype(np.float32) / 32768.0)


def remuestrear(senal: np.ndarray, origen: int, destino: int = SAMPLE_RATE) -> np.ndarray:
    """Lleva la señal a `destino` Hz. 48000 → 16000 no necesita PyAV."""
    if origen == destino or senal.size == 0:
        return senal.astype(np.float32)
    if origen % destino == 0:
        return _decimar(senal, origen // destino)
    try:
        return _remuestrear_pyav(senal, origen, destino)
    except ImportError:
        # Último recurso: interpolación lineal. Mete alias, pero es
        # preferible a no oír nada. Se avisa porque explica un WER raro.
        log.warning(
            "sin PyAV para remuestrear %d→%d Hz: uso interpolación lineal (peor calidad)",
            origen, destino,
        )
        n = int(round(senal.size * destino / origen))
        origen_x = np.linspace(0, senal.size - 1, senal.size)
        destino_x = np.linspace(0, senal.size - 1, n)
        return np.interp(destino_x, origen_x, senal).astype(np.float32)
