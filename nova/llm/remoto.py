"""Cerebro de repuesto en la nube, gratis y APAGADO por defecto.

Por qué existe, cuando la regla nº7 del proyecto dice que no salen datos
del PC: porque un modelo de 4B no programa, y cuando hay un juego delante
ni siquiera cabe en la tarjeta.  Medido con Star Citizen abierto,
qwen3.5:4b tardaba entre 4 y 7 segundos con el 10% del modelo en la GPU.
Un cerebro en la nube no ocupa ni un megabyte de tu VRAM: el "no me
revientes el PC mientras juego" y el "quiero que programe bien" son, sin
querer, el mismo problema.

**Sin clave no existe**: `disponible` devuelve False y NOVA no lo mira
nunca.  Con clave, quién manda es la configuración: por defecto hay que
pedirlo («modo rápido») porque encenderlo significa que lo que dices sale
de tu ordenador, y con `NOVA_REMOTO_SIEMPRE=true` arranca ya encendido —
que es como lo quiso Benja el 02/09.  Decir «modo local» gana siempre.

La clave se lee de `NOVA_GROQ_KEY` o de `data/groq.key`, que está fuera
del repositorio.  Nunca se escribe en el log.

**Lo que de verdad limita esto son los tokens por minuto, no el día.**
Medido el 02/09 en las cabeceras: 1000 peticiones y **8000 tokens/min**.
Un turno con el catálogo entero gastaba 3849 —3683 sólo de entrada— así
que salían DOS turnos por minuto y el tercero se comía un 429 pidiendo
23 segundos de espera.  Por eso existe `elegir_herramientas`: mandando
sólo las 18 que vienen a cuento, el turno baja a ~1800 y salen cinco.
Eso es lo que separa "usable" de "inusable" aquí.

Groq porque es el único con capa gratuita de verdad y porque es el más
rápido que hay (sirve desde LPUs, no desde GPUs).  Habla el dialecto de
OpenAI, así que cambiar de proveedor es cambiar la URL: si algún día
cierra la capa gratis, `NOVA_REMOTO_URL` apunta a otro sitio y ya está.

**El nombre del modelo caduca.**  Los proveedores retiran modelos cada
pocos meses. Si un día NOVA dice que el modelo remoto no existe, es eso:
`NOVA_REMOTO_MODEL` con el nombre nuevo y listo.
"""

from __future__ import annotations

import json
import logging
import re
import time
import unicodedata
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

# Se reutiliza la excepción de Ollama a propósito: `Agent` la captura por
# nombre, y un cerebro de repuesto que rompa el manejo de errores del
# agente no sería de repuesto de nada.
from .ollama import LLMResponse, OllamaError, ToolCall

log = logging.getLogger("nova.llm.remoto")


def leer_clave(fichero: Path | None, entorno: str = "") -> str:
    """La clave, del entorno o de un fichero suelto.

    Del fichero además de la variable de entorno porque poner una
    variable de entorno permanente en Windows es entrar en un diálogo de
    tres ventanas; pegar una línea en `data/groq.key` no.
    """
    if entorno.strip():
        return entorno.strip()
    if fichero is not None and fichero.is_file():
        try:
            return fichero.read_text(encoding="utf-8").strip()
        except OSError:
            log.debug("no pude leer %s", fichero, exc_info=True)
    return ""


