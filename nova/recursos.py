"""Presupuesto por hardware y carga. Las extrapolaciones son ESTIMACIONES.

Medición histórica, sólo Ryzen 5600G/12 hilos: saturar CPU llevó TTS de
11 a 1909 ms; CLIP tardó 31 ms con 8 hilos y 51 ms con 12. Eso motiva
reservar capacidad, NO demuestra un porcentaje universal para otros PCs.
"""
from __future__ import annotations

import ctypes
import logging
import math
import subprocess
import threading
import time
from dataclasses import dataclass, field

from .hardware import GIB, PerfilHardware, ocupacion_dxcore, perfilar

log = logging.getLogger("nova.recursos")
# Conservado: nvidia-smi tardó ~40 ms en el equipo de referencia.
# 3 s es la política anterior, no una frecuencia óptima medida en otros PCs.
CADUCIDAD_S = 3.0


@dataclass(frozen=True)
class Umbrales:
    # Valores de compatibilidad para Estado sintético sin inventario.
    # Producción SIEMPRE los sustituye por calibrar(perfil).
    cpu_justo: float = 70.0
    cpu_apretado: float = 88.0
    ram_justo: float = 85.0
    ram_apretado: float = 93.0
    # El 85% motivó el ajuste en la 3060; extrapolarlo es estimado.
    vram_justo: float = 0.85
    vram_apretado: float = 0.95
    libre_justo: int = 0
    libre_apretado: int = 0


def calibrar(perfil: PerfilHardware) -> Umbrales:
    h = max(1, perfil.hilos)
    # ESTIMADO: 55/73% con 4 hilos, 65/81% con 8, 70/88% desde 12.
    # Núcleos/SMT no miden IPC ni latencia TTS; validar con bench/medir_energia.py.
    cpu_j = min(70.0, 45.0 + 2.5 * h)
    cpu_a = min(88.0, 65.0 + 2.0 * h)
    total = perfil.ram_total
    if not total:
        return Umbrales(cpu_j, cpu_a)
    # ESTIMADO: proteger 2–4 GiB para Windows y la voz; 1–2 GiB es urgencia.
    # Los límites por porcentaje impiden reservas imposibles en PCs < 4 GiB.
    j = int(min(total * 0.40, max(2 * GIB, min(4 * GIB, total / 8))))
    a = int(min(total * 0.20, max(GIB, min(2 * GIB, total / 16))))
    return Umbrales(cpu_j, cpu_a, 100 * (1 - j / total),
                    100 * (1 - a / total), libre_justo=j, libre_apretado=a)


