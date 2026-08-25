"""Escucha por micrófono: wake word "NOVA" + captura del comando.

Vosk, offline y gratis, en el mismo proceso que el resto de NOVA.  En la
versión anterior esto vivía en un backend separado y el audio viajaba en
base64 por WebSocket: el 90% de los fallos de voz venían de ese viaje
(el navegador arrancaba el audio suspendido, el bucle de recepción se
bloqueaba mientras el modelo respondía...).  Aquí el micrófono y el
reconocedor están a una llamada de función de distancia.

Máquina de estados. Conversación continua: tras un comando NO se
vuelve a DORMIDA, se sigue escuchando el siguiente turno sin repetir
"nova" — solo el silencio prolongado o una despedida cierran el turno.

    DORMIDA  ── oye "nova" ──▶  DESPIERTA  ── frase ──▶  comando (sigue DESPIERTA)
       ▲                            │
       └──── silencio / adiós ──────┘
"""

from __future__ import annotations

import json
import logging
import queue
import re
import threading
import unicodedata
from collections.abc import Callable
from pathlib import Path

log = logging.getLogger("nova.voice.listener")

SAMPLE_RATE = 16000
BLOCK = 4000  # ~0.25 s: suficientemente fino para reaccionar rápido

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


def _norm(text: str) -> str:
    out = []
    for ch in unicodedata.normalize("NFD", (text or "").lower()):
        if unicodedata.category(ch) != "Mn":
            out.append(ch)
    return " ".join("".join(out).split())


