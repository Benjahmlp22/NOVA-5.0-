"""Manejar los plugins hablando.

«Abre los plugins», «qué plugins tengo», «activa el mayordomo».

Abrir el panel es MEDIUM y no SAFE porque abre una ventana encima de lo
que estés haciendo. Activar y desactivar también: cambian cómo se
comporta NOVA a partir de ese momento, y eso no es una consulta.

Lo que NO se puede hacer por voz: activar un plugin que trae código.
Para eso hay que abrir el panel, leer lo que hace y darle al botón. Un
«activa tal cosa» dicho de pasada no es consentimiento informado para
ejecutar código de un tercero, y decirlo por el micrófono desde el
salón, menos.
"""

from __future__ import annotations

import logging

from .registry import Risk, Tool, ToolResult

log = logging.getLogger("nova.tools.plugins")

# Lo pone la app al arrancar: el gestor y una función para abrir la
# ventana, que sólo puede llamarse desde el hilo de la interfaz.
_gestor = None
_abrir_panel = None


def conectar(gestor, abrir_panel) -> None:  # noqa: ANN001
    global _gestor, _abrir_panel  # noqa: PLW0603
    _gestor = gestor
    _abrir_panel = abrir_panel


def _sin_gestor() -> ToolResult:
    return ToolResult(ok=False, message="Ahora mismo no puedo con los plugins.")


# ── Herramientas ─────────────────────────────────────────────────────

def abrir() -> ToolResult:
    if _abrir_panel is None:
        return _sin_gestor()
    try:
        _abrir_panel()
    except Exception as exc:  # noqa: BLE001
        log.warning("no pude abrir el panel", exc_info=True)
        return ToolResult(ok=False, message=f"No pude abrir el panel: {exc}")
    return ToolResult(ok=True, message="Ahí tienes el panel de plugins.")


def listar() -> ToolResult:
    if _gestor is None:
        return _sin_gestor()

    _gestor.buscar()
    todos = _gestor.todos()
    if not todos:
        return ToolResult(ok=True, message="No tienes ningún plugin instalado todavía.")

    activos = [p.nombre for p in _gestor.activos()]
    apagados = [p.nombre for p in todos if not _gestor.esta_activo(p.id)]

    partes = []
    if activos:
        partes.append("Activos: " + ", ".join(activos) + ".")
    else:
        partes.append("No tienes ninguno activo.")
    if apagados:
        partes.append("Apagados: " + ", ".join(apagados) + ".")
    return ToolResult(ok=True, message=" ".join(partes), data={"cuantos": len(todos)})


def activar(nombre: str) -> ToolResult:
    if _gestor is None:
        return _sin_gestor()

    plugin = _gestor.obtener(nombre)
    if plugin is None:
        return ToolResult(ok=False, message=f"No tengo ningún plugin que se llame «{nombre}».")

    # Los que traen código no se activan de oído. Ver el docstring de
    # arriba: decir un nombre de pasada no es permiso para ejecutar
    # código de otra persona.
    if plugin.tiene_codigo:
        return ToolResult(
            ok=False,
            message=f"{plugin.nombre} trae código propio, así que eso no lo activo "
                    "de oído. Te abro el panel para que lo veas y lo actives tú.",
            data={"abrir_panel": True},
        )

    hecho, mensaje = _gestor.activar(plugin.id)
    return ToolResult(ok=hecho, message=mensaje)


def desactivar(nombre: str) -> ToolResult:
    if _gestor is None:
        return _sin_gestor()
    plugin = _gestor.obtener(nombre)
    if plugin is None:
        return ToolResult(ok=False, message=f"No tengo ningún plugin que se llame «{nombre}».")
    # Apagar siempre se puede: quitar algo nunca es más peligroso que
    # dejarlo puesto.
    hecho, mensaje = _gestor.desactivar(plugin.id)
    return ToolResult(ok=hecho, message=mensaje)


def register(reg) -> None:  # noqa: ANN001
    reg.register(Tool(
        name="plugin.panel",
        description=(
            "Abre el panel de plugins: «abre los plugins», «enséñame los "
            "plugins», «quiero personalizarte»"
        ),
        handler=abrir,
        risk=Risk.MEDIUM,
    ))
    reg.register(Tool(
        name="plugin.listar",
        description="Qué plugins tiene instalados y cuáles están activos",
        handler=listar,
        risk=Risk.SAFE,
        responde_sola=True,
    ))
    reg.register(Tool(
        name="plugin.activar",
        description="Enciende un plugin por su nombre. Los que traen código no, ésos por el panel",
        handler=activar,
        schema={
            "type": "object",
            "properties": {"nombre": {"type": "string"}},
            "required": ["nombre"],
        },
        risk=Risk.MEDIUM,
    ))
    reg.register(Tool(
        name="plugin.desactivar",
        description="Apaga un plugin por su nombre",
        handler=desactivar,
        schema={
            "type": "object",
            "properties": {"nombre": {"type": "string"}},
            "required": ["nombre"],
        },
        risk=Risk.MEDIUM,
    ))
