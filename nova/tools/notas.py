"""Dictado: "nova, apunta esto" y queda escrito.

Diferente de `memory.py` a propósito.  La memoria son hechos sobre ti
que NOVA mete en su prompt para conocerte; una nota es texto tuyo que
quieres guardado tal cual y poder releer.  Meter la lista de la compra
en la memoria ensuciaría el prompt de cada turno para siempre.

Y diferente de `file.create`: aquí no hay que inventarse un nombre de
archivo ni acordarse de cuál era.  Todo va a un cuaderno por día.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from ..config import CONFIG
from .registry import Risk, Tool, ToolResult

log = logging.getLogger("nova.tools.notas")


def _raiz() -> Path:
    """Dónde viven los cuadernos.

    Indirección a propósito: CONFIG es inmutable (que es lo correcto), y
    esta función es el punto por el que los tests apuntan a una carpeta
    temporal sin tocar el workspace real del usuario. Mismo truco que en
    memory.py.
    """
    return CONFIG.workspace / "notas"


def _cuaderno(dia: datetime | None = None) -> Path:
    """Un archivo por día. Buscar "lo que apunté el martes" es abrir uno."""
    fecha = (dia or datetime.now()).strftime("%Y-%m-%d")
    return _raiz() / f"{fecha}.txt"


def apuntar(text: str) -> ToolResult:
    texto = (text or "").strip()
    if not texto:
        return ToolResult(ok=False, message="¿Qué quieres que apunte?")

    destino = _cuaderno()
    ahora = datetime.now()
    try:
        destino.parent.mkdir(parents=True, exist_ok=True)
        # Append y no write: dictar la segunda nota del día no puede
        # borrar la primera.
        with destino.open("a", encoding="utf-8") as f:
            f.write(f"[{ahora:%H:%M}] {texto}\n")
    except OSError as exc:
        log.warning("no pude escribir la nota: %s", exc)
        return ToolResult(ok=False, message=f"No pude guardar la nota: {exc}")

    return ToolResult(
        ok=True,
        message=f"Apuntado a las {ahora:%H:%M}.",
        data={"archivo": str(destino)},
    )


def leer_notas(dia: str = "") -> ToolResult:
    """Lo apuntado hoy, o el día que se pida en formato AAAA-MM-DD."""
    if dia.strip():
        try:
            cuando = datetime.strptime(dia.strip(), "%Y-%m-%d")
        except ValueError:
            return ToolResult(ok=False, message="La fecha tiene que ser tipo 2026-08-27.")
    else:
        cuando = datetime.now()

    destino = _cuaderno(cuando)
    if not destino.exists():
        cuando_dicho = "hoy" if not dia.strip() else f"el {cuando:%d/%m}"
        return ToolResult(ok=True, message=f"No apuntaste nada {cuando_dicho}.")

    try:
        lineas = [ln.strip() for ln in destino.read_text(encoding="utf-8").splitlines() if ln.strip()]
    except OSError as exc:
        return ToolResult(ok=False, message=f"No pude leer las notas: {exc}")

    if not lineas:
        return ToolResult(ok=True, message="Ese cuaderno está vacío.")
    return ToolResult(
        ok=True,
        message="Apuntaste: " + " ".join(lineas),
        data={"cuantas": len(lineas)},
    )


def register(reg) -> None:  # noqa: ANN001
    reg.register(Tool(
        name="nota.apuntar",
        description=(
            "Guarda literalmente lo que dicta: «apunta esto», «toma nota». "
            "Para datos sobre él usa memory.remember"
        ),
        handler=apuntar,
        schema={
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
        risk=Risk.SAFE,
    ))
    reg.register(Tool(
        name="nota.leer",
        description="Lee lo que apuntó hoy, o el día que diga (2026-08-27)",
        handler=leer_notas,
        schema={"type": "object", "properties": {"dia": {"type": "string"}}},
        risk=Risk.SAFE,
    ))
