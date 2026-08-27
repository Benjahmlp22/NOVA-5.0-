"""Voz de NOVA: TTS offline y gratis (SAPI5 de Windows vía pyttsx3).

Cero coste, cero internet, cero API key.

**NOVA5 no deja hablar a SAPI: le pide el audio y lo reproduce ella.**
`save_to_file` + reproducción propia con sounddevice, en vez de
`runAndWait()`.  Suena raro hasta que se miran los números, medidos el
26/08 con la voz Sabina:

    frase de 73 caracteres → 4.89 s de audio
    sintetizarla a WAV:      0.16 - 0.47 s
    hablarla con runAndWait: ~1.4 s antes de la primera sílaba

Tres cosas se arreglan de una vez:

**La forma de onda puede ser real.**  Teniendo las muestras, el nivel que
pinta el panel es el del audio que está sonando, no un `sin()` decorativo
que finge estar vivo.  Era un requisito explícito y no había otra forma:
SAPI no expone su búfer.

**Un segundo menos de latencia.**  Sintetizar va ~9x más rápido que el
tiempo real, así que se empieza a oír antes.

**Interrumpir deja de ser una súplica.**  Antes se le pedía a SAPI que
parase; ahora se corta el flujo de audio, que es inmediato y no depende
de que el driver haga caso.

Queda de NOVA4 la razón para crear un motor nuevo por frase: un mismo
motor de pyttsx3/SAPI5 sólo funciona la PRIMERA vez, y `pyttsx3.init()`
NO crea uno nuevo — devuelve el anterior mientras alguien conserve una
referencia viva (cachea con weakrefs).  Por eso se instancia `Engine()`
directamente.
"""

from __future__ import annotations

import logging
import os
import queue
import re
import tempfile
import threading
import time
import wave
from collections.abc import Callable
from pathlib import Path

import numpy as np

from .onecore import SintetizadorOneCore, Voz

log = logging.getLogger("nova.voice.speaker")


def elegir_por_defecto(voces: list[Voz]) -> Voz | None:
    """Con cuál habla NOVA si nadie ha dicho otra cosa.

    Helena, que es la MISMA que venía usando por SAPI. Estrenar
    sintetizador no es motivo para cambiarle la voz a NOVA de un día para
    otro, y menos el sexo: eso se pide, no se hereda de en qué orden
    liste Windows sus voces (lista a Pablo primero).

    Cuál suena mejor no lo decide ninguna medida, lo decide el oído. Por
    eso se puede cambiar hablando.
    """
    if not voces:
        return None
    for v in voces:
        if "helena" in v.nombre.lower():
            return v
    espanolas = [v for v in voces if v.idioma.lower() == "es-es"]
    return (espanolas or [v for v in voces if v.es_espanol] or voces)[0]

_STOP = object()  # centinela de apagado

