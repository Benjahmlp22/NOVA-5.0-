"""Qué es un plugin y qué permisos pide.

Un plugin es una carpeta con un `plugin.json` (el manifiesto) y, si hace
falta, un `plugin.py`.

**Lo que hay que tener claro antes de leer nada más:** un plugin con
código Python corre con TUS permisos. Puede leer tus archivos, borrarlos
o mandarlos por internet. Python no tiene forma real de encerrar código
ajeno — no hay caja de arena que valga, y quien diga lo contrario te
está vendiendo humo.

Así que aquí no se promete seguridad. Lo que se hace es más modesto y
más honesto:

1. **El plugin declara lo que necesita.** Sin declararlo, NOVA no le da
   la herramienta.
2. **Se te enseña antes de activarlo**, en castellano y sin tecnicismos.
3. **Se avisa de lo que huele mal** al leer el código: `eval`, `exec`,
   `subprocess`, `socket`, `shutil.rmtree`... Eso NO garantiza nada: el
   que quiera esconderlo puede. Es un detector de descuidos, no de
   malicia.
4. **Los plugins de sólo datos no ejecutan nada** y no pueden hacer
   daño. Esos van sin ceremonia.

La diferencia importante: un plugin *de datos* (personalidad, frases,
voz) es imposible que te haga nada. Uno *con código* puede hacer
cualquier cosa. La interfaz los distingue a propósito.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger("nova.plugins")

MANIFIESTO = "plugin.json"
CODIGO = "plugin.py"


# Lo que un plugin puede pedir, y cómo se le explica al usuario. El
# texto es lo que se lee en el panel antes de activar: si no se entiende
# leyéndolo en voz alta, está mal escrito.
PERMISOS = {
    "personalidad": "Cambiar cómo habla y se comporta NOVA",
    "voz":          "Elegir con qué voz habla",
    "frases":       "Añadir respuestas y expresiones suyas",
    "herramientas": "Añadir cosas nuevas que NOVA sabe hacer",
    "archivos":     "Leer y escribir archivos en tu PC",
    "red":          "Conectarse a internet",
    "pantalla":     "Ver lo que hay en tu pantalla",
    "microfono":    "Escuchar por el micrófono",
    "sistema":      "Ejecutar programas y tocar la configuración",
}

# Los que dan miedo de verdad. El panel los enseña en ámbar y con el
# código a la vista antes de dejar activar nada.
DELICADOS = frozenset({"archivos", "red", "microfono", "sistema"})


@dataclass
class Plugin:
    """Un plugin ya leído del disco. Nada se ha ejecutado todavía."""

    id: str
    nombre: str
    descripcion: str
    version: str = "1.0.0"
    autor: str = "anónimo"
    permisos: list[str] = field(default_factory=list)
    carpeta: Path | None = None

    # Lo que el plugin aporta, si es de datos.
    personalidad: str = ""
    frases: dict[str, list[str]] = field(default_factory=dict)
    voz: str = ""
    ajustes: dict = field(default_factory=dict)

    # Problemas encontrados al leerlo. Si hay alguno, no se activa.
    errores: list[str] = field(default_factory=list)

    @property
    def tiene_codigo(self) -> bool:
        return bool(self.carpeta and (self.carpeta / CODIGO).exists())

    @property
    def es_delicado(self) -> bool:
        """¿Pide algo con lo que podría hacerte daño?"""
        return self.tiene_codigo or bool(set(self.permisos) & DELICADOS)

    @property
    def valido(self) -> bool:
        return not self.errores

    def permisos_en_castellano(self) -> list[str]:
        return [PERMISOS.get(p, p) for p in self.permisos]


def leer(carpeta: Path) -> Plugin | None:
    """Lee el manifiesto. NO ejecuta nada del plugin.

    Devuelve None si ni siquiera parece un plugin; devuelve un Plugin
    con `errores` si lo parece pero está mal. La diferencia importa: lo
    segundo hay que enseñárselo al usuario, lo primero no.
    """
    archivo = carpeta / MANIFIESTO
    if not archivo.exists():
        return None

    try:
        datos = json.loads(archivo.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return Plugin(id=carpeta.name, nombre=carpeta.name, descripcion="",
                      carpeta=carpeta, errores=[f"el plugin.json no se puede leer: {exc}"])

    if not isinstance(datos, dict):
        return Plugin(id=carpeta.name, nombre=carpeta.name, descripcion="",
                      carpeta=carpeta, errores=["el plugin.json no es un objeto"])

    errores: list[str] = []
    for obligatorio in ("id", "nombre", "descripcion"):
        if not str(datos.get(obligatorio, "")).strip():
            errores.append(f"le falta «{obligatorio}»")

    permisos = datos.get("permisos") or []
    if not isinstance(permisos, list):
        errores.append("«permisos» tiene que ser una lista")
        permisos = []
    desconocidos = [p for p in permisos if p not in PERMISOS]
    if desconocidos:
        errores.append(f"pide permisos que no existen: {', '.join(desconocidos)}")

    plugin = Plugin(
        id=str(datos.get("id") or carpeta.name),
        nombre=str(datos.get("nombre") or carpeta.name),
        descripcion=str(datos.get("descripcion") or ""),
        version=str(datos.get("version") or "1.0.0"),
        autor=str(datos.get("autor") or "anónimo"),
        permisos=[p for p in permisos if p in PERMISOS],
        carpeta=carpeta,
        personalidad=str(datos.get("personalidad") or ""),
        frases=datos.get("frases") if isinstance(datos.get("frases"), dict) else {},
        voz=str(datos.get("voz") or ""),
        ajustes=datos.get("ajustes") if isinstance(datos.get("ajustes"), dict) else {},
        errores=errores,
    )

    # Coherencia: lo que aporta tiene que estar declarado. Un plugin que
    # cambia la personalidad sin pedir el permiso «personalidad» no es
    # necesariamente malo, pero sí está mal hecho, y el usuario merece
    # que la lista de permisos sea de verdad la lista de lo que hace.
    if plugin.personalidad and "personalidad" not in plugin.permisos:
        plugin.errores.append("cambia la personalidad sin pedir ese permiso")
    if plugin.voz and "voz" not in plugin.permisos:
        plugin.errores.append("cambia la voz sin pedir ese permiso")
    if plugin.tiene_codigo and "herramientas" not in plugin.permisos:
        plugin.errores.append("trae código sin pedir el permiso «herramientas»")

    return plugin
