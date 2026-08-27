"""Leer lo que pone en la pantalla.

Para lo que no se puede copiar: un error en un diálogo, el menú de un
juego, un PDF escaneado, un vídeo pausado.  Cuando el texto SÍ se puede
copiar, `portapapeles.leer` es mejor y más barato.

Se puede leer la pantalla entera o sólo la ventana de delante.  Lo
segundo es casi siempre lo que quieres: la pantalla entera trae también
la barra de tareas, el navegador de detrás y los nombres de tus
carpetas, y el modelo se pierde entre todo eso.
"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path

from ..vista import LectorDePantalla
from .registry import Risk, Tool, ToolResult

log = logging.getLogger("nova.tools.pantalla")

# Se crea al vuelo la primera vez que hace falta. Arrancarlo cuesta
# ~0.6 s y hay gente que no va a usar esto nunca: no se le cobra a todo
# el mundo en el arranque de NOVA.
_lector: LectorDePantalla | None = None

# Tope de lo que se le pasa al modelo. Una pantalla llena de código son
# miles de caracteres, y todo eso es prefill que se paga en latencia.
MAX_CARACTERES = 4000


def _obtener_lector() -> LectorDePantalla | None:
    global _lector  # noqa: PLW0603
    if _lector is None:
        lector = LectorDePantalla()
        if not lector.start():
            log.info("OCR no disponible: %s", lector.error)
            return None
        _lector = lector
    return _lector


def cerrar() -> None:
    """La llama la app al salir, para no dejar PowerShell suelto."""
    global _lector  # noqa: PLW0603
    if _lector is not None:
        _lector.stop()
        _lector = None
    # Con el proceso cerrado, Windows ya suelta las capturas.
    _barrer()


# Las capturas ya usadas, para borrarlas cuando se pueda.
_usadas: list[Path] = []


def _capturar(solo_ventana: bool) -> Path | None:
    """Deja un PNG temporal con lo que haya que leer.

    Un archivo NUEVO cada vez, y no siempre el mismo, porque WinRT deja
    la imagen bloqueada después de leerla: reescribir la misma ruta
    fallaba con "Invalid argument" en la segunda lectura, y NOVA decía
    que no podía capturar la pantalla sin más explicación.
    """
    import mss
    import mss.tools

    _barrer()
    destino = Path(tempfile.gettempdir()) / f"nova_ocr_{len(_usadas)}_{id(_usadas):x}.png"
    try:
        region = _region_de_la_ventana() if solo_ventana else None
        with mss.MSS() as sct:
            captura = sct.grab(region or sct.monitors[0])
            mss.tools.to_png(captura.rgb, captura.size, output=str(destino))
        _usadas.append(destino)
        return destino
    except Exception:  # noqa: BLE001
        log.warning("no pude capturar la pantalla", exc_info=True)
        return None


def _barrer() -> None:
    """Borra las capturas viejas que Windows ya haya soltado."""
    for viejo in list(_usadas):
        try:
            viejo.unlink(missing_ok=True)
            _usadas.remove(viejo)
        except OSError:
            # Sigue bloqueada. Se reintenta la próxima vez; son 500 KB
            # en la carpeta temporal, no un problema.
            pass


def _region_de_la_ventana() -> dict | None:
    """El rectángulo de la ventana de delante, o None si no se sabe."""
    try:
        import ctypes

        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return None

        class RECT(ctypes.Structure):
            _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                        ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

        r = RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(r))
        ancho, alto = r.right - r.left, r.bottom - r.top
        if ancho < 50 or alto < 50:
            return None
        return {"left": r.left, "top": r.top, "width": ancho, "height": alto}
    except Exception:  # noqa: BLE001
        return None


def leer(todo: bool = False) -> ToolResult:
    lector = _obtener_lector()
    if lector is None:
        return ToolResult(
            ok=False,
            message="No puedo leer la pantalla: a este Windows le falta el OCR en español.",
        )

    imagen = _capturar(solo_ventana=not todo)
    if imagen is None:
        return ToolResult(ok=False, message="No pude hacer la captura.")

    texto = lector.leer(imagen)
    if not texto:
        return ToolResult(ok=True, message="No veo texto en la pantalla.")

    entero = len(texto)
    if entero > MAX_CARACTERES:
        texto = texto[:MAX_CARACTERES]
        aviso = f" (hay {entero} caracteres; te leo los primeros {MAX_CARACTERES})"
    else:
        aviso = ""
    donde = "la pantalla" if todo else "la ventana que tienes delante"
    return ToolResult(
        ok=True,
        message=f"Esto es lo que pone en {donde}{aviso}:\n\n{texto}",
        data={"caracteres": entero},
    )


def register(reg) -> None:  # noqa: ANN001
    reg.register(Tool(
        name="pantalla.leer",
        description=(
            "Lee el texto que hay en pantalla. Para lo que el usuario NO "
            "puede copiar: un error, un menú, un juego. Con todo=true lee "
            "la pantalla entera en vez de sólo la ventana de delante"
        ),
        handler=leer,
        schema={"type": "object", "properties": {"todo": {"type": "boolean"}}},
        risk=Risk.SAFE,
    ))
