"""Decisiones de la orquestación, sin montar Qt ni Ollama.

Las dos funciones que se prueban aquí viven sueltas en `app.py` justo
para esto: eran `if` incrustados dentro de métodos que pintan widgets, y
sus dos bugs (orbe clavado, ciclo sin cerrar) no se podían probar.
"""

from __future__ import annotations

from logging.handlers import RotatingFileHandler

import pytest

from nova.app import Nova, estado_en_reposo, va_a_sonar
from nova.config import CONFIG

CONFIG_NORMAL = CONFIG.model
CONFIG_LIGERO = CONFIG.model_ligero


@pytest.fixture
def logging_restaurado():
    """Devuelve el logging global como estaba.

    `configurar_logging` toca el logger raíz a propósito. Sin esto, los
    handlers apuntando a ficheros temporales ya borrados sobreviven a los
    tests que los crearon y ensucian a los siguientes según el orden.
    """
    import logging

    raiz = logging.getLogger()
    previos, nivel = list(raiz.handlers), raiz.level
    yield
    for h in list(raiz.handlers):
        raiz.removeHandler(h)
        try:
            h.close()
        except Exception:
            pass
    for h in previos:
        raiz.addHandler(h)
    raiz.setLevel(nivel)


# ── ¿Va a sonar? ─────────────────────────────────────────────────────

def test_caso_normal_suena():
    assert va_a_sonar(hablar=True, silenciada=False, tts_activo=True)


def test_con_la_voz_apagada_no_suena():
    """NOVA_TTS=false.

    Bug de NOVA4: `_decir` daba por hecho que `speaker.say()` acabaría
    disparando `on_end`, y con el TTS desactivado eso no pasa nunca. El
    orbe se quedaba en "pensando" para siempre desde la primera
    respuesta.
    """
    assert not va_a_sonar(hablar=True, silenciada=False, tts_activo=False)


def test_silenciada_desde_el_menu_no_suena():
    assert not va_a_sonar(hablar=True, silenciada=True, tts_activo=True)


def test_mensaje_que_no_se_dice_no_suena():
    """Los avisos de error se enseñan pero no se leen (hablar=False)."""
    assert not va_a_sonar(hablar=False, silenciada=False, tts_activo=True)


# ── Estado del orbe al terminar de hablar ────────────────────────────

@pytest.mark.parametrize("ocupada,escuchando,esperado", [
    (True,  True,  "pensando"),   # sigue trabajando: manda eso
    (True,  False, "pensando"),
    (False, True,  "escucha"),    # conversación continua abierta
    (False, False, "dormida"),
])
def test_estado_en_reposo(ocupada, escuchando, esperado):
    assert estado_en_reposo(ocupada=ocupada, escuchando=escuchando) == esperado


def test_ocupada_manda_sobre_escuchando():
    """Nunca "escucha" mientras el modelo aún está pensando.

    Si no, el orbe invita a hablar justo cuando la respuesta anterior
    todavía está en camino.
    """
    assert estado_en_reposo(ocupada=True, escuchando=True) == "pensando"


def test_despierta_pero_fuera_del_hueco_no_es_escuchar():
    """El bug que se veía a diario: "te escucho" mientras te ignoraba.

    Despierta dura veinte segundos; atenderte sin repetir su nombre,
    ocho. En los doce restantes el panel decía que sí.
    """
    assert estado_en_reposo(ocupada=False, escuchando=False) == "dormida"


# ── Logging ──────────────────────────────────────────────────────────

def test_debug_manda_el_detalle_al_fichero_y_deja_limpia_la_consola(tmp_path, logging_restaurado):
    """Cuando el audio falla es en directo: o quedó escrito, o no hay nada.

    Pero volcar el detalle también a la consola la vuelve ilegible justo
    cuando hace falta mirarla. Fichero: todo. Consola: INFO.
    """
    import logging

    from nova.bootstrap import configurar_logging

    destino = tmp_path / "nova.log"
    configurar_logging(debug=True, destino=destino)
    raiz = logging.getLogger()

    assert raiz.level == logging.DEBUG
    consolas = [h for h in raiz.handlers if isinstance(h, logging.StreamHandler)
                and not isinstance(h, RotatingFileHandler)]
    ficheros = [h for h in raiz.handlers if isinstance(h, RotatingFileHandler)]
    assert consolas and consolas[0].level == logging.INFO
    assert ficheros and ficheros[0].level == logging.DEBUG

    logging.getLogger("nova.prueba").debug("parcial oído: %r", "abre discord")
    for h in raiz.handlers:
        h.flush()
    assert "abre discord" in destino.read_text(encoding="utf-8")


