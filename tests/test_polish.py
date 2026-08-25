"""El pulido de respuestas: quitar tics de chatbot de forma determinista.

Los casos vienen de salidas reales de qwen2.5:3b, no inventados.
"""

from __future__ import annotations

import pytest

from nova.core.agent import _extraer_recuerdo
from nova.core.polish import (
    MAX_CARACTERES_VOZ,
    pulir,
    recortar_para_voz,
)


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


# ── Agujeros medidos en NOVA4 (log del 25/07 y smoke del 26/08) ──────

@pytest.mark.parametrize("entrada,esperado", [
    # Lo que NOVA contestó de verdad a "¿cómo estás?" (log 23:11:27). El
    # filtro de NOVA4 lo dejaba pasar entero: exigía "ayudarTE" y aquí el
    # pronombre va delante del verbo.
    ("Todo bien. ¿En qué te puedo ayudar hoy?", "Todo bien."),
    ("¡Hola! ¿En qué te puedo ayudar hoy?", "¡Hola!"),
    # Cierre del monólogo de las 23:11:38, con el "aquí" duplicado.
    ("Tu PC está ligero. ¡Aquí estoy para ayudarte desde aquí!",
     "Tu PC está ligero."),
    ("Hecho. ¿Puedo ayudar con algo?", "Hecho."),
])
def test_coletillas_que_nova4_dejaba_pasar(entrada, esperado):
    assert pulir(entrada) == esperado


@pytest.mark.parametrize("entrada,esperado", [
    # Smoke del 26/08 sobre qwen3.5:4b, con el filtro anterior ya puesto.
    ("Ahora son las 01:52. ¿Necesitas que haga algo más por ti?",
     "Ahora son las 01:52."),
    ("Estás viendo a Claude. ¿Quieres que haga algo más?",
     "Estás viendo a Claude."),
    ("Hecho. ¿Puedo hacer alguna otra cosa?", "Hecho."),
])
def test_pregunta_por_algo_mas_es_relleno_lo_envuelva_el_verbo_que_sea(entrada, esperado):
    assert pulir(entrada) == esperado


def test_coletilla_de_ayuda_tambien_en_medio():
    """Caso real del 26/08: el modelo la coló y siguió hablando.

    El resto de filtros van anclados al final porque una pregunta en
    medio suele ser contenido. Ésta no: "¿en qué puedo ayudarte?" es
    relleno esté donde esté.
    """
    sucio = "¡Hola! ¿En qué puedo ayudarte hoy? Ya has abierto un juego."
    assert pulir(sucio) == "¡Hola! Ya has abierto un juego."


def test_oferta_de_accion_concreta_sigue_viva():
    """Una oferta accionable no es relleno: se conserva a propósito.

    Es la misma decisión que `test_no_borra_pregunta_util_que_menciona_ayudar`,
    repetida aquí porque en NOVA5 se amplió el filtro de coletillas y
    conviene que un cambio futuro que se pase de listo rompa un test.
    """
    assert "cerrar" in pulir("Tienes Discord y Opera abiertos. ¿Quieres cerrar alguna?")


# ── Longitud de lo que se dice en voz alta ───────────────────────────

def test_respuesta_corta_no_se_toca():
    corta = "Listo, he abierto Chrome."
    assert recortar_para_voz(corta) == corta


def test_monologo_real_se_queda_en_dos_frases():
    """El caso exacto del log: 200 caracteres = 12.34 s hablando."""
    largo = (
        "¡Bien! ¿Qué tal te va el break room? Ya veo que tu PC está muy "
        "ligero con solo un 13% de carga y más del doble de RAM libre. Si "
        "necesitas algo mientras estés ahí, ¡aquí estoy para ayudarte desde aquí!"
    )
    corto = recortar_para_voz(largo)
    assert corto == "¡Bien! ¿Qué tal te va el break room?"
    assert len(corto) < len(largo)


def test_frase_unica_kilometrica_se_corta_por_palabra():
    """Sin puntos que cortar, manda el tope de caracteres."""
    largo = "vale " * 80
    corto = recortar_para_voz(largo)
    assert len(corto) <= MAX_CARACTERES_VOZ + 1  # +1 por el punto final
    assert "val." not in corto  # nunca a mitad de palabra
    assert corto.endswith(".")


def test_recorte_respeta_signos_de_cierre():
    largo = "¿Quieres que te lo abra ahora mismo o prefieres luego? " * 5
    corto = recortar_para_voz(largo)
    assert corto.endswith(("?", "."))


def test_recorte_de_vacio_es_vacio():
    assert recortar_para_voz("") == ""
    assert recortar_para_voz("   ") == ""


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
