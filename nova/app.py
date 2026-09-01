"""NOVA — orquestación.

Une voz, cerebro y overlays. Un solo proceso, varios hilos:

    Hilo Qt (principal)   pinta orbe y glow. Nunca bloquea.
    Hilo de trabajo       LLM + herramientas (tardan segundos).
    Hilo de escucha       las dos etapas de voz, dentro de VoiceListener.
    Hilo de audio         lo abre PortAudio; sólo copia bloques a una cola.
    Hilo de voz (TTS)     pyttsx3, dentro de Speaker.

Los tres hilos que no son el de Qt avisan por señales, nunca llamando
a un método que pinte un widget directamente: tocar un QWidget desde
un hilo suelto es la forma clásica de que una app Qt se cuelgue o se
caiga sin explicación (y sin traceback que lo delate).

Flujo (conversación continua, no de un solo turno):
    dormida → algo suena a "NOVA" (etapa 1, barata)
            → se captura la frase entera, con el segundo anterior incluido
            → etapa 2 (Whisper) confirma el nombre Y transcribe la orden
            → si el nombre no estaba, vuelve a dormir SIN hacer ruido
            → si estaba: chime + glow + pensando → herramientas → responde
            → sigue escuchando sin repetir "NOVA"
            → ... → despedida o silencio prolongado → vuelve a dormida

El chime suena en la etapa 2 y no en la 1 a propósito: "nova" y "no va"
son la misma secuencia de fonemas en español, así que una puerta abierta
no es todavía una llamada.
"""

from __future__ import annotations

import logging
import sys
import threading
import time

from PyQt5.QtCore import (
    QObject,
    QtCriticalMsg,
    QtFatalMsg,
    QThread,
    QTimer,
    QtWarningMsg,
    pyqtSignal,
    qInstallMessageHandler,
)
from PyQt5.QtWidgets import QApplication

from .bootstrap import configurar_logging, parsear_argumentos
from .config import CONFIG
from .core.agent import Agent
from .core.awareness import Awareness
from .core.conversation import Conversation, build_system_prompt
from .core.polish import recortar_para_voz
from .llm.ollama import OllamaClient
from .llm.remoto import ClienteRemoto, leer_clave
from .plugins import Gestor, cargar_activos
from .tools import (
    PendingConfirmation,
    apps,
    build_registry,
    cerebro,
    imagenes,
    memory,
    pantalla,
    recordatorios,
    voz,
)
from .tools import (
    plugins as tool_plugins,
)
from .ui import Interfaz
from .voice import Speaker, Transcriptor, VoiceListener, play_chime

log = logging.getLogger("nova.app")

# Lo que NOVA contesta al oír su nombre. Corto: es un acuse de recibo,
# no una frase.
# Cada cuánto se mira si ha vencido algún recordatorio. Cinco segundos
# es de sobra para algo que se mide en minutos, y no gasta nada.
SEGUNDOS_ENTRE_REVISIONES = 5.0

SALUDOS = ["Dime.", "Te escucho.", "¿Sí?"]
DESPEDIDAS = ["Hasta luego.", "Aquí estaré.", "Vale."]

# Afirmaciones que valen como "sí" para una acción pendiente. Se acepta
# ruido alrededor ("si dale wacho") porque hablando nadie dice "sí" seco.
_AFIRMA = ("si", "sí", "claro", "dale", "vale", "ok", "okey", "hazlo",
           "adelante", "venga", "confirmo", "correcto", "eso")

# Y las que son un "no" claro. Hace falta distinguirlas de "ni sí ni no":
# si contestas otra cosa a una pregunta pendiente, lo que has dicho es
# una orden nueva y NO se puede tirar a la basura. Antes se cancelaba y
# se respondía "vale, lo dejo" a una frase que no tenía nada que ver.
_NIEGA = ("no", "nop", "nada", "cancela", "cancelar", "olvidalo", "olvídalo",
          "déjalo", "dejalo", "para", "mejor no", "negativo")


def va_a_sonar(*, hablar: bool, silenciada: bool, tts_activo: bool) -> bool:
    """¿Este texto va a producir audio de verdad?

    Existe como función suelta para poder probarla sin montar Qt. La
    tercera condición es la que faltaba en NOVA4: con `NOVA_TTS=false`,
    `Speaker.say()` vuelve sin disparar `on_start`/`on_end`, así que nadie
    movía el orbe nunca más y se quedaba clavado en "pensando" para
    siempre. El bug no se veía porque casi nadie apaga la voz.
    """
    return hablar and not silenciada and tts_activo


def elegir_cerebro(*, preferencia: str, hay_clave: bool,
                   auto: bool = False, apretado: bool = False) -> bool:
    """¿Toca pensar en la nube? Suelta para poder probarla sin montar Qt.

    Manda lo que Benja haya pedido, en los dos sentidos: "modo rápido"
    enciende aunque el PC vaya sobrado, y "modo local" apaga aunque haya
    un juego delante comiéndose la VRAM.

    Sin preferencia se queda en local, salvo que se haya activado el
    cambio automático a mano (`NOVA_REMOTO_AUTO`). Va apagado a
    propósito: que tus datos salgan del PC no puede ser un efecto
    secundario de abrir un juego.
    """
    if not hay_clave or preferencia == "local":
        return False
    if preferencia == "rapido":
        return True
    return auto and apretado


