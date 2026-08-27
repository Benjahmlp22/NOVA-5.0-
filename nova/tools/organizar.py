"""Ordenar Descargas y Escritorio por tipo de archivo.

Esto es lo más peligroso que hace NOVA, así que conviene decir primero
lo que **nunca** hace:

* **No borra nada.** Sólo mueve. Ni un `unlink` en todo el módulo.
* **No se sale de la carpeta.** Lo de Descargas va a subcarpetas *de
  Descargas*. Nada cruza a otra carpeta, ni a otro disco.
* **No toca lo que ya estaba ordenado.** Sólo los archivos sueltos del
  primer nivel. Si algo está dentro de una subcarpeta, lo pusiste tú.
* **No pisa nada.** Ante un nombre repetido, añade un número.
* **Se puede deshacer.** Cada tanda queda apuntada y `deshacer()` la
  revierte. Mover 627 archivos sin vuelta atrás no es una función, es
  una amenaza.

Y pide permiso siempre (`Risk.DANGEROUS`), después de haber dicho
exactamente cuántos archivos y a dónde.
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from datetime import datetime
from pathlib import Path

from ..config import CONFIG
from .registry import Risk, Tool, ToolResult

log = logging.getLogger("nova.tools.organizar")

# Las carpetas que se pueden ordenar, por el nombre que usa la gente.
# Nada de rutas libres: "ordena C:\Windows" no es una orden que deba
# existir.
_CARPETAS = {
    "descargas": ("Downloads", "Descargas"),
    "escritorio": ("Desktop", "Escritorio"),
}

# A qué subcarpeta va cada extensión. El orden importa poco; lo que
# importa es que sean categorías que una persona reconozca al abrir la
# carpeta, no las que sean cómodas de programar.
_CATEGORIAS: dict[str, tuple[str, ...]] = {
    "Imágenes": (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg",
                 ".avif", ".heic", ".ico", ".tiff"),
    "Vídeos": (".mp4", ".mkv", ".avi", ".mov", ".webm", ".flv", ".m4v"),
    "Música": (".mp3", ".wav", ".flac", ".m4a", ".ogg", ".aac", ".opus"),
    "Documentos": (".pdf", ".docx", ".doc", ".txt", ".odt", ".rtf", ".epub",
                   ".xlsx", ".xls", ".pptx", ".ppt", ".csv", ".md"),
    "Comprimidos": (".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz"),
    "Programas": (".exe", ".msi", ".appx", ".msix"),
    "Modelos 3D": (".3mf", ".stl", ".obj", ".gcode", ".fbx", ".blend"),
    "Código": (".py", ".js", ".ts", ".json", ".html", ".css", ".jar",
               ".java", ".c", ".cpp", ".cs", ".lua", ".sh", ".ps1"),
}

# Lo que no se mueve NUNCA, aunque encaje en una categoría.
#
# .lnk y .url son accesos directos: moverlos del escritorio es
# exactamente lo que nadie quiere. desktop.ini y compañía los usa
# Windows para pintar la propia carpeta y moverlos la rompe.
_INTOCABLES = frozenset({".lnk", ".url"})
_NOMBRES_INTOCABLES = frozenset({"desktop.ini", "thumbs.db", ".ds_store"})


def _carpeta(nombre: str) -> Path | None:
    clave = (nombre or "").strip().lower()
    for alias, posibles in _CARPETAS.items():
        if clave and (clave in alias or alias in clave):
            for p in posibles:
                ruta = Path.home() / p
                if ruta.is_dir():
                    return ruta
    return None


def _categoria_de(archivo: Path) -> str | None:
    if archivo.name.lower() in _NOMBRES_INTOCABLES:
        return None
    sufijo = archivo.suffix.lower()
    if not sufijo or sufijo in _INTOCABLES:
        return None
    # Los ocultos y los de sistema los puso alguien que sabía lo que
    # hacía, y no era el usuario hablando por el micrófono.
    if archivo.name.startswith("."):
        return None
    for categoria, extensiones in _CATEGORIAS.items():
        if sufijo in extensiones:
            return categoria
    # Sin categoría se QUEDA donde está. Una carpeta "Otros" es un cajón
    # de sastre: mueve el problema, no lo resuelve.
    return None


def _sueltos(raiz: Path) -> list[Path]:
    """Sólo el primer nivel. Lo de dentro de una subcarpeta ya lo ordenaste tú."""
    try:
        return [f for f in raiz.iterdir() if f.is_file()]
    except OSError:
        return []


def _plan(raiz: Path) -> list[tuple[Path, Path]]:
    movimientos = []
    for archivo in _sueltos(raiz):
        categoria = _categoria_de(archivo)
        if categoria:
            movimientos.append((archivo, raiz / categoria / archivo.name))
    return movimientos


def _libre(destino: Path) -> Path:
    """Un nombre que no exista. Nunca se pisa un archivo del usuario."""
    if not destino.exists():
        return destino
    for n in range(2, 1000):
        alternativa = destino.with_name(f"{destino.stem} ({n}){destino.suffix}")
        if not alternativa.exists():
            return alternativa
    return destino.with_name(f"{destino.stem} ({datetime.now():%H%M%S}){destino.suffix}")


def _plural(n: int, singular: str, plural: str) -> str:
    return f"{n} {singular if n == 1 else plural}"


def _resumen(movimientos: list[tuple[Path, Path]]) -> str:
    cuenta = Counter(destino.parent.name for _, destino in movimientos)
    partes = [f"{n} a {carpeta}" for carpeta, n in cuenta.most_common()]
    return ", ".join(partes)


# ── Diario, para poder deshacer ──────────────────────────────────────

def _diario() -> Path:
    return CONFIG.data_dir / "organizado.json"


def _apuntar(movimientos: list[tuple[Path, Path]]) -> None:
    try:
        _diario().parent.mkdir(parents=True, exist_ok=True)
        _diario().write_text(
            json.dumps(
                {
                    "cuando": datetime.now().isoformat(timespec="seconds"),
                    "movimientos": [[str(o), str(d)] for o, d in movimientos],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    except OSError as exc:
        log.warning("no pude apuntar lo movido: %s", exc)


def _ultima_tanda() -> list[tuple[Path, Path]]:
    try:
        datos = json.loads(_diario().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [(Path(o), Path(d)) for o, d in datos.get("movimientos", [])]


# ── Herramientas ─────────────────────────────────────────────────────

def revisar(carpeta: str = "descargas") -> ToolResult:
    """Qué haría, sin tocar nada. Es SAFE: no pide permiso para mirar."""
    raiz = _carpeta(carpeta)
    if raiz is None:
        return ToolResult(ok=False, message="Sólo puedo ordenar Descargas o el Escritorio.")

    movimientos = _plan(raiz)
    sueltos = len(_sueltos(raiz))
    if not movimientos:
        return ToolResult(ok=True, message=f"{raiz.name} ya está ordenada.")
    quedan = sueltos - len(movimientos)
    cola = f" Otros {quedan} se quedan donde están." if quedan else ""
    # "Todavía no he movido nada" delante y en primera persona, porque
    # el modelo leía "puedo mover 611 archivos" y respondía "he movido
    # los archivos de Descargas según su tipo". Decía que había hecho
    # algo que no había hecho.
    return ToolResult(
        ok=True,
        message=f"Todavía no he movido nada. Si quieres, en {raiz.name} puedo ordenar "
                f"{_plural(len(movimientos), 'archivo', 'archivos')}: "
                f"{_resumen(movimientos)}.{cola} Dime si lo hago.",
        data={"cuantos": len(movimientos)},
    )


def organizar(carpeta: str = "descargas") -> ToolResult:
    raiz = _carpeta(carpeta)
    if raiz is None:
        return ToolResult(ok=False, message="Sólo puedo ordenar Descargas o el Escritorio.")

    movimientos = _plan(raiz)
    if not movimientos:
        return ToolResult(ok=True, message=f"{raiz.name} ya está ordenada.")

    hechos: list[tuple[Path, Path]] = []
    fallos = 0
    for origen, destino in movimientos:
        try:
            destino.parent.mkdir(parents=True, exist_ok=True)
            final = _libre(destino)
            origen.rename(final)
            hechos.append((origen, final))
        except OSError as exc:
            # Un archivo abierto en otro programa no se puede mover. No
            # es motivo para abortar los otros 600.
            log.info("no pude mover %s: %s", origen.name, exc)
            fallos += 1

    _apuntar(hechos)
    aviso = f" {fallos} no pude moverlos, estarán abiertos." if fallos else ""
    return ToolResult(
        ok=True,
        message=f"Ordenada {raiz.name}: {_resumen(hechos)}.{aviso} "
                "Si no te gusta, dime que lo deshaga.",
        data={"movidos": len(hechos), "fallos": fallos},
    )


def deshacer() -> ToolResult:
    """Devuelve la última tanda a donde estaba."""
    movimientos = _ultima_tanda()
    if not movimientos:
        return ToolResult(ok=False, message="No tengo nada que deshacer.")

    vueltos = 0
    fallos = 0
    for origen, destino in movimientos:
        try:
            if not destino.exists():
                # Lo has movido o borrado tú después. Es tuyo: no se
                # toca ni se inventa nada.
                fallos += 1
                continue
            destino.rename(_libre(origen))
            vueltos += 1
        except OSError as exc:
            log.info("no pude devolver %s: %s", destino.name, exc)
            fallos += 1

    _apuntar([])
    aviso = f" {fallos} ya no estaban donde los dejé." if fallos else ""
    return ToolResult(ok=True, message=f"Devueltos {vueltos} archivos.{aviso}")


def _explicar(args: dict) -> str:
    """Lo que se le dice al usuario ANTES de mover nada.

    Contar los archivos cuesta un `iterdir` y evita que la pregunta sea
    "¿confirmas que quiero ordenar descargas?", que no avisa de nada.
    """
    raiz = _carpeta(args.get("carpeta", "descargas"))
    if raiz is None:
        return ""
    movimientos = _plan(raiz)
    if not movimientos:
        return f"no tocar nada en {raiz.name}, que ya está ordenada"
    return (f"mover {_plural(len(movimientos), 'archivo', 'archivos')} "
            f"en {raiz.name} a {_plural(len({d.parent for _, d in movimientos}), 'carpeta', 'carpetas')}")


def register(reg) -> None:  # noqa: ANN001
    reg.register(Tool(
        name="organizar.revisar",
        description="Qué archivos ordenaría en Descargas o el Escritorio, sin tocar nada",
        handler=revisar,
        schema={"type": "object", "properties": {"carpeta": {"type": "string"}}},
        risk=Risk.SAFE,
        responde_sola=True,
    ))
    reg.register(Tool(
        name="organizar.hacerlo",
        description=(
            "Ordena por tipo los archivos sueltos de Descargas o del "
            "Escritorio. Mueve, no borra. Antes usa organizar.revisar"
        ),
        handler=organizar,
        schema={"type": "object", "properties": {"carpeta": {"type": "string"}}},
        risk=Risk.DANGEROUS,
        resumir=_explicar,
    ))
    reg.register(Tool(
        name="organizar.deshacer",
        description="Deshace la última ordenación y devuelve todo a su sitio",
        handler=deshacer,
        risk=Risk.SAFE,
    ))
