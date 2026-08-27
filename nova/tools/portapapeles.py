"""El portapapeles: copias algo y le preguntas por ello.

Es lo más cómodo que puede hacer un asistente que vive en el escritorio:
copias un correo largo, un error de compilación o un párrafo en inglés,
y dices "nova, resúmeme esto".  Sin subir nada a ningún sitio.

Se lee con `win32clipboard`, que ya estaba instalado.  La alternativa
obvia era `Get-Clipboard` de PowerShell, pero arrancar PowerShell cuesta
entre 300 y 700 ms y esto está en el camino crítico de una respuesta
hablada — justo lo que se pasó el proyecto entero quitando.
"""

from __future__ import annotations

import logging

from .registry import Risk, Tool, ToolResult

log = logging.getLogger("nova.tools.portapapeles")

# Cuánto texto se le pasa al modelo. 6000 caracteres son ~1500 tokens de
# prefill, que en local ya se notan; y para resumir o traducir, con eso
# hay de sobra. Cortar por el final avisa de que se ha cortado en vez de
# dejar que el modelo resuma media cosa creyendo que era entera.
MAX_CARACTERES = 6000


def _leer_crudo() -> str | None:
    """El texto del portapapeles, o None si no hay texto.

    None y "" no son lo mismo: "no has copiado nada" y "has copiado una
    imagen" son quejas distintas para el usuario.
    """
    try:
        import win32clipboard
    except ImportError:
        return None

    try:
        win32clipboard.OpenClipboard()
    except Exception:  # noqa: BLE001
        # Otro programa lo tiene abierto un instante. Pasa, y no es un
        # error del que haya que informar al usuario con un susto.
        log.debug("el portapapeles estaba ocupado", exc_info=True)
        return None
    try:
        if not win32clipboard.IsClipboardFormatAvailable(win32clipboard.CF_UNICODETEXT):
            return None
        return win32clipboard.GetClipboardData(win32clipboard.CF_UNICODETEXT)
    except Exception:  # noqa: BLE001
        log.debug("no pude leer el portapapeles", exc_info=True)
        return None
    finally:
        try:
            win32clipboard.CloseClipboard()
        except Exception:  # noqa: BLE001
            pass


def leer() -> ToolResult:
    texto = _leer_crudo()
    if texto is None:
        return ToolResult(
            ok=False,
            message="No tienes texto copiado. Si es una imagen, no la puedo leer.",
        )
    texto = texto.strip()
    if not texto:
        return ToolResult(ok=False, message="Lo que tienes copiado está vacío.")

    entero = len(texto)
    if entero > MAX_CARACTERES:
        texto = texto[:MAX_CARACTERES]
        aviso = f" (son {entero} caracteres; te leo los primeros {MAX_CARACTERES})"
    else:
        aviso = ""
    return ToolResult(
        ok=True,
        message=f"Esto es lo que tienes copiado{aviso}:\n\n{texto}",
        data={"caracteres": entero},
    )


def register(reg) -> None:  # noqa: ANN001
    reg.register(Tool(
        name="portapapeles.leer",
        description=(
            "Lee lo que el usuario tiene COPIADO. Úsala si dice «esto» o "
            "«lo que he copiado» sin decirte el texto"
        ),
        handler=leer,
        risk=Risk.SAFE,
    ))