def estado_en_reposo(*, ocupada: bool, escuchando: bool, sorda: bool = False) -> str:
    """Qué estado le toca al orbe cuando NOVA termina de hablar.

    `escuchando` NO es "despierta". Despierta sigue veinte segundos, pero
    sin repetir su nombre sólo te atiende dentro del hueco de
    seguimiento. Enseñar "te escucho" en los doce segundos restantes era
    mentir: el panel decía que sí y ella te ignoraba.
    """
    if sorda:
        # Manda sobre todo lo demás: si has apagado el oído, el panel no
        # puede decir "te escucho" ni un segundo.
        return "dormida"
    if ocupada:
        return "pensando"
    return "escucha" if escuchando else "dormida"


class _Worker(QObject):
    """Ejecuta el turno pesado fuera del hilo de la interfaz."""

    listo = pyqtSignal(str, list, bool)    # texto, herramientas, ya dicho
    frase = pyqtSignal(str)                # una frase suelta, según se genera
    pendiente = pyqtSignal(object, str)     # list[PendingConfirmation], qué decir
    estado = pyqtSignal(str, str, str)      # etapa, herramienta, dato

    def __init__(self, agent: Agent, conv: Conversation, awareness: Awareness,
                 plugins=None) -> None:  # noqa: ANN001
        super().__init__()
        self.agent = agent
        self.conv = conv
        self.awareness = awareness
        self.plugins = plugins

    def procesar(self, mensaje: str) -> None:
        """Un turno completo. Nunca deja escapar una excepción.

        Esto no es cinturón y tirantes. Un slot de Qt que revienta se
        lleva el proceso por delante —PyQt5 llama a `qFatal()` y aborta,
        que es el 0xc0000409 en Qt5Core.dll del Visor de sucesos— y
        además deja `_ocupada` en True para siempre, así que NOVA se
        quedaría contestando "todavía estoy con lo anterior" a todo.
        Pasó de verdad: `build_system_prompt` se quedó sin actualizar al
        añadir los plugins y NOVA moría en TODAS las órdenes.
        """
        try:
            self._procesar(mensaje)
        except Exception:
            log.exception("el turno se rompió: %r", mensaje)
            self.listo.emit("Me he atascado con eso.", [], False)

    def _procesar(self, mensaje: str) -> None:
        # `memory.para_prompt()` lee un JSON de unos pocos KB; a
        # diferencia del clima, esto sí puede estar en el camino crítico.
        # Los plugins activos añaden su personalidad al final del
        # prompt. Se lee en cada turno y no una vez al arrancar: así
        # activar uno en el panel se nota en la frase siguiente, sin
        # reiniciar NOVA.
        extra = self.plugins.personalidad() if self.plugins else ""
        # Los apuntes son lo que las herramientas ya averiguaron en esta
        # conversación. Sin ellos NOVA volvía a buscar lo mismo cada vez
        # que se le hablaba del tema. Ver `Conversation.apuntar`.
        prompt = build_system_prompt(
            self.awareness.snapshot(), memory.para_prompt(), extra, self.conv.apuntes()
        )
        respuesta = self.agent.run(prompt, self.conv.history(), mensaje)

        for herramienta, resultado in respuesta.resultados:
            self.conv.apuntar(herramienta, resultado)
        self.conv.add_user(mensaje)
        if respuesta.pendientes:
            # El texto ya cuenta lo que se hizo Y pregunta por lo que
            # falta: se compone en el agente para no gastar otra vuelta
            # al modelo con el usuario esperando.
            self.conv.add_assistant(respuesta.text)
            self.pendiente.emit(respuesta.pendientes, respuesta.text)
            return
        self.conv.add_assistant(respuesta.text)
        self.listo.emit(respuesta.text, respuesta.tools_used, respuesta.ya_dicho)

    def confirmar(self, pendiente: PendingConfirmation) -> None:
        # Mismo motivo que en `procesar`: esto también es un slot.
        try:
            resultado = self.agent.confirm(pendiente)
            if resultado.ok:
                self.conv.apuntar(pendiente.tool, resultado.message)
            self.conv.add_assistant(resultado.message)
            self.listo.emit(resultado.message, [pendiente.tool], False)
        except Exception:
            log.exception("la confirmación se rompió: %s", pendiente.tool)
            self.listo.emit("No he podido hacerlo.", [], False)


