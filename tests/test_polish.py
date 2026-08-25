"""El pulido de respuestas: quitar tics de chatbot de forma determinista.

Los casos vienen de salidas reales de qwen2.5:3b, no inventados.
"""

from __future__ import annotations

import pytest

from nova.core.agent import _extraer_recuerdo
from nova.core.polish import pulir


@pytest.mark.parametrize("entrada,esperado", [
    ("Son las 10:36.  ¿Necesitas algo más?", "Son las 10:36."),
    ("Listo, Chrome abierto. ¿En qué más puedo ayudarte?", "Listo, Chrome abierto."),
    ("Tu IP es 192.168.1.10. ¿Hay algo más en lo que pueda ayudarte?", "Tu IP es 192.168.1.10."),
    ("He guardado la nota. Avísame si necesitas ayuda.", "He guardado la nota."),
    ("Tu color favorito es el verde. ¡Estoy aquí para ayudarte!", "Tu color favorito es el verde."),
    ("Hecho. No dudes en preguntar si necesitas algo.", "Hecho."),
])
def test_quita_relleno_del_final(entrada, esperado):
    assert pulir(entrada) == esperado


@pytest.mark.parametrize("entrada,esperado", [
    # Variantes que el modelo inventa sobre la marcha (casos reales).
    ("Ahora es sábado, 10:38. ¿Cómo puedo asistirte hoy?", "Ahora es sábado, 10:38."),
    ("Estás hablando conmigo. ¿Hay algo en lo que pueda ayudarte específicamente?",
     "Estás hablando conmigo."),
    ("Hecho. ¿Puedo servirte en algo?", "Hecho."),
])
def test_red_generica_pilla_variantes_nuevas(entrada, esperado):
    assert pulir(entrada) == esperado


def test_no_borra_pregunta_util_que_menciona_ayudar():
    """Si la pregunta es el contenido, no es relleno."""
    texto = "No encontré Discord. ¿Quieres que refresque el índice?"
    assert "refresque" in pulir(texto)


def test_quita_relleno_encadenado():
    sucio = "Captura hecha. ¿Necesitas algo más? ¿En qué puedo ayudarte hoy?"
    assert pulir(sucio) == "Captura hecha."


def test_respeta_pregunta_legitima_en_medio():
    """Una pregunta real no es relleno: no se toca."""
    texto = "¿Necesitas algo más de la tienda? Te lo apunto."
    assert "tienda" in pulir(texto)


def test_quita_imagen_markdown_inventada():
    """Caso real: la captura se anunció con una URL que no existe."""
    sucio = "He hecho una captura. ![aquí está](https://yourserverurl/captura_1.png)"
    limpio = pulir(sucio)
    assert "yourserverurl" not in limpio
    assert "![" not in limpio
    assert "He hecho una captura." in limpio


def test_enlace_markdown_deja_el_texto():
    assert pulir("Mira [la documentación](https://example.com/docs).") == "Mira la documentación."


def test_nunca_devuelve_vacio():
    """Si limpiar se lo come todo, algo hay que responder."""
    assert pulir("¿Necesitas algo más?") == "Listo."
    assert pulir("") == ""


def test_texto_limpio_no_se_toca():
    bueno = "Listo, he abierto Chrome."
    assert pulir(bueno) == bueno


# ── Atajo de memoria ─────────────────────────────────────────────────

@pytest.mark.parametrize("frase,dato", [
    ("recuerda que mi color favorito es el verde", "mi color favorito es el verde"),
    ("Recuerda que mi proyecto está en la unidad D", "mi proyecto está en la unidad D"),
    ("acuérdate de que juego a Stormworks", "juego a Stormworks"),
    ("nova, apúntate que mi cumple es en marzo", "mi cumple es en marzo"),
    ("no olvides que odio el cilantro", "odio el cilantro"),
])
def test_detecta_peticion_de_recordar(frase, dato):
    assert _extraer_recuerdo(frase) == dato


@pytest.mark.parametrize("frase", [
    "abre chrome",
    "qué hora es",
    "recuerda",
    "cuál es mi color favorito",
])
def test_no_confunde_ordenes_normales(frase):
    assert _extraer_recuerdo(frase) == ""