class VoiceListener:
    """Hilo de escucha continua. Avisa por callbacks; no sabe nada de UI."""

    def __init__(
        self,
        model_path: Path,
        wake_word: str = "nova",
        *,
        device: int | None = None,
        awake_timeout_s: float = 20.0,
        on_wake: Callable[[], None] | None = None,
        on_partial: Callable[[str], None] | None = None,
        on_command: Callable[[str], None] | None = None,
        on_sleep: Callable[[str], None] | None = None,
    ) -> None:
        self.model_path = Path(model_path)
        # Detección: Vosk transcribe "nova" mal muy a menudo — sobre todo
        # como "no va" (dos palabras) o "noba" — así que el patrón que
        # decide si NOVA se despierta tiene que cubrir esas variantes.
        variantes = {re.escape(wake_word), "noba", "nova"}
        patron = "|".join(sorted(variantes, key=len, reverse=True))
        if wake_word.lower() == "nova":
            patron = rf"{patron}|no\s+va"
        self.wake_re = re.compile(rf"\b({patron})\b")
        # Recorte: se usa SOLO para quitar una mención repetida de "nova"
        # del texto de un comando ya en curso (p. ej. "NOVA, ¿NOVA?, abre
        # chrome"). Aquí NO puede incluir "no va" suelto — corromperías
        # comandos reales como "dile que no va a funcionar el mando".
        self._strip_re = re.compile(rf"\b({re.escape(wake_word)}|noba)\b")
        self.device = device
        self.awake_timeout_s = awake_timeout_s

        self._on_wake = on_wake or (lambda: None)
        self._on_partial = on_partial or (lambda text: None)
        self._on_command = on_command or (lambda text: None)
        self._on_sleep = on_sleep or (lambda motivo: None)

        self._awake = False
        self._awake_bytes = 0
        self._running = False
        self._muted = threading.Event()
        self._audio_q: queue.Queue[bytes] = queue.Queue(maxsize=64)
        self._thread: threading.Thread | None = None
        self._model = None
        self.error = ""

    # ── Estado ───────────────────────────────────────────────────────

    @property
    def awake(self) -> bool:
        return self._awake

    @property
    def available(self) -> bool:
        return self._model is not None

    def mute(self) -> None:
        """Deja de procesar audio (mientras NOVA habla, para no oírse)."""
        self._muted.set()

    def unmute(self) -> None:
        self._muted.clear()

    def sleep_now(self, motivo: str = "fin") -> None:
        if self._awake:
            self._awake = False
            self._awake_bytes = 0
            self._on_sleep(motivo)

    # ── Ciclo de vida ────────────────────────────────────────────────

    def start(self) -> bool:
        if not (self.model_path / "am").exists() and not (self.model_path / "conf").exists():
            self.error = f"No encuentro el modelo de voz en {self.model_path}"
            log.error(self.error)
            return False
        try:
            from vosk import Model, SetLogLevel

            SetLogLevel(-1)  # sin el spam interno de kaldi
            self._model = Model(str(self.model_path))
        except ImportError:
            self.error = "Falta el paquete vosk (pip install vosk)"
            log.error(self.error)
            return False
        except Exception as exc:
            self.error = f"No pude cargar el modelo de voz: {exc}"
            log.exception(self.error)
            return False

        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True, name="voz")
        self._thread.start()
        log.info("Escuchando. Di «%s».", self.wake_re.pattern)
        return True

    def stop(self) -> None:
        self._running = False

    # ── Bucle ────────────────────────────────────────────────────────

    def _run(self) -> None:
        try:
            import sounddevice as sd
            from vosk import KaldiRecognizer
        except ImportError as exc:
            self.error = f"Falta una dependencia de audio: {exc}"
            log.error(self.error)
            return

        rec = KaldiRecognizer(self._model, SAMPLE_RATE)
        rec.SetWords(False)

        def callback(indata, frames, time_info, status):  # noqa: ANN001, ARG001
            if status:
                log.debug("audio: %s", status)
            try:
                self._audio_q.put_nowait(bytes(indata))
            except queue.Full:
                # Preferimos perder un bloque viejo a acumular retraso:
                # el audio atrasado ya no sirve para reaccionar.
                try:
                    self._audio_q.get_nowait()
                    self._audio_q.put_nowait(bytes(indata))
                except queue.Empty:
                    pass

        try:
            with sd.RawInputStream(
                samplerate=SAMPLE_RATE,
                blocksize=BLOCK,
                device=self.device,
                dtype="int16",
                channels=1,
                callback=callback,
            ):
                while self._running:
                    try:
                        data = self._audio_q.get(timeout=0.5)
                    except queue.Empty:
                        continue
                    if self._muted.is_set():
                        continue
                    self._feed(rec, data)
        except Exception as exc:
            self.error = f"Error con el micrófono: {exc}"
            log.exception(self.error)

    def _feed(self, rec, data: bytes) -> None:  # noqa: ANN001
        # Timeout de "despierta": se mide en audio procesado, no en reloj
        # de pared, así el silencio absoluto también cuenta.
        if self._awake:
            self._awake_bytes += len(data)
            if self._awake_bytes > SAMPLE_RATE * 2 * self.awake_timeout_s:
                self.sleep_now("silencio")

        if rec.AcceptWaveform(data):
            texto = _norm(self._read(rec.Result()))
            if texto:
                log.info("oído (final): %r", texto)
                self._on_final(texto)
        else:
            parcial = _norm(self._read(rec.PartialResult(), "partial"))
            if not parcial:
                return
            log.debug("oído (parcial): %r", parcial)
            if self._awake:
                self._on_partial(parcial)
            elif self.wake_re.search(parcial):
                # Despertar con el resultado PARCIAL: esperar al final de
                # la frase añadía ~1 s antes de que NOVA reaccionara.
                rec.Reset()
                self._wake()

    def _on_final(self, texto: str) -> None:
        if not self._awake:
            m = self.wake_re.search(texto)
            if not m:
                return
            resto = texto[m.end():].strip(" ,.")
            if resto:
                # "nova abre chrome" — despertar y comando de una tacada.
                # Se queda DESPIERTA: el siguiente turno no necesita que
                # se repita "nova" (conversación continua).
                self._wake()
                self._on_command(resto)
            else:
                self._wake()
            return

        # Despierta: esto es el comando... salvo que se esté despidiendo.
        if _DESPEDIDAS.search(texto):
            self._awake = False
            self._awake_bytes = 0
            self._on_sleep("despedida")
            return

        comando = self._strip_re.sub("", texto).strip(" ,.")
        if not comando:
            # Dijo "nova" otra vez sin nada detrás — típico al comprobar
            # si le está haciendo caso ("¿NOVA? ¿NOVA, me oyes?"). Bug
            # real visto en directo: esto dormía a NOVA en silencio, así
            # que cuando el usuario decía su orden de verdad justo
            # después, ya no había nadie escuchando. Se queda despierta
            # y vuelve a avisar en vez de rendirse.
            self._awake_bytes = 0
            self._wake()
            return

        # Se queda DESPIERTA tras el comando: conversación continua, sin
        # tener que repetir "nova" para el siguiente turno. Solo una
        # despedida explícita o el timeout de silencio la duermen.
        self._awake_bytes = 0
        self._on_command(comando)

    def _wake(self) -> None:
        self._awake = True
        self._awake_bytes = 0
        self._on_wake()

    @staticmethod
    def _read(payload: str, key: str = "text") -> str:
        try:
            return str(json.loads(payload or "{}").get(key) or "")
        except (ValueError, TypeError):
            return ""
