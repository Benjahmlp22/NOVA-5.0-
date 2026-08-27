"""Buscar archivos tuyos: "abre el PDF de la factura".

Distinto de `files.py`, que sólo ve `workspace/` porque ahí NOVA
escribe.  Aquí sólo se LEE, y por eso puede mirar en tus carpetas de
verdad: Escritorio, Descargas, Documentos, Imágenes.

Nunca se abre un ejecutable.  Un .exe recién caído en Descargas es
exactamente lo que no quieres que se lance porque una frase sonó
parecida; para programas está `app.open`, que va contra un índice de lo
que hay instalado y no contra lo que haya suelto en una carpeta.
"""

from __future__ import annotations

import logging
import os
import time
import unicodedata
from pathlib import Path

from .registry import Risk, Tool, ToolResult

log = logging.getLogger("nova.tools.buscar")

# Dónde se mira. En este orden: lo más probable primero, porque la
# búsqueda para en cuanto tiene suficientes resultados.
def _carpetas() -> list[Path]:
    casa = Path.home()
    candidatas = [
        casa / "Desktop", casa / "Escritorio",
        casa / "Downloads", casa / "Descargas",
        casa / "Documents", casa / "Documentos",
        casa / "Pictures", casa / "Imágenes",
    ]
    return [c for c in candidatas if c.is_dir()]


# Hasta dónde se baja. Con 3 se cubre "Documentos/proyectos/web/logo.png"
# sin recorrer un node_modules entero por accidente.
PROFUNDIDAD = 3

# Con cuántos aciertos ya no hace falta seguir mirando. Leídos en alto,
# más de seis nombres de archivo no los retiene nadie.
MAX_RESULTADOS = 6

# Carpetas que sólo hacen perder tiempo: son enormes y nunca guardas ahí
# lo que buscas por voz.
_SALTAR = frozenset({
    "node_modules", ".git", ".venv", "venv", "__pycache__", "AppData",
    "$RECYCLE.BIN", "System Volume Information", ".cache", "dist", "build",
})

# Lo que NO se abre nunca, por mucho que aparezca en la búsqueda.
_NO_SE_ABREN = frozenset({
    ".exe", ".msi", ".bat", ".cmd", ".ps1", ".vbs", ".scr", ".com",
    ".reg", ".jar", ".lnk",
})


def _sin_tildes(texto: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", (texto or "").lower())
        if unicodedata.category(c) != "Mn"
    )


def _palabras(consulta: str) -> list[str]:
    """Todas tienen que aparecer. "factura luz" no puede traer cualquier factura."""
    return [p for p in _sin_tildes(consulta).replace(".", " ").split() if len(p) > 1]


def _recorrer(raiz: Path, palabras: list[str], tope: int) -> list[Path]:
    encontrados: list[Path] = []
    base = len(raiz.parts)
    for actual, subcarpetas, archivos in os.walk(raiz):
        aqui = Path(actual)
        if len(aqui.parts) - base >= PROFUNDIDAD:
            subcarpetas.clear()          # no bajar más
        else:
            # Modificar la lista EN SITIO es lo que hace que os.walk no
            # entre; reasignarla no serviría de nada.
            subcarpetas[:] = [d for d in subcarpetas if d not in _SALTAR and not d.startswith(".")]
        for nombre in archivos:
            limpio = _sin_tildes(nombre)
            if all(p in limpio for p in palabras):
                encontrados.append(aqui / nombre)
                if len(encontrados) >= tope:
                    return encontrados
    return encontrados


def buscar(query: str) -> ToolResult:
    palabras = _palabras(query)
    if not palabras:
        return ToolResult(ok=False, message="¿Qué archivo busco?")

    t0 = time.perf_counter()
    encontrados: list[Path] = []
    for carpeta in _carpetas():
        encontrados.extend(_recorrer(carpeta, palabras, MAX_RESULTADOS - len(encontrados)))
        if len(encontrados) >= MAX_RESULTADOS:
            break
    tardo = time.perf_counter() - t0
    log.info("busqué %r en %.2f s: %d resultados", query, tardo, len(encontrados))

    if not encontrados:
        return ToolResult(ok=False, message=f"No encontré ningún archivo de «{query}».")

    # El más reciente primero: si hay tres facturas, casi siempre quieres
    # la última que tocaste.
    encontrados.sort(key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)
    nombres = [p.name for p in encontrados]
    if len(nombres) == 1:
        mensaje = f"Encontré {nombres[0]}."
    else:
        mensaje = f"Encontré {len(nombres)}: " + ", ".join(nombres) + "."
    return ToolResult(ok=True, message=mensaje, data={"rutas": [str(p) for p in encontrados]})


def abrir(query: str) -> ToolResult:
    palabras = _palabras(query)
    if not palabras:
        return ToolResult(ok=False, message="¿Qué archivo abro?")

    encontrados: list[Path] = []
    for carpeta in _carpetas():
        encontrados.extend(_recorrer(carpeta, palabras, MAX_RESULTADOS - len(encontrados)))
        if len(encontrados) >= MAX_RESULTADOS:
            break
    if not encontrados:
        return ToolResult(ok=False, message=f"No encontré ningún archivo de «{query}».")

    encontrados.sort(key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)
    elegido = encontrados[0]

    if elegido.suffix.lower() in _NO_SE_ABREN:
        return ToolResult(
            ok=False,
            message=f"{elegido.name} es un programa y no lo abro así. "
                    "Si quieres lanzar una aplicación, dímelo por su nombre.",
        )
    try:
        os.startfile(str(elegido))  # noqa: S606
    except OSError as exc:
        return ToolResult(ok=False, message=f"No pude abrir {elegido.name}: {exc}")

    extra = f" (había {len(encontrados)}, abro el más reciente)" if len(encontrados) > 1 else ""
    return ToolResult(ok=True, message=f"Abriendo {elegido.name}{extra}.")


def register(reg) -> None:  # noqa: ANN001
    reg.register(Tool(
        name="archivo.buscar",
        description="Busca un archivo suyo en Escritorio, Descargas, Documentos e Imágenes",
        handler=buscar,
        schema={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
        risk=Risk.SAFE,
    ))
    reg.register(Tool(
        name="archivo.abrir",
        description="Abre un archivo suyo por su nombre. Para programas usa app.open",
        handler=abrir,
        schema={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
        risk=Risk.MEDIUM,
    ))
