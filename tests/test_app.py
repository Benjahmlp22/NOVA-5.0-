"""Decisiones de la orquestación, sin montar Qt ni Ollama.

Las dos funciones que se prueban aquí viven sueltas en `app.py` justo
para esto: eran `if` incrustados dentro de métodos que pintan widgets, y
sus dos bugs (orbe clavado, ciclo sin cerrar) no se podían probar.
"""

from __future__ import annotations

import pytest

from nova.app import estado_en_reposo, va_a_sonar

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
