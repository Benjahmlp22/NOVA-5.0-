"""Cómo se abre el micrófono, y por qué camino.

**La captura va por callback, nunca por `InputStream.read()` en un bucle
de Python.**  Esto no es una preferencia de estilo: es lo único que
separó un corpus inservible de uno perfecto.

Historia, porque el síntoma apuntaba a cualquier otro sitio.  El 26/08 se
grabaron 20 frases con un bucle de lectura bloqueante y salieron
inservibles: sólo el 21% de la energía del tramo hablado caía en la banda
de voz (lo normal es ~53%) y los dos reconocedores devolvían basura —
Whisper contestaba "¡Suscríbete!", que es lo que alucina con ruido.  Por
MME el mismo bucle daba audio audible pero metálico, con Vosk al 22.4% de
WER donde con audio limpio da 6.1%.

Se sospechó del motor de audio de Windows y sus efectos (supresión de
ruido, "voice clarity"), que al pasarse dejan "ruido musical" y suenan
exactamente así.  **Era falso.**  Con este módulo, la misma frase por los
cuatro caminos —MME, DirectSound, WASAPI compartido y WASAPI exclusivo—
da 61-67% de energía en banda de voz y Vosk la transcribe entera y
correcta por los cuatro.  Lo que estaba roto era el bucle de lectura.

Por qué: el hilo de audio no puede esperar a que Python vuelva a pedirle
el siguiente bloque.  Con callback, el driver entrega cuando toca y lo
único que ocurre en ese hilo es copiar a una cola.  WASAPI, que es el más
sensible al timing, era también el que peor salía; WDM-KS directamente no
admite lectura bloqueante.

Queda entonces que los cuatro caminos valen.  Se enumeran probando a
abrirlos de verdad, no leyendo la tabla de capacidades: un dispositivo
puede anunciar 48 kHz y luego fallar con "Invalid device".
"""

from __future__ import annotations

import logging
import queue
import time
from dataclasses import dataclass

import numpy as np

from .audio import SAMPLE_RATE, remuestrear, rms

log = logging.getLogger("nova.voice.captura")

BLOQUE_MS = 50


@dataclass(frozen=True)
class Camino:
    """Una forma concreta de abrir un micrófono."""

    nombre: str
    device: int
    tasa: int
    exclusivo: bool = False

    @property
    def etiqueta(self) -> str:
        return f"{self.nombre}{' (exclusivo)' if self.exclusivo else ''}"

    def extra(self):
        if not self.exclusivo:
            return None
        import sounddevice as sd

        return sd.WasapiSettings(exclusive=True)


def _api(indice: int) -> str:
    import sounddevice as sd

    return sd.query_hostapis()[indice]["name"]


def caminos_para(texto_micro: str = "") -> list[Camino]:
    """Los caminos que de verdad se pueden abrir para ese micrófono.

    Se prueban abriendo el flujo, no leyendo la tabla de capacidades: un
    dispositivo puede anunciar 48 kHz y luego fallar con "Invalid device"
    (le pasa a WDM-KS aquí).
    """
    import sounddevice as sd

    salida: list[Camino] = []
    filtro = texto_micro.lower()
    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] <= 0:
            continue
        if filtro and filtro not in d["name"].lower():
            continue
        api = _api(d["hostapi"])
        tasa = int(d["default_samplerate"])
        variantes = [Camino(f"{api} · {d['name'][:28]}", i, tasa)]
        if api == "Windows WASAPI":
            variantes.append(Camino(f"{api} · {d['name'][:28]}", i, tasa, exclusivo=True))
        for camino in variantes:
            if _se_puede_abrir(camino):
                salida.append(camino)
    return salida


def camino_por_defecto() -> Camino | None:
    """El micrófono predeterminado de Windows, si se puede abrir.

    Atajo para el arranque: `caminos_para()` prueba a abrir TODOS los
    dispositivos de entrada, y cada intento fallido de WDM-KS cuesta
    segundos — 10 s medidos en esta máquina, que son 10 s de NOVA sorda.
    Aquí se prueba sólo el que el usuario ya eligió en Windows, y la
    enumeración completa queda como reserva.
    """
    import sounddevice as sd

    try:
        indice = sd.default.device[0]
        info = sd.query_devices(indice, "input")
    except Exception:  # noqa: BLE001
        log.debug("no hay dispositivo de entrada predeterminado", exc_info=True)
        return None

    camino = Camino(
        f"{_api(info['hostapi'])} · {info['name'][:28]}",
        indice,
        int(info["default_samplerate"]),
    )
    return camino if _se_puede_abrir(camino) else None


def _se_puede_abrir(camino: Camino) -> bool:
    import sounddevice as sd

    try:
        with sd.InputStream(
            samplerate=camino.tasa,
            blocksize=int(camino.tasa * BLOQUE_MS / 1000),
            channels=1,
            dtype="int16",
            device=camino.device,
            callback=lambda *a: None,
            extra_settings=camino.extra(),
        ):
            pass
        return True
    except Exception as exc:  # noqa: BLE001
        log.debug("no se puede abrir %s: %s", camino.etiqueta, exc)
        return False


