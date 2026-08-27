"""Ventanas: minimizar, cambiar de una a otra, cerrar la de delante.

Distinto de `apps.py`: allí se ABRE y se CIERRA el programa; aquí se
maneja lo que ya está abierto.  "Cambia a Spotify" cuando Spotify lleva
una hora sonando no debe volver a lanzarlo.

Todo con `win32gui`, que ya estaba instalado.  Nada nuevo que instalar.
"""

from __future__ import annotations

import ctypes
import logging
import unicodedata

from .registry import Risk, Tool, ToolResult

log = logging.getLogger("nova.tools.ventanas")

_user32 = ctypes.windll.user32

# Constantes de la API de Windows, con nombre porque los números sueltos
# en medio del código no dicen nada.
_SW_RESTORE = 9
_SW_MINIMIZE = 6
_WM_CLOSE = 0x0010
_VK_LWIN = 0x5B
_VK_SHIFT = 0x10
_VK_M = 0x4D
_KEYUP = 0x0002

# Ventanas que existen pero que el usuario no considera ventanas.
_INVISIBLES = frozenset({
    "Program Manager",          # el escritorio
    "Windows Input Experience",
    "Configuración",
    "Microsoft Text Input Application",
    "NOVA",                     # su propio panel
})

# Procesos que SIEMPRE tienen una ventana visible con título y que nunca
# son lo que el usuario quiere. Se filtran por proceso y no por título
# porque el título lo ponen ellos y cambia: ApplicationFrameHost se
# titula con el nombre de la app UWP que aloja, así que por título
# parecería una app de verdad.
_PROCESOS_INVISIBLES = frozenset({
    "TextInputHost", "ApplicationFrameHost", "SystemSettings",
    "ShellExperienceHost", "StartMenuExperienceHost", "SearchHost",
    "LockApp", "python", "pythonw", "explorer",
})


def _sin_tildes(texto: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", (texto or "").lower())
        if unicodedata.category(c) != "Mn"
    )


def _abiertas() -> list[tuple[int, str]]:
    """Las ventanas de verdad: visibles, con título y no del sistema."""
    import win32gui

    encontradas: list[tuple[int, str]] = []

    def _mirar(hwnd: int, _) -> bool:  # noqa: ANN001
        if not win32gui.IsWindowVisible(hwnd):
            return True
        titulo = win32gui.GetWindowText(hwnd)
        if not titulo or titulo in _INVISIBLES:
            return True
        if _proceso_de(hwnd) in _PROCESOS_INVISIBLES:
            return True
        encontradas.append((hwnd, titulo))
        return True

    win32gui.EnumWindows(_mirar, None)
    return encontradas


def _proceso_de(hwnd: int) -> str:
    """El .exe dueño de la ventana, para poder decir «cambia a spotify».

    Hace falta porque el título casi nunca lleva el nombre del programa:
    Spotify se titula con la canción que suena, y el navegador con la
    pestaña. Buscar sólo por título fallaría justo en los casos normales.
    """
    try:
        import psutil

        pid = ctypes.c_ulong()
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return psutil.Process(pid.value).name().removesuffix(".exe")
    except Exception:  # noqa: BLE001
        return ""


def _buscar(nombre: str) -> tuple[int, str] | None:
    objetivo = _sin_tildes(nombre).strip()
    if not objetivo:
        return None
    ventanas = _abiertas()
    # Por nombre de proceso primero: es lo que la gente dice.
    for hwnd, titulo in ventanas:
        if objetivo in _sin_tildes(_proceso_de(hwnd)):
            return hwnd, titulo
    for hwnd, titulo in ventanas:
        if objetivo in _sin_tildes(titulo):
            return hwnd, titulo
    return None


def _al_frente(hwnd: int) -> bool:
    """Traer una ventana al frente, peleándose con Windows si hace falta.

    Windows sólo deja poner ventanas delante al proceso que tiene el
    foco, para que ningún programa te robe el teclado a mitad de frase.
    NOVA no lo tiene: el foco lo tiene el juego o el navegador.

    El truco conocido es engancharse al hilo de la ventana que manda
    ahora mismo (AttachThreadInput): durante ese rato Windows nos trata
    como si fuéramos ella. Se suelta siempre, incluso si falla.
    """
    import win32gui

    delante = _user32.GetForegroundWindow()
    hilo_suyo = _user32.GetWindowThreadProcessId(delante, None)
    hilo_mio = ctypes.windll.kernel32.GetCurrentThreadId()

    enganchado = False
    if hilo_suyo and hilo_suyo != hilo_mio:
        enganchado = bool(_user32.AttachThreadInput(hilo_suyo, hilo_mio, True))
    try:
        if win32gui.IsIconic(hwnd):
            _user32.ShowWindow(hwnd, _SW_RESTORE)
        _user32.BringWindowToTop(hwnd)
        _user32.SetForegroundWindow(hwnd)
    finally:
        if enganchado:
            _user32.AttachThreadInput(hilo_suyo, hilo_mio, False)
    return _user32.GetForegroundWindow() == hwnd


