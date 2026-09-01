"""Que NOVA no vuelva a buscar lo que ya buscó.

Todo este módulo sale de una queja de Benja del 01/09: «le dije búscame
quién ganó el mundial y al volverle a hablar lo buscó otra vez». Se
atacó por dos sitios a la vez, y aquí se prueban los dos: los apuntes,
que hacen que el modelo SEPA que ya lo miró, y la caché, que hace que si
aun así vuelve a llamar, sea instantáneo y conteste lo mismo.
"""

from __future__ import annotations

import pytest

from nova.core.conversation import LARGO_APUNTE, MAX_APUNTES, Conversation, build_system_prompt
from nova.tools import web

# ── Los apuntes ──────────────────────────────────────────────────────

def test_lo_averiguado_sobrevive_al_turno():
    """El fallo era estructural: el resultado moría con el turno."""
    conv = Conversation()
    conv.apuntar("web.search", "Argentina ganó el Mundial de 2022 en Qatar.")

    assert "Argentina" in conv.apuntes()
    assert "web.search" in conv.apuntes()


def test_los_apuntes_entran_en_el_prompt_con_la_orden_de_no_repetir():
    prompt = build_system_prompt("", "", "", "- (web.search) Ganó Argentina.")
    assert "Ganó Argentina" in prompt
    assert "NO vuelvas a llamar" in prompt
    # Y la orden va DESPUÉS del dato: un modelo pequeño la sigue mejor
    # cuando ya tiene delante a qué se refiere.
    assert prompt.index("Ganó Argentina") < prompt.index("NO vuelvas a llamar")


def test_sin_apuntes_el_prompt_no_cambia():
    assert build_system_prompt("## Ctx", "- algo") == build_system_prompt("## Ctx", "- algo", "", "")


def test_el_apunte_nuevo_de_una_herramienta_sustituye_al_viejo():
    """Vale lo último que se sabe, no la primera versión."""
    conv = Conversation()
    conv.apuntar("pc.hora", "Son las seis.")
    conv.apuntar("pc.hora", "Son las siete.")

    assert "siete" in conv.apuntes()
    assert "seis" not in conv.apuntes()


def test_dos_herramientas_distintas_conviven():
    conv = Conversation()
    conv.apuntar("web.search", "Ganó Argentina.")
    conv.apuntar("pc.hora", "Son las seis.")
    assert "Argentina" in conv.apuntes()
    assert "seis" in conv.apuntes()


def test_los_apuntes_no_crecen_sin_freno():
    """Van en el prompt de CADA turno: cada uno se paga en prefill."""
    conv = Conversation()
    for i in range(MAX_APUNTES + 5):
        conv.apuntar(f"herramienta.{i}", f"resultado {i}")
    assert len(conv.apuntes().splitlines()) == MAX_APUNTES


def test_un_resultado_largo_se_recorta():
    conv = Conversation()
    conv.apuntar("codigo.ver", "x" * 5000)
    assert len(conv.apuntes()) < LARGO_APUNTE + 100


def test_un_resultado_vacio_no_se_apunta():
    conv = Conversation()
    conv.apuntar("web.search", "   ")
    assert conv.apuntes() == ""


def test_al_limpiar_la_conversacion_se_olvida_todo():
    conv = Conversation()
    conv.add_user("hola")
    conv.apuntar("web.search", "Ganó Argentina.")
    conv.clear()
    assert conv.apuntes() == ""
    assert len(conv) == 0


# ── La caché de búsquedas ────────────────────────────────────────────

@pytest.fixture(autouse=True)
def cache_limpia():
    web.olvidar_busquedas()
    yield
    web.olvidar_busquedas()


def test_la_misma_pregunta_dicha_de_otra_forma_es_la_misma():
    """Whisper pone las tildes unas veces sí y otras no.

    Sin normalizarlas la caché fallaba justo en el caso para el que se
    escribió: medido, la segunda consulta volvía a salir a la red y
    tardaba 1.75 s en contestar lo que ya sabía.
    """
    assert web._clave_cache("quien gano el mundial") == web._clave_cache(
        "¿Quién ganó el Mundial?"
    )
    # Y da igual el orden de las palabras.
    assert web._clave_cache("quien gano el mundial") == web._clave_cache(
        "el mundial quien gano"
    )
    # Pero dos preguntas distintas siguen siendo distintas.
    assert web._clave_cache("quien gano el mundial") != web._clave_cache(
        "cuanto cuesta una fuente Corsair"
    )


def test_lo_guardado_se_devuelve_tal_cual():
    web._a_la_cache("quién ganó el mundial", "Ganó Argentina en 2022.")
    assert web._de_la_cache("quien gano el mundial") == "Ganó Argentina en 2022."


def test_lo_caducado_ya_no_vale(monkeypatch):
    web._a_la_cache("quién ganó el mundial", "Ganó Argentina.")
    ahora = [0.0]
    monkeypatch.setattr(web.time, "monotonic", lambda: ahora[0])
    web._a_la_cache("otra cosa", "algo")
    ahora[0] = web.CACHE_S + 1
    assert web._de_la_cache("otra cosa") is None


def test_la_cache_tiene_tope():
    for i in range(120):
        web._a_la_cache(f"consulta numero {i}", f"respuesta {i}")
    assert len(web._cache) <= 100


def test_buscar_sin_consulta_no_toca_la_cache():
    r = web.buscar("")
    assert not r.ok
    assert web._cache == {}
