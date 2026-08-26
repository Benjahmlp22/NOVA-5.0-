"""Decisiones de la orquestación, sin montar Qt ni Ollama.

Las dos funciones que se prueban aquí viven sueltas en `app.py` justo
para esto: eran `if` incrustados dentro de métodos que pintan widgets, y
sus dos bugs (orbe clavado, ciclo sin cerrar) no se podían probar.
"""

from __future__ import annotations

from logging.handlers import RotatingFileHandler

import pytest

from nova.app import estado_en_reposo, va_a_sonar


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

@pytest.mark.parametrize("ocupada,despierta,esperado", [
    (True,  True,  "pensando"),   # sigue trabajando: manda eso
    (True,  False, "pensando"),
    (False, True,  "escucha"),    # conversación continua abierta
    (False, False, "dormida"),
])
def test_estado_en_reposo(ocupada, despierta, esperado):
    assert estado_en_reposo(ocupada=ocupada, despierta=despierta) == esperado


def test_ocupada_manda_sobre_despierta():
    """Nunca "escucha" mientras el modelo aún está pensando.

    Si no, el orbe invita a hablar justo cuando la respuesta anterior
    todavía está en camino.
    """
    assert estado_en_reposo(ocupada=True, despierta=True) == "pensando"


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
