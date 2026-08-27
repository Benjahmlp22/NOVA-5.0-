"""Memoria a largo plazo: hechos que NOVA recuerda entre sesiones.

Un JSON plano y búsqueda léxica.  Deliberadamente simple: para unas
decenas de hechos personales, montar embeddings + base vectorial es
complejidad que hay que mantener a cambio de nada perceptible.
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from datetime import datetime
from pathlib import Path

from ..config import CONFIG
from .registry import Risk, Tool, ToolResult

log = logging.getLogger("nova.tools.memory")

_STOPWORDS = frozenset({
    "el", "la", "los", "las", "un", "una", "de", "del", "que", "en", "y",
    "a", "mi", "me", "es", "por", "para", "con", "se", "su", "lo", "al",
})


def _norm(text: str) -> str:
    out = []
    for ch in unicodedata.normalize("NFD", (text or "").lower()):
        if unicodedata.category(ch) != "Mn":
            out.append(ch)
    return "".join(out)


def _tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"\w+", _norm(text)) if t not in _STOPWORDS and len(t) > 2}


def _path() -> Path:
    """Dónde vive la memoria.

    Indirección a propósito: CONFIG es inmutable (que es lo correcto),
    así que esta función es el punto por el que los tests apuntan a un
    archivo temporal sin tocar la configuración real del usuario.
    """
    return CONFIG.memory_file


def _load() -> list[dict]:
    path = _path()
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (ValueError, OSError):
        log.warning("memoria corrupta, empiezo de cero")
        return []


def _save(facts: list[dict]) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(facts, ensure_ascii=False, indent=2), encoding="utf-8")


def remember(text: str) -> ToolResult:
    texto = (text or "").strip()
    if not texto:
        return ToolResult(ok=False, message="¿Qué quieres que recuerde?")
    facts = _load()
    # Evita duplicados exactos: repetir "me llamo Benja" 5 veces no
    # mejora la memoria, solo ensucia el recall.
    if any(_norm(f["text"]) == _norm(texto) for f in facts):
        return ToolResult(ok=True, message="Eso ya lo tenía guardado.")
    facts.append({"text": texto, "ts": datetime.now().isoformat(timespec="seconds")})
    _save(facts)
    return ToolResult(ok=True, message="Guardado, lo recordaré.")


def recall(query: str = "", limit: int = 4) -> ToolResult:
    facts = _load()
    if not facts:
        return ToolResult(ok=True, message="Todavía no tengo nada guardado sobre ti.")

    if not query.strip():
        recientes = [f["text"] for f in facts[-limit:]]
        return ToolResult(ok=True, message="Recuerdo: " + "; ".join(recientes) + ".")

    q = _tokens(query)
    puntuados = []
    for f in facts:
        solape = len(q & _tokens(f["text"]))
        if solape:
            puntuados.append((solape, f["text"]))
    if not puntuados:
        return ToolResult(ok=True, message=f"No tengo nada guardado sobre «{query}».")
    puntuados.sort(reverse=True)
    mejores = [t for _, t in puntuados[:limit]]
    return ToolResult(ok=True, message="Recuerdo: " + "; ".join(mejores) + ".")


def forget(query: str) -> ToolResult:
    facts = _load()
    q = _tokens(query)
    quedan = [f for f in facts if not (q & _tokens(f["text"]))]
    borrados = len(facts) - len(quedan)
    if not borrados:
        return ToolResult(ok=False, message=f"No encontré nada sobre «{query}».")
    _save(quedan)
    return ToolResult(ok=True, message=f"Olvidado ({borrados} recuerdo(s)).")


# Cuántos hechos se le meten al modelo en CADA turno. Seis caben en unas
# pocas decenas de tokens de prefill; la lista entera crecería sin techo
# y el prefill se paga en cada mensaje, que es justo lo que encarece la
# latencia en inferencia local.
MAX_HECHOS_EN_PROMPT = 6


def para_prompt(limite: int = MAX_HECHOS_EN_PROMPT) -> str:
    """Los hechos más recientes, listos para el system prompt.

    `build_system_prompt` acepta un `memory_hint` desde NOVA4... y nadie
    se lo pasaba nunca. El resultado es que NOVA sólo recordaba algo si
    el modelo acertaba a llamar a `memory.recall` por su cuenta, cosa que
    en el smoke no hizo ni una vez: acertó el color favorito porque
    seguía en el historial de la conversación, no porque lo recordara.
    """
    facts = _load()
    if not facts:
        return ""
    return "\n".join(f"- {f['text']}" for f in facts[-limite:])


def register(reg) -> None:  # noqa: ANN001
    reg.register(Tool(
        name="memory.remember",
        description="Guarda un dato del usuario: nombre, gustos, rutas, preferencias",
        handler=remember,
        schema={
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
        risk=Risk.SAFE,
    ))
    reg.register(Tool(
        name="memory.recall",
        description="Busca lo guardado del usuario. ANTES de decir que no sabes algo suyo",
        handler=recall,
        schema={
            "type": "object",
            "properties": {"query": {"type": "string"}},
        },
        risk=Risk.SAFE,
        responde_sola=True,
    ))
    reg.register(Tool(
        name="memory.forget",
        description="Olvida lo guardado sobre un tema",
        handler=forget,
        schema={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
        risk=Risk.DANGEROUS,
    ))
