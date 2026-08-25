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
from .polish import pulir

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


def _extraer_recuerdo(mensaje: str) -> str:
    m = _RECUERDA.match(mensaje or "")
    if not m:
        return ""
    dato = m.group(1).strip(" .,;")
    # "recuerda buscar el archivo" es una orden, no un dato que guardar.
    if len(dato) < 3 or dato.lower().startswith(("que hiciste", "lo que")):
        return ""
    return dato


@dataclass
class AgentReply:
    text: str
    tools_used: list[str] = field(default_factory=list)
    pending: PendingConfirmation | None = None
    rounds: int = 0


class Agent:
    def __init__(
        self,
        llm: OllamaClient,
        tools: ToolRegistry,
        *,
        max_rounds: int = 4,
        on_status: Callable[[str, str], None] | None = None,
    ) -> None:
        self.llm = llm
        self.tools = tools
        self.max_rounds = max_rounds
        # Callback (etapa, detalle) para que la UI muestre "pensando",
        # "ejecutando captura", etc. sin que el agente sepa de Qt.
        self._on_status = on_status or (lambda stage, detail: None)

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
        schemas = self.tools.llm_schemas(exclude=set(LLM_HIDDEN))
        used: list[str] = []

        for round_n in range(1, self.max_rounds + 1):
            self._status("thinking" if round_n == 1 else "reasoning")
            try:
                resp: LLMResponse = self.llm.chat(messages, tools=schemas)
            except OllamaError as exc:
                return AgentReply(text=str(exc), rounds=round_n)

            if not resp.tool_calls:
                self._status("writing")
                return AgentReply(
                    text=pulir(resp.text),
                    tools_used=used,
                    rounds=round_n,
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

            for call in resp.tool_calls:
                tool = self.tools.resolve(call.name)
                if tool is None:
                    messages.append(self._tool_msg(call.name, f"No existe la herramienta {call.name}."))
                    continue

                self._status("tool", tool.name)
                outcome = self.tools.execute(tool.name, call.args)

                if isinstance(outcome, PendingConfirmation):
                    # Se para aquí: el usuario decide. No seguimos dando
                    # vueltas ni ejecutamos el resto de llamadas.
                    self._status("waiting")
                    return AgentReply(
                        text=f"¿Confirmas que quiero {outcome.summary}?",
                        tools_used=used,
                        pending=outcome,
                        rounds=round_n,
                    )

                if outcome.ok:
                    used.append(tool.name)
                # Éxito → frase en español tal cual. Fallo → también
                # texto, pero explicando el error para que pueda reaccionar.
                messages.append(self._tool_msg(call.name, outcome.message))

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
        """Ejecuta lo que quedó pendiente tras el 'sí' del usuario."""
        self._status("tool", pending.tool)
        outcome = self.tools.execute(pending.tool, pending.args, confirmed=True)
        if isinstance(outcome, PendingConfirmation):  # no debería pasar
            return ToolResult(ok=False, message="La confirmación no se aplicó.")
        return outcome

    # ── Internos ─────────────────────────────────────────────────────

    @staticmethod
    def _tool_msg(name: str, content: str) -> dict[str, Any]:
        return {"role": "tool", "name": name, "content": content}

    def _status(self, stage: str, detail: str = "") -> None:
        try:
            self._on_status(stage, detail)
        except Exception:
            log.debug("callback de estado falló", exc_info=True)
