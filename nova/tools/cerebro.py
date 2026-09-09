"""Cambiar de cerebro hablando: «modo rápido» y «modo local».

Encender el cerebro de la nube no es un ajuste como el volumen: es la
diferencia entre que lo que dices se quede en tu ordenador o no.  Por eso
se pide en voz alta, NOVA contesta diciendo lo que implica, y se apaga
sola al reiniciar — un permiso que sobrevive a los reinicios acaba siendo
un permiso que nadie recuerda haber dado.

Sin clave configurada las tres herramientas siguen existiendo, pero
dicen que no hay nada que encender y cómo conseguirlo.  Es mejor que
esconderlas: si no aparecen, NOVA contesta "no puedo hacer eso" y Benjahmlp22
no se entera de que la opción existe.
"""

from __future__ import annotations

import logging

from .registry import Risk, Tool, ToolResult

log = logging.getLogger("nova.tools.cerebro")

# Lo pone la app al arrancar (ver `Nova.start`). Sin él estas
# herramientas no pueden hacer nada, y lo dicen en vez de mentir.
_nova = None


def conectar(nova) -> None:  # noqa: ANN001
    global _nova  # noqa: PLW0603
    _nova = nova


def _sin_conectar() -> ToolResult:
    return ToolResult(ok=False, message="Ahora mismo no puedo cambiar de cerebro.")


def rapido() -> ToolResult:
    """Enciende el cerebro de la nube, diciendo lo que eso significa."""
    if _nova is None:
        return _sin_conectar()
    if not _nova.remoto.disponible:
        return ToolResult(
            ok=False,
            message="No tengo clave para el cerebro rápido. Cuando pongas una en "
                    "data barra groq punto key, lo enciendo.",
        )
    _nova.preferir_cerebro("rapido")
    return ToolResult(
        ok=True,
        message="Modo rápido. A partir de ahora pienso en la nube, así que lo que "
                "hablemos sale del ordenador. Dime «modo local» para volver.",
        data={"remoto": True},
    )


def local() -> ToolResult:
    """Vuelve al modelo de casa."""
    if _nova is None:
        return _sin_conectar()
    _nova.preferir_cerebro("local")
    return ToolResult(
        ok=True,
        message="Modo local. Vuelvo a pensar aquí dentro; no sale nada del PC.",
        data={"remoto": False},
    )


def estado() -> ToolResult:
    """Con qué está pensando ahora mismo, y por qué."""
    if _nova is None:
        return _sin_conectar()
    if _nova.en_remoto:
        return ToolResult(
            ok=True,
            message=f"Estoy en modo rápido, pensando con {_nova.remoto.model} en la nube.",
            data={"remoto": True, "modelo": _nova.remoto.model},
        )
    cola = " en modo ligero, porque no hay VRAM libre" if _nova.modo_ligero else ""
    return ToolResult(
        ok=True,
        message=f"Estoy pensando aquí, con {_nova.llm.model}{cola}.",
        data={"remoto": False, "modelo": _nova.llm.model},
    )


def register(reg) -> None:  # noqa: ANN001
    reg.register(Tool(
        name="cerebro.rapido",
        description=(
            "Enciende el cerebro rápido en la nube, para programar o razonar mejor. "
            "Sólo si él lo pide: implica que sus datos salen del PC"
        ),
        handler=rapido,
        risk=Risk.MEDIUM,
        responde_sola=True,
    ))
    reg.register(Tool(
        name="cerebro.local",
        description="Vuelve a pensar con el modelo local, sin que salga nada del ordenador",
        handler=local,
        risk=Risk.SAFE,
        responde_sola=True,
    ))
    reg.register(Tool(
        name="cerebro.estado",
        description="Con qué modelo está pensando ahora mismo y por qué",
        handler=estado,
        risk=Risk.SAFE,
        responde_sola=True,
    ))
