"""NOVA — orquestación.

Une voz, cerebro y overlays. Un solo proceso, varios hilos:

    Hilo Qt (principal)   pinta orbe y glow. Nunca bloquea.
    Hilo de trabajo       LLM + herramientas (tardan segundos).
    Hilo de escucha       Vosk, dentro de VoiceListener.
    Hilo de voz (TTS)     pyttsx3, dentro de Speaker.

Los tres hilos que no son el de Qt avisan por señales, nunca llamando
a un método que pinte un widget directamente: tocar un QWidget desde
un hilo suelto es la forma clásica de que una app Qt se cuelgue o se
caiga sin explicación (y sin traceback que lo delate).

Flujo (conversación continua, no de un solo turno):
    dormida → "NOVA" → chime + glow + escucha → comando → pensando
    → herramientas → responde (voz) → sigue escuchando sin repetir "NOVA"
    → ... → despedida o silencio prolongado → vuelve a dormida
"""

from __future__ import annotations

import logging
import sys
import threading
import time

from PyQt5.QtCore import QObject, QThread, pyqtSignal
from PyQt5.QtWidgets import QApplication

from .config import CONFIG
from .core.agent import Agent
from .core.awareness import Awareness
from .core.conversation import Conversation, build_system_prompt
from .core.polish import recortar_para_voz
from .llm.ollama import OllamaClient
from .tools import PendingConfirmation, build_registry
from .ui import GlowBorder, Orb
from .voice import Speaker, VoiceListener, play_chime

log = logging.getLogger("nova.app")

# Lo que NOVA contesta al oír su nombre. Corto: es un acuse de recibo,
# no una frase.
SALUDOS = ["Dime.", "Te escucho.", "¿Sí?"]
DESPEDIDAS = ["Hasta luego.", "Aquí estaré.", "Vale."]

# Afirmaciones que valen como "sí" para una acción pendiente. Se acepta
# ruido alrededor ("si dale wacho") porque hablando nadie dice "sí" seco.
_AFIRMA = ("si", "sí", "claro", "dale", "vale", "ok", "okey", "hazlo",
           "adelante", "venga", "confirmo", "correcto", "eso")


def va_a_sonar(*, hablar: bool, silenciada: bool, tts_activo: bool) -> bool:
    """¿Este texto va a producir audio de verdad?

    Existe como función suelta para poder probarla sin montar Qt. La
    tercera condición es la que faltaba en NOVA4: con `NOVA_TTS=false`,
    `Speaker.say()` vuelve sin disparar `on_start`/`on_end`, así que nadie
    movía el orbe nunca más y se quedaba clavado en "pensando" para
    siempre. El bug no se veía porque casi nadie apaga la voz.
    """
    return hablar and not silenciada and tts_activo


def estado_en_reposo(*, ocupada: bool, despierta: bool) -> str:
    """Qué estado le toca al orbe cuando NOVA termina de hablar."""
    if ocupada:
        return "pensando"
    return "escucha" if despierta else "dormida"


class _Worker(QObject):
    """Ejecuta el turno pesado fuera del hilo de la interfaz."""

    listo = pyqtSignal(str, list)          # texto, herramientas usadas
    pendiente = pyqtSignal(object)          # PendingConfirmation
    estado = pyqtSignal(str, str)           # etapa, detalle

    def __init__(self, agent: Agent, conv: Conversation, awareness: Awareness) -> None:
        super().__init__()
        self.agent = agent
        self.conv = conv
        self.awareness = awareness

    def procesar(self, mensaje: str) -> None:
        prompt = build_system_prompt(self.awareness.snapshot())
        respuesta = self.agent.run(prompt, self.conv.history(), mensaje)

        self.conv.add_user(mensaje)
        if respuesta.pending is not None:
            self.conv.add_assistant(respuesta.text)
            self.pendiente.emit(respuesta.pending)
            return
        self.conv.add_assistant(respuesta.text)
        self.listo.emit(respuesta.text, respuesta.tools_used)

    def confirmar(self, pendiente: PendingConfirmation) -> None:
        resultado = self.agent.confirm(pendiente)
        self.conv.add_assistant(resultado.message)
        self.listo.emit(resultado.message, [pendiente.tool])


