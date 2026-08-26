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
    """Un resultado a medias no puede tumbar la respuesta entera."""
    completo = {"title": "Precio", "href": "https://a.es/x",
                "body": "La fuente de 750 vatios cuesta noventa euros en varias tiendas."}
    texto = redactar("fuente 750 vatios", [{"title": "Sólo título"}, completo])
    assert "noventa euros" in texto


def test_un_resultado_sin_sustancia_se_descarta():
    """Un título de tienda sin datos no es un resultado, es ruido."""
    texto = redactar("precio", [{"title": "Ofertas", "href": "https://a.es", "body": "Ver más"}])
    assert "no he encontrado nada útil" in texto.lower()


def test_no_se_repite_el_mismo_dominio():
    """Tres resultados de Amazon no son tres resultados."""
    from nova.tools.web import _palabras_clave, _utiles

    crudos = [
        {"title": "A", "href": "https://www.amazon.es/1",
         "body": "La RTX 4070 cuesta 549 euros en esta tienda ahora mismo."},
        {"title": "B", "href": "https://amazon.es/2",
         "body": "La RTX 4070 está a 559 euros con envío gratis incluido."},
        {"title": "C", "href": "https://pccomponentes.com/3",
         "body": "La RTX 4070 aparece listada a 539 euros esta semana."},
    ]
    utiles = _utiles(crudos, _palabras_clave("precio rtx 4070"))
    dominios = [r["dominio"] for r in utiles]
    assert len(dominios) == len(set(dominios))
    assert "pccomponentes.com" in dominios


def test_se_elige_la_frase_que_habla_de_lo_preguntado():
    """En una página de tienda, los primeros 220 caracteres son el menú."""
    from nova.tools.web import _frases_utiles, _palabras_clave

    cuerpo = (
        "Inicio | Componentes | Tarjetas gráficas | Placas base. "
        "La RTX 4070 cuesta 549 euros en la mayoría de tiendas. "
        "Envíos y devoluciones. Atención al cliente."
    )
    elegido = _frases_utiles(cuerpo, _palabras_clave("precio de la rtx 4070"))
    assert "549 euros" in elegido
    assert "Atención al cliente" not in elegido


def test_el_texto_pide_al_modelo_que_conteste_no_que_lea():
    """Unos resultados de búsqueda no son una respuesta, son material.

    Sin la instrucción, el modelo lee la lista de títulos en voz alta,
    que es exactamente lo que nadie quiere oír.
    """
    texto = redactar("quien ganó el mundial", [
        {"title": "Final", "href": "https://a.es",
         "body": "Argentina se impuso a Francia en los penaltis y fue campeona."},
    ])
    assert "una frase corta" in texto.lower()
    assert "no leas la lista" in texto.lower()


def test_nunca_se_devuelven_mas_de_los_pedidos():
    crudos = [
        {"title": f"T{i}", "href": f"https://sitio{i}.es",
         "body": f"Un resultado con sustancia suficiente número {i} para pasar el filtro."}
        for i in range(10)
    ]
    texto = redactar("algo", crudos, tope=3)
    assert len([ln for ln in texto.splitlines() if ln.startswith("- ")]) == 3


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


# ── Entrar en la página cuando el resumen no contesta ────────────────
#
# Los resúmenes de buscador para "cuánto cuesta X" son la descripción
# comercial de la tienda, sin un solo precio. La página sí lo tiene.

def test_la_unidad_de_la_pregunta_no_es_la_respuesta():
    """«cuánto cuesta una fuente de 750 vatios»: el 750 es la PREGUNTA.

    Con "cualquier número vale", el resumen parecía contestar y nunca se
    entraba en la página. Para un precio hace falta una cifra con moneda.
    """
    from nova.tools.web import _señal_esperada, _tiene_dato

    señal = _señal_esperada("cuanto cuesta una fuente de 750 vatios")
    assert not _tiene_dato("Fuentes de alimentación de 750 vatios y 850W", señal)
    assert _tiene_dato("Cooler Master 750W por 89,90 €", señal)


def test_para_temperatura_se_esperan_grados():
    from nova.tools.web import _señal_esperada, _tiene_dato

    señal = _señal_esperada("que temperatura hace en madrid")
    assert _tiene_dato("Ahora mismo 21 grados", señal)
    assert not _tiene_dato("Madrid tiene 3 millones de habitantes", señal)


def test_para_lo_demas_vale_cualquier_cifra():
    from nova.tools.web import _señal_esperada, _tiene_dato

    señal = _señal_esperada("cuantos goles metio messi")
    assert _tiene_dato("Metió 7 goles", señal)


@pytest.mark.parametrize("url", [
    "https://www.amazon.es/nvidia-rtx-4070/s?k=nvidia+rtx+4070",
    "https://tienda.es/buscar?q=fuente",
    "https://otra.com/search?query=algo",
])
def test_las_paginas_de_busqueda_se_saltan(url):
    """Se montan con JavaScript: bajarlas sólo da el menú y el pie."""
    from nova.tools.web import _ES_BUSCADOR

    assert _ES_BUSCADOR.search(url)


def test_una_ficha_de_producto_no_se_salta():
    from nova.tools.web import _ES_BUSCADOR

    assert not _ES_BUSCADOR.search("https://ultimainformatica.com/nvidia-geforce-rtx-4070")


def test_solo_se_profundiza_si_se_pidio_un_dato():
    """Entrar en la página cuesta segundos: no se hace por gusto."""
    from nova.tools.web import _PIDE_UN_DATO

    assert _PIDE_UN_DATO.search("cuanto cuesta una fuente")
    assert _PIDE_UN_DATO.search("que precio tiene la 4070")
    assert not _PIDE_UN_DATO.search("quien gano el mundial de 2022")
    assert not _PIDE_UN_DATO.search("como se hace una tortilla")


def test_lo_sacado_de_la_pagina_va_marcado():
    """El modelo tiene que saber que esas cifras son de la ficha."""
    texto = redactar(
        "cuanto cuesta",
        [{"title": "T", "href": "https://a.es/p",
          "body": "Una descripción comercial larga y sin ninguna cifra concreta."}],
        detalle="139,95 €",
    )
    assert "139,95 €" in texto
    assert "página" in texto.lower()