class Nova(QObject):
    """La aplicación. Se crea, se arranca y vive en la bandeja del sistema."""

    _procesar = pyqtSignal(str)
    _confirmar = pyqtSignal(object)

    # VoiceListener y Speaker llaman a sus callbacks desde SUS PROPIOS
    # hilos (no el de Qt). Tocar un QWidget desde ahí es exactamente lo
    # que este módulo dice que hay que evitar — así que estos callbacks
    # no llaman a los métodos directamente, emiten una señal. Qt la
    # encola sola hacia el hilo principal porque Nova vive ahí.
    _voz_despierta = pyqtSignal(bool)
    _voz_comando = pyqtSignal(str)
    _voz_dormir = pyqtSignal(str)
    _voz_lista = pyqtSignal()
    _voz_error = pyqtSignal(str)
    _voz_escuchando = pyqtSignal()
    _voz_interrumpe = pyqtSignal()
    _voz_nada = pyqtSignal()
    _habla_inicio = pyqtSignal()
    _habla_fin = pyqtSignal()
    # El nivel llega desde el hilo de audio y desde el de TTS, ~30 veces
    # por segundo. Como todo lo que cruza hilos aquí, va por señal.
    _nivel = pyqtSignal(float)

    def __init__(self, app: QApplication, transcriptor: Transcriptor | None = None) -> None:
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
            num_ctx=CONFIG.num_ctx,
            timeout=CONFIG.request_timeout,
        )
        # Cerebro de repuesto en la nube. Sin clave es inerte: no se
        # contacta con nadie y NOVA funciona exactamente igual que antes.
        self.remoto = ClienteRemoto(
            CONFIG.remoto_url,
            CONFIG.remoto_model,
            leer_clave(CONFIG.remoto_key_file, CONFIG.remoto_key),
            temperature=CONFIG.temperature,
            max_tokens=CONFIG.max_tokens,
        )
        self._preferencia_cerebro = ""
        self._en_remoto = False

        self.tools = build_registry(CONFIG.confirm_policy)

        # Plugins. Encontrarlos es leer JSON; el código de los activos se
        # importa aquí y sólo aquí (ver nova/plugins/carga.py).
        self.plugins = Gestor()
        for aviso in cargar_activos(self.plugins, self.tools):
            log.warning("plugin: %s", aviso)
        self._panel_plugins = None
        self.conv = Conversation(CONFIG.history_turns)
        self.awareness = Awareness()
        self.agent = Agent(
            self.llm,
            self.tools,
            max_rounds=CONFIG.max_rounds,
            on_status=lambda etapa, herramienta, dato: (
                self._worker.estado.emit(etapa, herramienta, dato)
            ),
            on_frase=lambda frase: self._worker.frase.emit(frase),
        )

        # ── Interfaz ─────────────────────────────────────────────────
        self.ui = Interfaz(on_quit=self.salir, on_toggle_mute=self.alternar_voz,
                           on_toggle_sordo=self.alternar_oido)
        self.glow = self.ui.glow

        # ── Voz ──────────────────────────────────────────────────────
        # Los callbacks emiten señales (ver arriba) en vez de llamar a
        # los métodos directamente: esos métodos pintan widgets, y este
        # objeto vive en el hilo de voz/TTS, no en el de Qt.
        self.speaker = Speaker(
            rate=CONFIG.tts_rate,
            enabled=CONFIG.tts_enabled,
            on_start=self._habla_inicio.emit,
            on_end=self._habla_fin.emit,
            # Su nivel va a dos sitios: al panel y al oyente. Ver
            # `_nivel_voz_nova`.
            on_nivel=self._nivel_voz_nova,
        )
        # Etapa 2: entiende la orden Y confirma que el nombre estaba de
        # verdad. Llega ya cargado desde `run()`, antes de que existiera
        # Qt — construirlo después mata el proceso (ver voice/cuda.py).
        self.transcriptor = transcriptor or Transcriptor(
            modelo=CONFIG.whisper_model,
            compute_type=CONFIG.whisper_compute,
            device=CONFIG.whisper_device,
            vosk_model=CONFIG.vosk_model,
        )
        self.listener = VoiceListener(
            CONFIG.wake_model,
            CONFIG.wake_word,
            transcriptor=self.transcriptor,
            device=CONFIG.mic_device,
            exclusivo=CONFIG.mic_exclusive,
            awake_timeout_s=CONFIG.awake_timeout_s,
            preroll_s=CONFIG.preroll_s,
            silencio_fin_s=CONFIG.silencio_fin_s,
            max_enunciado_s=CONFIG.max_enunciado_s,
            on_wake=self._voz_despierta.emit,
            on_command=self._voz_comando.emit,
            on_sleep=self._voz_dormir.emit,
            on_ready=self._voz_lista.emit,
            on_error=self._voz_error.emit,
            on_escuchando=self._voz_escuchando.emit,
            on_nivel=self._nivel.emit,
            on_interrupcion=self._voz_interrumpe.emit,
            on_nada=self._voz_nada.emit,
            seguimiento_s=CONFIG.seguimiento_s,
            espera_respuesta_s=CONFIG.espera_respuesta_s,
            interrumpir=CONFIG.interrumpir,
        )
        self._voz_despierta.connect(self._al_despertar)
        self._voz_comando.connect(self._al_comando)
        self._voz_dormir.connect(self._al_dormir)
        self._voz_lista.connect(self._al_voz_lista)
        self._voz_error.connect(self._al_voz_error)
        self._voz_escuchando.connect(self._al_voz_escuchando)
        self._voz_interrumpe.connect(self._al_interrumpirme)
        self._voz_nada.connect(self._al_nada)
        self._nivel.connect(self.ui.set_nivel)
        self._habla_inicio.connect(self._hablando_inicio)
        self._habla_fin.connect(self._hablando_fin)

        # ── Hilo de trabajo ──────────────────────────────────────────
        self._hilo = QThread()
        self._worker = _Worker(self.agent, self.conv, self.awareness, self.plugins)
        self._worker.moveToThread(self._hilo)
        self._procesar.connect(self._worker.procesar)
        self._confirmar.connect(self._worker.confirmar)
        self._worker.listo.connect(self._al_responder)
        self._worker.pendiente.connect(self._al_pendiente)
        self._worker.estado.connect(self._al_estado)
        self._worker.frase.connect(self._al_frase)

        self._pendientes: list[PendingConfirmation] = []
        self._ocupada = False
        self._respuesta_en_curso = ""
        self._avisos_pendientes: list = []
        self._modo_ligero = False
        self._ligero_disponible: bool | None = None
        self._voz_silenciada = False

    # ── Arranque / apagado ───────────────────────────────────────────

    def start(self) -> None:
        self._hilo.start()
        self.awareness.start()
        # El índice de apps se construye ya, no en el primer
        # "abre Discord" del día.
        apps.precalentar_indice()

        # Vigilante de recordatorios. Un temporizador de Qt y no un hilo:
        # esto toca la interfaz, y todo lo que toca la interfaz vive en
        # el hilo de Qt (ver la cabecera de este módulo).
        self._reloj = QTimer(self)
        self._reloj.timeout.connect(self._revisar_recordatorios)
        self._reloj.timeout.connect(self._revisar_recursos)
        self._reloj.start(int(SEGUNDOS_ENTRE_REVISIONES * 1000))

        # El hueco de seguimiento se cierra solo, sin que pase ningún
        # evento: nadie avisa de que han pasado ocho segundos. Sin este
        # latido, el panel se quedaba en "te escucho" y el borde azul
        # encendido hasta que volvieras a hablarle.
        self._latido = QTimer(self)
        self._latido.timeout.connect(self._sincronizar_estado)
        self._latido.start(500)
        self.speaker.start()
        # Las herramientas de voz necesitan el altavoz de verdad: quien
        # cambia de voz es él, no el registro.
        voz.conectar(self.speaker)
        voz.aplicar_guardado(self.speaker)
        tool_plugins.conectar(self.plugins, self.abrir_panel_plugins)
        cerebro.conectar(self)
        self._aplicar_voz_de_plugins()

        self.ui.mostrar()

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
        self.ui.set_estado("preparando")
        if not self.listener.start():
            self._al_voz_error(self.listener.error)

    def _al_voz_lista(self) -> None:
        self.ui.set_estado("dormida")
        log.info("NOVA lista (%s). Di «%s».", self.transcriptor.motor, CONFIG.wake_word)

    def _nivel_voz_nova(self, nivel: float) -> None:
        """El nivel de lo que NOVA está diciendo, a dos sitios.

        Al panel para pintar la onda, y al oyente para que sepa
        distinguir su propia voz de la tuya cuando la interrumpes.
        """
        self._nivel.emit(nivel)
        self.listener.nivel_salida(nivel)

    def _al_interrumpirme(self) -> None:
        """Le has hablado por encima: se calla y te escucha.

        Sin esto había que esperar a que terminara la frase para poder
        corregirla, que es justo cuando más ganas dan de cortarla.
        """
        log.info("me interrumpen")
        self.speaker.shut_up()
        self._respuesta_en_curso = ""
        self._avisos_pendientes: list = []
        self.ui.set_estado("escucha")

    def _al_nada(self) -> None:
        """Se capturó algo y no salió nada: la interfaz vuelve a su sitio.

        Sin esto el panel se quedaba en "te escucho" para siempre en
        cuanto la etapa 1 abría la puerta por un ruido y la etapa 2 lo
        descartaba — que es lo normal, para eso está.
        """
        if not self._ocupada:
            self.glow.apagar()
            self._reposo()

    def _sincronizar_estado(self) -> None:
        """Devuelve la interfaz a la verdad cuando el tiempo la cambia.

        Sólo actúa cuando NOVA no está haciendo nada: si está pensando,
        hablando o capturando, ese estado manda y no lo pisa nadie.
        """
        if self._ocupada or self.speaker.speaking or not self.listener.ready:
            return
        self._reposo()

    # ── Recursos ─────────────────────────────────────────────────────

    # ── Con qué piensa ───────────────────────────────────────────────

    @property
    def en_remoto(self) -> bool:
        return self._en_remoto

    @property
    def modo_ligero(self) -> bool:
        return self._modo_ligero

    def preferir_cerebro(self, cual: str) -> None:
        """Lo que Benja ha pedido: "rapido", "local" o "" para que decida ella.

        No se guarda en disco a propósito. Encender la nube es dar
        permiso para que lo que dices salga del ordenador, y un permiso
        que sobrevive a los reinicios acaba siendo un permiso que nadie
        recuerda haber dado.
        """
        self._preferencia_cerebro = cual
        self._aplicar_cerebro(apretado=self._modo_ligero)

    def _aplicar_cerebro(self, *, apretado: bool) -> None:
        """Pone el cerebro que toca, si no es el que ya estaba."""
        quiere = elegir_cerebro(
            preferencia=self._preferencia_cerebro,
            hay_clave=self.remoto.disponible,
            auto=CONFIG.remoto_auto,
            apretado=apretado,
        )
        if quiere == self._en_remoto:
            return
        self._en_remoto = quiere
        # El agente es el único que llama al modelo: cambiarle el cliente
        # cambia el cerebro entero sin tocar nada más.
        self.agent.llm = self.remoto if quiere else self.llm
        log.info("cerebro: %s", self.remoto.model if quiere else self.llm.model)
        self.ui.set_modo_ligero(self._modo_ligero and not quiere)

    def _revisar_recursos(self) -> None:
        """¿Sigue el modelo dentro de la GPU, o lo ha echado un juego?

        Cuando un juego se queda con la VRAM, el driver expulsa al modelo
        y Ollama sigue respondiendo... desde la CPU, cuatro veces más
        lento, sin que nada lo diga. Medido con Star Citizen abierto:
        qwen3.5:4b tardaba 4.36-7.44 s con sólo el 10% en la GPU, y
        qwen2.5:3b hacía lo mismo en 1.11-1.19 s porque SÍ cabía.

        Así que se cambia al pequeño mientras dure la escasez, y se
        vuelve al bueno cuando haya sitio otra vez.
        """
        if self._ocupada:
            return
        try:
            residencia = self.llm.residencia()
        except Exception:  # noqa: BLE001
            return

        apretado = residencia < CONFIG.residencia_minima
        # Falta de VRAM es justo el caso en que la nube gana: allí no
        # ocupa nada. Se mira SIEMPRE, aunque el modo ligero no cambie.
        self._aplicar_cerebro(apretado=apretado)
        if self._en_remoto or apretado == self._modo_ligero:
            return
        if apretado and self._hay_modelo_ligero():
            log.info("sólo el %.0f%% del modelo en la GPU: paso al ligero",
                     residencia * 100)
            self.llm.usar_modelo(CONFIG.model_ligero)
            self._modo_ligero = True
        elif not apretado and self._modo_ligero:
            log.info("hay VRAM otra vez: vuelvo a %s", CONFIG.model)
            self.llm.usar_modelo(CONFIG.model)
            self._modo_ligero = False
        self.ui.set_modo_ligero(self._modo_ligero)

    def _hay_modelo_ligero(self) -> bool:
        """¿Existe de verdad el modelo de repuesto? Se pregunta una vez.

        Si NOVA_MODEL_LIGERO apunta a algo que no está descargado,
        cambiarse a él la dejaría muda. Mejor seguir lenta que callada.
        """
        if self._ligero_disponible is None:
            nombre = CONFIG.model_ligero
            self._ligero_disponible = bool(
                nombre and nombre != CONFIG.model and self.llm.tiene_modelo(nombre)
            )
            if not self._ligero_disponible and nombre:
                log.warning(
                    "no encuentro %s: sin modelo de repuesto para cuando falte VRAM "
                    "(descárgalo con: ollama pull %s)", nombre, nombre,
                )
        return self._ligero_disponible

    # ── Recordatorios ────────────────────────────────────────────────

    def _revisar_recordatorios(self) -> None:
        """¿Ha vencido algo? Y sobre todo: ¿es momento de decirlo?

        Vencer no es hablar. Un aviso que te corta a mitad de partida
        para algo que podía esperar treinta segundos es peor que no
        tenerlo, así que por defecto queda pendiente y el panel parpadea.
        Sólo las alarmas con hora fija interrumpen — para eso las pones.
        """
        try:
            vencidos = recordatorios.pendientes()
        except Exception:  # noqa: BLE001
            log.debug("no pude leer los recordatorios", exc_info=True)
            return

        self._avisos_pendientes = vencidos
        self.ui.set_pendientes(len(vencidos))
        if not vencidos or self._ocupada or self.speaker.speaking:
            return

        alarmas = [r for r in vencidos if r.alarma]
        # Si está hablando con ella, cualquier recordatorio cabe: ya
        # tiene su atención y no se le está interrumpiendo nada.
        if alarmas or self.listener.awake:
            self._soltar_recordatorios(vencidos if self.listener.awake else alarmas)

    def _soltar_recordatorios(self, avisos: list) -> None:
        if not avisos:
            return
        textos = [r.texto for r in avisos]
        recordatorios.marcar_avisados(textos)
        self._avisos_pendientes = [r for r in self._avisos_pendientes if r not in avisos]
        self.ui.set_pendientes(len(self._avisos_pendientes))

        if len(textos) == 1:
            frase = f"Te recuerdo: {textos[0]}."
        else:
            frase = "Tenías apuntado: " + "; ".join(textos) + "."
        log.info("suelto %d recordatorio(s)", len(textos))
        self.ui.set_respondido(frase)
        self._decir(frase)

    def _al_voz_escuchando(self) -> None:
        """La etapa 1 ha abierto la puerta: se está capturando la frase.

        Todavía NO se sabe si dijeron el nombre — eso lo confirma la
        etapa 2 —, así que aquí no suena el chime ni se enciende el glow.
        El orbe sí cambia: es información honesta y no molesta si luego
        resulta que era "no va".
        """
        if not self._ocupada:
            self.ui.set_estado("escucha")

    def _al_voz_error(self, mensaje: str) -> None:
        self.ui.set_estado("apagada")
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

    # ── Plugins ──────────────────────────────────────────────────────

    def abrir_panel_plugins(self) -> None:
        """Abre el panel. Se llama desde el hilo de la interfaz.

        La ventana se guarda: abrirla dos veces trae al frente la que ya
        estaba en vez de apilar copias.
        """
        from .ui.panel_plugins import PanelPlugins

        if self._panel_plugins is None:
            self._panel_plugins = PanelPlugins(self.plugins)
            self._panel_plugins.cambiado.connect(self._al_cambiar_plugins)
        self._panel_plugins.recargar()
        self._panel_plugins.show()
        self._panel_plugins.raise_()
        self._panel_plugins.activateWindow()

    def _al_cambiar_plugins(self) -> None:
        """Alguien activó o apagó algo en el panel.

        La personalidad se relee sola en cada turno, así que aquí sólo
        hay que atender lo que NO se relee: la voz, y el código de los
        que se acaban de encender.
        """
        self._aplicar_voz_de_plugins()
        for aviso in cargar_activos(self.plugins, self.tools):
            log.warning("plugin: %s", aviso)

    def _aplicar_voz_de_plugins(self) -> None:
        """La voz que pida el plugin activo, si pide alguna.

        No pisa una elección tuya hecha a mano: si ya habías dicho «ponte
        voz de hombre», eso manda sobre lo que traiga el plugin. Que un
        plugin te cambie la voz sin avisar sería justo lo que no quieres.
        """
        from .tools import voz as tool_voz

        if tool_voz.guardado().get("voz"):
            return
        pedida = self.plugins.voz_preferida()
        if pedida and pedida != self.speaker.voz_actual:
            log.info("un plugin pide la voz %s", pedida)
            self.speaker.usar_voz(pedida)

    def salir(self) -> None:
        log.info("cerrando NOVA")
        self.ui.cerrar()
        self.listener.stop()
        self.speaker.stop()
        pantalla.cerrar()
        imagenes.cerrar()
        self.awareness.stop()
        self._hilo.quit()
        self._hilo.wait(1500)
        self.llm.close()
        self.app.quit()

    def alternar_voz(self) -> None:
        """El botón de callarla. Sigue oyéndote y sigue haciendo cosas."""
        self._voz_silenciada = not self._voz_silenciada
        if self._voz_silenciada:
            self.speaker.shut_up()
        self._pintar_conmutadores()

    def alternar_oido(self) -> None:
        """El botón de que no te escuche.

        Ensordecer calla también, y no por comodidad: una NOVA que no te
        oye pero te habla no puede ser interrumpida por voz. Te quedarías
        oyéndola sin ninguna forma de pararla salvo el ratón.

        Al volver a oír NO se le devuelve la voz sola: si la habías
        callado tú antes por tu cuenta, esa decisión sigue siendo tuya.
        """
        sordo = not self.listener.sordo
        self.listener.ensordecer(sordo)
        if sordo:
            self._voz_silenciada = True
            self.speaker.shut_up()
            self._pendientes = []
            self.ui.set_estado("dormida")
            self.glow.apagar()
        self._pintar_conmutadores()

    def _pintar_conmutadores(self) -> None:
        self.ui.set_conmutadores(mudo=self._voz_silenciada, sordo=self.listener.sordo)

    # ── Eventos de voz (llegan desde el hilo de escucha) ─────────────

    def _al_despertar(self, con_comando: bool) -> None:
        """La etapa 2 ha confirmado que el nombre estaba de verdad.

        Aquí y no antes es donde suena el chime: si sonara al abrir la
        puerta la etapa 1, NOVA haría ruido cada vez que dijeras "no va a
        funcionar" — la misma secuencia de fonemas que su nombre.
        """
        if self._ocupada:
            # Ya estaba trabajando: el usuario la interrumpe.
            self.speaker.shut_up()
        if CONFIG.chime_enabled:
            play_chime()
        self.ui.set_estado("escucha")
        if CONFIG.glow_enabled:
            self.glow.encender("escucha")

        # Lo que quedó esperando se suelta ANTES de nada: es el momento
        # en que por fin tienes su atención y ella la tuya.
        if self._avisos_pendientes:
            self._soltar_recordatorios(list(self._avisos_pendientes))
            return

        if con_comando:
            # "NOVA abre discord" del tirón. Saludar aquí sería hablar
            # encima de la respuesta que ya viene en camino.
            return
        import random

        self._decir(random.choice(SALUDOS), estado="escucha")

    def _al_comando(self, texto: str) -> None:
        # Primera línea que se escribe ya en el hilo de Qt. Marca la
        # frontera: si el log acaba en «oído» y no llega aquí, lo que
        # falló fue el salto entre hilos y no el turno.
        log.debug("comando en el hilo de Qt: %r", texto)

        # Mientras está pensando o hablando, lo que llegue NO es un
        # comando nuevo: o es ella misma, o es alguien hablando por
        # encima. Antes se encolaban y NOVA contestaba una detrás de
        # otra sin parar, que es el fallo que más molestaba.
        if self._ocupada:
            log.info("ignoro %r: todavía estoy con lo anterior", texto)
            return

        self.glow.apagar()
        self.ui.set_dicho(texto)
        self.ui.set_respondido("")
        self._respuesta_en_curso = ""
        self._avisos_pendientes: list = []

        # ¿Está contestando a una confirmación pendiente?
        if self._pendientes:
            primera = texto.strip().lower().split()[:1]
            palabra = primera[0].strip(".,!¡") if primera else ""
            if palabra in _AFIRMA:
                pendiente = self._pendientes.pop(0)
                self.listener.esperar_respuesta(False)
                self.ui.set_estado("pensando")
                self._ocupada = True
                self._confirmar.emit(pendiente)
                return
            if palabra in _NIEGA:
                # Un «no» tumba ESA acción, no las demás de la cadena.
                self._pendientes.pop(0)
                if self._pendientes:
                    self._preguntar_siguiente(previo="Vale, eso lo dejo.")
                else:
                    self.listener.esperar_respuesta(False)
                    self._decir("Vale, lo dejo.")
                return
            # Ni sí ni no: es otra cosa. Se descarta lo pendiente (no
            # contestar a una pregunta es no darle permiso) pero lo que
            # ha dicho SE PROCESA. Tirar su frase y responder "vale, lo
            # dejo" era perder una orden entera sin avisar.
            log.info("no era un sí ni un no: dejo lo pendiente y atiendo %r", texto)
            self._pendientes = []
            self.listener.esperar_respuesta(False)

        self.ui.set_estado("pensando")
        self._ocupada = True
        self._procesar.emit(texto)

    def _al_dormir(self, motivo: str) -> None:
        self.glow.apagar()
        self.ui.set_dicho("")
        self.ui.set_respondido("")
        self._respuesta_en_curso = ""
        self._avisos_pendientes: list = []
        if self._pendientes:
            # Dormirse con un "sí/no" en el aire es peligroso de verdad:
            # la próxima vez que la despiertes, un "sí" a CUALQUIER OTRA
            # cosa ejecutaría esta acción olvidada de la sesión anterior
            # — y puede ser un borrado. Se descarta al dormir, no se
            # arrastra.
            log.info("me duermo con %d confirmación(es) sin contestar: se descartan",
                     len(self._pendientes))
            self._pendientes = []
        if not self._ocupada:
            self.ui.set_estado("dormida")
        if motivo == "despedida":
            import random

            # `estado="dormida"` es la red por si la voz está apagada: sin
            # ella nadie devolvería el orbe a su sitio y se quedaba en
            # "te escucho" después de despedirse.
            self._decir(random.choice(DESPEDIDAS), estado="dormida")

    # ── Eventos del cerebro (llegan del hilo de trabajo) ─────────────

    def _al_estado(self, etapa: str, herramienta: str, dato: str) -> None:
        self.ui.set_estado("pensando")
        if etapa == "tool" and herramienta:
            self.ui.accion(herramienta, dato)

    def _al_frase(self, frase: str) -> None:
        """Una frase de la respuesta, recién salida del modelo.

        Se dice YA, sin esperar al punto final: es lo que quita el
        silencio largo entre que dejas de hablar y NOVA empieza. El
        agente sólo manda frases cerradas y no manda las que son puro
        relleno, así que aquí no hay que decidir nada.
        """
        self.ui.set_estado("hablando")
        self._decir(frase, acumular_subtitulo=True)

    def _al_responder(self, texto: str, herramientas: list, ya_dicho: bool) -> None:
        self._ocupada = False
        self.listener.marcar_turno()
        self.ui.turno_terminado()
        self.ui.set_respondido(texto)
        if self._pendientes:
            # Quedaban más acciones esperando permiso en la misma orden.
            self._preguntar_siguiente(previo=texto)
            return
        self.listener.esperar_respuesta(False)
        if ya_dicho:
            # Se fue diciendo mientras se generaba. Repetirla entera
            # ahora sería, literalmente, decirlo todo dos veces.
            return
        self._decir(texto)

    def _al_pendiente(self, pendientes: object, texto: str) -> None:
        self._ocupada = False
        self.listener.marcar_turno()
        self.ui.turno_terminado()
        self._pendientes = list(pendientes)  # type: ignore[arg-type]
        # Ha hecho una pregunta: no puede dormirse antes de oír la
        # respuesta. Sin esto, NOVA preguntaba, se dormía a los 20 s, y
        # el «sí» del usuario llegaba a una NOVA que ya no sabía de qué
        # le hablaban.
        self.listener.esperar_respuesta(True)
        self._decir(texto)

    def _preguntar_siguiente(self, *, previo: str = "") -> None:
        """Pregunta por la siguiente acción que quedó esperando permiso."""
        if not self._pendientes:
            self.listener.esperar_respuesta(False)
            if previo:
                self._decir(previo)
            return
        resumen = getattr(self._pendientes[0], "summary", "")
        pregunta = f"¿Confirmas que quiero {resumen}?" if resumen else "¿Lo confirmo?"
        self.listener.esperar_respuesta(True)
        self._decir(f"{previo} {pregunta}".strip())

    # ── Salida ───────────────────────────────────────────────────────

    def _decir(
        self,
        texto: str,
        *,
        estado: str = "",
        hablar: bool = True,
        acumular_subtitulo: bool = False,
    ) -> None:
        """Dice algo en alto y deja el orbe en un estado coherente.

        `texto` llega entero — es lo que se guarda y lo que verán los
        subtítulos. Al TTS va recortado: el modelo se salta el "1 o 2
        frases" del prompt y 200 caracteres son 12 s hablando con el
        micrófono mudo (ver `polish.recortar_para_voz`).
        """
        if acumular_subtitulo:
            self._respuesta_en_curso = f"{self._respuesta_en_curso} {texto}".strip()
            self.ui.set_respondido(self._respuesta_en_curso)
        else:
            self.ui.set_respondido(texto)
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
            self.ui.set_estado(estado)
            return
        siguiente = estado_en_reposo(
            ocupada=self._ocupada,
            escuchando=self.listener.escuchando,
            sorda=self.listener.sordo,
        )
        if siguiente == "escucha" and CONFIG.glow_enabled:
            # La conversación sigue abierta: se nota en el glow que no
            # hace falta repetir "NOVA" para el siguiente turno.
            self.glow.encender("escucha")
        else:
            # Y se apaga cuando deja de estarlo. Encenderlo sin apagarlo
            # nunca es la forma de que se quede pegado para siempre.
            self.glow.apagar()
        self.ui.set_estado(siguiente)

    def _hablando_inicio(self) -> None:
        # Mientras NOVA habla, el micrófono se ignora: si no, se oye a sí
        # misma y se despierta sola en bucle.
        self.listener.mute()
        self.ui.set_estado("hablando")

    def _hablando_fin(self) -> None:
        self.listener.unmute()
        self._reposo()


