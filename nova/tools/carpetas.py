"""Mirar y abrir tus carpetas: Descargas, Escritorio, Documentos, Imágenes.

`files.py` sólo ve `workspace/`, que es donde NOVA escribe.  Aquí sólo se
LEE y se abre el explorador, y por eso puede mirar tus carpetas de
verdad.

Sale de probar qué le pedía un usuario a NOVA y no sabía hacer:

    «abre la carpeta de descargas»   -> llamaba a folder.create y CREABA una
    «cuántas descargas tengo»        -> miraba workspace/, la carpeta equivocada
    «cuál es el archivo más grande»  -> no tenía forma de saberlo
    «qué he bajado hoy»              -> tampoco

Nada de rutas libres: las mismas cuatro carpetas de siempre.  "Abre
C:\\Windows\\System32" no es una orden que deba existir.
"""

from __future__ import annotations

import logging
import os
import time
from collections import Counter
from pathlib import Path

from .registry import Risk, Tool, ToolResult

log = logging.getLogger("nova.tools.carpetas")

_CARPETAS: dict[str, tuple[str, ...]] = {
    "descargas": ("Downloads", "Descargas"),
    "escritorio": ("Desktop", "Escritorio"),
    "documentos": ("Documents", "Documentos"),
    "imagenes": ("Pictures", "Imágenes", "Imagenes"),
    "musica": ("Music", "Música", "Musica"),
    "videos": ("Videos", "Vídeos"),
}

# Cuántos nombres se dicen. Leídos en alto, más de cinco no los retiene
# nadie.
CUANTOS = 5


def _resolver(nombre: str) -> Path | None:
    clave = (nombre or "").strip().lower()
    clave = clave.replace("á", "a").replace("é", "e").replace("í", "i")
    clave = clave.replace("ó", "o").replace("ú", "u")
    for alias, posibles in _CARPETAS.items():
        if clave and (clave in alias or alias in clave):
            for p in posibles:
                if (ruta := Path.home() / p).is_dir():
                    return ruta
    return None


def _no_la_conozco(nombre: str) -> ToolResult:
    return ToolResult(
        ok=False,
        message=f"No sé cuál es «{nombre}». Puedo mirar Descargas, el Escritorio, "
                "Documentos, Imágenes, Música y Vídeos.",
    )


def _archivos(raiz: Path) -> list[Path]:
    """Sólo el primer nivel: lo de dentro de una subcarpeta ya lo ordenaste tú."""
    try:
        return [f for f in raiz.iterdir() if f.is_file()]
    except OSError:
        return []


def _tamano(n: int) -> str:
    """Bytes en algo que se pueda decir en alto."""
    if n >= 1_000_000_000:
        return f"{n / 1e9:.1f} gigas"
    if n >= 1_000_000:
        return f"{round(n / 1e6)} megas"
    return f"{max(1, round(n / 1e3))} kilobytes"


# ── Herramientas ─────────────────────────────────────────────────────

def abrir(nombre: str = "descargas") -> ToolResult:
    raiz = _resolver(nombre)
    if raiz is None:
        return _no_la_conozco(nombre)
    try:
        os.startfile(str(raiz))  # noqa: S606
    except OSError as exc:
        return ToolResult(ok=False, message=f"No pude abrirla: {exc}")
    return ToolResult(ok=True, message=f"Ahí tienes {raiz.name}.")


def resumen(nombre: str = "descargas") -> ToolResult:
    """Cuántos archivos hay, cuánto ocupan y de qué tipo."""
    raiz = _resolver(nombre)
    if raiz is None:
        return _no_la_conozco(nombre)

    archivos = _archivos(raiz)
    if not archivos:
        return ToolResult(ok=True, message=f"{raiz.name} está vacía.")

    total = 0
    tipos: Counter[str] = Counter()
    for f in archivos:
        try:
            total += f.stat().st_size
        except OSError:
            continue
        tipos[f.suffix.lower().lstrip(".") or "sin extensión"] += 1

    frecuentes = ", ".join(f"{n} {ext}" for ext, n in tipos.most_common(3))
    try:
        carpetas = sum(1 for x in raiz.iterdir() if x.is_dir())
    except OSError:
        carpetas = 0
    cola = f" y {carpetas} carpetas" if carpetas else ""
    return ToolResult(
        ok=True,
        message=f"En {raiz.name} tienes {len(archivos)} archivos{cola}, "
                f"{_tamano(total)} en total. Sobre todo {frecuentes}.",
        data={"archivos": len(archivos), "bytes": total},
    )