# Bloque de reproducción. 30 ms da ~33 medidas de nivel por segundo, que
# es más de lo que el ojo distingue en una onda y no carga nada.
BLOQUE_MS = 30


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
        on_nivel: Callable[[float], None] | None = None,
    ) -> None:
        self.enabled = enabled
        self.rate = rate
        self._on_start = on_start or (lambda: None)
        self._on_end = on_end or (lambda: None)
        # Nivel real del audio que suena ahora mismo, 0.0-1.0.
        self._on_nivel = on_nivel or (lambda nivel: None)
        self._queue: queue.Queue = queue.Queue()
        self._thread: threading.Thread | None = None
        self._speaking = threading.Event()
        self._interrupt = threading.Event()
        self._voz_confirmada = False
        self.error = ""
        # Las voces buenas de Windows (Pablo, Laura, Raul...), que SAPI
        # no enseña. Si no arrancan, `disponible` se queda en False y
        # todo sigue por SAPI como siempre.
        self._onecore = SintetizadorOneCore()
        self._voz_onecore = ""
        self._velocidad = 1.0

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
        # Se intenta OneCore, pero su fallo NO es motivo para quedarse
        # muda: SAPI está en cualquier Windows.
        if self._onecore.start():
            preferida = elegir_por_defecto(self._onecore.voces)
            if preferida:
                self._usar_onecore(preferida.nombre)
        else:
            log.info("sigo con SAPI: %s", self._onecore.error)

        self._thread = threading.Thread(target=self._run, daemon=True, name="voz-tts")
        self._thread.start()
        return True

    def stop(self) -> None:
        self._queue.put(_STOP)
        self._onecore.stop()

    # ── Qué voz se usa ───────────────────────────────────────────────

    @property
    def voz_actual(self) -> str:
        return self._voz_onecore or "Microsoft Helena Desktop"

    def voces(self) -> list[Voz]:
        """Las que se pueden elegir. Sólo las de OneCore: las de SAPI son
        dos, las dos de mujer, y existen únicamente como red de
        seguridad."""
        return list(self._onecore.voces)

    def _usar_onecore(self, nombre: str) -> bool:
        if not self._onecore.elegir(nombre):
            return False
        self._voz_onecore = nombre
        self._onecore.velocidad(self._velocidad)
        log.info("voz: %s", nombre)
        return True

    def usar_voz(self, nombre: str) -> bool:
        return self._usar_onecore(nombre)

    def set_velocidad(self, factor: float) -> bool:
        """1.0 normal. Sólo con OneCore; SAPI usa `rate` en palabras/minuto."""
        self._velocidad = max(0.5, min(2.0, factor))
        if self._voz_onecore:
            return self._onecore.velocidad(self._velocidad)
        # En SAPI la velocidad son palabras por minuto, no un factor.
        self.rate = int(195 * self._velocidad)
        return True

    # ── Uso ──────────────────────────────────────────────────────────

    def say(self, texto: str) -> None:
        if not self.enabled:
            log.info("voz desactivada, no digo: %r", texto)
            return
        limpio = limpiar_para_voz(texto)
        if limpio:
            log.info("digo: %r", limpio)
            # Una petición nueva cancela la interrupción anterior: si no,
            # el "Dime." con el que NOVA acusa el wake word se perdería,
            # porque despertar mientras habla llama antes a shut_up().
            self._interrupt.clear()
            self._queue.put(limpio)
        else:
            log.info("nada que decir tras limpiar: %r", texto)

    def shut_up(self) -> None:
        """Corta lo que esté diciendo y descarta lo pendiente.

        Cortar es inmediato: el bucle de reproducción mira este flag en
        cada bloque de 30 ms. En NOVA4 había que pedirle a SAPI que
        parara y esperar a que hiciera caso.
        """
        self._interrupt.set()
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break

    # ── Hilo ─────────────────────────────────────────────────────────

    def _run(self) -> None:
        while True:
            item = self._queue.get()
            if item is _STOP:
                break

            # `shut_up()` vacía la cola, pero esta frase ya salió de ella:
            # está en la mano de este hilo y la cola no la puede tocar.
            if self._interrupt.is_set():
                log.debug("descarto por interrupción: %r", item)
                self._interrupt.clear()
                continue

            self._speaking.set()
            self._on_start()
            t0 = time.monotonic()
            try:
                senal, sr = self._sintetizar(item)
                if senal is None:
                    log.warning("no pude sintetizar %r", item)
                else:
                    self._reproducir(senal, sr)
                    log.info(
                        "hablado en %.2fs (%d caracteres, %.1fs de audio)",
                        time.monotonic() - t0, len(item), len(senal) / sr,
                    )
            except Exception:
                log.warning("fallo hablando", exc_info=True)
            finally:
                self._speaking.clear()
                self._on_nivel(0.0)
                self._on_end()

    # ── Síntesis ─────────────────────────────────────────────────────

    def _sintetizar(self, texto: str) -> tuple[np.ndarray | None, int]:
        """Pide el audio a Windows en vez de dejarle hablar.

        Por OneCore si está (11 ms medidos por frase) y por SAPI si no
        (142 ms, porque hay que construir un motor nuevo cada vez para
        esquivar el reciclado roto de pyttsx3). Los 130 ms de diferencia
        se pagan en CADA frase, porque NOVA habla frase a frase mientras
        el modelo sigue escribiendo.
        """
        destino = os.path.join(
            tempfile.gettempdir(), f"nova_tts_{threading.get_ident()}.wav"
        )
        try:
            if self._voz_onecore and self._onecore.sintetizar(texto, Path(destino)):
                pass
            else:
                motor = self._crear_motor()
                motor.save_to_file(texto, destino)
                motor.runAndWait()
                del motor

            with wave.open(destino, "rb") as w:
                sr = w.getframerate()
                canales = w.getnchannels()
                crudo = np.frombuffer(w.readframes(w.getnframes()), "<i2")
            senal = crudo.astype(np.float32) / 32768.0
            if canales > 1:
                senal = senal.reshape(-1, canales).mean(axis=1)
            return senal, sr
        finally:
            try:
                os.remove(destino)
            except OSError:
                pass

    def _reproducir(self, senal: np.ndarray, sr: int) -> None:
        """Reproduce y va contando el nivel real de cada bloque."""
        import sounddevice as sd

        bloque = max(1, int(sr * BLOQUE_MS / 1000))
        with sd.OutputStream(samplerate=sr, channels=1, dtype="float32",
                             blocksize=bloque) as flujo:
            for i in range(0, len(senal), bloque):
                if self._interrupt.is_set():
                    log.debug("corto la reproducción a media frase")
                    break
                trozo = senal[i:i + bloque]
                if len(trozo) < bloque:
                    trozo = np.pad(trozo, (0, bloque - len(trozo)))
                flujo.write(trozo.reshape(-1, 1))
                self._on_nivel(float(np.sqrt(np.mean(trozo.astype(np.float64) ** 2))))

    def _crear_motor(self):
        # Engine() a pelo, NUNCA pyttsx3.init(): init() recicla el motor
        # anterior mientras alguien lo mantenga vivo, y el reciclado es
        # justo el que ya no funciona (ver docstring del módulo).
        from pyttsx3.engine import Engine

        motor = Engine()
        motor.setProperty("rate", self.rate)
        self._pick_spanish_voice(motor)
        return motor

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
