"""Escucha por micrófono: dos etapas, con búfer de pre-roll y VAD.

    micro (callback) → búfer circular de 1 s ──┐
                                               ├─► etapa 1: ¿han dicho "nova"?
                                               │      Vosk small + gramática
                                               │      barato, siempre encendido
                                               ▼
                                     enunciado completo (pre-roll + voz)
                                               ▼
                                        etapa 2: Whisper
                                     confirma el nombre Y transcribe
                                               ▼
                                    on_wake (chime) + on_command

Todo en el mismo proceso, a una llamada de función del micrófono, como
manda la primera decisión sagrada del proyecto.

Cuatro cosas que NOVA4 hacía mal y aquí no:

**El pre-roll.**  Antes, cuando NOVA se enteraba de que la llamaban, el
principio de la orden ya había pasado y se perdía.  Ahora el último
segundo de audio está siempre guardado, así que la frase entra completa
aunque digas "NOVA abre discord" del tirón.

**La segmentación la decide el silencio, no el reconocedor.**  En el log
del 25/07 una sola frase se partió en dos comandos ("voy a break room" +
"por favor") y NOVA respondió dos veces seguidas, veinte segundos de
monólogo.  Ahora una frase acaba cuando te callas.

**El mute no tira el audio.**  Mientras NOVA habla no se procesa nada
—si no, se oye a sí misma y se despierta sola—, pero el búfer circular
sigue llenándose.  Al terminar de hablar, el último segundo sigue ahí:
si empezaste a contestar antes de que acabara, no se pierde.

**El wake word no se decide con un regex.**  La etapa 1 abre la puerta y
la etapa 2 confirma con contexto de lenguaje; "nova" y "no va" son la
misma secuencia de fonemas y sólo el contexto las separa.

Máquina de estados (conversación continua: tras un comando NO se vuelve
a DORMIDA):

    DORMIDA ──oye algo que suena a "nova"──► CAPTURANDO ──silencio──► etapa 2
       ▲                                                                │
       │                                              ¿estaba el nombre?│
       │                                          no ◄─────────┴───────► sí
       │                                           │                     │
       └───────────────────────────────────────────┘            on_wake + comando
                                                                         │
       ┌──── silencio prolongado o despedida ◄──── DESPIERTA ◄────────────┘
"""

from __future__ import annotations

import logging
import queue
import re
import threading
import time
from collections import deque
from collections.abc import Callable
from pathlib import Path

import numpy as np

from .audio import SAMPLE_RATE, a_int16, hay_senal, rms
from .captura import (
    BLOQUE_MS,
    Camino,
    camino_por_defecto,
    caminos_para,
    medir_ruido,
    umbral_de_voz,
)
from .wake import DetectorWake, normalizar_texto

log = logging.getLogger("nova.voice.listener")

# Frases con las que el usuario cierra la conversación. No son comandos:
# no se le mandan al modelo, cierran el turno y NOVA vuelve a dormir.
_DESPEDIDAS = re.compile(
    r"\b("
    r"despues hablamos|luego hablamos|hasta luego|hasta pronto|hasta manana|"
    r"eso es todo|eso seria todo|nada mas|ya esta|listo gracias|"
    r"adios|chao|chau|nos vemos|vete|dejalo|olvidalo|"
    r"gracias nova|ya no|nada"
    r")\b"
)