class ClienteRemoto:
    """Mismo trato que `OllamaClient`, para poder cambiarlo por él."""

    def __init__(
        self,
        url: str,
        model: str,
        clave: str,
        *,
        temperature: float = 0.6,
        max_tokens: int = 350,
        timeout: float = 60.0,
    ) -> None:
        self.url = url.rstrip("/")
        self.model = model
        self._clave = clave
        self.temperature = temperature
        self.max_tokens = max_tokens
        self._http = httpx.Client(timeout=timeout)
        self.error = ""

    def close(self) -> None:
        self._http.close()

    @property
    def disponible(self) -> bool:
        """Sin clave no hay cerebro remoto. No es un fallo: es lo normal."""
        return bool(self._clave)

    # ── Compatibilidad con OllamaClient ──────────────────────────────
    #
    # `app.py` pregunta estas tres cosas para decidir si cambia de modelo
    # local. En la nube no hay VRAM que se llene ni modelos que
    # descargar, así que las respuestas son fijas — pero tienen que
    # existir, o cambiar de cerebro rompería el vigilante de recursos.

    def available(self) -> bool:
        return self.disponible

    def residencia(self) -> float:
        return 1.0

    def tiene_modelo(self, nombre: str) -> bool:  # noqa: ARG002
        return False

    def usar_modelo(self, nombre: str) -> None:  # noqa: ARG002
        return

    # ── Conversación ─────────────────────────────────────────────────

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        on_trozo: Callable[[str], None] | None = None,
    ) -> LLMResponse:
        if not self.disponible:
            raise OllamaError("No tengo clave para el cerebro rápido.")

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": acotar_resultados(a_openai(messages)),
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "stream": on_trozo is not None,
        }
        if tools:
            # Sólo las que vienen a cuento: el catálogo entero se come el
            # presupuesto de tokens por minuto. Ver `elegir_herramientas`.
            ultimo = next(
                (m.get("content", "") for m in reversed(messages)
                 if m.get("role") == "user"),
                "",
            )
            payload["tools"] = relajar_esquemas(
                elegir_herramientas(tools, str(ultimo))
            )

        if on_trozo is not None:
            return self._en_trozos(payload, on_trozo)
        return self._de_una(payload)

    def _cabeceras(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._clave}",
                "Content-Type": "application/json"}

    def _de_una(self, payload: dict[str, Any]) -> LLMResponse:
        try:
            resp = self._http.post(
                f"{self.url}/chat/completions", json=payload, headers=self._cabeceras()
            )
            if resp.status_code == 429 and self._esperar_y_reintentar(resp):
                resp = self._http.post(
                    f"{self.url}/chat/completions", json=payload, headers=self._cabeceras()
                )
            resp.raise_for_status()
            datos = resp.json()
        except httpx.HTTPStatusError as exc:
            raise OllamaError(self._explicar(exc)) from exc
        except Exception as exc:
            log.warning("fallo llamando al cerebro remoto: %s", exc)
            raise OllamaError(f"El cerebro rápido no responde: {exc}") from exc

        msg = ((datos.get("choices") or [{}])[0].get("message")) or {}
        return LLMResponse(
            text=msg.get("content") or "",
            tool_calls=_llamadas(msg.get("tool_calls")),
            eval_count=int((datos.get("usage") or {}).get("completion_tokens") or 0),
        )

    def _en_trozos(
        self, payload: dict[str, Any], on_trozo: Callable[[str], None]
    ) -> LLMResponse:
        """Igual, pero soltando el texto según llega.

        Se acumulan los `tool_calls` a trozos porque el dialecto de
        OpenAI los parte: llega el nombre en un fragmento y los
        argumentos en veinte, con un `index` que dice a cuál pertenece
        cada uno. Sin juntarlos, NOVA vería veinte llamadas vacías.
        """
        partes: list[str] = []
        crudas: dict[int, dict[str, Any]] = {}
        try:
            with self._http.stream(
                "POST", f"{self.url}/chat/completions",
                json=payload, headers=self._cabeceras(),
            ) as resp:
                if resp.status_code == 429:
                    resp.read()
                    if self._esperar_y_reintentar(resp):
                        return self._en_trozos(payload, on_trozo)
                if resp.status_code >= 400:
                    resp.read()
                    resp.raise_for_status()
                for linea in resp.iter_lines():
                    if not linea.startswith("data:"):
                        continue
                    cuerpo = linea[5:].strip()
                    if not cuerpo or cuerpo == "[DONE]":
                        continue
                    try:
                        dato = json.loads(cuerpo)
                    except ValueError:
                        continue
                    delta = ((dato.get("choices") or [{}])[0].get("delta")) or {}
                    if trozo := delta.get("content"):
                        partes.append(trozo)
                        on_trozo(trozo)
                    for parcial in delta.get("tool_calls") or []:
                        _acumular(crudas, parcial)
        except httpx.HTTPStatusError as exc:
            raise OllamaError(self._explicar(exc)) from exc
        except Exception as exc:
            log.warning("fallo en streaming del cerebro remoto: %s", exc)
            raise OllamaError(f"El cerebro rápido no responde: {exc}") from exc

        return LLMResponse(
            text="".join(partes),
            tool_calls=_llamadas([crudas[i] for i in sorted(crudas)]),
        )

    # Cuánto se está dispuesto a esperar a que se reponga la cuota. Por
    # encima de esto, mejor contestar con el modelo de casa que dejar a
    # Benja mirando el orbe.
    ESPERA_MAXIMA_S = 4.0

    def _esperar_y_reintentar(self, resp: httpx.Response) -> bool:
        """Ante un 429, esperar suele ser mejor que rendirse.

        Los números de la capa gratuita, leídos de las cabeceras el
        01/09: 1000 peticiones y **8000 tokens por minuto**. Un turno de
        NOVA gasta 3849 (3683 de entrada, casi todo el catálogo de 68
        herramientas), así que caben dos por minuto y el tercero se
        pasa. Hablando, eso se toca.

        Pero el cubo de tokens se rellena en **615 ms**. Un 429 aquí casi
        nunca es "se te acabó", es "vas muy rápido": esperar medio
        segundo y repetir sale infinitamente mejor que caerse al modelo
        pequeño. Sin esto, NOVA se volvía a local a la tercera frase y
        se quedaba ahí.

        Lo que no se hace es esperar mucho: si la cabecera pide más de
        `ESPERA_MAXIMA_S`, es una cuota de verdad y toca volver a casa.
        """
        espera = _segundos(resp.headers.get("retry-after")) or _segundos(
            resp.headers.get("x-ratelimit-reset-tokens")
        )
        if espera is None or espera > self.ESPERA_MAXIMA_S:
            return False
        log.info("me he pasado de tokens por minuto; espero %.1fs y repito", espera)
        time.sleep(espera + 0.1)
        return True

    def _explicar(self, exc: httpx.HTTPStatusError) -> str:
        """El error HTTP, dicho de forma que se pueda arreglar oyéndolo."""
        codigo = exc.response.status_code
        if codigo in (401, 403):
            return "La clave del cerebro rápido no vale. Vuelvo a lo local."
        if codigo == 429:
            return "Me he pasado del límite gratuito por ahora. Vuelvo a lo local."
        if codigo == 404:
            return (f"El modelo {self.model} ya no existe en el proveedor. "
                    "Hay que poner uno nuevo en NOVA_REMOTO_MODEL.")
        return f"El cerebro rápido ha fallado con un {codigo}."