def test_sin_debug_el_fichero_no_se_llena_de_parciales(tmp_path, logging_restaurado):
    import logging

    from nova.bootstrap import configurar_logging

    destino = tmp_path / "nova.log"
    configurar_logging(debug=False, destino=destino)
    logging.getLogger("nova.prueba").debug("parcial ruidoso")
    logging.getLogger("nova.prueba").info("esto sí")
    for h in logging.getLogger().handlers:
        h.flush()

    texto = destino.read_text(encoding="utf-8")
    assert "parcial ruidoso" not in texto
    assert "esto sí" in texto


def test_configurar_dos_veces_no_duplica_handlers(tmp_path, logging_restaurado):
    """En NOVA4 era `basicConfig`, que la segunda vez no hace nada.

    Aquí se reconfigura de verdad, así que hay que limpiar lo anterior o
    cada línea de log saldría repetida.
    """
    import logging

    from nova.bootstrap import configurar_logging

    configurar_logging(destino=tmp_path / "a.log")
    n_primera = len(logging.getLogger().handlers)
    configurar_logging(destino=tmp_path / "b.log")
    assert len(logging.getLogger().handlers) == n_primera


def test_el_fichero_de_log_rota_y_no_crece_sin_fin(tmp_path, logging_restaurado):
    """Con --debug cada frase deja varias líneas; sin rotación se come el disco."""
    from nova.bootstrap import configurar_logging

    configurar_logging(destino=tmp_path / "nova.log")
    ficheros = [h for h in __import__("logging").getLogger().handlers
                if isinstance(h, RotatingFileHandler)]
    assert ficheros[0].maxBytes > 0
    assert ficheros[0].backupCount > 0


# ── Modo ligero: qué pasa cuando un juego se lleva la VRAM ───────────
#
# Se prueba la máquina de estados sin construir la app entera: lo que
# puede romperse aquí es CUÁNDO se cambia, no cómo se dibuja.

class _LLMFalso:
    def __init__(self, modelo: str) -> None:
        self.model = modelo
        self.residente = 1.0
        self.cambios: list[str] = []

    def residencia(self) -> float:
        return self.residente

    def tiene_modelo(self, nombre: str) -> bool:  # noqa: ARG002
        return True

    def usar_modelo(self, nombre: str) -> None:
        self.cambios.append(nombre)
        self.model = nombre


class _UIFalsa:
    def __init__(self) -> None:
        self.avisos: list[bool] = []

    def set_modo_ligero(self, activo: bool) -> None:
        self.avisos.append(activo)


class _NovaPelada:
    """Sólo las dos piezas que deciden el modo ligero."""

    _ocupada = False
    _modo_ligero = False
    _ligero_disponible = None
    _revisar_recursos = Nova._revisar_recursos
    _hay_modelo_ligero = Nova._hay_modelo_ligero

    def __init__(self) -> None:
        self.llm = _LLMFalso(CONFIG.model)
        self.ui = _UIFalsa()


def test_baja_al_modelo_ligero_cuando_la_gpu_se_llena():
    n = _NovaPelada()
    n._revisar_recursos()
    assert not n._modo_ligero

    n.llm.residente = 0.10          # lo medido con un juego abierto
    n._revisar_recursos()
    assert n._modo_ligero
    assert n.llm.model == CONFIG_LIGERO


def test_no_repite_el_cambio_mientras_dura_la_escasez():
    """Cambiar descarga el modelo anterior: hacerlo cada 5 s sería peor
    que la lentitud que intenta arreglar."""
    n = _NovaPelada()
    n.llm.residente = 0.10
    for _ in range(5):
        n._revisar_recursos()
    assert n.llm.cambios == [CONFIG_LIGERO]


def test_no_cambia_de_modelo_a_mitad_de_una_respuesta():
    n = _NovaPelada()
    n._ocupada = True
    n.llm.residente = 0.10
    n._revisar_recursos()
    assert not n._modo_ligero


def test_vuelve_al_bueno_al_cerrar_el_juego():
    n = _NovaPelada()
    n.llm.residente = 0.10
    n._revisar_recursos()
    n.llm.residente = 1.0
    n._revisar_recursos()
    assert not n._modo_ligero
    assert n.llm.model == CONFIG_NORMAL
    # Y la interfaz se entera de las dos veces: el usuario tiene que
    # poder saber por qué NOVA fue lenta un rato.
    assert n.ui.avisos == [True, False]


# ── Dormirse no puede arrastrar un "sí/no" pendiente ──────────────────
#
# Bug real: si dices "adiós" con una confirmación en el aire (p.ej.
# borrar algo), NOVA se dormía y la dejaba en la cola. La próxima vez
# que la despertaras, un "sí" a CUALQUIER otra cosa ejecutaba esa acción
# vieja y olvidada — que puede ser un borrado.

class _GlowFalso:
    def apagar(self):
        pass