class VoiceListener:
    """Hilo de escucha continua. Avisa por callbacks; no sabe nada de UI."""

    def __init__(
        self,
        wake_model: Path,
        wake_word: str = "nova",
        *,
        transcriptor=None,
        detector: DetectorWake | None = None,
        camino: Camino | None = None,
        device: int | None = None,
        exclusivo: bool = False,
        awake_timeout_s: float = 20.0,
        preroll_s: float = 1.0,
        silencio_fin_s: float = 0.7,
        max_enunciado_s: float = 12.0,
        on_wake: Callable[[bool], None] | None = None,
        on_command: Callable[[str], None] | None = None,
        on_sleep: Callable[[str], None] | None = None,
        on_ready: Callable[[], None] | None = None,
        on_error: Callable[[str], None] | None = None,
        on_escuchando: Callable[[], None] | None = None,
        on_nivel: Callable[[float], None] | None = None,
    ) -> None:
        self.wake_word = normalizar_texto(wake_word)
        self.detector = detector or DetectorWake(wake_model, self.wake_word)
        self.transcriptor = transcriptor
        self.camino = camino
        self.device = device
        self.exclusivo = exclusivo

        self.awake_timeout_s = awake_timeout_s
        self.preroll_s = preroll_s
        self.silencio_fin_s = silencio_fin_s
        self.max_enunciado_s = max_enunciado_s

        self._on_wake = on_wake or (lambda con_comando: None)
        self._on_command = on_command or (lambda t: None)
        self._on_sleep = on_sleep or (lambda m: None)
        self._on_ready = on_ready or (lambda: None)
        self._on_error = on_error or (lambda m: None)
        self._on_escuchando = on_escuchando or (lambda: None)
        # Nivel real del micro, para que la onda del panel no mienta.
        self._on_nivel = on_nivel or (lambda nivel: None)

        self._awake = False
        self._running = False
        self._muted = threading.Event()
        self._ready = threading.Event()
        self._thread: threading.Thread | None = None
        self._umbral = 0.006
        self.error = ""

        # Búfer circular: el último segundo, siempre. Se dimensiona en
        # bloques porque es la unidad en la que llega el audio.
        bloques_preroll = max(1, int(preroll_s * 1000 / BLOQUE_MS))
        self._preroll: deque[np.ndarray] = deque(maxlen=bloques_preroll)

    # ── Estado ───────────────────────────────────────────────────────

    @property
    def awake(self) -> bool:
        return self._awake

    @property
    def ready(self) -> bool:
        return self._ready.is_set()

    @property
    def available(self) -> bool:
        return self._ready.is_set()

    def mute(self) -> None:
        """Deja de procesar audio (mientras NOVA habla, para no oírse).

        El búfer circular NO se para: cuando NOVA termine, el último
        segundo sigue guardado. En NOVA4 el audio se tiraba entero y por
        eso se comía el principio de lo que dijeras justo después.
        """
        self._muted.set()

    def unmute(self) -> None:
        self._muted.clear()

    def sleep_now(self, motivo: str = "fin") -> None:
        if self._awake:
            self._awake = False
            self._on_sleep(motivo)

    # ── Ciclo de vida ────────────────────────────────────────────────

    def start(self) -> bool:
        """Arranca la escucha. No bloquea: todo se carga en su hilo."""
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True, name="voz")
        self._thread.start()
        return True

    def stop(self) -> None:
        self._running = False

    def _fallar(self, mensaje: str) -> None:
        self.error = mensaje
        log.error(mensaje)
        self._running = False
        self._ready.clear()
        try:
            self._on_error(mensaje)
        except Exception:  # noqa: BLE001
            log.debug("callback de error falló", exc_info=True)

    # ── Bucle ────────────────────────────────────────────────────────

    def _elegir_camino(self) -> Camino | None:
        if self.camino is not None:
            return self.camino

        # Camino rápido: el predeterminado de Windows. Enumerar todos los
        # dispositivos cuesta ~10 s aquí (cada WDM-KS que falla tarda lo
        # suyo), y son 10 s con NOVA sorda al arrancar.
        if self.device is None and not self.exclusivo:
            rapido = camino_por_defecto()
            if rapido is not None:
                return rapido

        caminos = caminos_para()
        if not caminos:
            return None
        if self.device is not None:
            for c in caminos:
                if c.device == self.device and c.exclusivo == self.exclusivo:
                    return c
            log.warning("el dispositivo %s no se puede abrir; busco otro", self.device)

        # El PREDETERMINADO de Windows, no el primero de la lista: el
        # primero suele ser el "Asignador de sonido", que es un
        # redirector genérico y no el micro que el usuario eligió.
        try:
            import sounddevice as sd

            por_defecto = sd.default.device[0]
            for c in caminos:
                if c.device == por_defecto:
                    return c
        except Exception:  # noqa: BLE001
            log.debug("no pude leer el dispositivo predeterminado", exc_info=True)
        return caminos[0]

    def _run(self) -> None:
        camino = self._elegir_camino()
        if camino is None:
            self._fallar("No encuentro ningún micrófono que se pueda abrir.")
            return

        if not self.detector.cargar():
            self._fallar(self.detector.error or "no pude cargar el detector de wake word")
            return
        if self.transcriptor is not None:
            # Normalmente llega ya cargado y caliente desde `run.py` — se
            # construye antes que PyQt5 por obligación (ver bootstrap).
            # Esto sólo actúa si alguien montó el listener por su cuenta.
            ya_estaba = self.transcriptor.cargado
            if not self.transcriptor.cargar():
                self._fallar(self.transcriptor.error or "no pude cargar el transcriptor")
                return
            if not ya_estaba:
                self.transcriptor.precalentar()

        try:
            ruido = medir_ruido(camino, segundos=1.0)
            self._umbral = umbral_de_voz(ruido)
            log.info("Ruido de fondo %.5f → umbral de voz %.5f", ruido, self._umbral)
        except Exception as exc:  # noqa: BLE001
            log.debug("no pude calibrar el ruido: %s", exc)

        try:
            self._escuchar(camino)
        except Exception as exc:  # noqa: BLE001
            log.exception("error en el bucle de escucha")
            self._fallar(f"Error con el micrófono: {exc}")

    def _escuchar(self, camino: Camino) -> None:
        import sounddevice as sd

        from .audio import remuestrear

        cola: queue.Queue = queue.Queue(maxsize=128)

        def callback(indata, frames, tiempo, estado):  # noqa: ANN001, ARG001
            if estado:
                log.debug("audio: %s", estado)
            try:
                cola.put_nowait(indata.copy())
            except queue.Full:
                # Preferimos perder un bloque viejo a acumular retraso:
                # el audio atrasado ya no sirve para reaccionar.
                try:
                    cola.get_nowait()
                    cola.put_nowait(indata.copy())
                except queue.Empty:
                    pass

        with sd.InputStream(
            samplerate=camino.tasa,
            blocksize=int(camino.tasa * BLOQUE_MS / 1000),
            channels=1,
            dtype="int16",
            device=camino.device,
            callback=callback,
            extra_settings=camino.extra(),
        ):
            self._ready.set()
            log.info("Escuchando por %s. Di «%s».", camino.etiqueta, self.wake_word)
            try:
                self._on_ready()
            except Exception:  # noqa: BLE001
                log.debug("callback de listo falló", exc_info=True)

            ultimo_habla = time.monotonic()
            while self._running:
                try:
                    bruto = cola.get(timeout=0.5)
                except queue.Empty:
                    continue

                bloque = remuestrear(
                    bruto.reshape(-1).astype(np.float32) / 32768.0, camino.tasa
                )
                # El búfer circular se llena SIEMPRE, incluso con NOVA
                # hablando: es lo que evita perder el principio de la
                # respuesta del usuario al terminar el TTS.
                self._preroll.append(bloque)

                if self._muted.is_set():
                    continue

                if self._awake and time.monotonic() - ultimo_habla > self.awake_timeout_s:
                    self.sleep_now("silencio")

                nivel = rms(bloque)
                self._on_nivel(nivel)
                if self._awake:
                    # Conversación continua: cualquier voz abre enunciado,
                    # sin repetir el nombre.
                    if nivel >= self._umbral:
                        ultimo_habla = time.monotonic()
                        self._capturar(cola, camino, exige_nombre=False)
                        ultimo_habla = time.monotonic()
                elif self.detector.escucha(a_int16(bloque)):
                    log.debug("etapa 1: algo suena a «%s»", self.wake_word)
                    self.detector.reiniciar()
                    self._capturar(cola, camino, exige_nombre=True)
                    ultimo_habla = time.monotonic()

    # ── Captura de un enunciado ──────────────────────────────────────

    def _capturar(self, cola: queue.Queue, camino: Camino, *, exige_nombre: bool) -> None:
        """Acumula hasta que el hablante se calla, y manda a la etapa 2."""
        from .audio import remuestrear

        try:
            self._on_escuchando()
        except Exception:  # noqa: BLE001
            log.debug("callback de escuchando falló", exc_info=True)

        trozos: list[np.ndarray] = list(self._preroll)
        silencio = 0.0
        t0 = time.monotonic()

        while self._running:
            try:
                bruto = cola.get(timeout=1.0)
            except queue.Empty:
                break
            bloque = remuestrear(
                bruto.reshape(-1).astype(np.float32) / 32768.0, camino.tasa
            )
            self._preroll.append(bloque)
            trozos.append(bloque)

            nivel = rms(bloque)
            self._on_nivel(nivel)
            if nivel >= self._umbral:
                silencio = 0.0
            else:
                silencio += BLOQUE_MS / 1000
                if silencio >= self.silencio_fin_s:
                    break
            if time.monotonic() - t0 > self.max_enunciado_s:
                log.debug("enunciado cortado por el tope de %.0fs", self.max_enunciado_s)
                break

        senal = np.concatenate(trozos) if trozos else np.zeros(0, dtype=np.float32)
        if not hay_senal(senal):
            return
        self._entender(senal, exige_nombre=exige_nombre)

    # ── Etapa 2 ──────────────────────────────────────────────────────

    def _entender(self, senal: np.ndarray, *, exige_nombre: bool) -> None:
        if self.transcriptor is None:
            return
        t0 = time.monotonic()
        texto = normalizar_texto(self.transcriptor.transcribir(senal))
        log.info("oído en %.2fs (%.1fs de audio): %r",
                 time.monotonic() - t0, senal.size / SAMPLE_RATE, texto)
        if not texto:
            return

        if exige_nombre:
            resto = self._quitar_nombre(texto)
            if resto is None:
                # La etapa 1 se equivocó: era "no va", no "nova". NOVA
                # vuelve a dormir sin haber hecho ruido — el chime no ha
                # sonado, así que el usuario ni se entera.
                log.debug("etapa 2 descarta la llamada: %r", texto)
                return
            self._awake = True
            # El aviso lleva si viene orden pegada: sin eso, NOVA saluda
            # ("Dime.") y procesa la orden a la vez, hablándose encima.
            self._on_wake(bool(resto))
            if resto:
                # "NOVA abre discord" del tirón: el pre-roll hizo que la
                # orden entera esté aquí, no sólo el nombre.
                self._on_command(resto)
            return

        if _DESPEDIDAS.search(texto):
            self.sleep_now("despedida")
            return

        comando = self._quitar_nombre(texto)
        comando = texto if comando is None else comando
        if comando:
            self._on_command(comando)

    def _quitar_nombre(self, texto: str) -> str | None:
        """Quita el nombre del principio. None si no estaba.

        Sólo cuenta como palabra suelta: "la novia de mi hermano" no
        contiene el nombre, aunque lo lleve dentro.

        Normaliza por su cuenta aunque quien llama suela hacerlo ya: es
        la comparación de la que depende despertar o no, y no puede
        romperse porque alguien la use con el texto crudo de Whisper —
        que llega puntuado ("NOVA, cierra Chrome.").
        """
        palabras = normalizar_texto(texto).split()
        if self.wake_word not in palabras:
            return None
        indice = palabras.index(self.wake_word)
        return " ".join(palabras[indice + 1:]).strip(" ,.")
