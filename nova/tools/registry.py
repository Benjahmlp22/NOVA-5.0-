"""Registro de herramientas + control de permisos.

Regla de oro: el modelo propone, el registro dispone.  Ninguna acción
peligrosa se ejecuta porque el LLM lo diga — pasa por aquí siempre.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

log = logging.getLogger("nova.tools")


class Risk(str, Enum):
    """Cuánto duele si esto sale mal."""

    SAFE = "safe"          # leer estado, listar, capturar: nunca pregunta
    MEDIUM = "medium"      # abrir apps, crear archivos: según política
    DANGEROUS = "dangerous"  # borrar, matar procesos, comandos: siempre pregunta


@dataclass
class ToolResult:
    ok: bool
    # `message` es la frase en español que se le da al modelo Y al
    # usuario. Que sea la misma cadena no es pereza: es lo que evita que
    # el modelo reinterprete un JSON y responda otra cosa (ver agent.py).
    message: str = ""
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class Tool:
    name: str
    description: str
    handler: Callable[..., ToolResult]
    schema: dict[str, Any] = field(default_factory=lambda: {"type": "object", "properties": {}})
    risk: Risk = Risk.SAFE
    # Herramientas útiles para el usuario pero que ensucian el catálogo
    # del modelo pueden registrarse sin ofrecerse al LLM.
    expose_to_llm: bool = True
    # Su resultado YA es la respuesta completa para el usuario, y no
    # hace falta que el modelo lo reescriba.
    #
    # Sale de verlo fallar: preguntándole "cuál es el archivo más grande
    # de descargas", NOVA llamaba bien a la herramienta, recibía "Lo que
    # más ocupa: setup.exe, 1.8 gigas..." y contestaba "si quieres que te
    # diga qué ocupa más, dímelo". Tenía la respuesta delante y no la
    # daba.
    #
    # El prompt ya se lo pide ("repítela tal cual") y no basta, así que
    # va por código, como el relleno y los permisos. De paso se ahorra
    # una vuelta entera al modelo.
    #
    # Sólo para las que informan. `web.search` o `pantalla.leer`
    # devuelven material EN BRUTO que el modelo tiene que resumir: ésas
    # no lo son.
    responde_sola: bool = False
    # Cómo se le cuenta al usuario lo que va a pasar, si la frase
    # genérica no basta. "¿Confirmas que quiero ordenar descargas?" no
    # avisa de nada; "mover 611 archivos" sí. Recibe los argumentos y
    # devuelve la frase.
    resumir: Callable[[dict[str, Any]], str] | None = None

    @property
    def llm_name(self) -> str:
        """OpenAI/Ollama no aceptan puntos en los nombres de función."""
        return self.name.replace(".", "_")


@dataclass
class PendingConfirmation:
    tool: str
    args: dict[str, Any]
    summary: str


class ToolRegistry:
    def __init__(self, confirm_policy: str = "solo_peligroso") -> None:
        self._tools: dict[str, Tool] = {}
        self.confirm_policy = confirm_policy

    # ── Registro ─────────────────────────────────────────────────────

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"herramienta duplicada: {tool.name}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def resolve(self, llm_name: str) -> Tool | None:
        """Acepta tanto 'app.open' como 'app_open'."""
        if llm_name in self._tools:
            return self._tools[llm_name]
        for tool in self._tools.values():
            if tool.llm_name == llm_name:
                return tool
        return None

    def names(self) -> list[str]:
        return sorted(self._tools)

    # ── Permisos ─────────────────────────────────────────────────────

    def needs_confirmation(self, tool: Tool) -> bool:
        if tool.risk is Risk.SAFE:
            return False
        if tool.risk is Risk.DANGEROUS:
            return True  # nunca se salta, da igual la política
        return self.confirm_policy == "estricto"

    # ── Ejecución ────────────────────────────────────────────────────

    def execute(
        self, name: str, args: dict[str, Any], *, confirmed: bool = False
    ) -> ToolResult | PendingConfirmation:
        tool = self.resolve(name)
        if tool is None:
            return ToolResult(ok=False, message=f"No tengo ninguna herramienta '{name}'.")

        if not confirmed and self.needs_confirmation(tool):
            return PendingConfirmation(
                tool=tool.name,
                args=args,
                summary=_summarize(tool, args),
            )

        try:
            result = tool.handler(**args) if args else tool.handler()
        except TypeError as exc:
            # Argumentos inventados por el modelo: es un error suyo, no
            # un fallo del sistema. Se lo decimos para que reintente bien.
            log.warning("argumentos inválidos para %s: %s", tool.name, exc)
            return ToolResult(ok=False, message=f"Argumentos incorrectos para {tool.name}: {exc}")
        except Exception as exc:
            log.exception("fallo ejecutando %s", tool.name)
            return ToolResult(ok=False, message=f"Falló {tool.name}: {exc}")

        log.info("tool %s -> ok=%s", tool.name, result.ok)
        return result

    # ── Catálogo para el LLM ─────────────────────────────────────────

    def llm_schemas(self, exclude: set[str] | None = None) -> list[dict[str, Any]]:
        """Esquemas estilo OpenAI de las tools ofrecidas al modelo.

        Cada esquema se reenvía en CADA ronda de CADA turno, así que el
        catálogo se mantiene corto a propósito: menos tokens de prefill
        es menos latencia en inferencia local.
        """
        skip = exclude or set()
        out = []
        for tool in self._tools.values():
            if not tool.expose_to_llm or tool.name in skip:
                continue
            desc = tool.description
            if tool.risk is Risk.DANGEROUS:
                desc += " (pedirá confirmación al usuario)"
            out.append(
                {
                    "type": "function",
                    "function": {
                        "name": tool.llm_name,
                        "description": desc[:900],
                        "parameters": tool.schema,
                    },
                }
            )
        return out


def _summarize(tool: Tool, args: dict[str, Any]) -> str:
    """Frase corta de lo que se va a hacer, para pedir permiso."""
    if tool.resumir is not None:
        try:
            propia = tool.resumir(args)
            if propia:
                return propia
        except Exception:  # noqa: BLE001
            log.debug("el resumen propio de %s falló", tool.name, exc_info=True)
    target = args.get("name") or args.get("path") or args.get("command") or ""
    if target:
        return f"{tool.description.split('.')[0].lower()}: {target}"
    return tool.description.split(".")[0].lower()
