"""Herramientas de sistema: estado del PC, ventana activa, procesos, red.

Todas devuelven ya la frase en español que verá el usuario y leerá el
modelo — nada de volcar diccionarios a la conversación.
"""

from __future__ import annotations

import ctypes
import logging
import platform
import socket
import subprocess

import psutil

from .registry import Risk, Tool, ToolResult

log = logging.getLogger("nova.tools.system")

# Procesos del sistema que jamás cerramos, aunque el usuario insista:
# matarlos deja Windows inutilizable hasta reiniciar.
PROTECTED = frozenset({
    "explorer", "winlogon", "csrss", "smss", "wininit",
    "services", "lsass", "svchost", "system", "dwm", "ntoskrnl",
})


def pc_status() -> ToolResult:
    cpu = psutil.cpu_percent(interval=0.4)
    mem = psutil.virtual_memory()
    n_proc = len(psutil.pids())
    libre = round(mem.available / 1024**3, 1)
    total = round(mem.total / 1024**3, 1)
    msg = (
        f"CPU al {cpu:.0f}%. RAM: {libre} GB libres de {total} GB "
        f"({mem.percent:.0f}% en uso). {n_proc} procesos activos."
    )
    bat = psutil.sensors_battery()
    if bat is not None:
        estado = "enchufado" if bat.power_plugged else "con batería"
        msg += f" Batería al {int(bat.percent)}% ({estado})."
    return ToolResult(ok=True, message=msg, data={"cpu": cpu, "ram_pct": mem.percent})


def hardware() -> ToolResult:
    uname = platform.uname()
    cores_f = psutil.cpu_count(logical=False) or 0
    cores_l = psutil.cpu_count(logical=True) or 0
    ram = round(psutil.virtual_memory().total / 1024**3, 1)
    partes = [f"{uname.system} {uname.release}", f"CPU {uname.processor or 'desconocida'}"]
    partes.append(f"{cores_f} núcleos físicos / {cores_l} lógicos")
    partes.append(f"{ram} GB de RAM")
    discos = []
    for part in psutil.disk_partitions(all=False):
        try:
            u = psutil.disk_usage(part.mountpoint)
        except (PermissionError, OSError):
            continue
        discos.append(f"{part.device.rstrip(chr(92))} {round(u.free / 1024**3)} GB libres")
    if discos:
        partes.append("Discos: " + ", ".join(discos))
    return ToolResult(ok=True, message=". ".join(partes) + ".")


def active_window() -> ToolResult:
    """Qué tiene el usuario delante ahora mismo (Windows)."""
    try:
        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return ToolResult(ok=True, message="No hay ninguna ventana en primer plano.")
        length = user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        title = buf.value.strip()

        pid = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        proc = ""
        try:
            proc = psutil.Process(pid.value).name().replace(".exe", "")
        except Exception:
            pass

        if title and proc:
            return ToolResult(
                ok=True,
                message=f"Ahora mismo tienes delante {proc}: «{title}».",
                data={"process": proc, "title": title},
            )
        return ToolResult(ok=True, message=f"Ventana activa: {title or proc or 'desconocida'}.")
    except Exception as exc:
        return ToolResult(ok=False, message=f"No pude leer la ventana activa: {exc}")


def running_apps(top_n: int = 12) -> ToolResult:
    """Apps con ventana visible, no los cientos de procesos del sistema."""
    vistos: dict[str, float] = {}
    for p in psutil.process_iter(["name", "memory_info"]):
        try:
            nombre = (p.info.get("name") or "").replace(".exe", "")
            if not nombre or nombre.lower() in PROTECTED:
                continue
            mem = (p.info.get("memory_info").rss if p.info.get("memory_info") else 0) / 1024**2
            vistos[nombre] = vistos.get(nombre, 0.0) + mem
        except (psutil.NoSuchProcess, psutil.AccessDenied, AttributeError):
            continue
    top = sorted(vistos.items(), key=lambda kv: kv[1], reverse=True)[: max(1, top_n)]
    if not top:
        return ToolResult(ok=True, message="No veo aplicaciones abiertas.")
    listado = ", ".join(n for n, _ in top)
    return ToolResult(
        ok=True,
        message=f"Tienes abiertas: {listado}.",
        data={"apps": [n for n, _ in top]},
    )


