"""Cargar el código de un plugin y registrar sus herramientas.

Aquí es donde se ejecuta código ajeno, así que este archivo es el que
hay que leer con más cuidado de todo el proyecto.

Reglas:

1. **Sólo se carga lo que está activo.** Un plugin encontrado pero
   apagado no se importa nunca. Importar un módulo YA ejecuta su cuerpo:
   no hace falta llamar a nada para que haga daño.
2. **Sólo si pidió el permiso `herramientas`.** Sin declararlo, su
   `plugin.py` se ignora aunque exista.
3. **Un plugin que revienta no tumba NOVA.** Se anota, se apaga y se
   sigue.

Lo que este archivo NO hace, y conviene tener claro: encerrar el código.
No hay caja de arena. Una vez importado, el plugin puede hacer lo que
podría hacer cualquier programa tuyo. La defensa está antes — en que se
te enseñe qué pide y qué código trae — no aquí.
"""

from __future__ import annotations

import importlib.util
import logging
import sys
from typing import Any

from ..tools.registry import Risk, Tool, ToolResult
from .manifiesto import Plugin

log = logging.getLogger("nova.plugins.carga")

# Los riesgos que un plugin puede pedir para sus herramientas. No puede
# declarar SAFE por su cuenta: lo mínimo es MEDIUM, porque nadie ha
# revisado su código como se ha revisado el de NOVA.
_RIESGOS = {"medium": Risk.MEDIUM, "dangerous": Risk.DANGEROUS}


def _a_resultado(salida: Any) -> ToolResult:
    """Lo que devuelva el plugin, convertido a algo que NOVA entienda.

    Se acepta un dict o directamente una cadena, para que escribir un
    plugin no obligue a importar nada de NOVA. Cuanto menos tenga que
    saber el autor, más gente escribirá plugins.
    """
    if isinstance(salida, ToolResult):
        return salida
    if isinstance(salida, dict):
        return ToolResult(
            ok=bool(salida.get("ok", True)),
            message=str(salida.get("message", "")),
            data=salida.get("data") if isinstance(salida.get("data"), dict) else {},
        )
    return ToolResult(ok=True, message=str(salida))


def _envolver(handler, id_plugin: str):  # noqa: ANN001
    """Blinda la llamada: un plugin que revienta no rompe el turno."""
    def envuelto(**kwargs):
        try:
            return _a_resultado(handler(**kwargs))
        except Exception as exc:  # noqa: BLE001
            log.warning("el plugin %s falló", id_plugin, exc_info=True)
            return ToolResult(ok=False, message=f"El plugin {id_plugin} ha fallado: {exc}")
    return envuelto


def cargar(plugin: Plugin, registro) -> tuple[int, str]:  # noqa: ANN001
    """Importa el plugin y le registra sus herramientas.

    Devuelve cuántas registró y un mensaje si algo fue mal.
    """
    if not plugin.tiene_codigo or plugin.carpeta is None:
        return 0, ""
    if "herramientas" not in plugin.permisos:
        return 0, f"{plugin.nombre} trae código pero no pidió el permiso «herramientas»"

    archivo = plugin.carpeta / "plugin.py"
    nombre_modulo = f"nova_plugin_{plugin.id}"

    try:
        spec = importlib.util.spec_from_file_location(nombre_modulo, archivo)
        if spec is None or spec.loader is None:
            return 0, f"no pude preparar {archivo.name}"
        modulo = importlib.util.module_from_spec(spec)
        # Registrarlo antes de ejecutarlo: si el plugin se importa a sí
        # mismo, así no entra en bucle.
        sys.modules[nombre_modulo] = modulo
        spec.loader.exec_module(modulo)          # aquí corre código ajeno
    except Exception as exc:  # noqa: BLE001
        sys.modules.pop(nombre_modulo, None)
        log.warning("no pude cargar el plugin %s", plugin.id, exc_info=True)
        return 0, f"{plugin.nombre} no se pudo cargar: {exc}"

    declaradas = getattr(modulo, "HERRAMIENTAS", None)
    if not isinstance(declaradas, list):
        return 0, f"{plugin.nombre} no declara ninguna herramienta"

    puestas = 0
    for cruda in declaradas:
        if not isinstance(cruda, dict):
            continue
        nombre = str(cruda.get("name") or "").strip()
        handler = cruda.get("handler")
        if not nombre or not callable(handler):
            continue

        # El nombre se prefija con el id del plugin si no lo lleva ya:
        # dos plugins no pueden pisarse una herramienta entre ellos, ni
        # pisar una de NOVA.
        if not nombre.startswith(f"{plugin.id}."):
            nombre = f"{plugin.id}.{nombre.split('.')[-1]}"

        if registro.get(nombre) is not None:
            log.info("el plugin %s intentó registrar %s dos veces", plugin.id, nombre)
            continue

        try:
            registro.register(Tool(
                name=nombre,
                description=str(cruda.get("description") or nombre),
                handler=_envolver(handler, plugin.id),
                schema=cruda.get("schema") or {"type": "object", "properties": {}},
                # Nunca SAFE: nadie ha revisado esto como el código de NOVA.
                risk=_RIESGOS.get(str(cruda.get("risk", "")).lower(), Risk.MEDIUM),
                responde_sola=bool(cruda.get("responde_sola")),
            ))
            puestas += 1
        except ValueError as exc:
            log.info("no pude registrar %s: %s", nombre, exc)

    return puestas, ""


def cargar_activos(gestor, registro) -> list[str]:  # noqa: ANN001
    """Carga el código de todos los plugins activos. Devuelve los avisos."""
    avisos: list[str] = []
    for plugin in gestor.activos():
        if not plugin.tiene_codigo:
            continue
        cuantas, problema = cargar(plugin, registro)
        if problema:
            avisos.append(problema)
            # Uno que no carga se apaga: dejarlo "activo" haría que el
            # panel mintiera sobre lo que está corriendo.
            gestor.desactivar(plugin.id)
        elif cuantas:
            log.info("plugin %s: %d herramienta(s)", plugin.id, cuantas)
    return avisos
