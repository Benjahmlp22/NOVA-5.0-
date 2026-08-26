"""Buscar en internet. Sin API key, sin cuenta y sin coste.

DuckDuckGo a través de `ddgs`, que es la regla de oro del proyecto: nada
que necesite registrarse ni pagar.

El resultado vuelve al modelo como **texto en español ya redactado**, no
como JSON — decisión sagrada nº3. Con un volcado de datos crudo, un
modelo pequeño no reconoce la forma como "esto ya está resuelto", vuelve
a llamar a la herramienta hasta agotar las rondas, y encima contesta en
inglés al intentar redactar sobre datos en inglés.

Y con timeout corto. NOVA es un asistente de voz: quedarse esperando a
una red que no responde es peor que decir "no tengo conexión" — el
usuario está delante, callado, sin saber si le oíste.
"""

from __future__ import annotations

import html
import logging
import re

from .registry import Risk, Tool, ToolResult

log = logging.getLogger("nova.tools.web")

# Corto a propósito. Si la red va mal, NOVA prefiere decirlo a hacerte
# esperar: en voz, cuatro segundos de silencio ya parecen una avería.
TIMEOUT_S = 4.0

# Entrar en una página es un extra sobre una espera que ya existe, así
# que se le da menos margen todavía.
TIMEOUT_PAGINA_S = 2.5

# Tres resultados. Con más, el modelo tiene que resumir un texto largo
# antes de contestar y se dispara la latencia; con menos, una sola página
# mala se lleva la respuesta.
MAX_RESULTADOS = 3

# Cuánto se conserva de cada resumen. Al leerse en voz alta, una frase
# larga cansa; y todo esto entra en el prompt del modelo, que se paga en
# latencia.
MAX_CARACTERES = 220


def _limpiar(texto: str) -> str:
    """Quita el HTML y los adornos que traen los resúmenes.

    Las entidades las traduce `html.unescape` y no una lista a mano: la
    lista se queda corta siempre. La primera versión cubría &quot;, &amp;
    y &#x27; y se le coló `&nbsp;`, que es de las más comunes — y leído en
    voz alta suena a "ampersand ene be ese pe".
    """
    t = re.sub(r"<[^>]+>", "", texto or "")
    t = html.unescape(t)
    return re.sub(r"\s+", " ", t).strip()


