"""La interfaz, en lo que se puede probar sin abrir ventanas.

Lo que se pinta no se testea aquí; lo que se DECIDE, sí: qué texto y qué
color le toca a cada acción, y que la paleta diga cosas distintas para
estados distintos.
"""

from __future__ import annotations

import math

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


# ── El borde de la pantalla ──────────────────────────────────────────
#
# Esto SÍ se pinta de verdad, en una imagen fuera de pantalla, porque lo
# que puede romperse aquí sólo se ve en los píxeles: que un cambio de
# opacidad tape el juego, o que quede una costura entre tramos.

def _pintar_borde(ancho: int = 1920, alto: int = 1080):
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    QtWidgets = pytest.importorskip("PyQt5.QtWidgets")
    from PyQt5.QtGui import QImage

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    assert app is not None
    from nova.ui.glow import GlowBorder

    borde = GlowBorder()
    borde.resize(ancho, alto)
    borde._intensidad = 1.0
    borde._activo = True
    borde._fase = math.pi / 2  # el pico del latido: el peor caso
    img = QImage(ancho, alto, QImage.Format_ARGB32_Premultiplied)
    img.fill(0)
    borde.render(img)
    return img


def _alfa(img, x: int, y: int) -> int:
    return (img.pixel(x, y) >> 24) & 0xFF


def test_el_borde_no_tapa_el_centro_de_la_pantalla():
    """Lo de siempre que puede romperse: dejar de ser transparente."""
    img = _pintar_borde()
    assert _alfa(img, 960, 540) == 0


def test_las_esquinas_pesan_mas_que_el_centro_de_los_lados():
    img = _pintar_borde()
    esquina = _alfa(img, 0, 0)
    centro_del_lado = _alfa(img, 960, 0)
    assert esquina > centro_del_lado * 2


def test_el_borde_no_llega_a_tapar_lo_que_hay_debajo():
    """En las esquinas se cruzan dos lados y la opacidad se acumula.

    Si esto sube, deja de ser un aviso periférico y pasa a ser una
    ventana encima del juego.
    """
    img = _pintar_borde()
    pico = max(_alfa(img, x, y) for x in (0, 1919) for y in (0, 1079))
    assert pico <= 0.60 * 255


def test_no_quedan_costuras_entre_tramos():
    """Se pinta por tramos; un salto grande sería una raya visible.

    El límite es 6/255: medido, el mayor salto real es 5 y está en la
    pendiente de la esquina (x=1..7), no en las juntas.
    """
    img = _pintar_borde()
    fila = [_alfa(img, x, 0) for x in range(1920)]
    saltos = [abs(fila[i + 1] - fila[i]) for i in range(len(fila) - 1)]
    assert max(saltos) <= 6
