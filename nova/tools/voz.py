"""Cambiar la voz de NOVA hablándole.

"Ponte voz de hombre", "habla más despacio", "quiero acento mexicano".
No hay que saberse los nombres: se describe cómo la quieres y aquí se
traduce a una de las que hay instaladas.

La elección se guarda, porque tener que repetirla en cada arranque sería
justo lo contrario de lo que se pide al pedirla.
"""

from __future__ import annotations

import json
import logging
import unicodedata
from pathlib import Path

from ..config import CONFIG
from .registry import Risk, Tool, ToolResult

log = logging.getLogger("nova.tools.voz")

# Lo pone la app al arrancar. Sin él, estas herramientas no pueden hacer
# nada y lo dicen en vez de fingir que sí.
_speaker = None


def conectar(speaker) -> None:  # noqa: ANN001
    global _speaker  # noqa: PLW0603
    _speaker = speaker


def _archivo() -> Path:
    return CONFIG.data_dir / "voz.json"


def _sin_tildes(texto: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", (texto or "").lower())
        if unicodedata.category(c) != "Mn"
    )


# ── Guardar la elección ──────────────────────────────────────────────

def guardado() -> dict:
    try:
        return json.loads(_archivo().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _guardar(nombre: str = "", velocidad: float | None = None) -> None:
    datos = guardado()
    if nombre:
        datos["voz"] = nombre
    if velocidad is not None:
        datos["velocidad"] = velocidad
    try:
        _archivo().parent.mkdir(parents=True, exist_ok=True)
        _archivo().write_text(json.dumps(datos, ensure_ascii=False), encoding="utf-8")
    except OSError as exc:
        log.warning("no pude guardar la voz: %s", exc)


def aplicar_guardado(speaker) -> None:  # noqa: ANN001
    """Deja a NOVA como la dejaste la última vez. La llama la app."""
    datos = guardado()
    if datos.get("voz"):
        speaker.usar_voz(datos["voz"])
    if datos.get("velocidad"):
        speaker.set_velocidad(float(datos["velocidad"]))


# ── Entender cómo la quiere ──────────────────────────────────────────

_HOMBRE = ("hombre", "masculina", "masculino", "chico", "tio", "varon", "grave")
_MUJER = ("mujer", "femenina", "femenino", "chica", "aguda")
_MEXICO = ("mexicana", "mexicano", "mexico", "latina", "latino", "latinoamericana")
_ESPANA = ("espanola", "espanol", "espana", "castellana", "de aqui")


def _puntuar(voz, deseo: str) -> int:  # noqa: ANN001
    """Cuánto se parece esta voz a lo que ha pedido. Nunca adivina."""
    puntos = 0
    # Por nombre propio gana siempre: si dice "Pablo", quiere Pablo.
    if _sin_tildes(voz.nombre.replace("Microsoft ", "")) in deseo:
        puntos += 10
    if any(p in deseo for p in _HOMBRE):
        puntos += 3 if voz.es_hombre else -3
    if any(p in deseo for p in _MUJER):
        puntos += 3 if not voz.es_hombre else -3
    if any(p in deseo for p in _MEXICO):
        puntos += 2 if voz.idioma.lower() == "es-mx" else -2
    if any(p in deseo for p in _ESPANA):
        puntos += 2 if voz.idioma.lower() == "es-es" else -2
    return puntos


def _describir(voz) -> str:  # noqa: ANN001
    corto = voz.nombre.replace("Microsoft ", "")
    sexo = "hombre" if voz.es_hombre else "mujer"
    sitio = "de México" if voz.idioma.lower() == "es-mx" else "de España"
    return f"{corto}, {sexo} {sitio}"


# ── Herramientas ─────────────────────────────────────────────────────

def listar() -> ToolResult:
    if _speaker is None:
        return ToolResult(ok=False, message="Ahora mismo no puedo cambiar de voz.")
    voces = _speaker.voces()
    if not voces:
        return ToolResult(ok=False, message="Sólo tengo la voz que estás oyendo.")
    return ToolResult(
        ok=True,
        message="Puedo hablar como " + "; ".join(_describir(v) for v in voces) + ".",
    )


def cambiar(descripcion: str = "") -> ToolResult:
    if _speaker is None:
        return ToolResult(ok=False, message="Ahora mismo no puedo cambiar de voz.")
    voces = _speaker.voces()
    if not voces:
        return ToolResult(ok=False, message="No tengo otras voces instaladas.")

    deseo = _sin_tildes(descripcion)
    if not deseo.strip():
        return ToolResult(ok=False, message="¿Cómo la quieres: de hombre, de mujer, mexicana?")

    mejor = max(voces, key=lambda v: _puntuar(v, deseo))
    puntos = _puntuar(mejor, deseo)

    # Si la que ya lleva puesta cumple lo que pide, no se cambia. Pedir
    # "voz de mujer" teniendo voz de mujer y que NOVA se cambie a OTRA
    # mujer es responder a algo que no se ha preguntado.
    actual = next((v for v in voces if v.nombre == _speaker.voz_actual), None)
    if actual is not None and _puntuar(actual, deseo) >= puntos > 0:
        return ToolResult(ok=True, message=f"Ya te hablo así: soy {_describir(actual)}.")

    if puntos <= 0:
        # Nada encajaba. Cambiar a una voz al azar sería peor que decirlo.
        return ToolResult(
            ok=False,
            message="No tengo ninguna así. Puedo " + "; ".join(_describir(v) for v in voces) + ".",
        )
    if mejor.nombre == _speaker.voz_actual:
        return ToolResult(ok=True, message="Ya te estoy hablando con esa voz.")
    if not _speaker.usar_voz(mejor.nombre):
        return ToolResult(ok=False, message="No pude cambiar de voz.")

    _guardar(nombre=mejor.nombre)
    return ToolResult(ok=True, message=f"Ya está, ahora soy {_describir(mejor)}.")


def velocidad(como: str = "") -> ToolResult:
    """Más rápido o más despacio. Por pasos, no por número: nadie dice
    "ponte a 1.15"."""
    if _speaker is None:
        return ToolResult(ok=False, message="Ahora mismo no puedo cambiar la velocidad.")
    deseo = _sin_tildes(como)
    actual = getattr(_speaker, "_velocidad", 1.0)

    if any(p in deseo for p in ("normal", "de siempre", "por defecto")):
        nueva = 1.0
    elif any(p in deseo for p in ("rapid", "deprisa", "corriendo", "acelera")):
        nueva = min(1.6, actual + 0.2)
    elif any(p in deseo for p in ("lent", "despacio", "pausad", "calma")):
        nueva = max(0.6, actual - 0.2)
    else:
        return ToolResult(ok=False, message="¿Más rápido o más despacio?")

    if abs(nueva - actual) < 0.01:
        tope = "todo lo rápido" if nueva > 1 else "todo lo despacio"
        return ToolResult(ok=True, message=f"Ya voy {tope} que puedo.")
    _speaker.set_velocidad(nueva)
    _guardar(velocidad=nueva)
    return ToolResult(ok=True, message="Hecho." if nueva == 1.0 else "Así mejor, ¿no?")


def register(reg) -> None:  # noqa: ANN001
    reg.register(Tool(
        name="voz.cambiar",
        description=(
            "Cambia SU PROPIA voz cuando se lo piden: «ponte voz de hombre», "
            "«habla con acento mexicano». Pásale lo que ha pedido tal cual"
        ),
        handler=cambiar,
        schema={
            "type": "object",
            "properties": {"descripcion": {"type": "string"}},
            "required": ["descripcion"],
        },
        risk=Risk.SAFE,
    ))
    reg.register(Tool(
        name="voz.velocidad",
        description="Habla más rápido o más despacio: «habla más despacio»",
        handler=velocidad,
        schema={
            "type": "object",
            "properties": {"como": {"type": "string"}},
            "required": ["como"],
        },
        risk=Risk.SAFE,
    ))
    reg.register(Tool(
        name="voz.listar",
        description="Qué voces tiene disponibles para hablar",
        handler=listar,
        risk=Risk.SAFE,
    ))