def _tecla_windows(*teclas: int) -> None:
    """Win + lo que sea. Pulsar y soltar en orden inverso, como una mano."""
    for t in teclas:
        _user32.keybd_event(t, 0, 0, 0)
    for t in reversed(teclas):
        _user32.keybd_event(t, 0, _KEYUP, 0)


# ── Herramientas ─────────────────────────────────────────────────────

def listar() -> ToolResult:
    ventanas = _abiertas()
    if not ventanas:
        return ToolResult(ok=True, message="No veo ninguna ventana abierta.")
    nombres = []
    for hwnd, titulo in ventanas[:12]:
        proceso = _proceso_de(hwnd)
        nombres.append(proceso or titulo[:40])
    # Sin repetir: seis pestañas de Chrome son seis ventanas y una sola
    # respuesta útil.
    unicos = list(dict.fromkeys(n for n in nombres if n))
    return ToolResult(
        ok=True,
        message="Tienes abierto: " + ", ".join(unicos) + ".",
        data={"ventanas": unicos},
    )


def cambiar_a(name: str) -> ToolResult:
    encontrada = _buscar(name)
    if encontrada is None:
        return ToolResult(ok=False, message=f"No tengo ninguna ventana de {name} abierta.")
    hwnd, titulo = encontrada
    if _al_frente(hwnd):
        return ToolResult(ok=True, message=f"Ahí lo tienes: {titulo[:60]}.")
    # Puede fallar si Windows se pone tonto con el foco. Decirlo, no
    # fingir que salió bien.
    return ToolResult(
        ok=False,
        message=f"Encontré {name} pero Windows no me deja ponerlo delante.",
    )


def minimizar(name: str = "", todas: bool = False) -> ToolResult:
    if todas:
        _tecla_windows(_VK_LWIN, _VK_M)
        return ToolResult(ok=True, message="Minimizado todo.")
    if not name:
        hwnd = _user32.GetForegroundWindow()
        if not hwnd:
            return ToolResult(ok=False, message="No hay ninguna ventana delante.")
        _user32.ShowWindow(hwnd, _SW_MINIMIZE)
        return ToolResult(ok=True, message="Minimizada.")
    encontrada = _buscar(name)
    if encontrada is None:
        return ToolResult(ok=False, message=f"No tengo ninguna ventana de {name} abierta.")
    _user32.ShowWindow(encontrada[0], _SW_MINIMIZE)
    return ToolResult(ok=True, message=f"{name} minimizado.")


def restaurar() -> ToolResult:
    _tecla_windows(_VK_LWIN, _VK_SHIFT, _VK_M)
    return ToolResult(ok=True, message="Restaurado.")


def cerrar_ventana() -> ToolResult:
    """Cierra la ventana de delante, educadamente.

    WM_CLOSE es el mismo mensaje que la X del título: si el programa
    tiene algo sin guardar, sale su propio diálogo. Por eso esto es
    MEDIUM y no DANGEROUS como `app.close`, que mata el proceso y se
    lleva por delante lo que hubiera sin guardar.
    """
    import win32gui

    hwnd = _user32.GetForegroundWindow()
    if not hwnd:
        return ToolResult(ok=False, message="No hay ninguna ventana delante.")
    titulo = win32gui.GetWindowText(hwnd) or "la ventana"
    if titulo in _INVISIBLES:
        return ToolResult(ok=False, message="Eso no es una ventana que pueda cerrar.")
    _user32.PostMessageW(hwnd, _WM_CLOSE, 0, 0)
    return ToolResult(ok=True, message=f"Cerrando {titulo[:50]}.")


def register(reg) -> None:  # noqa: ANN001
    reg.register(Tool(
        name="ventana.listar",
        description="Qué ventanas tiene abiertas ahora mismo",
        handler=listar,
        risk=Risk.SAFE,
        responde_sola=True,
    ))
    reg.register(Tool(
        name="ventana.cambiar",
        description="Trae al frente una app YA abierta. Si no lo está, usa app.open",
        handler=cambiar_a,
        schema={
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        },
        risk=Risk.MEDIUM,
    ))
    reg.register(Tool(
        name="ventana.minimizar",
        description="Minimiza una ventana; con todas=true, deja el escritorio a la vista",
        handler=minimizar,
        schema={
            "type": "object",
            "properties": {"name": {"type": "string"}, "todas": {"type": "boolean"}},
        },
        risk=Risk.MEDIUM,
    ))
    reg.register(Tool(
        name="ventana.restaurar",
        description="Devuelve a la vista las ventanas minimizadas",
        handler=restaurar,
        risk=Risk.MEDIUM,
    ))
    reg.register(Tool(
        name="ventana.cerrar",
        description="Cierra la ventana que tiene delante ahora mismo",
        handler=cerrar_ventana,
        risk=Risk.MEDIUM,
    ))