class Nova(QObject):
    """La aplicación. Se crea, se arranca y vive en la bandeja del sistema."""

    _procesar = pyqtSignal(str)
    _confirmar = pyqtSignal(object)

    # VoiceListener y Speaker llaman a sus callbacks desde SUS PROPIOS
    # hilos (no el de Qt). Tocar un QWidget desde ahí es exactamente lo
    # que este módulo dice que hay que evitar — así que estos callbacks
    # no llaman a los métodos directamente, emiten una señal. Qt la
    # encola sola hacia el hilo principal porque Nova vive ahí.
    _voz_despierta = pyqtSignal()
    _voz_comando = pyqtSignal(str)
    _voz_dormir = pyqtSignal(str)
    _voz_lista = pyqtSignal()
    _voz_error = pyqtSignal(str)
    _habla_inicio = pyqtSignal()
    _habla_fin = pyqtSignal()

    def __init__(self, app: QApplication) -> None:
        super().__init__()
        self.app = app
        CONFIG.ensure_dirs()

        # ── Cerebro ──────────────────────────────────────────────────
        self.llm = OllamaClient(
            CONFIG.ollama_url,
            CONFIG.model,
            keep_alive=CONFIG.keep_alive,
            temperature=CONFIG.temperature,
            max_tokens=CONFIG.max_tokens,
            timeout=CONFIG.request_timeout,
        )
        self.tools = build_registry(CONFIG.confirm_policy)
        self.conv = Conversation(CONFIG.history_turns)
        self.awareness = Awareness()
        self.agent = Agent(
            self.llm,
            self.tools,
            max_rounds=CONFIG.max_rounds,
            on_status=lambda etapa, detalle: self._worker.estado.emit(etapa, detalle),
        )

        # ── Interfaz ─────────────────────────────────────────────────
        self.orb = Orb(on_quit=self.salir, on_toggle_mute=self.alternar_voz)
        self.glow = GlowBorder()

        # ── Voz ──────────────────────────────────────────────────────
        # Los callbacks emiten señales (ver arriba) en vez de llamar a
        # los métodos directamente: esos métodos pintan widgets, y este
        # objeto vive en el hilo de voz/TTS, no en el de Qt.
        self.speaker = Speaker(
            rate=CONFIG.tts_rate,
            enabled=CONFIG.tts_enabled,
            on_start=self._habla_inicio.emit,
            on_end=self._habla_fin.emit,
        )
        self.listener = VoiceListener(
            CONFIG.vosk_model,
            CONFIG.wake_word,
            device=CONFIG.mic_device,
            awake_timeout_s=CONFIG.awake_timeout_s,
            on_wake=self._voz_despierta.emit,
            on_command=self._voz_comando.emit,
            on_sleep=self._voz_dormir.emit,
            on_ready=self._voz_lista.emit,
            on_error=self._voz_error.emit,
        )
        self._voz_despierta.connect(self._al_despertar)
        self._voz_comando.connect(self._al_comando)
        self._voz_dormir.connect(self._al_dormir)
        self._voz_lista.connect(self._al_voz_lista)
        self._voz_error.connect(self._al_voz_error)
        self._habla_inicio.connect(self._hablando_inicio)
        self._habla_fin.connect(self._hablando_fin)

        # ── Hilo de trabajo ──────────────────────────────────────────
        self._hilo = QThread()
        self._worker = _Worker(self.agent, self.conv, self.awareness)
        self._worker.moveToThread(self._hilo)
        self._procesar.connect(self._worker.procesar)
        self._confirmar.connect(self._worker.confirmar)
        self._worker.listo.connect(self._al_responder)
        self._worker.pendiente.connect(self._al_pendiente)
        self._worker.estado.connect(self._al_estado)

        self._pendiente: PendingConfirmation | None = None
        self._ocupada = False
        self._voz_silenciada = False

    # ── Arranque / apagado ───────────────────────────────────────────

    def start(self) -> None:
        self._hilo.start()
        self.awareness.start()
        self.speaker.start()

        self.orb.colocar(CONFIG.orb_corner)
        self.orb.show()

        if not self.llm.available():
            log.warning("Ollama no responde en %s", CONFIG.ollama_url)
            self._decir(
                "No encuentro Ollama. Ábrelo y vuelvo a estar operativa.",
                estado="apagada",
            )
        else:
            self._precalentar_modelo()

        # "preparando" hasta que el modelo de voz esté cargado de verdad.
        # start() ya no bloquea, así que aquí NOVA todavía no oye nada:
        # decir "dormida" en este punto sería mentirle al usuario durante
        # los segundos que tarde la carga (42 s medidos con el es-0.42).
        self.orb.set_estado("preparando")
        if not self.listener.start():
            self._al_voz_error(self.listener.error)

    def _al_voz_lista(self) -> None:
        self.orb.set_estado("dormida")
        log.info("NOVA lista. Di «%s».", CONFIG.wake_word)

    def _al_voz_error(self, mensaje: str) -> None:
        self.orb.set_estado("apagada")
        log.error("Voz no disponible: %s", mensaje)
        self._decir(f"Voz no disponible: {mensaje}", estado="apagada", hablar=False)

    def _precalentar_modelo(self) -> None:
        """Paga la carga en frío del modelo al arrancar, no al primer "NOVA".

        En un PC ajustado de recursos, leer ~2 GB de disco a VRAM la
        primera vez puede tardar bastantes segundos (mucho más si el
        antivirus escanea el archivo del modelo al vuelo). Haciéndolo
        aquí, en un hilo aparte durante el arranque, esa espera ocurre
        en segundo plano mientras aparece el orbe — no cuando el usuario
        ya está esperando una respuesta.
        """

        def _tarea() -> None:
            t0 = time.monotonic()
            try:
                self.llm.chat([{"role": "user", "content": "hola"}])
                log.info("Modelo precalentado en %.1fs", time.monotonic() - t0)
            except Exception:
                log.debug("no pude precalentar el modelo", exc_info=True)

        threading.Thread(target=_tarea, daemon=True, name="precalentar").start()

    def salir(self) -> None:
        log.info("cerrando NOVA")
        self.listener.stop()
        self.speaker.stop()
        self.awareness.stop()
        self._hilo.quit()
        self._hilo.wait(1500)
        self.llm.close()
        self.app.quit()

    def alternar_voz(self) -> None:
        self._voz_silenciada = not self._voz_silenciada
        if self._voz_silenciada:
            self.speaker.shut_up()

    # ── Eventos de voz (llegan desde el hilo de escucha) ─────────────

    def _al_despertar(self) -> None:
        if self._ocupada:
            # Ya estaba trabajando: el usuario la interrumpe.
            self.speaker.shut_up()
        if CONFIG.chime_enabled:
            play_chime()
        self.orb.set_estado("escucha")
        if CONFIG.glow_enabled:
            self.glow.encender()
        import random

        self._decir(random.choice(SALUDOS), estado="escucha")

    def _al_comando(self, texto: str) -> None:
        self.glow.apagar()

        # ¿Está contestando a una confirmación pendiente?
        if self._pendiente is not None:
            primera = texto.strip().lower().split()[:1]
            if primera and primera[0].strip(".,!¡") in _AFIRMA:
                pendiente, self._pendiente = self._pendiente, None
                self.orb.set_estado("pensando")
                self._ocupada = True
                self._confirmar.emit(pendiente)
                return
            self._pendiente = None
            self._decir("Vale, lo dejo.")
            return

        self.orb.set_estado("pensando")
        self._ocupada = True
        self._procesar.emit(texto)

    def _al_dormir(self, motivo: str) -> None:
        self.glow.apagar()
        if not self._ocupada:
            self.orb.set_estado("dormida")
        if motivo == "despedida":
            import random

            self._decir(random.choice(DESPEDIDAS), estado="dormida")

    # ── Eventos del cerebro (llegan del hilo de trabajo) ─────────────

    def _al_estado(self, etapa: str, detalle: str) -> None:
        self.orb.set_estado("pensando")

    def _al_responder(self, texto: str, herramientas: list) -> None:
        self._ocupada = False
        self._decir(texto)

    def _al_pendiente(self, pendiente: object) -> None:
        self._ocupada = False
        self._pendiente = pendiente  # type: ignore[assignment]
        resumen = getattr(pendiente, "summary", "")
        self._decir(f"¿Confirmas que quiero {resumen}?" if resumen else "¿Lo confirmo?")

    # ── Salida ───────────────────────────────────────────────────────

    def _decir(
        self,
        texto: str,
        *,
        estado: str = "",
        hablar: bool = True,
    ) -> None:
        """Dice algo en alto y deja el orbe en un estado coherente.

        `texto` llega entero — es lo que se guarda y lo que verán los
        subtítulos. Al TTS va recortado: el modelo se salta el "1 o 2
        frases" del prompt y 200 caracteres son 12 s hablando con el
        micrófono mudo (ver `polish.recortar_para_voz`).
        """
        if va_a_sonar(
            hablar=hablar,
            silenciada=self._voz_silenciada,
            tts_activo=self.speaker.enabled,
        ):
            self.speaker.say(recortar_para_voz(texto))
            return

        # Si no va a sonar, nadie va a llamar a `_hablando_fin`: hay que
        # cerrar el ciclo a mano o el orbe se queda como estuviera.
        self._reposo(estado)

    def _reposo(self, estado: str = "") -> None:
        """Deja orbe y glow como toca cuando NOVA deja de hablar."""
        if estado:
            self.orb.set_estado(estado)
            return
        siguiente = estado_en_reposo(ocupada=self._ocupada, despierta=self.listener.awake)
        if siguiente == "escucha" and CONFIG.glow_enabled:
            # La conversación sigue abierta: se nota en el glow que no
            # hace falta repetir "NOVA" para el siguiente turno.
            self.glow.encender()
        self.orb.set_estado(siguiente)

    def _hablando_inicio(self) -> None:
        # Mientras NOVA habla, el micrófono se ignora: si no, se oye a sí
        # misma y se despierta sola en bucle.
        self.listener.mute()
        self.orb.set_estado("hablando")

    def _hablando_fin(self) -> None:
        self.listener.unmute()
        self._reposo()


def run() -> int:
    """Punto de entrada: monta la app Qt y entra en el bucle de eventos."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(CONFIG.log_file, encoding="utf-8"),
        ],
    )
    for ruidoso in ("httpx", "httpcore", "urllib3"):
        logging.getLogger(ruidoso).setLevel(logging.WARNING)

    app = QApplication(sys.argv)
    app.setApplicationName("NOVA")
    # Sin ventanas visibles no debe morir: NOVA vive en overlays.
    app.setQuitOnLastWindowClosed(False)

    nova = Nova(app)
    nova.start()
    return app.exec_()
