"""Cuánto sitio hay, y cuánto puede ocupar NOVA sin estorbar.

Un asistente que vive en tu escritorio compite por la misma máquina que
lo que estás haciendo. Si te pones a jugar y NOVA sigue indexando
imágenes con ocho hilos, le has cambiado los fotogramas por una función
que no le habías pedido en ese momento.

Antes esto se miraba en un solo sitio y sólo para una cosa: la VRAM, y
sólo para cambiar de modelo. Ahora hay un único vigilante que mide la
máquina entera y dice en qué **modo** está, y todo lo caro le pregunta
antes de ponerse:

    holgado    haz lo que quieras
    justo      lo caro, más despacio y con menos hilos
    apretado   lo caro se espera; sólo lo que el usuario acaba de pedir

Los números de abajo salen de medir en la máquina de referencia (Ryzen
5 5600G, 12 hilos, RTX 3060 de 12 GB), no de una regla general.
"""

from __future__ import annotations

import ctypes
import logging
import os
import subprocess
import time
from dataclasses import dataclass

log = logging.getLogger("nova.recursos")

# ── Los umbrales, y de dónde salen ───────────────────────────────────
#
# CPU al 70%: medido, con los 12 hilos al tope sintetizar una frase pasa
# de 11 ms a 1909 ms. A partir de ahí NOVA ya se nota lenta al hablar,
# que es lo que primero molesta.
CPU_JUSTO = 70.0
CPU_APRETADO = 88.0

# VRAM al 85%: es el punto en que Ollama empieza a dejar parte del modelo
# fuera de la tarjeta. Con Star Citizen abierto se midió el 92% y el
# modelo corriendo al 10% en GPU.
VRAM_JUSTO = 0.85
VRAM_APRETADO = 0.95

# RAM al 90%: por encima Windows empieza a tirar de disco y todo se
# arrastra, NOVA la primera.
RAM_JUSTO = 85.0
RAM_APRETADO = 93.0

# Cada cuánto se vuelve a mirar de verdad. Preguntar por la VRAM llama a
# nvidia-smi, que tarda ~40 ms: hacerlo en cada frase sería absurdo.
CADUCIDAD_S = 3.0

# Procesos que tienen una ventana a pantalla completa y NO son un juego.
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


@dataclass(frozen=True)
class Estado:
    cpu: float = 0.0            # porcentaje 0-100
    ram: float = 0.0            # porcentaje 0-100
    vram: float = 0.0           # fracción usada, 0-1
    juego: str = ""             # proceso a pantalla completa, si lo hay
    medido: float = 0.0         # cuándo, en time.monotonic()

    @property
    def hay_juego(self) -> bool:
        return bool(self.juego)

    def modo(self) -> str:
        """holgado, justo o apretado."""
        if (self.cpu >= CPU_APRETADO or self.ram >= RAM_APRETADO
                or self.vram >= VRAM_APRETADO):
            return "apretado"
        # Un juego delante cuenta como apretado aunque los números den
        # justo: los fotogramas son suyos, no de NOVA.
        if self.hay_juego:
            return "apretado"
        if (self.cpu >= CPU_JUSTO or self.ram >= RAM_JUSTO
                or self.vram >= VRAM_JUSTO):
            return "justo"
        return "holgado"

    def en_una_frase(self) -> str:
        """Para decirlo en alto cuando pregunten por qué va lenta."""
        if self.hay_juego:
            return (f"Tienes {self.juego} a pantalla completa, así que voy a lo "
                    f"justo. CPU al {self.cpu:.0f} por ciento y la gráfica al "
                    f"{self.vram * 100:.0f}.")
        modos = {
            "holgado": "Voy sobrada.",
            "justo": "El PC está algo cargado, voy con cuidado.",
            "apretado": "El PC está al límite, dejo lo pesado para luego.",
        }
        return (f"{modos[self.modo()]} CPU al {self.cpu:.0f} por ciento, "
                f"memoria al {self.ram:.0f} y la gráfica al {self.vram * 100:.0f}.")


def _vram() -> float:
    """Fracción de VRAM ocupada. 0.0 si no hay NVIDIA o no se puede saber."""
    try:
        salida = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used,memory.total",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=4,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).stdout.strip().splitlines()[0]
        usada, total = (float(x) for x in salida.split(","))
        return usada / total if total else 0.0
    except Exception:  # noqa: BLE001
        # Sin NVIDIA, o nvidia-smi no está. No es un error: hay PCs sin
        # gráfica dedicada y NOVA funciona igual.
        return 0.0


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


class Vigilante:
    """Mide la máquina, con la respuesta guardada unos segundos."""

    def __init__(self) -> None:
        self._ultimo = Estado()
        self._primera = True
        try:
            import psutil

            # Cebar el contador: `cpu_percent(interval=None)` mide desde
            # la llamada ANTERIOR, y sin una anterior devuelve 0.0.
            psutil.cpu_percent(interval=None)
        except Exception:  # noqa: BLE001
            log.debug("psutil no disponible", exc_info=True)

    def estado(self, forzar: bool = False) -> Estado:
        ahora = time.monotonic()
        if not forzar and ahora - self._ultimo.medido < CADUCIDAD_S:
            return self._ultimo

        cpu = ram = 0.0
        try:
            import psutil

            # La PRIMERA vez se mide con una ventana corta de verdad.
            # Cebar el contador y preguntar acto seguido daba 0.0, y NOVA
            # llegó a decir "CPU al 0 por ciento" con un juego abierto y
            # la máquina al 90. 150 ms de bloqueo, una sola vez.
            #
            # Después ya sin intervalo: no bloquea, y mide desde la
            # llamada anterior, que con la caducidad de 3 s es justo la
            # ventana que interesa.
            cpu = psutil.cpu_percent(interval=0.15 if self._primera else None)
            self._primera = False
            ram = psutil.virtual_memory().percent
        except Exception:  # noqa: BLE001
            log.debug("no pude medir CPU/RAM", exc_info=True)

        self._ultimo = Estado(cpu=cpu, ram=ram, vram=_vram(),
                              juego=_juego_delante(), medido=ahora)
        return self._ultimo

    def modo(self) -> str:
        return self.estado().modo()

    def hay_sitio_para_lo_pesado(self) -> bool:
        """¿Se puede poner a indexar imágenes ahora mismo?"""
        return self.modo() != "apretado"

    def hilos_para_lo_pesado(self) -> int:
        """Cuántos hilos usar en una tarea de fondo, según cómo esté todo.

        Nunca todos: dejar cuatro núcleos libres no es un sacrificio.
        Medido en este PC (12 hilos), por imagen con CLIP —

            6 hilos  33 ms      10 hilos  37 ms
            8 hilos  31 ms      12 hilos  51 ms

        — con ocho va MÁS rápido que con doce, y además NOVA puede seguir
        hablando mientras indexa.
        """
        nucleos = os.cpu_count() or 4
        holgado = max(2, nucleos - 4)
        return {"holgado": holgado,
                "justo": max(2, holgado // 2),
                "apretado": 1}[self.modo()]