def network() -> ToolResult:
    try:
        ip = socket.gethostbyname(socket.gethostname())
    except OSError:
        ip = "desconocida"
    try:
        socket.create_connection(("1.1.1.1", 53), timeout=2).close()
        internet = "con conexión a internet"
    except OSError:
        internet = "sin conexión a internet"
    return ToolResult(ok=True, message=f"Tu IP local es {ip}, {internet}.")


def close_app(name: str) -> ToolResult:
    """Cierra una app por nombre. DANGEROUS: mata procesos de verdad."""
    objetivo = (name or "").strip().lower().replace(".exe", "")
    if not objetivo:
        return ToolResult(ok=False, message="¿Qué aplicación quieres cerrar?")
    if objetivo in PROTECTED:
        return ToolResult(
            ok=False,
            message=f"No voy a cerrar {objetivo}: es un proceso crítico de Windows.",
        )
    cerrados = 0
    for p in psutil.process_iter(["name"]):
        try:
            if (p.info.get("name") or "").lower().replace(".exe", "") == objetivo:
                p.terminate()
                cerrados += 1
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    if not cerrados:
        return ToolResult(ok=False, message=f"No encontré ninguna ventana de {name} abierta.")
    return ToolResult(ok=True, message=f"He cerrado {name} ({cerrados} proceso(s)).")


def volume(level: int) -> ToolResult:
    """Sube/baja el volumen maestro con nircmd si está; si no, avisa."""
    nivel = max(0, min(100, int(level)))
    try:
        subprocess.run(
            ["nircmd.exe", "setsysvolume", str(int(nivel * 655.35))],
            check=True, capture_output=True, timeout=5,
        )
        return ToolResult(ok=True, message=f"Volumen al {nivel}%.")
    except (FileNotFoundError, subprocess.SubprocessError):
        return ToolResult(
            ok=False,
            message="No puedo cambiar el volumen: falta nircmd en el sistema.",
        )


def register(reg) -> None:  # noqa: ANN001
    reg.register(Tool(
        name="pc.status",
        description="Estado del PC ahora: CPU, RAM, procesos y batería",
        handler=pc_status,
        risk=Risk.SAFE,
    ))
    reg.register(Tool(
        name="pc.hardware",
        description="Especificaciones del equipo: CPU, RAM, discos, sistema",
        handler=hardware,
        risk=Risk.SAFE,
    ))
    reg.register(Tool(
        name="pc.active_window",
        description=(
            "Qué aplicación o juego tiene el usuario en primer plano ahora mismo. "
            "Úsala cuando pregunte qué está haciendo, jugando o mirando"
        ),
        handler=active_window,
        risk=Risk.SAFE,
    ))
    reg.register(Tool(
        name="pc.running_apps",
        description="Lista las aplicaciones abiertas",
        handler=running_apps,
        schema={
            "type": "object",
            "properties": {"top_n": {"type": "integer", "description": "Cuántas listar"}},
        },
        risk=Risk.SAFE,
    ))
    reg.register(Tool(
        name="pc.network",
        description="IP local y si hay conexión a internet",
        handler=network,
        risk=Risk.SAFE,
    ))
    reg.register(Tool(
        name="app.close",
        description="Cierra una aplicación abierta",
        handler=close_app,
        schema={
            "type": "object",
            "properties": {"name": {"type": "string", "description": "Nombre de la app"}},
            "required": ["name"],
        },
        risk=Risk.DANGEROUS,
    ))
    reg.register(Tool(
        name="pc.volume",
        description="Ajusta el volumen del sistema (0-100)",
        handler=volume,
        schema={
            "type": "object",
            "properties": {"level": {"type": "integer", "description": "Nivel 0-100"}},
            "required": ["level"],
        },
        risk=Risk.MEDIUM,
    ))