# ── Traducción del dialecto ──────────────────────────────────────────
#
# `Agent` construye la conversación con la forma de Ollama, que es
# parecida a la de OpenAI pero no igual. Las dos diferencias que
# importan: OpenAI exige un `id` en cada llamada a herramienta y que el
# mensaje de respuesta lo cite en `tool_call_id`, y quiere los argumentos
# como cadena JSON y no como objeto. Sin esto la API contesta 400 y NOVA
# se quedaría muda justo en el modo que se supone que es el bueno.


def _sin_tildes(texto: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", texto)
        if unicodedata.category(c) != "Mn"
    )


def _segundos(valor: str | None) -> float | None:
    """Las cabeceras de espera vienen en formatos distintos.

    `retry-after` es un número de segundos; `x-ratelimit-reset-tokens`
    viene como "615ms", "1m26.4s" o "2.5s". Se admiten los tres porque
    los proveedores no se ponen de acuerdo y quedarse sin entender la
    cabecera significa no reintentar nunca.
    """
    if not valor:
        return None
    texto = valor.strip().lower()
    try:
        return float(texto)
    except ValueError:
        pass
    total = 0.0
    encontrado = False
    for cantidad, unidad in re.findall(r"([\d.]+)\s*(ms|s|m|h)", texto):
        try:
            n = float(cantidad)
        except ValueError:
            continue
        total += n * {"ms": 0.001, "s": 1.0, "m": 60.0, "h": 3600.0}[unidad]
        encontrado = True
    return total if encontrado else None


# Cuántas herramientas se le enseñan al cerebro de la nube.
#
# 18, medido el 02/09 sobre 18 frases reales con la herramienta que
# debería salir en cada una:
#
#     tope=12   17/18 aciertos    ~684 tokens de catálogo
#     tope=18   18/18 aciertos   ~1013 tokens
#     tope=24   18/18 aciertos   ~1373 tokens
#
# 18 es donde deja de fallar y todavía no se paga de más: con 24 son 360
# tokens por turno a cambio de nada. Ver `elegir_herramientas`.
TOPE_HERRAMIENTAS = 18

