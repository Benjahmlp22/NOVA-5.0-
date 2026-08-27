"""El cerebro: bucle LLM ↔ herramientas.

El modelo decide qué herramienta usar (function calling nativo), este
bucle la ejecuta pasando por el control de permisos, le devuelve el
resultado y repite hasta que produce una respuesta de texto.

Dos decisiones aprendidas a base de verlas fallar en producción:

1. El resultado de una herramienta que fue bien se le devuelve al modelo
   como **texto plano en español**, no como JSON.  Con JSON, un modelo
   local pequeño no reconocía la forma como "ya está hecho" y repetía la
   misma llamada hasta agotar las rondas (reproducido: 4 capturas de
   pantalla seguidas, el 100% de las veces), además de contestar en
   inglés al intentar redactar sobre un volcado de datos crudo.

2. El catálogo de herramientas que ve el modelo es más corto que el
   registro completo.  Cada esquema se reenvía en cada ronda; las de
   depuración o poder solo añadían latencia y ruido de decisión.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ..llm.ollama import LLMResponse, OllamaClient, OllamaError
from ..tools.registry import PendingConfirmation, ToolRegistry, ToolResult
from .charla import es_pura_charla
from .polish import es_relleno, frases_completas, pulir

log = logging.getLogger("nova.agent")

# Herramientas que existen pero NO se le ofrecen al modelo.
#
# Está vacío a propósito y conviene que siga estándolo: para ocultarle
# una herramienta al modelo, lo correcto es registrarla con
# `expose_to_llm=False`, que deja la decisión junto a la herramienta en
# vez de en una lista lejana que nadie actualiza.
#
# NOVA4 traía aquí {"logs.read", "command.run", "process.kill"} y ninguna
# de las tres existía: se registraban 19 herramientas y se ofrecían las
# 19. El código parecía estar protegiendo algo y no protegía nada, que es
# peor que no tenerlo.
LLM_HIDDEN: frozenset[str] = frozenset()

# "recuerda que X", "apúntate que X", "no olvides que X" → guardar X.
_RECUERDA = re.compile(
    r"^\s*(?:oye\s+|nova[,\s]+)?"
    r"(?:recuerda|acu[eé]rdate|ap[uú]ntate|no olvides|memoriza|gu[aá]rdate)"
    r"(?:\s+(?:que|de que|esto))?\s*[:,]?\s+(.+)$",
    re.IGNORECASE,
)


def _dato_visible(argumentos: dict) -> str:
    """El argumento que el usuario reconocería al verlo en la interfaz.

    "Abriendo Discord" dice mucho más que "app.open", y el único sitio
    donde está ese "Discord" es en los argumentos de la llamada.
    """
    if not isinstance(argumentos, dict):
        return ""
    for clave in ("name", "query", "path", "url", "text", "command"):
        valor = argumentos.get(clave)
        if isinstance(valor, str) and valor.strip():
            return valor.strip()
    return ""


def _extraer_recuerdo(mensaje: str) -> str:
    m = _RECUERDA.match(mensaje or "")
    if not m:
        return ""
    dato = m.group(1).strip(" .,;")
    # "recuerda buscar el archivo" es una orden, no un dato que guardar.
    if len(dato) < 3 or dato.lower().startswith(("que hiciste", "lo que")):
        return ""
    return dato


def _contar_y_preguntar(hechas: list[str], pendiente: PendingConfirmation) -> str:
    """Lo que YA se ha hecho, y luego la pregunta por lo que falta.

    Se compone aquí y no con otra vuelta al modelo a propósito: el
    usuario está esperando con el micro mudo, y los mensajes de las
    herramientas ya vienen escritos en español para él (esa es la razón
    de que ToolResult.message sea una frase y no un JSON).
    """
    pregunta = (
        f"¿Confirmas que quiero {pendiente.summary}?"
        if pendiente.summary
        else "¿Lo confirmo?"
    )
    if not hechas:
        return pregunta
    return " ".join([*hechas, pregunta])


@dataclass
class AgentReply:
    text: str
    tools_used: list[str] = field(default_factory=list)
    # Cola, no una sola: "olvida lo mío y recuerda que soy Messi" son dos
    # acciones y sólo una pide permiso. Antes la primera que pedía
    # confirmación abortaba TODA la ronda y la otra no llegaba a
    # ejecutarse nunca — el usuario decía "sí", se hacía una cosa, y la
    # otra se había perdido por el camino sin avisar.
    pendientes: list[PendingConfirmation] = field(default_factory=list)
    rounds: int = 0
    # Si la respuesta ya se fue diciendo en voz alta mientras se
    # generaba, quien reciba esto NO debe volver a decirla.
    ya_dicho: bool = False

    @property
    def pending(self) -> PendingConfirmation | None:
        """La primera que espera permiso, o None."""
        return self.pendientes[0] if self.pendientes else None


class _EmisorDeFrases:
    """Va soltando frases completas mientras el modelo sigue escribiendo.

    Dos reglas, y las dos vienen de fallos concretos:

    Sólo se dicen frases CERRADAS. Decir un trozo a medias suena a corte,
    y el TTS lo entona como si fuera el final.

    Y una frase que sea puro relleno no se dice, aunque esté cerrada. El
    filtro de coletillas mira el final del texto completo, y con
    streaming no se sabe cuál es el final hasta que termina: sin esto,
    NOVA soltaría en alto un "¿en qué puedo ayudarte?" que el filtro
    habría quitado un segundo después.
    """

    def __init__(self, on_frase: Callable[[str], None]) -> None:
        self._on_frase = on_frase
        self._buffer = ""
        self._dicho = ""
        self.dijo_algo = False

    def recibir(self, trozo: str) -> None:
        self._buffer += trozo
        cerradas, cola = frases_completas(self._buffer)
        for frase in cerradas:
            self._decir(frase)
        self._buffer = cola

    def cerrar(self, texto_pulido: str) -> None:
        """Dice lo que quede del texto final que no se haya dicho ya."""
        resto = texto_pulido
        if self._dicho and texto_pulido.startswith(self._dicho):
            resto = texto_pulido[len(self._dicho):]
        elif self._dicho:
            # El pulido cambió algo de lo ya dicho (raro). No se puede
            # des-decir, así que se calla el resto antes que repetirse.
            return
        resto = resto.strip()
        if resto:
            self._decir(resto, marcar=False)

    def _decir(self, frase: str, *, marcar: bool = True) -> None:
        if marcar:
            self._dicho = f"{self._dicho} {frase}".strip() if self._dicho else frase
        if es_relleno(frase):
            log.debug("no digo %r: es relleno", frase)
            return
        self.dijo_algo = True
        self._on_frase(frase)


class Agent:
    def __init__(
        self,
        llm: OllamaClient,
        tools: ToolRegistry,
        *,
        max_rounds: int = 4,
        on_status: Callable[[str, str, str], None] | None = None,
        on_frase: Callable[[str], None] | None = None,
    ) -> None:
        self.llm = llm
        self.tools = tools
        self.max_rounds = max_rounds
        # Callback (etapa, herramienta, dato) para que la UI pueda
        # enseñar "Abriendo Discord" y no sólo "app.open", sin que el
        # agente sepa nada de Qt.
        self._on_status = on_status or (lambda etapa, herramienta, dato: None)
        # Frases sueltas de la respuesta, según el modelo las termina de
        # escribir. Es lo que permite empezar a hablar sin esperar al
        # punto final: hasta 2.66 s de silencio en el peor caso medido.
        self._on_frase = on_frase

    def run(
        self,
        system_prompt: str,
        history: list[dict[str, str]],
        user_message: str,
    ) -> AgentReply:
        # Atajo determinista para "recuerda que ...": medido con el
        # modelo real, respondía "vale, lo recuerdo" sin llamar nunca a
        # memory.remember — así que no sobrevivía al cierre de sesión.
        # Guardar es barato y sin riesgo: no merece jugárselo a que el
        # modelo acierte.
        recuerdo = _extraer_recuerdo(user_message)
        if recuerdo:
            resultado = self.tools.execute("memory.remember", {"text": recuerdo})
            if isinstance(resultado, ToolResult) and resultado.ok:
                return AgentReply(text=resultado.message, tools_used=["memory.remember"], rounds=0)

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            *history,
            {"role": "user", "content": user_message},
        ]
        # A un "hola" o un "adiós" no se le enseña el catálogo. Medido con
        # el modelo real: "adiós" llamaba a memory.forget, que BORRA
        # cosas, y "hola" pedía tres herramientas de estado a la vez. Sin
        # catálogo delante le es imposible; con el prompt sólo, no.
        schemas = (
            None if es_pura_charla(user_message)
            else self.tools.llm_schemas(exclude=set(LLM_HIDDEN))
        )
        used: list[str] = []

        for round_n in range(1, self.max_rounds + 1):
            self._status("thinking" if round_n == 1 else "reasoning")
            emisor = _EmisorDeFrases(self._on_frase) if self._on_frase else None
            try:
                resp: LLMResponse = self.llm.chat(
                    messages,
                    tools=schemas,
                    on_trozo=emisor.recibir if emisor else None,
                )
            except OllamaError as exc:
                return AgentReply(text=str(exc), rounds=round_n)

            if not resp.tool_calls:
                self._status("writing")
                limpio = pulir(resp.text)
                if emisor:
                    # Lo ya dicho no se repite; lo que quede del texto
                    # pulido se dice ahora.
                    emisor.cerrar(limpio)
                return AgentReply(
                    text=limpio,
                    tools_used=used,
                    rounds=round_n,
                    ya_dicho=bool(emisor and emisor.dijo_algo),
                )

            messages.append(
                {
                    "role": "assistant",
                    "content": resp.text or "",
                    "tool_calls": [
                        {"function": {"name": tc.name, "arguments": tc.args}}
                        for tc in resp.tool_calls
                    ],
                }
            )

            aplazadas: list[PendingConfirmation] = []
            hechas: list[str] = []
            directas = True
            for call in resp.tool_calls:
                tool = self.tools.resolve(call.name)
                if tool is None:
                    messages.append(self._tool_msg(call.name, f"No existe la herramienta {call.name}."))
                    continue

                self._status("tool", tool.name, _dato_visible(call.args))
                outcome = self.tools.execute(tool.name, call.args)

                if isinstance(outcome, PendingConfirmation):
                    # Pedir permiso para ESTO no puede cancelar lo demás.
                    # Se aparta y se sigue con el resto de la cadena; la
                    # pregunta va al final, cuando ya está hecho lo que no
                    # necesitaba permiso.
                    aplazadas.append(outcome)
                    messages.append(self._tool_msg(
                        call.name, "Esperando a que el usuario dé permiso."))
                    continue

                if outcome.ok:
                    used.append(tool.name)
                    hechas.append(outcome.message)
                    directas = directas and tool.responde_sola
                else:
                    directas = False
                # Éxito → frase en español tal cual. Fallo → también
                # texto, pero explicando el error para que pueda reaccionar.
                messages.append(self._tool_msg(call.name, outcome.message))

            # Si todo lo de esta ronda informa por sí solo, ya está la
            # respuesta: la del modelo sólo puede empeorarla, y encima
            # cuesta otra vuelta entera.
            if not aplazadas and hechas and directas and len(hechas) == len(resp.tool_calls):
                self._status("writing")
                # Sin pasar por `emisor.cerrar()`: si el modelo hubiera
                # dicho algo suyo ("voy a mirarlo") mientras pedía la
                # herramienta, cerrar() se callaría por no repetirse y la
                # respuesta no llegaría a decirse NUNCA. Va aparte, y si
                # acaso se oye "voy a mirarlo. Ocupa 1.8 gigas", que es
                # exactamente lo que diría una persona.
                return AgentReply(text=" ".join(hechas), tools_used=used,
                                  rounds=round_n, ya_dicho=False)

            if aplazadas:
                self._status("waiting")
                return AgentReply(
                    text=_contar_y_preguntar(hechas, aplazadas[0]),
                    tools_used=used,
                    pendientes=aplazadas,
                    rounds=round_n,
                )

        # Se acabaron las rondas y el modelo seguía pidiendo herramientas.
        self._status("writing")
        messages.append(
            {
                "role": "system",
                "content": "Resume en una frase lo que has hecho y responde ya. No pidas más herramientas.",
            }
        )
        try:
            final = self.llm.chat(messages)
            text = pulir(final.text)
        except OllamaError as exc:
            text = str(exc)
        return AgentReply(text=text, tools_used=used, rounds=self.max_rounds)

    def confirm(self, pending: PendingConfirmation) -> ToolResult:
        """Ejecuta lo que quedó pendiente tras el «sí» del usuario."""
        self._status("tool", pending.tool, _dato_visible(pending.args))
        outcome = self.tools.execute(pending.tool, pending.args, confirmed=True)
        if isinstance(outcome, PendingConfirmation):  # no debería pasar
            return ToolResult(ok=False, message="La confirmación no se aplicó.")
        return outcome

    # ── Internos ─────────────────────────────────────────────────────

    @staticmethod
    def _tool_msg(name: str, content: str) -> dict[str, Any]:
        return {"role": "tool", "name": name, "content": content}

    def _status(self, etapa: str, herramienta: str = "", dato: str = "") -> None:
        try:
            self._on_status(etapa, herramienta, dato)
        except Exception:
            log.debug("callback de estado falló", exc_info=True)
