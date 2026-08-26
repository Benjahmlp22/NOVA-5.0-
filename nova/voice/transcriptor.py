"""Etapa 2: entender de verdad lo que se ha dicho.

faster-whisper en la GPU, con Vosk de reserva si no hay CUDA.  Medido el
26/08 sobre 20 órdenes reales grabadas hablando:

    Vosk es-0.42                17.9%  10/20  0.76 s
    whisper small               16.1%  10/20  0.30 s
    whisper small + vocabulario  9.3%  11/20  0.32 s
    whisper medium              12.9%  13/20  0.61 s
    whisper medium + vocabulario 3.5%  17/20  0.50 s
    whisper large-v3-turbo      24.1%   8/20  0.61 s

El "+ vocabulario" es la mitad del resultado y no cuesta latencia: es un
`initial_prompt` con las palabras que NOVA oye a diario.  Sin él, Whisper
escribía "blog de notas", "calor favorito" y "cuánta de morir a RAM" — no
son errores de audio, son grafías que el decodificador no esperaba.

Y lleva el nombre EN CONTEXTO DE NOMBRE ("NOVA, abre Discord"), que es lo
que permite distinguir "nova" de "no va" — la misma secuencia de fonemas
en español, imposible de separar sin modelo de lenguaje.  Con eso,
small acierta las 8 llamadas de prueba y no se come ninguna de las 6
trampas; Vosk, que no tiene contexto, no pasa de 5/8 con 3 falsas.
"""

from __future__ import annotations

import json
import logging
import time

import numpy as np

from .audio import a_int16, normalizar

log = logging.getLogger("nova.voice.transcriptor")

PROMPT_NOVA = (
    "Órdenes habladas al asistente NOVA en español. Se le llama por su "
    "nombre: NOVA, abre Discord. NOVA, qué hora es. Oye, NOVA. "
    "Vocabulario habitual: Discord, Chrome, Spotify, Steam, WhatsApp, "
    "Visual Studio Code, bloc de notas, captura de pantalla, memoria RAM, "
    "volumen, RTX 4070, vatios, fuente de alimentación, carpeta, archivo."
)


class Transcriptor:
    """Convierte audio en texto. Whisper si puede, Vosk si no."""

    def __init__(
        self,
        *,
        modelo: str = "medium",
        compute_type: str = "int8_float16",
        device: str = "auto",
        prompt: str = PROMPT_NOVA,
        vosk_model=None,
    ) -> None:
        self.nombre_modelo = modelo
        self.compute_type = compute_type
        self.device_pedido = device
        self.prompt = prompt
        self.vosk_model = vosk_model
        self._whisper = None
        self._vosk = None
        self.device = ""
        self.error = ""

    @property
    def motor(self) -> str:
        if self._whisper is not None:
            return f"faster-whisper {self.nombre_modelo} ({self.device})"
        if self._vosk is not None:
            return f"Vosk {getattr(self.vosk_model, 'name', '?')} (reserva)"
        return "sin motor"

    # ── Carga ────────────────────────────────────────────────────────

    @property
    def cargado(self) -> bool:
        return self._whisper is not None or self._vosk is not None

    def cargar(self) -> bool:
        # Idempotente: en marcha se carga ANTES de montar Qt (ver
        # `nova/voice/cuda.py`), y el hilo de voz vuelve a pedirlo.
        if self.cargado:
            return True
        if self._cargar_whisper():
            return True
        log.warning("faster-whisper no disponible; tiro de Vosk, que entiende peor")
        return self._cargar_vosk()

    def _cargar_whisper(self) -> bool:
        try:
            from .cuda import hay_gpu, preparar_dlls

            preparar_dlls()
            device = self.device_pedido
            if device == "auto":
                device = "cuda" if hay_gpu() else "cpu"
            # En CPU, int8_float16 no existe: hay que bajar a int8 o
            # CTranslate2 falla al construir el modelo.
            compute = self.compute_type if device == "cuda" else "int8"

            from faster_whisper import WhisperModel

            t0 = time.monotonic()
            self._whisper = WhisperModel(
                self.nombre_modelo, device=device, compute_type=compute
            )
            self.device = device
            log.info(
                "Whisper %s cargado en %s (%s) en %.1fs",
                self.nombre_modelo, device, compute, time.monotonic() - t0,
            )
            return True
        except Exception as exc:  # noqa: BLE001
            log.info("no pude cargar faster-whisper: %s", exc)
            self._whisper = None
            return False

    def _cargar_vosk(self) -> bool:
        if self.vosk_model is None:
            self.error = "no hay ni faster-whisper ni modelo de Vosk"
            return False
        try:
            from vosk import Model, SetLogLevel

            SetLogLevel(-1)
            t0 = time.monotonic()
            self._vosk = Model(str(self.vosk_model))
            log.info("Vosk de reserva cargado en %.1fs", time.monotonic() - t0)
            return True
        except Exception as exc:  # noqa: BLE001
            self.error = f"tampoco pude cargar Vosk: {exc}"
            log.error(self.error)
            return False

    def precalentar(self) -> None:
        """Primera inferencia en vacío: la carga perezosa se paga ahora.

        La primera llamada a un modelo recién cargado reserva búferes en
        la GPU y tarda bastante más que las siguientes. Pagarlo al
        arrancar evita que ese coste caiga sobre la primera orden real.
        """
        try:
            # Ruido flojo y no silencio: con ceros, Whisper no encuentra
            # nada, recorre toda la escalera de temperaturas buscando algo
            # y tarda 8 s en alucinar "¡Suscríbete al canal!". Con algo de
            # señal encima se resuelve en una pasada.
            ruido = (np.random.default_rng(0).normal(0, 0.01, 16000)).astype(np.float32)
            self.transcribir(ruido)
        except Exception:  # noqa: BLE001
            log.debug("no pude precalentar el transcriptor", exc_info=True)

    # ── Uso ──────────────────────────────────────────────────────────

    def transcribir(self, senal: np.ndarray) -> str:
        if senal.size == 0:
            return ""
        # Normalizar aquí y no en el que llama: los dos motores rinden
        # peor con señal floja, y así nadie se olvida de hacerlo.
        senal = normalizar(senal)

        if self._whisper is not None:
            return self._con_whisper(senal)
        if self._vosk is not None:
            return self._con_vosk(senal)
        return ""

    def _con_whisper(self, senal: np.ndarray) -> str:
        t0 = time.monotonic()
        segmentos, _info = self._whisper.transcribe(
            senal,
            language="es",   # fijo: detectarlo cuesta una pasada y falla en frases cortas
            beam_size=5,
            initial_prompt=self.prompt or None,
            # Cada orden es independiente. Arrastrar la anterior hace que
            # una transcripción mala contamine la siguiente, y los turnos
            # de un asistente no son un texto continuo.
            condition_on_previous_text=False,
        )
        texto = " ".join(s.text for s in segmentos).strip()
        log.debug("whisper %.2fs (%.1fs de audio): %r",
                  time.monotonic() - t0, senal.size / 16000, texto)
        return texto

    def _con_vosk(self, senal: np.ndarray) -> str:
        from vosk import KaldiRecognizer

        rec = KaldiRecognizer(self._vosk, 16000)
        rec.SetWords(False)
        rec.AcceptWaveform(a_int16(senal))
        try:
            return str(json.loads(rec.FinalResult() or "{}").get("text") or "")
        except (ValueError, TypeError):
            return ""