# Palabras que no distinguen nada y sólo meten ruido al puntuar.
_VACIAS = frozenset({
    "que", "qué", "el", "la", "los", "las", "un", "una", "de", "del", "en",
    "por", "para", "con", "y", "o", "a", "al", "me", "mi", "tu", "te", "se",
    "es", "esta", "está", "estoy", "hay", "lo", "le", "su", "mas", "más",
    "como", "cómo", "cuando", "cuándo", "donde", "dónde", "quiero", "puedes",
    "dime", "haz", "hazme", "ponme", "nova", "por favor", "esto", "eso",
})


def _palabras(texto: str) -> set[str]:
    limpio = _sin_tildes((texto or "").lower())
    return {p for p in re.findall(r"[a-z0-9]+", limpio) if p not in _VACIAS and len(p) > 2}


def elegir_herramientas(
    tools: list[dict[str, Any]], mensaje: str, tope: int = TOPE_HERRAMIENTAS
) -> list[dict[str, Any]]:
    """Las que vienen a cuento, no las 68.

    Medido el 02/09 contra la capa gratuita de Groq, y es la diferencia
    entre usable e inusable:

        catálogo entero (68)   3849 tokens por turno  ->  2 turnos/minuto
        sólo las que encajan   ~1000 tokens por turno -> ~8 turnos/minuto

    El límite son 8000 tokens POR MINUTO, y 3683 de esos 3849 eran de
    entrada: casi todo el catálogo. Con todo dentro, a la tercera frase
    seguida NOVA se comía un 429 y el proveedor pedía esperar 23
    segundos. Recortar descripciones no servía —ahorraba 200 tokens de
    4400—: lo que pesa es el NÚMERO de herramientas.

    Esto no se le hace al modelo local: Ollama no cobra por token y allí
    el catálogo entero sólo cuesta un poco de prefill.

    Se puntúa por palabras compartidas con lo que ha dicho Benja, y ante
    el empate manda el orden del registro. Si aun así falta la que hacía
    falta, el agente recibe "no existe esa herramienta" y lo reintenta,
    que es un camino que ya existía.
    """
    if len(tools) <= tope:
        return tools

    claves = _palabras(mensaje)

    def puntuar(tool: dict[str, Any]) -> int:
        fn = (tool or {}).get("function") or {}
        texto = _palabras(f"{fn.get('name', '')} {fn.get('description', '')}")
        # El nombre pesa doble: "codigo_escribir" para "escribe un juego"
        # es una señal mucho más firme que una palabra de la descripción.
        nombre = _palabras(str(fn.get("name", "")))
        return len(claves & texto) + 2 * len(claves & nombre)

    puntuadas = [(puntuar(t), i, t) for i, t in enumerate(tools)]
    # `i` desempata: mantiene el orden del registro y hace la elección
    # reproducible, que importa para poder probarla.
    puntuadas.sort(key=lambda p: (-p[0], p[1]))
    return [t for _, _, t in puntuadas[:tope]]


# Cuánto se le deja de un resultado de herramienta al mandarlo a la
# nube. Ver `acotar_resultados`.
TOPE_RESULTADO_HERRAMIENTA = 600


