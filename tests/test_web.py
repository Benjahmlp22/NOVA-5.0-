"""Búsqueda en internet: sin red de verdad, que un test no puede depender de eso.

Lo que se fija aquí es lo que no puede fallar: que el resultado vuelva
como texto en español (decisión sagrada nº3) y que quedarse sin conexión
sea una frase, no una excepción.
"""

from __future__ import annotations

import sys

import pytest

from nova.tools import build_registry
from nova.tools.web import _acortar, _limpiar, buscar, redactar


class _DDGSFalso:
    """Sustituye al paquete ddgs. Devuelve o revienta, según se le pida."""

    def __init__(self, resultados=None, explota: Exception | None = None) -> None:
        self.resultados = resultados or []
        self.explota = explota
        self.consultas: list[str] = []

    def __call__(self, *a, **kw):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def text(self, consulta, **kw):
        self.consultas.append(consulta)
        if self.explota:
            raise self.explota
        return self.resultados


@pytest.fixture
def ddgs_falso(monkeypatch):
    def instalar(resultados=None, explota=None):
        falso = _DDGSFalso(resultados, explota)
        modulo = type(sys)("ddgs")
        modulo.DDGS = falso
        monkeypatch.setitem(sys.modules, "ddgs", modulo)
        return falso

    return instalar


RESULTADOS = [
    {"title": "RTX 4070 al mejor precio", "body": "Desde 549 euros en varias tiendas."},
    {"title": "Análisis de la 4070", "body": "Rinde como una 3080 gastando menos."},
    {"title": "Ofertas de hoy", "body": "Bajada de 40 euros esta semana."},
]


# ── El resultado vuelve como texto, no como datos ────────────────────

def test_el_resultado_es_una_frase_en_espanol(ddgs_falso):
    """Decisión sagrada nº3: al modelo se le da texto redactado.

    Con un volcado crudo, un modelo pequeño no reconoce que la tarea ya
    está hecha, repite la llamada hasta agotar las rondas y contesta en
    inglés.
    """
    ddgs_falso(RESULTADOS)
    r = buscar("precio de la 4070")

    assert r.ok
    assert "precio de la 4070" in r.message
    assert "549 euros" in r.message
    assert "{" not in r.message and "[" not in r.message


def test_se_queda_con_los_tres_primeros(ddgs_falso):
    ddgs_falso(RESULTADOS)
    r = buscar("lo que sea")
    assert r.message.count("\n") == 3  # cabecera + 3 resultados


def test_sin_resultados_lo_dice_sin_inventar(ddgs_falso):
    ddgs_falso([])
    r = buscar("asdkjhasdkjh")
    assert "no he encontrado nada" in r.message.lower()


# ── Degradación cuando no hay red ────────────────────────────────────

def test_sin_conexion_nova_sigue_viva(ddgs_falso):
    """NOVA tiene a alguien delante esperando: callarse no es una opción."""
    ddgs_falso(explota=OSError("getaddrinfo failed"))
    r = buscar("cualquier cosa")

    assert not r.ok
    assert "conexión" in r.message.lower()


def test_un_timeout_tampoco_revienta(ddgs_falso):
    ddgs_falso(explota=TimeoutError("tardó demasiado"))
    r = buscar("cualquier cosa")
    assert not r.ok
    assert r.message


def test_sin_el_paquete_lo_dice_claro(monkeypatch):
    """Y dice cómo arreglarlo, no sólo que falla."""
    monkeypatch.setitem(sys.modules, "ddgs", None)
    r = buscar("algo")
    assert not r.ok
    assert "ddgs" in r.message


def test_consulta_vacia_pregunta(ddgs_falso):
    ddgs_falso(RESULTADOS)
    r = buscar("   ")
    assert not r.ok
    assert "?" in r.message


# ── Limpieza del texto ───────────────────────────────────────────────

def test_se_quitan_las_etiquetas_html():
    assert _limpiar("<b>Precio</b>: 549 euros") == "Precio: 549 euros"


def test_se_traducen_las_entidades_html():
    """Leídas en voz alta, "&nbsp;" suena a "ampersand ene be ese pe".

    La primera versión traducía una lista a mano de tres entidades y se
    le colaba justo &nbsp;, que es de las más frecuentes.
    """
    assert _limpiar("549&nbsp;euros") == "549 euros"
    assert _limpiar("dijo &quot;hola&quot;") == 'dijo "hola"'
    assert _limpiar("Marks &amp; Spencer") == "Marks & Spencer"
    assert _limpiar("caf&eacute;") == "café"


def test_los_resumenes_largos_se_cortan_por_palabra():
    largo = "palabra " * 100
    corto = _acortar(largo, tope=50)
    assert len(corto) <= 51
    assert corto.endswith("…")
    assert "palabr…" not in corto  # nunca a mitad de palabra


def test_un_resumen_corto_no_se_toca():
    assert _acortar("Cuesta 549 euros.") == "Cuesta 549 euros."


def test_redactar_aguanta_resultados_a_medias():
    """Un resultado sin cuerpo no puede tumbar la respuesta entera."""
    texto = redactar("algo", [{"title": "Sólo título"}, {"body": "Sólo cuerpo"}])
    assert "Sólo título" in texto
    assert "Sólo cuerpo" in texto


# ── Permisos ─────────────────────────────────────────────────────────

def test_buscar_no_pide_confirmacion():
    """Se usa a mitad de frase; preguntar la haría inútil."""
    reg = build_registry("estricto")
    assert not reg.needs_confirmation(reg.get("web.search"))


def test_abrir_una_url_si_pasa_por_politica():
    """Abrir el navegador sí toca el escritorio del usuario."""
    reg = build_registry("estricto")
    assert reg.needs_confirmation(reg.get("web.open"))


def test_abrir_algo_que_no_es_una_url_no_hace_nada():
    from nova.tools.web import abrir_url

    r = abrir_url("borra todo")
    assert not r.ok


def test_el_modelo_ve_la_herramienta_de_busqueda():
    reg = build_registry()
    ofrecidas = {e["function"]["name"] for e in reg.llm_schemas()}
    assert "web_search" in ofrecidas
