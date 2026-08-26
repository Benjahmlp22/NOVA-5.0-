"""Archivos y captura de pantalla.

Todo lo que escribe vive dentro de `workspace/`.  La comprobación no es
"la ruta no empieza por ..", es resolver la ruta final y verificar que
sigue dentro del sandbox — que es lo único que aguanta symlinks, rutas
absolutas disfrazadas y `..` creativos.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from ..config import CONFIG
from .registry import Risk, Tool, ToolResult

log = logging.getLogger("nova.tools.files")


class SandboxError(Exception):
    """Alguien intentó salirse de workspace/."""


def _resolve(rel_path: str) -> Path:
    if not rel_path or rel_path in ("/", "\\"):
        raise SandboxError("ruta vacía")
    if Path(rel_path).is_absolute():
        raise SandboxError("solo acepto rutas relativas dentro del workspace")
    root = CONFIG.workspace.resolve()
    target = (root / rel_path).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise SandboxError("esa ruta se sale de mi carpeta de trabajo") from exc
    return target


def create_file(path: str, content: str = "") -> ToolResult:
    try:
        target = _resolve(path)
    except SandboxError as exc:
        return ToolResult(ok=False, message=f"No puedo: {exc}.")
    target.parent.mkdir(parents=True, exist_ok=True)
    existia = target.exists()
    target.write_text(content or "", encoding="utf-8")
    verbo = "He sobrescrito" if existia else "He creado"
    return ToolResult(ok=True, message=f"{verbo} el archivo {path}.", data={"path": str(target)})


def create_folder(path: str) -> ToolResult:
    try:
        target = _resolve(path)
    except SandboxError as exc:
        return ToolResult(ok=False, message=f"No puedo: {exc}.")
    target.mkdir(parents=True, exist_ok=True)
    return ToolResult(ok=True, message=f"He creado la carpeta {path}.", data={"path": str(target)})


def read_file(path: str, max_chars: int = 4000) -> ToolResult:
    try:
        target = _resolve(path)
    except SandboxError as exc:
        return ToolResult(ok=False, message=f"No puedo: {exc}.")
    if not target.is_file():
        return ToolResult(ok=False, message=f"No existe el archivo {path}.")
    texto = target.read_text(encoding="utf-8", errors="replace")[:max_chars]
    return ToolResult(ok=True, message=f"Contenido de {path}:\n{texto}", data={"content": texto})


def list_workspace() -> ToolResult:
    root = CONFIG.workspace
    root.mkdir(parents=True, exist_ok=True)
    entradas = sorted(root.rglob("*"))[:60]
    if not entradas:
        return ToolResult(ok=True, message="Tu carpeta de trabajo está vacía.")
    listado = ", ".join(p.relative_to(root).as_posix() for p in entradas)
    return ToolResult(ok=True, message=f"En tu carpeta de trabajo tienes: {listado}.")


def delete_file(path: str) -> ToolResult:
    """DANGEROUS: borra de verdad. Siempre pasa por confirmación."""
    try:
        target = _resolve(path)
    except SandboxError as exc:
        return ToolResult(ok=False, message=f"No puedo: {exc}.")
    if not target.exists():
        return ToolResult(ok=False, message=f"No existe {path}.")
    if target.is_dir():
        import shutil

        shutil.rmtree(target)
        return ToolResult(ok=True, message=f"He borrado la carpeta {path}.")
    target.unlink()
    return ToolResult(ok=True, message=f"He borrado el archivo {path}.")


def screenshot() -> ToolResult:
    CONFIG.screenshots.mkdir(parents=True, exist_ok=True)
    destino = CONFIG.screenshots / f"captura_{datetime.now():%Y%m%d_%H%M%S}.png"
    try:
        import mss
        import mss.tools

        with mss.mss() as sct:
            img = sct.grab(sct.monitors[0])
            mss.tools.to_png(img.rgb, img.size, output=str(destino))
    except Exception as exc:
        return ToolResult(ok=False, message=f"No pude capturar la pantalla: {exc}")
    kb = round(destino.stat().st_size / 1024)
    return ToolResult(
        ok=True,
        message=f"Captura guardada en {destino.name} ({kb} KB).",
        data={"path": str(destino)},
    )


def register(reg) -> None:  # noqa: ANN001
    reg.register(Tool(
        name="file.create",
        description="Crea un archivo de texto en la carpeta de trabajo",
        handler=create_file,
        schema={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["path"],
        },
        risk=Risk.MEDIUM,
    ))
    reg.register(Tool(
        name="folder.create",
        description="Crea una carpeta en la carpeta de trabajo",
        handler=create_folder,
        schema={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
        risk=Risk.MEDIUM,
    ))
    reg.register(Tool(
        name="file.read",
        description="Lee un archivo de la carpeta de trabajo",
        handler=read_file,
        schema={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
        risk=Risk.SAFE,
    ))
    reg.register(Tool(
        name="workspace.list",
        description="Lista lo que hay en la carpeta de trabajo",
        handler=list_workspace,
        risk=Risk.SAFE,
    ))
    reg.register(Tool(
        name="file.delete",
        description="Borra un archivo o carpeta de la carpeta de trabajo",
        handler=delete_file,
        schema={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
        risk=Risk.DANGEROUS,
    ))
    reg.register(Tool(
        name="screen.capture",
        description="Hace una captura de pantalla",
        handler=screenshot,
        risk=Risk.SAFE,
    ))