def acotar_resultados(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Recorta lo que devuelven las herramientas, sólo camino a la nube.

    Encontrado el 02/09, en directo: «qué estás viendo en mi pantalla»
    tumbó el modo rápido a la SEGUNDA frase, cuando `elegir_herramientas`
    ya debería haber dejado margen para cinco. El motivo es que
    `pantalla.leer` no es `responde_sola` — a propósito, es material en
    bruto para que el modelo lo resuma, ver su docstring — y puede
    devolver hasta 4000 caracteres de OCR. Eso entra en la SEGUNDA ronda
    (la que compone la respuesta) junto con el catálogo entero de esa
    ronda: medido, 3215 tokens sólo esa ronda, sobre un presupuesto de
    8000 por minuto.

    No se toca el mensaje real que ve Ollama —local no cobra por token,
    y cortarlo ahí sólo empeoraría lo que ve el modelo—, así que esto va
    aparte de `a_openai` y sólo se llama camino a la nube.

    600 caracteres siguen siendo material de sobra para que el modelo
    conteste "veo un menú de estación con estas opciones..."; no hace
    falta el texto entero para redactar un resumen hablado de 1-2 frases.
    """
    salida = []
    for msg in messages:
        contenido = msg.get("content")
        if (msg.get("role") == "tool" and isinstance(contenido, str)
                and len(contenido) > TOPE_RESULTADO_HERRAMIENTA):
            recortado = contenido[:TOPE_RESULTADO_HERRAMIENTA] + "… (recortado)"
            salida.append({**msg, "content": recortado})
        else:
            salida.append(msg)
    return salida


def relajar_esquemas(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Deja que los parámetros opcionales lleguen como `null`.

    Encontrado probando con la clave de verdad el 01/09. Los modelos
    grandes rellenan TODOS los parámetros de una herramienta, y a los que
    no aplican les ponen `null`:

        {"nombre": null}   para codigo.proyectos, que los lleva opcionales

    Groq valida el esquema en su lado y devuelve un 400:

        parameters for tool codigo_proyectos did not match schema:
        `/nombre`: expected string, but got null

    Y con eso el turno entero se cae. Ollama no valida nada, así que el
    mismo esquema le vale — por eso esto se hace SÓLO aquí, al salir, y
    el catálogo de casa se queda como está.

    Sólo se tocan los que no son obligatorios: si un parámetro es
    `required`, mandarlo nulo sigue siendo un error y conviene que lo
    diga.
    """
    salida: list[dict[str, Any]] = []
    for tool in tools:
        fn = dict((tool or {}).get("function") or {})
        params = dict(fn.get("parameters") or {})
        propiedades = params.get("properties") or {}
        obligatorios = set(params.get("required") or ())

        nuevas = {}
        for nombre, definicion in propiedades.items():
            copia = dict(definicion or {})
            tipo = copia.get("type")
            if nombre not in obligatorios and isinstance(tipo, str) and tipo != "null":
                copia["type"] = [tipo, "null"]
            nuevas[nombre] = copia

        params["properties"] = nuevas
        fn["parameters"] = params
        salida.append({**tool, "function": fn})
    return salida


def a_openai(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """La misma conversación, en el dialecto estricto de OpenAI."""
    salida: list[dict[str, Any]] = []
    pendientes: list[str] = []

    for msg in messages:
        papel = msg.get("role")

        if papel == "tool":
            # Los mensajes de herramienta van en el mismo orden en que se
            # pidieron, así que se emparejan por orden de llegada.
            ident = pendientes.pop(0) if pendientes else f"call_{uuid.uuid4().hex[:8]}"
            salida.append({
                "role": "tool",
                "tool_call_id": ident,
                "content": str(msg.get("content") or ""),
            })
            continue

        if papel == "assistant" and msg.get("tool_calls"):
            llamadas = []
            for n, tc in enumerate(msg["tool_calls"]):
                fn = (tc or {}).get("function") or {}
                ident = str(tc.get("id") or f"call_{n}_{uuid.uuid4().hex[:6]}")
                pendientes.append(ident)
                argumentos = fn.get("arguments")
                llamadas.append({
                    "id": ident,
                    "type": "function",
                    "function": {
                        "name": str(fn.get("name") or ""),
                        # Cadena, no objeto: es lo que exige la API.
                        "arguments": argumentos if isinstance(argumentos, str)
                        else json.dumps(argumentos or {}, ensure_ascii=False),
                    },
                })
            salida.append({
                "role": "assistant",
                "content": msg.get("content") or "",
                "tool_calls": llamadas,
            })
            continue

        salida.append({"role": papel, "content": str(msg.get("content") or "")})

    return salida


def _acumular(crudas: dict[int, dict[str, Any]], parcial: dict[str, Any]) -> None:
    """Junta los trozos de una llamada que llega partida en el stream."""
    indice = int(parcial.get("index") or 0)
    hueco = crudas.setdefault(indice, {"id": "", "function": {"name": "", "arguments": ""}})
    if ident := parcial.get("id"):
        hueco["id"] = ident
    fn = parcial.get("function") or {}
    if nombre := fn.get("name"):
        hueco["function"]["name"] = nombre
    if trozo := fn.get("arguments"):
        hueco["function"]["arguments"] += trozo


def _llamadas(crudas: list[dict[str, Any]] | None) -> list[ToolCall]:
    salida: list[ToolCall] = []
    for item in crudas or []:
        fn = (item or {}).get("function") or {}
        args = fn.get("arguments") or {}
        if isinstance(args, str):
            try:
                args = json.loads(args or "{}")
            except ValueError:
                args = {}
        if not fn.get("name"):
            continue
        salida.append(ToolCall(
            id=str(item.get("id") or uuid.uuid4().hex[:12]),
            name=str(fn["name"]),
            args=args if isinstance(args, dict) else {},
        ))
    return salida