def presupuesto_hilos(hilos: int) -> int:
    """Hilos para una tarea de fondo, según el tamaño de la máquina.

    La regla es proporcional, no un `-4` fijo: restar cuatro dejaba a un
    portátil de 4 hilos con dos (la mitad del PC) y a uno de 2 con nada.

    Pero la proporción se elige para NO perder lo ya medido en esta
    máquina (12 hilos), donde indexar con CLIP da:

        6 hilos  33 ms      10 hilos  37 ms
        8 hilos  31 ms      12 hilos  51 ms

    Con `hilos // 4` salían 3 y se tiraba a la basura ese 31 ms. Con
    tres cuartos redondeando a la baja salen 9 → recortado a 8, que es
    justo el óptimo medido. En un portátil de 4 hilos da 3, que deja
    uno libre para la voz y Windows.

    MEDIDO sólo el tope de 8 en el Ryzen 5600G; el resto de la curva es
    ESTIMADO por proporción y está sin comprobar en otras máquinas.
    """
    return max(1, min(8, (hilos * 3) // 4))


@dataclass(frozen=True)
class Estado:
    cpu: float | None = 0.0
    ram: float | None = 0.0
    vram: float | None = None
    juego: str = ""
    medido: float = 0.0
    umbrales: Umbrales = field(default_factory=Umbrales)
    ram_disponible: int | None = None
    perfil: PerfilHardware | None = None
    residencia: float | None = None
    modelo_cargado: bool | None = None

    @property
    def hay_juego(self) -> bool:
        return bool(self.juego)

    def modo(self) -> str:
        u = self.umbrales
        ram_a = (self.ram_disponible <= u.libre_apretado
                 if self.ram_disponible is not None and u.libre_apretado
                 else self.ram is not None and self.ram >= u.ram_apretado)
        ram_j = (self.ram_disponible <= u.libre_justo
                 if self.ram_disponible is not None and u.libre_justo
                 else self.ram is not None and self.ram >= u.ram_justo)
        if (self.hay_juego or ram_a
                or (self.cpu is not None and self.cpu >= u.cpu_apretado)
                or (self.vram is not None and self.vram >= u.vram_apretado)):
            return "apretado"
        if (ram_j or self.cpu is None or self.ram is None
                or self.cpu >= u.cpu_justo
                or (self.vram is not None and self.vram >= u.vram_justo)):
            return "justo"
        return "holgado"

    def en_una_frase(self) -> str:
        modos = {"holgado": "Hay margen de recursos.",
                 "justo": "El PC está algo cargado, voy con cuidado.",
                 "apretado": "El PC está al límite, dejo lo pesado para luego."}
        partes = [modos[self.modo()]]
        if self.juego:
            partes.append(f"Tienes {self.juego} a pantalla completa.")
        partes.append(f"CPU al {self.cpu:.0f} por ciento." if self.cpu is not None
                      else "No puedo medir la CPU.")
        partes.append(f"Memoria al {self.ram:.0f} por ciento." if self.ram is not None
                      else "No puedo medir la memoria.")
        if self.vram is not None:
            partes.append(f"La gráfica al {self.vram * 100:.0f} por ciento.")
        elif self.perfil and self.perfil.gpus == ():
            partes.append("No he detectado una gráfica de hardware.")
        else:
            partes.append("No sé cuánto espacio queda en la gráfica.")
        if self.residencia == 0.0:
            partes.append("El modelo está en la memoria del procesador; puede responder más despacio.")
        elif self.modelo_cargado is False:
            partes.append("El modelo está descargado; la próxima respuesta necesitará cargarlo.")
        return " ".join(partes)


def _vram() -> float | None:
    """Ocupación global cuando el driver la publica, de cualquier marca."""
    try:
        dato = ocupacion_dxcore()
        if dato is not None:
            return dato
        perfil = perfilar()
        if perfil.gpus is not None and not any(g.marca == "NVIDIA" for g in perfil.gpus):
            return None
        salida = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=1.0, check=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        fracciones = []
        for linea in salida.stdout.strip().splitlines():
            usado, total = map(float, linea.split(","))
            if total > 0 and math.isfinite(usado) and 0 <= usado <= total:
                fracciones.append(usado / total)
        return max(fracciones) if fracciones else None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


_residencia_compartida = None


def establecer_residencia(proveedor):
    global _residencia_compartida
    _residencia_compartida = proveedor


class Vigilante:
    def __init__(self, perfil: PerfilHardware | None = None, residencia=None) -> None:
        self.perfil = perfil if perfil is not None else perfilar()
        self.umbrales = calibrar(self.perfil)
        self._residencia = residencia
        self._ultimo = Estado(perfil=self.perfil, umbrales=self.umbrales)
        self._primera = True
        self._cerrojo = threading.RLock()
        try:
            import psutil
            psutil.cpu_percent(interval=None)
        except ImportError:
            pass

    def estado(self, forzar: bool = False) -> Estado:
        with self._cerrojo:
            ahora = time.monotonic()
            if not forzar and not self._primera and ahora - self._ultimo.medido < CADUCIDAD_S:
                return self._ultimo
            cpu = ram = libre = None
            try:
                import psutil
                cpu = psutil.cpu_percent(interval=0.15 if self._primera else None)
                memoria = psutil.virtual_memory()
                ram = memoria.percent
                libre = getattr(memoria, "available", None)
            except (ImportError, OSError):
                log.debug("No pude medir CPU/RAM", exc_info=True)
            self._primera = False
            residencia, cargado = None, None
            proveedor = self._residencia or _residencia_compartida
            if proveedor:
                lectura = proveedor()
                residencia, cargado = lectura.fraccion, lectura.cargado
            self._ultimo = Estado(cpu, ram, _vram(), _juego_delante(), time.monotonic(),
                                  self.umbrales, libre, self.perfil, residencia, cargado)
            return self._ultimo

    def modo(self) -> str:
        return self.estado().modo()

    def hay_sitio_para_lo_pesado(self) -> bool:
        return self.modo() != "apretado"

    def hilos_para_lo_pesado(self) -> int:
        h = presupuesto_hilos(self.perfil.hilos)
        return {"holgado": h, "justo": max(1, h // 2), "apretado": 1}[self.modo()]


_NO_SON_JUEGOS = frozenset({
    "explorer", "opera", "chrome", "firefox", "msedge", "brave",
    "code", "devenv", "pycharm64", "obs64", "vlc", "mpc-hc64",
    "ApplicationFrameHost", "TextInputHost", "python", "pythonw",
    # Añadidos el 01/09 tras verlo fallar en vivo: con Discord maximizado
    # NOVA se negó a correr unos tests diciendo "tienes Discord a pantalla
    # completa". Estas se ponen a pantalla completa a todas horas y no le
    # quitan fotogramas a nadie.
    "Discord", "Spotify", "steam", "steamwebhelper", "EpicGamesLauncher",
    "WhatsApp", "Telegram", "Notion", "slack", "Teams", "olk", "OUTLOOK",
    "WindowsTerminal", "cmd", "powershell", "notepad", "Notepad",
})


def _juego_delante() -> str:
    """El proceso que ocupa la pantalla entera, si parece un juego.

    Heurística: ventana del tamaño de la pantalla y proceso que no está
    en la lista de los que también se ponen a pantalla completa sin ser
    juegos (navegador, editor, reproductor).
    """
    try:
        import psutil

        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return ""

        class RECT(ctypes.Structure):
            _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                        ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

        r = RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(r))
        pantalla_ancho = user32.GetSystemMetrics(0)
        pantalla_alto = user32.GetSystemMetrics(1)

        # Margen de 2 px: una ventana maximizada sin bordes puede
        # sobresalir un pixel.
        if (r.right - r.left) < pantalla_ancho - 2 or (r.bottom - r.top) < pantalla_alto - 2:
            return ""

        pid = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        nombre = psutil.Process(pid.value).name().removesuffix(".exe")
        return "" if nombre in _NO_SON_JUEGOS else nombre
    except Exception:  # noqa: BLE001
        return ""


