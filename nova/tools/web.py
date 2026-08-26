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


def redactar(consulta: str, resultados: list[dict]) -> str:
    """Convierte los resultados en algo que se pueda leer en voz alta."""
    if not resultados:
        return f"No he encontrado nada sobre «{consulta}»."

    partes = [f"Esto he encontrado sobre «{consulta}»:"]
    for i, r in enumerate(resultados, start=1):
        titulo = _limpiar(r.get("title") or "")
        cuerpo = _acortar(r.get("body") or "")
        if not titulo and not cuerpo:
            continue
        partes.append(f"{i}. {titulo}. {cuerpo}".strip())
    return "\n".join(partes)


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
                    max_results=max(1, min(max_resultados, 5)),
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
    return ToolResult(
        ok=True,
        message=redactar(consulta, crudos),
        data={"query": consulta, "resultados": len(crudos)},
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
        description=(
            "Busca algo en internet: precios, noticias, datos que no sabes. "
            "Úsala cuando te pregunten por información que no está en el PC"
        ),
        handler=buscar,
        schema={
            "type": "object",
            "properties": {"query": {"type": "string", "description": "Qué buscar"}},
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
            "properties": {"url": {"type": "string", "description": "La dirección"}},
            "required": ["url"],
        },
        risk=Risk.MEDIUM,
    ))
