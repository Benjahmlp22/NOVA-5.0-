"""La interfaz, en lo que se puede probar sin abrir ventanas.

Lo que se pinta no se testea aquí; lo que se DECIDE, sí: qué texto y qué
color le toca a cada acción, y que la paleta diga cosas distintas para
estados distintos.
"""

from __future__ import annotations

import pytest

from nova.core.agent import _dato_visible
from nova.ui.actividad import MAX_DETALLE, describir

# ── Traducción de herramienta a lenguaje humano ──────────────────────

@pytest.mark.parametrize("herramienta,dato,esperado", [
    ("app.open", "Discord", "Abriendo Discord"),
    ("app.close", "Chrome", "Cerrando Chrome"),
    ("web.search", "precio de la 4070", "Buscando en internet: precio de la 4070"),
    ("file.delete", "notas.txt", "Borrando notas.txt"),
    ("screen.capture", "", "Capturando la pantalla"),
    ("pc.status", "", "Mirando cómo va el PC"),
])
def test_describir_dice_algo_que_se_entiende(herramienta, dato, esperado):
    _tipo, texto = describir(herramienta, dato)
    assert texto == esperado


def test_abrir_y_cerrar_no_pueden_parecer_lo_mismo():
    """Es el motivo de que cada acción lleve tipo además de texto.

    De reojo, en una barra pequeña, el color es lo único que se lee.
    """
    tipo_abrir, _ = describir("app.open", "Discord")
    tipo_cerrar, _ = describir("app.close", "Discord")
    assert tipo_abrir != tipo_cerrar


def test_una_herramienta_desconocida_no_deja_hueco():
    """Añadir una herramienta y olvidar la traducción no rompe el panel."""
    tipo, texto = describir("cosa.nueva", "algo")
    assert tipo
    assert "algo" in texto


def test_sin_dato_no_queda_una_frase_coja():
    """"Abriendo " a secas se lee como un fallo. Mejor sólo el verbo."""
    _tipo, texto = describir("app.open", "")
    assert not texto.endswith(" ")
    assert texto.strip() == texto
    assert texto


def test_un_dato_larguisimo_se_recorta():
    _tipo, texto = describir("web.search", "x" * 200)
    assert len(texto) < 200
    assert "…" in texto


# ── De los argumentos del modelo al dato que se enseña ───────────────

@pytest.mark.parametrize("argumentos,esperado", [
    ({"name": "Discord"}, "Discord"),
    ({"query": "precio"}, "precio"),
    ({"path": "notas.txt"}, "notas.txt"),
    ({"level": 30}, ""),          # un número no dice nada al verlo
    ({}, ""),
    (None, ""),
])
def test_dato_visible(argumentos, esperado):
    assert _dato_visible(argumentos) == esperado


def test_dato_visible_prefiere_el_nombre():
    """Con varios candidatos, el que el usuario reconocería."""
    assert _dato_visible({"query": "algo", "name": "Discord"}) == "Discord"


# ── Paleta ───────────────────────────────────────────────────────────

def _distancia_de_tono(a, b) -> int:  # noqa: ANN001
    """Grados de separación en el círculo de color.

    Se compara el TONO y no los canales RGB: la suma de diferencias de
    canal está dominada por el azul y da por "muy distintos" a dos
    colores que el ojo lee igual. Con eso, el primer verde menta que
    probé pasaba el test y en pantalla no se distinguía del azul.
    """
    diferencia = abs(a.hue() - b.hue())
    return min(diferencia, 360 - diferencia)


def test_hablar_y_escuchar_tienen_colores_bien_distintos():
    """Era un requisito explícito: ver que está hablando sin leer nada."""
    from nova.ui.panel import COLORES

    assert _distancia_de_tono(COLORES["hablando"], COLORES["escucha"]) >= 60


def test_hablar_no_se_confunde_con_error():
    from nova.ui.panel import COLORES

    assert _distancia_de_tono(COLORES["hablando"], COLORES["error"]) >= 60


def test_cada_estado_tiene_color_y_etiqueta():
    from nova.ui.panel import COLORES, ETIQUETAS

    assert set(COLORES) == set(ETIQUETAS)
    for estado, etiqueta in ETIQUETAS.items():
        assert etiqueta, f"{estado} sin etiqueta"


def test_el_orbe_usa_la_misma_paleta_que_el_panel():
    """Dos tablas de color para lo mismo se desincronizan a la primera."""
    from nova.ui.orb import _COLORES
    from nova.ui.panel import COLORES

    assert _COLORES is COLORES


def test_los_tipos_de_accion_tienen_color():
    from nova.ui.panel import _COLOR_ACCION, color_de_accion

    for herramienta in ("app.open", "app.close", "web.search", "file.create",
                        "memory.remember", "pc.status"):
        tipo, _ = describir(herramienta, "x")
        assert tipo in _COLOR_ACCION, f"{herramienta} usa un tipo sin color"
    # Y un tipo inventado tampoco revienta.
    assert color_de_accion("inexistente") is not None


def test_el_recorte_de_detalle_es_razonable():
    assert 20 <= MAX_DETALLE <= 60