_NIVEL_QT = {
    QtWarningMsg: logging.WARNING,
    QtCriticalMsg: logging.ERROR,
    QtFatalMsg: logging.CRITICAL,
}


def _mensaje_de_qt(tipo, contexto, texto) -> None:  # noqa: ANN001, ARG001
    """Lo que Qt dice antes de abortar, al log en vez de a stderr.

    Qt avisa de sus propios errores fatales por su cuenta —"Cannot create
    children for a parent that is in a different thread", por ejemplo— y
    lo hace por `stderr`. NOVA se lanza en segundo plano, así que ese
    aviso no lo lee nadie: el proceso desaparece y el log se queda
    cortado a media frase, que es justo lo que costó días de diagnóstico.
    """
    log.log(_NIVEL_QT.get(tipo, logging.DEBUG), "Qt: %s", texto)


def run(args=None, transcriptor: Transcriptor | None = None) -> int:  # noqa: ANN001
    """Punto de entrada: monta la app Qt y entra en el bucle de eventos.

    El transcriptor llega YA CARGADO desde `run.py`, que lo prepara antes
    de importar este módulo. No es una optimización: construirlo después
    de que PyQt5 esté en el proceso mata a NOVA con un segmentation
    fault. El porqué medido está en `nova/bootstrap.py`.
    """
    if args is None:
        args = parsear_argumentos(None)
        configurar_logging(debug=args.debug)
    if transcriptor is None:
        # Camino de conveniencia (tests, `python -m nova.app`): aquí
        # PyQt5 ya está importado, así que Whisper no va a poder cargar y
        # el transcriptor caerá a Vosk. Para uso normal, `run.py`.
        log.warning("arrancando sin transcriptor precargado; usa run.py")
        transcriptor = Transcriptor(
            modelo=CONFIG.whisper_model,
            compute_type=CONFIG.whisper_compute,
            device=CONFIG.whisper_device,
            vosk_model=CONFIG.vosk_model,
        )

    # Antes de que exista la QApplication: los primeros avisos de Qt
    # salen durante su construcción.
    qInstallMessageHandler(_mensaje_de_qt)
    app = QApplication(sys.argv)
    app.setApplicationName("NOVA")
    # Sin ventanas visibles no debe morir: NOVA vive en overlays.
    app.setQuitOnLastWindowClosed(False)

    nova = Nova(app, transcriptor)
    nova.start()
    return app.exec_()