def _acortar(texto: str, tope: int = MAX_CARACTERES) -> str:
    t = _limpiar(texto)
    if len(t) <= tope:
        return t
    corte = t.rfind(" ", 0, tope)
    return t[: corte if corte > tope // 2 else tope].rstrip(" ,;:.") + "…"


def _dominio(url: str) -> str:
    m = re.match(r"https?://(?:www\.)?([^/]+)", url or "")
    return m.group(1).lower() if m else ""


def _palabras_clave(consulta: str) -> set[str]:
    vacias = {"que", "qué", "cual", "cuál", "cuanto", "cuánto", "como", "cómo",
              "de", "la", "el", "los", "las", "un", "una", "en", "por", "para",
              "es", "son", "hay", "busca", "dime", "cuesta", "vale", "y", "a"}
    palabras = re.findall(r"\w+", _limpiar(consulta).lower())
    return {p for p in palabras if p not in vacias and len(p) > 2}


def _frases_utiles(cuerpo: str, claves: set[str], tope: int = 2) -> str:
    """Se queda con las frases del resumen que hablan de lo preguntado.

    Antes se cogían los primeros 220 caracteres a pelo, y en una página
    de tienda esos 220 caracteres son el menú de navegación. Puntuar por
    palabras de la consulta y por números saca la frase que importa.
    """
    texto = _limpiar(cuerpo)
    if not texto:
        return ""
    frases = [f.strip() for f in re.split(r"(?<=[.!?])\s+|\s+\|\s+", texto) if f.strip()]
    if not frases:
        return ""

    def puntuar(frase: str) -> tuple[int, int]:
        minus = frase.lower()
        coincidencias = sum(1 for c in claves if c in minus)
        # Un número suele ser la respuesta cuando se pregunta un precio,
        # una fecha o una cantidad, que es casi siempre.
        numeros = len(re.findall(r"\d", frase))
        return coincidencias, min(numeros, 6)

    mejores = sorted(frases, key=puntuar, reverse=True)[:tope]
    # Se devuelven en el orden original: leídas fuera de orden suenan raro.
    ordenadas = [f for f in frases if f in mejores]
    return _acortar(" ".join(ordenadas))


def _utiles(resultados: list[dict], claves: set[str],
            tope: int = MAX_RESULTADOS) -> list[dict]:
    """Quita duplicados de dominio y resultados sin sustancia."""
    vistos: set[str] = set()
    salida: list[dict] = []
    for r in resultados:
        dominio = _dominio(r.get("href") or "")
        # Tres resultados de Amazon no son tres resultados.
        if dominio and dominio in vistos:
            continue
        cuerpo = _frases_utiles(r.get("body") or "", claves)
        if len(cuerpo) < 30:
            continue
        vistos.add(dominio)
        salida.append({"titulo": _limpiar(r.get("title") or ""),
                       "cuerpo": cuerpo, "dominio": dominio})
        # Más de tres no ayudan y sí cuestan: cada uno entra en el prompt
        # del modelo y se paga en latencia de respuesta.
        if len(salida) >= tope:
            break
    return salida


def redactar(consulta: str, resultados: list[dict],
             tope: int = MAX_RESULTADOS, detalle: str = "") -> str:
    """Material en bruto para que el modelo CONTESTE, no para leerlo.

    Aquí se rompe a medias la regla de devolver la frase ya redactada, y
    a propósito: "he abierto Discord" es una respuesta, pero unos
    resultados de búsqueda no lo son — nadie quiere que le lean tres
    títulos de páginas. Así que se le da al modelo el material y una
    instrucción explícita de qué hacer con él.
    """
    claves = _palabras_clave(consulta)
    utiles = _utiles(resultados, claves, tope)
    if not utiles:
        return f"No he encontrado nada útil sobre «{consulta}»."

    partes = [
        f"Resultados de buscar «{consulta}» en internet. "
        "Responde a la pregunta con estos datos en UNA frase corta, como se "
        "dice en voz alta. No leas la lista ni digas de qué web es.",
    ]
    for r in utiles:
        partes.append(f"- {r['titulo']}: {r['cuerpo']}")
    if detalle:
        partes.append(f"- Sacado de la página, con cifras: {detalle}")
    return "\n".join(partes)


# Preguntas cuya respuesta es un dato concreto, no una explicación. En
# esas, un resumen de buscador que no trae ni un número no ha contestado
# nada, y merece la pena entrar en la página.
_PIDE_UN_DATO = re.compile(
    r"\b(cu[aá]nto|cu[aá]nta|cu[aá]ntos|cu[aá]ntas|precio|cuesta|vale|"
    r"cu[aá]ndo|qu[eé] d[ií]a|a qu[eé] hora|temperatura|grados|"
    r"cotiza|cambio|d[oó]lar|euro)\b",
    re.IGNORECASE,
)

# Qué cifra cuenta como respuesta, según lo que se preguntó. La unidad
# importa: para "cuánto cuesta una fuente de 750 vatios", el "750 vatios"
# es la PREGUNTA, no la respuesta. Buscando cualquier número, el resumen
# parecía contestar y nunca se entraba en la página.
_DINERO = re.compile(r"\d[\d.,]*\s*(?:€|eur\b|euros|\$|usd|dólares|dolares)"
                     r"|(?:€|\$)\s*\d", re.IGNORECASE)
_TEMPERATURA = re.compile(r"-?\d[\d.,]*\s*(?:°|grados)", re.IGNORECASE)
_CUALQUIER_CIFRA = re.compile(r"\d")

_PREGUNTA_DE_DINERO = re.compile(r"\b(precio|cuesta|vale|valen|cu[aá]nto vale|"
                                 r"cotiza|cambio|d[oó]lar|euro)\b", re.IGNORECASE)
_PREGUNTA_DE_TEMPERATURA = re.compile(r"\b(temperatura|grados|calor|fr[ií]o|tiempo hace)\b",
                                      re.IGNORECASE)


def _señal_esperada(consulta: str) -> re.Pattern:
    if _PREGUNTA_DE_DINERO.search(consulta):
        return _DINERO
    if _PREGUNTA_DE_TEMPERATURA.search(consulta):
        return _TEMPERATURA
    return _CUALQUIER_CIFRA


def _tiene_dato(texto: str, señal: re.Pattern = _CUALQUIER_CIFRA) -> bool:
    return bool(señal.search(texto))


# Páginas de BÚSQUEDA de una tienda, no de producto. Se montan con
# JavaScript, así que bajarlas sólo da el menú y el pie: entrar ahí es
# gastar 0.7 s para nada.
_ES_BUSCADOR = re.compile(r"[?&](k|q|s|search|query)=|/s\?|/buscar|/search", re.IGNORECASE)


def _entrar_en_la_pagina(ddgs, url: str, claves: set[str],  # noqa: ANN001
                         señal: re.Pattern = _CUALQUIER_CIFRA) -> str:
    """Baja la página y saca las líneas donde está el dato.

    Los resúmenes de buscador para "cuánto cuesta X" son casi siempre
    la descripción comercial de la tienda, sin un solo número. La página
    sí lo tiene, pero también tiene el menú, el pie y la política de
    cookies: se filtra por líneas cortas que contengan una cifra con
    unidad y alguna palabra de lo preguntado.
    """
    try:
        pagina = ddgs.extract(url)
    except Exception as exc:  # noqa: BLE001
        log.debug("no pude entrar en %s: %s", url, exc)
        return ""

    texto = str(pagina.get("text") or pagina.get("content") or "")
    candidatas: list[tuple[int, str]] = []
    for linea in texto.splitlines():
        limpia = _limpiar(linea)
        # Ni un título suelto ni un párrafo entero: lo que trae precios
        # son líneas de producto, de largo intermedio.
        if not (8 <= len(limpia) <= 160) or not _tiene_dato(limpia, señal):
            continue
        # Las palabras de la consulta suben la línea, pero no son
        # obligatorias: en una ficha de producto el precio suele ir en su
        # propia línea ("549,00 €") sin repetir el nombre.
        relevancia = sum(1 for c in claves if c in limpia.lower())
        candidatas.append((relevancia, limpia))

    if not candidatas:
        return ""
    candidatas.sort(key=lambda x: x[0], reverse=True)
    vistas: list[str] = []
    for _puntos, linea in candidatas:
        if linea not in vistas:
            vistas.append(linea)
        if len(vistas) == 3:
            break
    return " · ".join(vistas)


def buscar(query: str, max_resultados: int = MAX_RESULTADOS) -> ToolResult:
    consulta = (query or "").strip()
    if not consulta:
        return ToolResult(ok=False, message="¿Qué quieres que busque?")

    try:
        from ddgs import DDGS
    except ImportError:
        return ToolResult(
            ok=False,
            message="No puedo buscar: falta el paquete ddgs. Instálalo con pip install ddgs.",
        )

    try:
        with DDGS(timeout=TIMEOUT_S) as ddgs:
            crudos = list(
                ddgs.text(
                    consulta,
                    region="es-es",
                    safesearch="moderate",
                    # Se piden más de los que se enseñan: entre duplicados
                    # de dominio y páginas sin sustancia, la mitad se cae.
                    max_results=max(3, min(max_resultados * 3, 12)),
                )
            )
    except Exception as exc:  # noqa: BLE001
        # Degradación limpia: cualquier fallo de red termina en una frase
        # que NOVA puede decir, nunca en una excepción que la calle.
        log.info("la búsqueda falló: %s", exc)
        return ToolResult(
            ok=False,
            message="No tengo conexión ahora mismo, así que no he podido buscarlo.",
        )

    log.info("busqué %r y encontré %d resultados", consulta, len(crudos))
    claves = _palabras_clave(consulta)
    utiles = _utiles(crudos, claves, max_resultados)

    # Si preguntaste por un dato concreto y ningún resumen trae una cifra,
    # los resúmenes no han contestado: se entra en la primera página. Sólo
    # entonces, porque cuesta ~0.7 s.
    detalle = ""
    señal = _señal_esperada(consulta)
    if utiles and _PIDE_UN_DATO.search(consulta) and not any(
        _tiene_dato(r["cuerpo"], señal) for r in utiles
    ):
        candidatas = [
            c.get("href", "") for c in crudos
            if _dominio(c.get("href") or "") in {u["dominio"] for u in utiles}
            and not _ES_BUSCADOR.search(c.get("href") or "")
        ]
        try:
            # Timeout más corto que el de la búsqueda: esto es un extra
            # sobre una espera que ya existe.
            #
            # Dos páginas y no una: con una sola, "cuánto cuesta una
            # fuente Corsair RM750e" se quedaba sin respuesta porque la
            # primera tienda no soltaba el dato y la segunda sí (139,95 €).
            # El precio de eso es el peor caso, que sube a ~5 s.
            with DDGS(timeout=TIMEOUT_PAGINA_S) as ddgs:
                for url in candidatas[:2]:
                    detalle = _entrar_en_la_pagina(ddgs, url, claves, señal)
                    if detalle:
                        break
        except Exception as exc:  # noqa: BLE001
            log.debug("no pude profundizar: %s", exc)
        if detalle:
            log.info("entré en la página para sacar el dato")

    return ToolResult(
        ok=True,
        message=redactar(consulta, crudos, max_resultados, detalle=detalle),
        data={"query": consulta, "resultados": len(crudos), "profundizo": bool(detalle)},
    )


def abrir_url(url: str) -> ToolResult:
    """Abre un enlace en el navegador del usuario."""
    enlace = (url or "").strip()
    if not enlace.startswith(("http://", "https://")):
        return ToolResult(ok=False, message="Eso no parece una dirección web.")
    try:
        import webbrowser

        webbrowser.open(enlace)
    except Exception as exc:  # noqa: BLE001
        return ToolResult(ok=False, message=f"No pude abrir el enlace: {exc}")
    return ToolResult(ok=True, message="Listo, lo he abierto en el navegador.")


def register(reg) -> None:  # noqa: ANN001
    reg.register(Tool(
        name="web.search",
        description="Busca en internet: precios, noticias, datos que no están en el PC",
        handler=buscar,
        schema={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
        # Segura: no toca nada del sistema y no cuesta dinero. Que pidiera
        # confirmación haría inútil la única herramienta que se usa a
        # mitad de una frase ("busca cuánto cuesta...").
        risk=Risk.SAFE,
    ))
    reg.register(Tool(
        name="web.open",
        description="Abre una dirección web en el navegador",
        handler=abrir_url,
        schema={
            "type": "object",
            "properties": {"url": {"type": "string"}},
            "required": ["url"],
        },
        risk=Risk.MEDIUM,
    ))
