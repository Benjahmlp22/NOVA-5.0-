"""Leer el código de un plugin y avisar de lo que huele mal.

**Esto NO es un antivirus y no hay que tratarlo como tal.** Es un
detector de descuidos, no de malicia. Quien quiera esconder algo lo
esconde: `getattr(__builtins__, "e"+"val")` se salta cualquier búsqueda
de texto, y en Python siempre hay diez formas de llegar al mismo sitio.

Entonces, ¿para qué sirve?

Para que la mayoría de los plugins —que están escritos por gente
normal, no por atacantes— enseñen de un vistazo qué tocan. Y para que
cuando algo pida abrir sockets o borrar carpetas, eso salga en pantalla
en ámbar antes de que le des a activar, en vez de enterarte después.

La protección de verdad son otras dos cosas, y ninguna es técnica:

- que el código te lo enseñemos ENTERO antes de activarlo, y
- que sepas de dónde viene el plugin.

El análisis se hace sobre el árbol sintáctico (`ast`) y no con
expresiones regulares, porque leer el código como texto se confunde con
cualquier comentario o cadena que mencione una palabra prohibida.
"""

from __future__ import annotations

import ast
import logging
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger("nova.plugins.revision")

# Módulos que dan poder de verdad. La frase es lo que se le enseña al
# usuario: tiene que entenderse sin saber programar.
MODULOS_DELICADOS = {
    "subprocess": "puede ejecutar otros programas",
    "socket":     "puede abrir conexiones de red por su cuenta",
    "shutil":     "puede copiar y borrar carpetas enteras",
    "ctypes":     "puede llamar directamente a Windows",
    "pickle":     "puede ejecutar código al cargar datos",
    "marshal":    "puede ejecutar código al cargar datos",
    "importlib":  "puede cargar más código en marcha",
    "winreg":     "puede tocar el registro de Windows",
    "http":       "puede conectarse a internet",
    "urllib":     "puede conectarse a internet",
    "httpx":      "puede conectarse a internet",
    "requests":   "puede conectarse a internet",
    "smtplib":    "puede enviar correos",
    "ftplib":     "puede subir archivos a un servidor",
}

# Funciones que convierten texto en código. Si un plugin las usa, lo que
# haga de verdad no se puede saber leyéndolo.
FUNCIONES_DELICADAS = {
    "eval":       "ejecuta texto como si fuera código",
    "exec":       "ejecuta texto como si fuera código",
    "compile":    "convierte texto en código",
    "__import__": "carga módulos por su nombre en texto",
}

# Lo que además de delicado es directamente destructivo.
LLAMADAS_DESTRUCTIVAS = {
    "rmtree": "borra carpetas enteras, con todo lo que haya dentro",
    "unlink": "borra archivos",
    "remove": "borra archivos",
    "system": "ejecuta órdenes del sistema",
}


# Los `__nombre__` que sale cualquier archivo normal. El resto
# (`__builtins__`, `__globals__`, `__subclasses__`) es la forma
# habitual de llegar a algo sin nombrarlo.
_DUNDERS_NORMALES = frozenset({"__name__", "__file__", "__doc__", "__init__"})


@dataclass
class Hallazgo:
    linea: int
    que: str
    porque: str
    grave: bool = False


@dataclass
class Revision:
    hallazgos: list[Hallazgo] = field(default_factory=list)
    error: str = ""

    @property
    def limpio(self) -> bool:
        return not self.hallazgos and not self.error

    @property
    def graves(self) -> list[Hallazgo]:
        return [h for h in self.hallazgos if h.grave]

    def resumen(self) -> str:
        """Una frase para decir en alto."""
        if self.error:
            return f"No pude leer el código: {self.error}"
        if not self.hallazgos:
            return "No he visto nada raro en el código."
        graves = len(self.graves)
        if graves:
            return (f"Ojo: {graves} cosa(s) que pueden hacer daño de verdad, "
                    f"y {len(self.hallazgos) - graves} más que conviene mirar.")
        return f"{len(self.hallazgos)} cosa(s) que conviene mirar antes de activarlo."


