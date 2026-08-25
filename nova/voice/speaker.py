"""Voz de NOVA: TTS offline y gratis (SAPI5 de Windows vía pyttsx3).

Cero coste, cero internet, cero API key.

Detalle importante: pyttsx3 no es seguro entre hilos y su `runAndWait()`
bloquea.  Por eso vive en su propio hilo con una cola, y "interrumpir"
significa parar el motor y vaciar lo pendiente — que es justo lo que
hace falta cuando el usuario vuelve a decir "NOVA" mientras habla.

Otro detalle, más raro y confirmado en directo: un mismo motor de
pyttsx3/SAPI5 solo habla de verdad la PRIMERA vez que se le pide
`runAndWait()` — a partir de la segunda, la llamada vuelve casi al
instante sin sonar nada (bug conocido del driver sapi5, no nuestro).
Por eso aquí se crea un motor nuevo para CADA frase en vez de reusar
uno solo durante toda la vida del hilo.

Y la trampa dentro de la trampa: `pyttsx3.init()` NO crea un motor
nuevo — devuelve el mismo de antes mientras alguien conserve una
referencia viva al anterior (cachea con weakrefs). Como `self._engine`
siempre apunta al último, `init()` devolvía eternamente el motor ya
mudo. Se instancia `Engine()` directamente para saltarse esa caché.
"""

from __future__ import annotations

import logging
import queue
import re
import threading
import time
from collections.abc import Callable

log = logging.getLogger("nova.voice.speaker")

_STOP = object()  # centinela de apagado


def limpiar_para_voz(texto: str) -> str:
    """Quita lo que se lee fatal en alto: markdown, rutas, URLs."""
    t = texto or ""
    t = re.sub(r"[*_`#]+", "", t)                       # markdown
    t = re.sub(r"https?://\S+", "un enlace", t)          # URLs
    t = re.sub(r"[A-Za-z]:\\[\w\\.\- ]+", "la ruta indicada", t)  # rutas Windows
    t = re.sub(r"[╭╮╰╯│─]+", " ", t)                     # cajas de texto
    t = re.sub(r"\s+", " ", t)
    return t.strip()


class Speaker:
    def __init__(
        self,
        *,
        rate: int = 195,
        enabled: bool = True,
        on_start: Callable[[], None] | None = None,
        on_end: Callable[[], None] | None = None,
    ) -> None:
        self.enabled = enabled
        self.rate = rate
        self._on_start = on_start or (lambda: None)
        self._on_end = on_end or (lambda: None)
        self._queue: queue.Queue = queue.Queue()
        self._thread: threading.Thread | None = None
        self._engine = None
        self._speaking = threading.Event()
        self._interrupt = threading.Event()
        self._voz_confirmada = False
        self.error = ""

    @property
    def speaking(self) -> bool:
        return self._speaking.is_set()

    # ── Ciclo de vida ────────────────────────────────────────────────

    def start(self) -> bool:
        if not self.enabled:
            return False
        try:
            import pyttsx3  # noqa: F401
        except ImportError:
            self.error = "Falta pyttsx3 (pip install pyttsx3)"
            log.warning(self.error)
            self.enabled = False
            return False
        self._thread = threading.Thread(target=self._run, daemon=True, name="voz-tts")
        self._thread.start()
        return True

    def stop(self) -> None:
        self._queue.put(_STOP)

    # ── Uso ──────────────────────────────────────────────────────────

    def say(self, texto: str) -> None:
        if not self.enabled:
            log.info("voz desactivada, no digo: %r", texto)
            return
        limpio = limpiar_para_voz(texto)
        if limpio:
            log.info("digo: %r", limpio)
            self._queue.put(limpio)
        else:
            log.info("nada que decir tras limpiar: %r", texto)

    def shut_up(self) -> None:
        """Corta lo que esté diciendo y descarta lo pendiente."""
        self._interrupt.set()
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break
        if self._engine is not None:
            try:
                self._engine.stop()
            except Exception:
                log.debug("no pude parar el motor de voz", exc_info=True)

    # ── Hilo ─────────────────────────────────────────────────────────

    def _run(self) -> None:
        try:
            motor_prueba = self._crear_motor()
            del motor_prueba
        except Exception as exc:
            self.error = f"No pude iniciar la voz: {exc}"
            log.exception(self.error)
            self.enabled = False
            return

        while True:
            item = self._queue.get()
            if item is _STOP:
                break
            self._interrupt.clear()
            self._speaking.set()
            self._on_start()
            t0 = time.monotonic()
            try:
                # Motor nuevo por frase: ver el porqué en el docstring del
                # módulo (reusar uno solo hace que solo la primera suene).
                self._engine = self._crear_motor()
                self._engine.say(item)
                self._engine.runAndWait()
                log.info("hablado en %.2fs (%d caracteres)", time.monotonic() - t0, len(item))
            except Exception:
                log.warning("fallo hablando", exc_info=True)
            finally:
                self._speaking.clear()
                self._on_end()

    def _crear_motor(self):
        # Engine() a pelo, NUNCA pyttsx3.init(): init() recicla el motor
        # anterior mientras self._engine lo mantenga vivo, y el reciclado
        # es justo el que ya no suena (ver docstring del módulo).
        from pyttsx3.engine import Engine

        motor = Engine()
        motor.setProperty("rate", self.rate)
        self._pick_spanish_voice(motor)
        self._forzar_dispositivo_actual(motor)
        return motor

    def _forzar_dispositivo_actual(self, motor) -> None:  # noqa: ANN001
        """SAPI5 guarda su propio dispositivo de salida (independiente del
        predeterminado general de Windows) y no siempre se entera cuando
        cambia — típico tras conectar auriculares nuevos: el resto del
        sistema (incluido el chime, que usa PortAudio) sigue al
        dispositivo actual y SAPI se queda pegado al de antes, hablando
        hacia un dispositivo que ya no escuchas. Crear un SpMMAudioOut
        nuevo obliga a SAPI a re-preguntar cuál es el predeterminado de
        AHORA en vez de usar el que tenía cacheado.
        """
        try:
            import comtypes.client

            salida = comtypes.client.CreateObject("SAPI.SpMMAudioOut")
            motor.proxy._driver._tts.AudioOutputStream = salida  # noqa: SLF001
        except Exception:
            log.debug("no pude forzar el dispositivo de audio de la voz", exc_info=True)

    def _pick_spanish_voice(self, motor) -> None:  # noqa: ANN001
        try:
            for v in motor.getProperty("voices"):
                blob = f"{v.id} {getattr(v, 'name', '')}".lower()
                if "es-" in blob or "spanish" in blob or "español" in blob or "helena" in blob:
                    motor.setProperty("voice", v.id)
                    if not self._voz_confirmada:
                        log.info("Voz: %s", getattr(v, "name", v.id))
                        self._voz_confirmada = True
                    else:
                        log.debug("Voz: %s", getattr(v, "name", v.id))
                    return
            if not self._voz_confirmada:
                log.info("Sin voz en español instalada; uso la del sistema.")
                self._voz_confirmada = True
        except Exception:
            log.debug("no pude elegir voz", exc_info=True)