def recientes(nombre: str = "descargas", dias: int = 1) -> ToolResult:
    """Lo último que ha llegado. "Qué he bajado hoy" es esto."""
    raiz = _resolver(nombre)
    if raiz is None:
        return _no_la_conozco(nombre)

    try:
        limite = time.time() - max(1, int(dias)) * 86400
    except (TypeError, ValueError):
        limite = time.time() - 86400

    nuevos = []
    for f in _archivos(raiz):
        try:
            if f.stat().st_mtime >= limite:
                nuevos.append((f.stat().st_mtime, f))
        except OSError:
            continue
    if not nuevos:
        cuando = "hoy" if dias <= 1 else f"en los últimos {dias} días"
        return ToolResult(ok=True, message=f"No ha llegado nada a {raiz.name} {cuando}.")

    nuevos.sort(reverse=True)
    nombres = ", ".join(f.name for _, f in nuevos[:CUANTOS])
    resto = len(nuevos) - CUANTOS
    cola = f", y {resto} más" if resto > 0 else ""
    return ToolResult(
        ok=True,
        message=f"Lo último en {raiz.name}: {nombres}{cola}.",
        data={"cuantos": len(nuevos)},
    )


def mas_grandes(nombre: str = "descargas") -> ToolResult:
    """Los que más ocupan. Para cuando el disco se llena y no sabes por qué."""
    raiz = _resolver(nombre)
    if raiz is None:
        return _no_la_conozco(nombre)

    pesos = []
    for f in _archivos(raiz):
        try:
            pesos.append((f.stat().st_size, f))
        except OSError:
            continue
    if not pesos:
        return ToolResult(ok=True, message=f"{raiz.name} está vacía.")

    pesos.sort(reverse=True)
    partes = [f"{f.name}, {_tamano(t)}" for t, f in pesos[:3]]
    return ToolResult(
        ok=True,
        message=f"Lo que más ocupa en {raiz.name}: " + "; ".join(partes) + ".",
        data={"rutas": [str(f) for _, f in pesos[:3]]},
    )


def register(reg) -> None:  # noqa: ANN001
    reg.register(Tool(
        name="carpeta.abrir",
        description=(
            "Abre una carpeta suya en el explorador: Descargas, Escritorio, "
            "Documentos, Imágenes. NO crea nada"
        ),
        handler=abrir,
        schema={"type": "object", "properties": {"nombre": {"type": "string"}}},
        risk=Risk.MEDIUM,
    ))
    reg.register(Tool(
        name="carpeta.resumen",
        description="Cuántos archivos hay en una carpeta suya, cuánto ocupan y de qué tipo",
        handler=resumen,
        schema={"type": "object", "properties": {"nombre": {"type": "string"}}},
        risk=Risk.SAFE,
        responde_sola=True,
    ))
    reg.register(Tool(
        name="carpeta.recientes",
        description="Lo último que ha llegado a una carpeta: «qué he bajado hoy»",
        handler=recientes,
        schema={
            "type": "object",
            "properties": {"nombre": {"type": "string"}, "dias": {"type": "integer"}},
        },
        risk=Risk.SAFE,
        responde_sola=True,
    ))
    reg.register(Tool(
        name="carpeta.mas_grandes",
        description="Qué archivos ocupan más en una carpeta suya",
        handler=mas_grandes,
        schema={"type": "object", "properties": {"nombre": {"type": "string"}}},
        risk=Risk.SAFE,
        responde_sola=True,
    ))