class _Visitante(ast.NodeVisitor):
    def __init__(self) -> None:
        self.hallazgos: list[Hallazgo] = []

    def _apuntar(self, nodo, que: str, porque: str, grave: bool = False) -> None:  # noqa: ANN001
        self.hallazgos.append(Hallazgo(getattr(nodo, "lineno", 0), que, porque, grave))

    def visit_Import(self, nodo: ast.Import) -> None:  # noqa: N802
        for alias in nodo.names:
            raiz = alias.name.split(".")[0]
            if raiz in MODULOS_DELICADOS:
                self._apuntar(nodo, f"importa {alias.name}", MODULOS_DELICADOS[raiz])
        self.generic_visit(nodo)

    def visit_ImportFrom(self, nodo: ast.ImportFrom) -> None:  # noqa: N802
        raiz = (nodo.module or "").split(".")[0]
        if raiz in MODULOS_DELICADOS:
            self._apuntar(nodo, f"importa de {nodo.module}", MODULOS_DELICADOS[raiz])
        self.generic_visit(nodo)

    def visit_Call(self, nodo: ast.Call) -> None:  # noqa: N802
        nombre = ""
        if isinstance(nodo.func, ast.Name):
            nombre = nodo.func.id
        elif isinstance(nodo.func, ast.Attribute):
            nombre = nodo.func.attr

        if nombre in FUNCIONES_DELICADAS:
            self._apuntar(nodo, f"usa {nombre}()", FUNCIONES_DELICADAS[nombre], grave=True)
        elif nombre in LLAMADAS_DESTRUCTIVAS:
            self._apuntar(nodo, f"llama a {nombre}()", LLAMADAS_DESTRUCTIVAS[nombre], grave=True)
        elif nombre == "getattr" and len(nodo.args) >= 2 and not isinstance(
                nodo.args[1], ast.Constant):
            # `getattr(algo, "e" + "val")`: el nombre se construye en
            # marcha, así que leyendo el código no se sabe a qué llama.
            self._apuntar(nodo, "usa getattr() con un nombre calculado",
                          "el nombre de lo que llama no se puede saber leyéndolo",
                          grave=True)

        self.generic_visit(nodo)

    def visit_Name(self, nodo: ast.Name) -> None:  # noqa: N802
        # `getattr(__builtins__, "e" + "val")` es el truco de manual
        # para llegar a eval sin escribir "eval". Aquí `__builtins__` es
        # un Name, no un Attribute, así que la comprobación de abajo NO
        # lo veía: se coló en la primera prueba con código malicioso.
        if (nodo.id.startswith("__") and nodo.id.endswith("__")
                and nodo.id not in _DUNDERS_NORMALES):
            self._apuntar(nodo, f"usa {nodo.id}",
                          "está rebuscando en las tripas de Python", grave=True)
        self.generic_visit(nodo)

    def visit_Attribute(self, nodo: ast.Attribute) -> None:  # noqa: N802
        # `__builtins__`, `__globals__` y compañía: la forma habitual de
        # llegar a algo sin nombrarlo.
        if (nodo.attr.startswith("__") and nodo.attr.endswith("__")
                and nodo.attr not in _DUNDERS_NORMALES):
            self._apuntar(nodo, f"toca {nodo.attr}",
                          "está rebuscando en las tripas de Python", grave=True)
        self.generic_visit(nodo)


def revisar(archivo: Path) -> Revision:
    """Mira el código de un plugin sin ejecutarlo."""
    if not archivo.exists():
        return Revision()

    try:
        arbol = ast.parse(archivo.read_text(encoding="utf-8"), filename=str(archivo))
    except (OSError, SyntaxError) as exc:
        return Revision(error=str(exc))

    visitante = _Visitante()
    visitante.visit(arbol)

    # Ordenados: primero lo grave, y dentro de eso por línea.
    visitante.hallazgos.sort(key=lambda h: (not h.grave, h.linea))
    return Revision(hallazgos=visitante.hallazgos)