class _UIFalsaDormir:
    def __init__(self):
        self.estados = []

    def set_dicho(self, t):
        pass

    def set_respondido(self, t):
        pass

    def set_estado(self, e):
        self.estados.append(e)


class _NovaDormida:
    _ocupada = False
    _respuesta_en_curso = ""
    _avisos_pendientes: list = []

    def __init__(self, pendientes):
        self.glow = _GlowFalso()
        self.ui = _UIFalsaDormir()
        self._pendientes = pendientes
        self.dichos = []

    def _decir(self, *a, **kw):
        self.dichos.append(a)

    _al_dormir = Nova._al_dormir


def test_dormirse_descarta_una_confirmacion_sin_contestar():
    from nova.tools.registry import PendingConfirmation
    n = _NovaDormida([PendingConfirmation(tool="test.borrar", args={}, summary="borrar algo")])
    n._al_dormir("silencio")
    assert n._pendientes == []


def test_dormirse_por_despedida_tambien_descarta_lo_pendiente():
    from nova.tools.registry import PendingConfirmation
    n = _NovaDormida([PendingConfirmation(tool="test.borrar", args={}, summary="borrar algo")])
    n._al_dormir("despedida")
    assert n._pendientes == []


def test_dormirse_sin_nada_pendiente_no_hace_nada_raro():
    n = _NovaDormida([])
    n._al_dormir("silencio")
    assert n._pendientes == []


# ── Sorda manda sobre lo que enseña el panel ─────────────────────────

def test_sorda_nunca_dice_te_escucho():
    """Aunque la ventana de seguimiento siga abierta: si has apagado el
    oído, el panel no puede decir «te escucho» ni un segundo."""
    assert estado_en_reposo(ocupada=False, escuchando=True, sorda=True) == "dormida"
    assert estado_en_reposo(ocupada=True, escuchando=True, sorda=True) == "dormida"


def test_sin_estar_sorda_todo_sigue_igual():
    assert estado_en_reposo(ocupada=False, escuchando=True, sorda=False) == "escucha"


# ── El turno no puede llevarse el proceso por delante ─────────────────
#
# El 01/09 NOVA se cerró seis veces seguidas, siempre justo después de
# entender una orden. La causa: `build_system_prompt` se quedó sin
# actualizar cuando los plugins añadieron su personalidad al prompt, así
# que `procesar` lanzaba un TypeError... dentro de un slot de Qt, que es
# la única excepción de Python que mata el proceso entero (PyQt5 llama a
# `qFatal()`). Dos pruebas, una por cada mitad del fallo.


def test_el_prompt_admite_la_personalidad_de_los_plugins():
    from nova.core.conversation import build_system_prompt

    prompt = build_system_prompt("## Contexto", "- odio el cilantro", "Hablas como un pirata")
    assert "Hablas como un pirata" in prompt
    # Al final: un plugin matiza el carácter, no borra las reglas.
    assert prompt.index("Hablas como un pirata") > prompt.index("odio el cilantro")


def test_sin_plugins_el_prompt_no_cambia():
    from nova.core.conversation import build_system_prompt

    assert build_system_prompt("## Contexto", "- algo") == build_system_prompt(
        "## Contexto", "- algo", ""
    )


class _AgenteRoto:
    def run(self, *a, **k):  # noqa: ANN002, ANN003, ANN201, ARG002
        raise TypeError("build_system_prompt() takes from 0 to 2 positional arguments")

    def confirm(self, *a, **k):  # noqa: ANN002, ANN003, ANN201, ARG002
        raise RuntimeError("la herramienta explotó")


def test_un_turno_roto_contesta_en_vez_de_matar_el_proceso():
    """Lo que importa no es el mensaje: es que `listo` se emita.

    Sin esa señal, `_ocupada` se queda en True para siempre y NOVA
    responde "todavía estoy con lo anterior" a todo lo que le digas
    después — suponiendo que llegue a haber un después.
    """
    from nova.app import _Worker
    from nova.core.awareness import Awareness
    from nova.core.conversation import Conversation

    trabajador = _Worker(_AgenteRoto(), Conversation(), Awareness())
    recibido = []
    trabajador.listo.connect(lambda t, h, d: recibido.append(t))

    trabajador.procesar("que estoy jugando")

    assert len(recibido) == 1
    assert recibido[0]


def test_una_confirmacion_rota_tampoco_deja_a_nova_colgada():
    from nova.app import _Worker
    from nova.core.awareness import Awareness
    from nova.core.conversation import Conversation
    from nova.tools import PendingConfirmation

    trabajador = _Worker(_AgenteRoto(), Conversation(), Awareness())
    recibido = []
    trabajador.listo.connect(lambda t, h, d: recibido.append(t))

    trabajador.confirmar(PendingConfirmation(tool="files.delete", args={}, summary="borrar"))

    assert len(recibido) == 1
