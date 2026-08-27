"""Buscar una imagen por lo que se ve en ella, no por cómo se llame.

"Búscame aquella imagen de League of Legends que descargué" funciona
aunque el archivo se llame `descarga (7).png`, que es como se llaman de
verdad las imágenes que uno descarga.

Detrás está CLIP (`nova/vista/album.py`).  Lo caro es mirar todas las
imágenes una primera vez —13 minutos en este PC— y por eso se hace en
segundo plano y NOVA sigue atendiéndote mientras tanto.  Buscar después
es instantáneo.

La descripción se le pasa **en inglés**: CLIP se entrenó con textos en
inglés y en español acierta bastante menos.  No hace falta traducir
nada a mano — el modelo que decide llamar a esta herramienta ya lo
traduce al rellenar el argumento, que para eso está.
"""

from __future__ import annotations

import logging
import os
import threading

from ..vista import clip
from ..vista.album import Album
from .registry import Risk, Tool, ToolResult

log = logging.getLogger("nova.tools.imagenes")

_album: Album | None = None
_hilo: threading.Thread | None = None
_parar = threading.Event()

# Cuántas se le enseñan. Más de tres nombres de archivo leídos en alto no
# los retiene nadie, y la primera es la buena casi siempre.
CUANTAS = 3


def _obtener() -> Album:
    global _album  # noqa: PLW0603
    if _album is None:
        _album = Album()
    return _album


def cerrar() -> None:
    """La app al salir: que un indexado a medias no impida cerrar."""
    _parar.set()


def _indexando() -> bool:
    return _hilo is not None and _hilo.is_alive()


# ── Herramientas ─────────────────────────────────────────────────────

def revisar() -> ToolResult:
    """Cómo está el álbum: cuántas hay miradas y cuántas faltan."""
    if not clip.hay_modelos():
        return ToolResult(
            ok=False,
            message="Todavía no puedo buscar por lo que se ve: me faltan los "
                    "modelos de visión. Hay que descargarlos una vez.",
        )
    album = _obtener()
    if _indexando():
        p = album.progreso
        return ToolResult(ok=True, message=f"Voy por el {p.porcentaje} por ciento, "
                                           f"{p.hechas} de {p.total}.")
    faltan = len(album.pendientes())
    if not album.listo:
        return ToolResult(ok=True, message=f"Aún no he mirado ninguna. Tienes {faltan} "
                                           "imágenes; dime que las mire y lo hago.")
    if faltan:
        return ToolResult(ok=True, message=f"Tengo {album.cuantas} imágenes miradas y "
                                           f"{faltan} nuevas por mirar.")
    return ToolResult(ok=True, message=f"Tengo tus {album.cuantas} imágenes al día.")


def indexar() -> ToolResult:
    """Arranca el repaso en segundo plano y contesta ya."""
    global _hilo  # noqa: PLW0603
    if not clip.hay_modelos():
        return ToolResult(ok=False, message="Me faltan los modelos de visión.")
    if _indexando():
        return ToolResult(ok=True, message="Ya estoy en ello.")

    album = _obtener()
    faltan = len(album.pendientes())
    if not faltan:
        return ToolResult(ok=True, message="Ya las tengo todas miradas.")

    _parar.clear()
    _hilo = threading.Thread(
        target=lambda: album.indexar(parar=_parar.is_set),
        daemon=True, name="album",
    )
    _hilo.start()

    # 47 ms por imagen, medido. Se dice en minutos porque un número de
    # segundos de tres cifras no lo procesa nadie escuchándolo.
    minutos = max(1, round(faltan * 0.047 / 60))
    return ToolResult(
        ok=True,
        message=f"Voy a mirar {faltan} imágenes, tardo unos {minutos} minutos. "
                "Sigue a lo tuyo, que te atiendo igual.",
        data={"cuantas": faltan},
    )


def buscar(descripcion: str) -> ToolResult:
    if not clip.hay_modelos():
        return ToolResult(ok=False, message="Me faltan los modelos para eso.")
    if not (descripcion or "").strip():
        return ToolResult(ok=False, message="¿Qué se ve en la imagen que buscas?")

    album = _obtener()
    if not album.listo:
        if _indexando():
            return ToolResult(ok=True, message=f"Todavía las estoy mirando, voy por el "
                                               f"{album.progreso.porcentaje} por ciento.")
        return ToolResult(
            ok=False,
            message="Aún no he mirado tus imágenes. Dime que las repase y luego te "
                    "la busco.",
        )

    encontradas = album.buscar(descripcion, cuantas=CUANTAS)
    if not encontradas:
        cola = " Y todavía me faltan por mirar algunas." if _indexando() else ""
        return ToolResult(ok=False, message=f"No encuentro ninguna imagen así.{cola}")

    mejor = encontradas[0]
    otras = len(encontradas) - 1
    extra = f" Tengo {otras} más parecidas." if otras else ""
    # NOVA no afirma lo que no sabe. CLIP ordena bien pero no sabe decir
    # "no la tengo": sobre este PC, "an underwater photo of a coral reef"
    # sacaba mejor nota que "a screenshot of Minecraft", y sólo una de las
    # dos existe. Cuando la confianza es baja se dice, y se enseña igual:
    # buscar una foto es mirar candidatas.
    if mejor.segura:
        principio = f"Es {mejor.ruta.name}, en {mejor.ruta.parent.name}."
    else:
        principio = (f"No estoy segura, pero lo que más se parece es "
                     f"{mejor.ruta.name}, en {mejor.ruta.parent.name}.")
    return ToolResult(
        ok=True,
        message=f"{principio}{extra} Dime si la abro.",
        data={
            "rutas": [str(c.ruta) for c in encontradas],
            "confianza": round(mejor.confianza, 3),
            "parecido": round(mejor.parecido, 3),
        },
    )


def abrir(descripcion: str) -> ToolResult:
    """Busca y abre la que mejor encaje, sin dos pasos."""
    resultado = buscar(descripcion)
    if not resultado.ok or not resultado.data.get("rutas"):
        return resultado
    ruta = resultado.data["rutas"][0]
    try:
        os.startfile(ruta)  # noqa: S606
    except OSError as exc:
        return ToolResult(ok=False, message=f"La encontré pero no pude abrirla: {exc}")
    from pathlib import Path
    return ToolResult(ok=True, message=f"Ahí la tienes: {Path(ruta).name}.")


def register(reg) -> None:  # noqa: ANN001
    reg.register(Tool(
        name="imagen.buscar",
        description=(
            "Busca una imagen del usuario por LO QUE SE VE en ella, no por su "
            "nombre. Pasa la descripción EN INGLÉS: «a screenshot of League of "
            "Legends», «a photo of a dog on the beach»"
        ),
        handler=buscar,
        schema={
            "type": "object",
            "properties": {"descripcion": {"type": "string"}},
            "required": ["descripcion"],
        },
        risk=Risk.SAFE,
    ))
    reg.register(Tool(
        name="imagen.abrir",
        description="Busca una imagen por lo que se ve y la abre. Descripción EN INGLÉS",
        handler=abrir,
        schema={
            "type": "object",
            "properties": {"descripcion": {"type": "string"}},
            "required": ["descripcion"],
        },
        risk=Risk.MEDIUM,
    ))
    reg.register(Tool(
        name="imagen.repasar",
        description="Mira las imágenes nuevas del usuario para poder buscarlas después",
        handler=indexar,
        risk=Risk.MEDIUM,
    ))
    reg.register(Tool(
        name="imagen.estado",
        description="Cuántas imágenes tiene ya miradas y cuántas le faltan",
        handler=revisar,
        risk=Risk.SAFE,
    ))
