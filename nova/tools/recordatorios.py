"""Alarmas y recordatorios que respetan lo que estás haciendo.

Un recordatorio que te interrumpe a mitad de una partida para decirte
algo que podía esperar treinta segundos es peor que no tenerlo.  Así que
aquí vencer NO significa hablar: significa quedar pendiente.

    vence mientras hablas con NOVA  → te lo dice ahí mismo, en el turno
    vence mientras estás a lo tuyo  → el panel parpadea y espera
    le hablas después               → te lo suelta antes de nada

La única excepción son las alarmas con hora fija ("despiértame a las
ocho"), que sí interrumpen: para eso las pones.

Se guardan en JSON como la memoria, y por el mismo motivo: son unas
pocas entradas y montar una base de datos sería complejidad que hay que
mantener a cambio de nada perceptible.
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from ..config import CONFIG
from .registry import Risk, Tool, ToolResult

log = logging.getLogger("nova.tools.recordatorios")

_DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]


@dataclass
class Recordatorio:
    texto: str
    cuando: str                    # ISO 8601
    alarma: bool = False           # true = interrumpe aunque estés a otra cosa
    avisado: bool = False
    creado: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))

    @property
    def momento(self) -> datetime:
        return datetime.fromisoformat(self.cuando)

    def vencido(self, ahora: datetime | None = None) -> bool:
        return not self.avisado and self.momento <= (ahora or datetime.now())


# ── Entender cuándo ──────────────────────────────────────────────────

def _sin_tildes(texto: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", (texto or "").lower())
        if unicodedata.category(c) != "Mn"
    )


# "en 10 minutos", "dentro de media hora"... y también "un temporizador
# DE 3 minutos": pidiendo un timer nadie dice "en". Sin aceptar el "de",
# NOVA entendía la orden entera menos la parte que importaba.
#
# El "de" es peligroso suelto ("hablamos de fútbol"), así que va pegado a
# una cantidad Y una unidad de tiempo: "de 3 minutos" sí, "de nada" no.
_EN_UN_RATO = re.compile(
    r"\b(?:en|dentro de|de)\s+(\d+|un|una|medi[ao])\s*"
    r"(segundos?|minutos?|min|horas?|h|dias?|semanas?)\b"
)
_A_LAS = re.compile(r"\ba\s+la?s?\s+(\d{1,2})(?:[:.](\d{2}))?\s*(de la\s+\w+|am|pm)?\b")
_MANANA = re.compile(r"\bmanana\b")
_NUMEROS = {"un": 1, "una": 1, "medio": 0.5, "media": 0.5}
_UNIDADES = {
    "segundo": "segundos", "segundos": "segundos",
    "minuto": "minutos", "minutos": "minutos", "min": "minutos",
    "hora": "horas", "horas": "horas", "h": "horas",
    "dia": "dias", "dias": "dias", "semana": "semanas", "semanas": "semanas",
}


def interpretar_cuando(texto: str, ahora: datetime | None = None) -> tuple[datetime | None, bool]:
    """De "en 10 minutos" o "a las 8" a una fecha. Devuelve (cuándo, es_alarma).

    Es deliberadamente cortito: cubre lo que la gente dice de verdad por
    voz. Lo raro se lo puede pasar el modelo ya resuelto en ISO, que para
    eso tiene el contexto con la fecha de hoy.
    """
    ahora = ahora or datetime.now()
    limpio = _sin_tildes(texto)

    # ISO directo, que es como lo mandará el modelo si sabe la fecha.
    try:
        return datetime.fromisoformat(texto.strip()), True
    except (ValueError, TypeError):
        pass

    m = _EN_UN_RATO.search(limpio)
    if m:
        cantidad_txt, unidad_txt = m.group(1), m.group(2).rstrip(".")
        cantidad = _NUMEROS.get(cantidad_txt)
        if cantidad is None:
            try:
                cantidad = float(cantidad_txt)
            except ValueError:
                return None, False
        unidad = _UNIDADES.get(unidad_txt, unidad_txt)
        delta = {
            "segundos": timedelta(seconds=cantidad),
            "minutos": timedelta(minutes=cantidad),
            "horas": timedelta(hours=cantidad),
            "dias": timedelta(days=cantidad),
            "semanas": timedelta(weeks=cantidad),
        }.get(unidad)
        if delta:
            # "en un rato" no es una alarma: no tiene por qué interrumpir.
            return ahora + delta, False

    m = _A_LAS.search(limpio)
    if m:
        hora = int(m.group(1))
        minuto = int(m.group(2) or 0)
        sufijo = (m.group(3) or "").strip()
        if "tarde" in sufijo or "noche" in sufijo or sufijo == "pm":
            if hora < 12:
                hora += 12
        elif "manana" in sufijo or sufijo == "am":
            hora = 0 if hora == 12 else hora
        objetivo = ahora.replace(hour=hora % 24, minute=minuto, second=0, microsecond=0)
        if _MANANA.search(limpio) or objetivo <= ahora:
            # Una hora que ya pasó se entiende del día siguiente: nadie
            # pone una alarma para hace dos horas.
            objetivo += timedelta(days=1)
        # Con hora fija SÍ es alarma: para eso la pones.
        return objetivo, True

    return None, False


def describir_cuando(momento: datetime, ahora: datetime | None = None) -> str:
    """"en 10 minutos", "mañana a las 8", como lo diría una persona."""
    ahora = ahora or datetime.now()
    falta = momento - ahora
    segundos = falta.total_seconds()
    if segundos < 90:
        return "en menos de un minuto"
    if segundos < 3600:
        return f"en {int(round(segundos / 60))} minutos"
    if momento.date() == ahora.date():
        return f"hoy a las {momento:%H:%M}"
    if momento.date() == (ahora + timedelta(days=1)).date():
        return f"mañana a las {momento:%H:%M}"
    return f"el {_DIAS[momento.weekday()]} a las {momento:%H:%M}"


# ── Almacén ──────────────────────────────────────────────────────────

def _path() -> Path:
    """Indirección para que los tests apunten a un fichero temporal."""
    return CONFIG.data_dir / "recordatorios.json"


def cargar() -> list[Recordatorio]:
    try:
        crudo = json.loads(_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    salida = []
    for d in crudo if isinstance(crudo, list) else []:
        try:
            salida.append(Recordatorio(**d))
        except TypeError:
            continue
    return salida


def guardar(lista: list[Recordatorio]) -> None:
    ruta = _path()
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(
        json.dumps([asdict(r) for r in lista], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def pendientes(ahora: datetime | None = None) -> list[Recordatorio]:
    """Los que ya han vencido y no se han dicho todavía."""
    return [r for r in cargar() if r.vencido(ahora)]


def marcar_avisados(textos: list[str]) -> None:
    lista = cargar()
    for r in lista:
        if r.texto in textos:
            r.avisado = True
    # Los ya avisados de hace más de un día no aportan nada.
    limite = datetime.now() - timedelta(days=1)
    guardar([r for r in lista if not (r.avisado and r.momento < limite)])


# ── Herramientas ─────────────────────────────────────────────────────

def crear(texto: str, cuando: str) -> ToolResult:
    """Apunta un recordatorio o una alarma."""
    contenido = (texto or "").strip()
    if not contenido:
        return ToolResult(ok=False, message="¿De qué quieres que te avise?")

    momento, alarma = interpretar_cuando(cuando or "")
    if momento is None:
        return ToolResult(
            ok=False,
            message="No he entendido para cuándo. Dímelo como «en diez minutos» "
                    "o «a las ocho».",
        )
    if momento <= datetime.now():
        return ToolResult(ok=False, message="Eso ya ha pasado.")

    lista = cargar()
    lista.append(Recordatorio(texto=contenido, cuando=momento.isoformat(timespec="seconds"),
                              alarma=alarma))
    guardar(lista)
    log.info("recordatorio para %s: %r", momento, contenido)
    return ToolResult(
        ok=True,
        message=f"Hecho, te lo recuerdo {describir_cuando(momento)}.",
        data={"cuando": momento.isoformat(), "alarma": alarma},
    )


def listar() -> ToolResult:
    """Qué tiene apuntado, sin lo ya avisado."""
    activos = [r for r in cargar() if not r.avisado]
    if not activos:
        return ToolResult(ok=True, message="No tienes nada apuntado.")
    activos.sort(key=lambda r: r.momento)
    partes = [f"{r.texto}, {describir_cuando(r.momento)}" for r in activos[:5]]
    return ToolResult(ok=True, message="Tienes apuntado: " + "; ".join(partes) + ".")


def olvidar(query: str) -> ToolResult:
    """Cancela un recordatorio."""
    busca = _sin_tildes(query or "")
    lista = cargar()
    quedan = [r for r in lista if busca not in _sin_tildes(r.texto)]
    borrados = len(lista) - len(quedan)
    if not borrados:
        return ToolResult(ok=False, message=f"No tengo nada apuntado sobre «{query}».")
    guardar(quedan)
    return ToolResult(ok=True, message=f"Cancelado ({borrados}).")


def register(reg) -> None:  # noqa: ANN001
    reg.register(Tool(
        name="recordatorio.crear",
        description="Apunta un recordatorio o alarma: «recuérdame X en 10 minutos»",
        handler=crear,
        schema={
            "type": "object",
            "properties": {
                "texto": {"type": "string"},
                "cuando": {"type": "string", "description": "«en 10 minutos», «a las 8» o fecha ISO"},
            },
            "required": ["texto", "cuando"],
        },
        risk=Risk.SAFE,
    ))
    reg.register(Tool(
        name="recordatorio.listar",
        description="Qué recordatorios y alarmas tiene apuntados",
        handler=listar,
        risk=Risk.SAFE,
        responde_sola=True,
    ))
    reg.register(Tool(
        name="recordatorio.olvidar",
        description="Cancela un recordatorio ya apuntado",
        handler=olvidar,
        schema={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
        risk=Risk.SAFE,
    ))
