"""Abrir aplicaciones: builtins de Windows + índice de apps instaladas.

El índice se construye una vez (menú inicio, escritorio, registro) y se
cachea: escanear en cada petición añadía segundos a un "abre Chrome".
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import time
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path

from ..config import CONFIG
from .registry import Risk, Tool, ToolResult

log = logging.getLogger("nova.tools.apps")

_CACHE = CONFIG.data_dir / "apps_index.json"
_CACHE_TTL = 24 * 3600

# Apps que trae Windows: se abren por nombre de ejecutable, sin índice.
BUILTINS: dict[str, str] = {
    "calculadora": "calc.exe", "calc": "calc.exe", "calculator": "calc.exe",
    "bloc de notas": "notepad.exe", "notepad": "notepad.exe", "notas": "notepad.exe",
    "paint": "mspaint.exe",
    "explorador": "explorer.exe", "explorador de archivos": "explorer.exe",
    "archivos": "explorer.exe", "carpetas": "explorer.exe",
    "cmd": "cmd.exe", "consola": "cmd.exe", "simbolo del sistema": "cmd.exe",
    "powershell": "powershell.exe",
    "terminal": "wt.exe", "windows terminal": "wt.exe",
    "administrador de tareas": "taskmgr.exe", "task manager": "taskmgr.exe",
    "panel de control": "control.exe",
    "configuracion": "ms-settings:", "ajustes": "ms-settings:", "settings": "ms-settings:",
}

# Correcciones de lo que suele entender mal el reconocimiento de voz o
# escribe rápido el usuario.
ALIASES: dict[str, str] = {
    "chorme": "chrome", "crome": "chrome", "google chrome": "chrome",
    "discor": "discord", "discordia": "discord",
    "spotifai": "spotify", "espotifai": "spotify",
    "vscode": "visual studio code", "vs code": "visual studio code",
    "wasap": "whatsapp", "guasap": "whatsapp",
    "estim": "steam",
}


def _norm(text: str) -> str:
    out = []
    for ch in unicodedata.normalize("NFD", (text or "").lower()):
        if unicodedata.category(ch) != "Mn":
            out.append(ch)
    return "".join(out).strip()


def _scan_installed() -> dict[str, str]:
    """Recorre menú inicio y escritorio buscando accesos directos."""
    found: dict[str, str] = {}
    roots = [
        Path(os.environ.get("APPDATA", "")) / "Microsoft/Windows/Start Menu/Programs",
        Path(os.environ.get("PROGRAMDATA", "")) / "Microsoft/Windows/Start Menu/Programs",
        Path.home() / "Desktop",
        Path(os.environ.get("PUBLIC", "")) / "Desktop",
    ]
    for root in roots:
        if not root or not root.is_dir():
            continue
        try:
            for lnk in root.rglob("*.lnk"):
                name = _norm(lnk.stem)
                # El primero gana: el menú inicio va antes que el escritorio.
                found.setdefault(name, str(lnk))
        except (PermissionError, OSError):
            continue
    return found


def _load_index(force: bool = False) -> dict[str, str]:
    if not force and _CACHE.exists():
        try:
            raw = json.loads(_CACHE.read_text(encoding="utf-8"))
            if time.time() - raw.get("ts", 0) < _CACHE_TTL:
                return raw.get("apps", {})
        except (ValueError, OSError):
            pass
    apps = _scan_installed()
    try:
        _CACHE.parent.mkdir(parents=True, exist_ok=True)
        _CACHE.write_text(
            json.dumps({"ts": time.time(), "apps": apps}, ensure_ascii=False),
            encoding="utf-8",
        )
    except OSError:
        log.debug("no pude cachear el índice de apps", exc_info=True)
    return apps


def _best_match(query: str, apps: dict[str, str]) -> tuple[str, str, float] | None:
    q = _norm(ALIASES.get(_norm(query), query))
    mejor: tuple[str, str, float] | None = None
    for nombre, ruta in apps.items():
        if q == nombre:
            return nombre, ruta, 1.0
        if q in nombre:
            score = 0.9 - (len(nombre) - len(q)) / 200
        else:
            score = SequenceMatcher(None, q, nombre).ratio()
        if mejor is None or score > mejor[2]:
            mejor = (nombre, ruta, score)
    if mejor and mejor[2] >= 0.62:
        return mejor
    return None


def open_app(name: str) -> ToolResult:
    consulta = (name or "").strip()
    if not consulta:
        return ToolResult(ok=False, message="¿Qué aplicación abro?")

    clave = _norm(ALIASES.get(_norm(consulta), consulta))
    if clave in BUILTINS:
        exe = BUILTINS[clave]
        try:
            if exe.endswith(":"):
                os.startfile(exe)  # noqa: S606 — protocolo ms-settings:
            else:
                subprocess.Popen([exe], shell=False)
            return ToolResult(ok=True, message=f"Listo, he abierto {consulta}.")
        except Exception as exc:
            return ToolResult(ok=False, message=f"No pude abrir {consulta}: {exc}")

    apps = _load_index()
    match = _best_match(consulta, apps)
    if match is None:
        return ToolResult(
            ok=False,
            message=f"No encuentro «{consulta}» instalado. Puedo reescanear si lo instalaste hace poco.",
        )
    nombre, ruta, _ = match
    try:
        os.startfile(ruta)  # noqa: S606 — es un .lnk del propio usuario
    except Exception as exc:
        return ToolResult(ok=False, message=f"No pude abrir {nombre}: {exc}")
    return ToolResult(ok=True, message=f"Listo, he abierto {nombre.title()}.", data={"app": nombre})


def find_app(query: str) -> ToolResult:
    apps = _load_index()
    match = _best_match(query, apps)
    if match is None:
        return ToolResult(ok=False, message=f"No tienes «{query}» instalado, o no lo encuentro.")
    nombre, ruta, score = match
    seguridad = "seguro" if score > 0.85 else "probablemente"
    return ToolResult(
        ok=True,
        message=f"Sí, {seguridad} tienes {nombre.title()} instalado.",
        data={"app": nombre, "path": ruta},
    )


def refresh_index() -> ToolResult:
    apps = _load_index(force=True)
    return ToolResult(ok=True, message=f"Índice actualizado: {len(apps)} aplicaciones encontradas.")


def register(reg) -> None:  # noqa: ANN001
    reg.register(Tool(
        name="app.open",
        description="Abre una aplicación o programa por su nombre",
        handler=open_app,
        schema={
            "type": "object",
            "properties": {"name": {"type": "string", "description": "Nombre de la app"}},
            "required": ["name"],
        },
        risk=Risk.MEDIUM,
    ))
    reg.register(Tool(
        name="app.find",
        description="Comprueba si una aplicación está instalada",
        handler=find_app,
        schema={
            "type": "object",
            "properties": {"query": {"type": "string", "description": "Nombre a buscar"}},
            "required": ["query"],
        },
        risk=Risk.SAFE,
    ))
    reg.register(Tool(
        name="app.refresh_index",
        description="Vuelve a escanear las aplicaciones instaladas",
        handler=refresh_index,
        risk=Risk.SAFE,
    ))