class _Grabadora:
    """Captura por callback. El hilo de audio sólo copia y se va."""

    def __init__(self, camino: Camino) -> None:
        self.camino = camino
        self.cola: queue.Queue = queue.Queue()
        self.avisos: set[str] = set()

    def _callback(self, indata, frames, tiempo, estado):  # noqa: ANN001, ARG002
        if estado:
            # Overflow aquí significa muestras perdidas, y muestras
            # perdidas suenan a corte. Se anota para poder decirlo.
            self.avisos.add(str(estado))
        self.cola.put(indata.copy())

    def _flujo(self):
        import sounddevice as sd

        return sd.InputStream(
            samplerate=self.camino.tasa,
            blocksize=int(self.camino.tasa * BLOQUE_MS / 1000),
            channels=1,
            dtype="int16",
            device=self.camino.device,
            callback=self._callback,
            extra_settings=self.camino.extra(),
        )

    def _vaciar(self) -> np.ndarray:
        trozos = []
        while not self.cola.empty():
            trozos.append(self.cola.get())
        if not trozos:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(trozos).reshape(-1).astype(np.float32) / 32768.0


# Techo del umbral de voz. Habla normal a un palmo del micro mide RMS
# 0.015-0.026 en esta máquina; un umbral por encima de eso no se dispara
# nunca y la grabación se queda esperando a alguien que ya está hablando.
# Pasó de verdad: una calibración devolvió 0.0425 y las tres grabaciones
# salieron mudas.
UMBRAL_MAXIMO = 0.012
UMBRAL_MINIMO = 0.004


@dataclass
class Grabacion:
    """Lo grabado y, sobre todo, si de verdad hay voz dentro."""

    senal: np.ndarray
    voz_detectada: bool
    pico_bloque: float
    umbral: float
    avisos: set[str]

    @property
    def segundos(self) -> float:
        return len(self.senal) / SAMPLE_RATE


def medir_ruido(camino: Camino, segundos: float = 1.5) -> float:
    """Nivel del ruido de fondo, en RMS por bloque.

    Se usa la MEDIANA de los bloques, no el RMS del total: si durante la
    calibración cae un golpe de teclado o una silla, el RMS global sube y
    el umbral que sale de ahí ya no lo alcanza ninguna voz. La mediana ni
    se entera.
    """
    g = _Grabadora(camino)
    with g._flujo():  # noqa: SLF001
        time.sleep(segundos)
    bloques = []
    while not g.cola.empty():
        b = g.cola.get().reshape(-1).astype(np.float32) / 32768.0
        if b.size:
            bloques.append(rms(b))
    if not bloques:
        return 0.0
    return float(np.median(bloques))


def umbral_de_voz(ruido: float) -> float:
    """Umbral acotado por arriba y por abajo. Ver UMBRAL_MAXIMO."""
    return float(min(max(ruido * 3.0, UMBRAL_MINIMO), UMBRAL_MAXIMO))


def grabar_hasta_silencio(
    camino: Camino,
    umbral: float,
    *,
    silencio_fin: float = 0.8,
    espera_inicio: float = 8.0,
    maximo: float = 15.0,
) -> Grabacion:
    """Graba hasta que el hablante se calla. Devuelve la señal a 16 kHz.

    El corte lo decide el silencio y no un cronómetro, que es el mismo
    criterio que usará NOVA en marcha: así, grabar sirve además para ver
    si el umbral está bien puesto para este micro y esta sala.

    Devuelve SIEMPRE lo grabado, con `voz_detectada` diciendo si el
    umbral llegó a dispararse. Antes, agotar la espera se reportaba como
    grabación correcta: salían tres ficheros de exactamente 8 s, sin voz
    dentro, marcados con un visto. Un fallo silencioso es peor que uno
    ruidoso.
    """
    g = _Grabadora(camino)
    trozos: list[np.ndarray] = []
    hablando = False
    silencio = 0.0
    pico_bloque = 0.0
    t0 = time.monotonic()

    with g._flujo():  # noqa: SLF001
        while True:
            try:
                bruto = g.cola.get(timeout=1.0)
            except queue.Empty:
                break
            bloque = bruto.reshape(-1).astype(np.float32) / 32768.0
            trozos.append(bloque)

            nivel = rms(bloque)
            pico_bloque = max(pico_bloque, nivel)
            if nivel >= umbral:
                hablando = True
                silencio = 0.0
            elif hablando:
                silencio += BLOQUE_MS / 1000
                if silencio >= silencio_fin:
                    break

            transcurrido = time.monotonic() - t0
            if not hablando and transcurrido > espera_inicio:
                break
            if transcurrido > maximo:
                break

    senal = (
        remuestrear(np.concatenate(trozos), camino.tasa)
        if trozos
        else np.zeros(0, dtype=np.float32)
    )
    return Grabacion(
        senal=senal,
        voz_detectada=hablando,
        pico_bloque=pico_bloque,
        umbral=umbral,
        avisos=g.avisos,
    )
