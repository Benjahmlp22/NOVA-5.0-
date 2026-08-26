"""Cómo se abre el micrófono, y por qué camino.

En Windows el mismo micrófono se puede abrir por cuatro APIs distintas y
**no suenan igual**.  Medido en esta máquina el 26/08 con un G435:

    WASAPI compartido   audio inservible: el 21% de la energía del tramo
                        hablado en la banda de voz (lo normal es ~53%).
                        Los dos reconocedores devolvieron basura.
    MME                 usable pero con artefactos metálicos audibles.
                        WER de Vosk 22.4% donde con audio limpio da 6.1%.
    WDM-KS              no se puede abrir aquí ("Invalid device").
    WASAPI exclusivo    se salta el motor de audio de Windows entero.

Ese motor es el que aplica los efectos del sistema — supresión de ruido,
"voice clarity", cancelación de eco — y es el sospechoso de lo metálico:
esos algoritmos, cuando se pasan, dejan "ruido musical", que es
exactamente lo que se oye como robótico.  En modo exclusivo el audio
llega crudo del driver.

Segunda cosa que este módulo arregla: la captura va por **callback**, no
por `InputStream.read()` en un bucle de Python.  Da igual lo rápido que
sea el bucle, es el diseño frágil (y WDM-KS directamente no admite
lectura bloqueante).  El hilo de audio sólo copia el bloque a una cola;
todo lo demás pasa fuera.
"""

from __future__ import annotations

import logging
import queue
import time
from dataclasses import dataclass

import numpy as np

from .audio import remuestrear, rms

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


def medir_ruido(camino: Camino, segundos: float = 2.0) -> float:
    """RMS del ruido de fondo, a 16 kHz."""
    g = _Grabadora(camino)
    with g._flujo():  # noqa: SLF001
        time.sleep(segundos)
    return rms(remuestrear(g._vaciar(), camino.tasa))  # noqa: SLF001


def grabar_hasta_silencio(
    camino: Camino,
    umbral: float,
    *,
    silencio_fin: float = 0.8,
    espera_inicio: float = 6.0,
    maximo: float = 12.0,
) -> tuple[np.ndarray, set[str]]:
    """Graba hasta que el hablante se calla. Devuelve la señal a 16 kHz.

    El corte lo decide el silencio y no un cronómetro, que es el mismo
    criterio que usará NOVA en marcha: así, grabar sirve además para ver
    si el umbral está bien puesto para este micro y esta sala.
    """
    g = _Grabadora(camino)
    trozos: list[np.ndarray] = []
    hablando = False
    silencio = 0.0
    t0 = time.monotonic()

    with g._flujo():  # noqa: SLF001
        while True:
            try:
                bruto = g.cola.get(timeout=1.0)
            except queue.Empty:
                break
            bloque = bruto.reshape(-1).astype(np.float32) / 32768.0
            trozos.append(bloque)

            if rms(bloque) >= umbral:
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

    if not trozos:
        return np.zeros(0, dtype=np.float32), g.avisos
    return remuestrear(np.concatenate(trozos), camino.tasa), g.avisos
