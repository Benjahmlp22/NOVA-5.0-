"""Volumen: el general y el de cada aplicación por separado.

"Baja el volumen de Spotify" es una petición normal y NOVA4 no sabía
hacerla: sólo tenía el volumen maestro, y encima dependía de que
`nircmd.exe` estuviera instalado — un ejecutable de terceros que casi
nadie tiene.

Ahora se habla directamente con el mezclador de Windows (pycaw sobre
Core Audio), que es lo mismo que usa el panel de volumen del sistema. Sin
programas externos y sin depender de nada que se pueda desinstalar.

La gracia del volumen por aplicación es justo la que se pide en voz:
bajar el juego sin bajarte la música, o callar el navegador sin tocar
Discord.
"""

from __future__ import annotations

import logging
from difflib import SequenceMatcher

from .registry import Risk, Tool, ToolResult

log = logging.getLogger("nova.tools.audio")

# Palabras genéricas que no nombran un programa sino una CATEGORÍA. "Baja
# el navegador" no puede resolverse a un ejecutable fijo: aquí hay Opera,
# en otro PC habrá Chrome. Se busca cuál de la familia está sonando.
_CATEGORIAS = {
    "navegador": ("chrome", "opera", "firefox", "msedge", "brave", "vivaldi", "zen"),
    "explorador": ("chrome", "opera", "firefox", "msedge", "brave"),
    "musica": ("spotify", "musica", "itunes", "foobar2000", "aimp", "vlc"),
    "música": ("spotify", "musica", "itunes", "foobar2000", "aimp", "vlc"),
    "video": ("vlc", "mpc-hc", "potplayer"),
    "vídeo": ("vlc", "mpc-hc", "potplayer"),
}

# Procesos que suenan pero NO son "el juego" ni nada que el usuario
# quisiera bajar al decir algo genérico: utilidades, capturadoras, la voz
# del propio sistema.
_NO_SON_APPS = frozenset({
    "medalencoder", "obs64", "obs32", "nvcontainer", "audiodg",
    "xboxpcapp", "gamebar", "steamwebhelper", "discord",
    # Los lanzadores suenan (notificaciones, vídeos de la tienda) pero
    # cuando alguien dice "baja el juego" no se refiere a Steam.
    "steam", "epicgameslauncher", "eadesktop", "battle.net", "riotclient",
})

# Palabras que sobran al principio: "baja EL volumen de LA música".
_ARTICULOS = ("el ", "la ", "los ", "las ", "un ", "una ", "mi ")


def _sesiones():
    from pycaw.pycaw import AudioUtilities

    return [s for s in AudioUtilities.GetAllSessions() if s.Process]


def _volumen_de(sesion):  # noqa: ANN001
    from pycaw.pycaw import ISimpleAudioVolume

    return sesion._ctl.QueryInterface(ISimpleAudioVolume)  # noqa: SLF001


def _nombre_limpio(sesion) -> str:  # noqa: ANN001
    return (sesion.Process.name() or "").replace(".exe", "").lower()


def _buscar_sesion(nombre: str):  # noqa: ANN001
    """La aplicación que suena y que el usuario quiso decir.

    Cuatro intentos, de más seguro a menos: nombre exacto, categoría
    ("el navegador"), contenido ("star citizen" → starcitizen.exe) y
    parecido. El parecido va el último y con listón alto: confundirse de
    aplicación al bajar el volumen es peor que decir "no la encuentro".
    """
    sesiones = _sesiones()
    if not sesiones:
        return None, []

    objetivo = (nombre or "").strip().lower().replace(".exe", "")
    for articulo in _ARTICULOS:
        if objetivo.startswith(articulo):
            objetivo = objetivo[len(articulo):]
    objetivo = objetivo.strip()
    if not objetivo:
        return None, sesiones

    procesos = {_nombre_limpio(s): s for s in sesiones}

    # 1) Tal cual.
    if objetivo in procesos:
        return procesos[objetivo], sesiones

    # 2) Categoría: cuál de la familia está sonando.
    for familia in _CATEGORIAS.get(objetivo, ()):
        if familia in procesos:
            return procesos[familia], sesiones

    # 3) "el juego": lo que suena y no es utilidad, lanzador ni navegador.
    #    Un navegador suena tanto como un juego, pero nadie llama "el
    #    juego" a Opera.
    if objetivo in ("juego", "partida"):
        descartar = _NO_SON_APPS | set(_CATEGORIAS["navegador"]) | set(_CATEGORIAS["musica"])
        candidatos = [s for p, s in procesos.items() if p not in descartar]
        if len(candidatos) == 1:
            return candidatos[0], sesiones

    # 4) Contenido y parecido.
    compacto = objetivo.replace(" ", "")
    mejor, puntos = None, 0.0
    for proceso, s in procesos.items():
        if compacto and (compacto in proceso or proceso in compacto):
            return s, sesiones
        ratio = SequenceMatcher(None, compacto, proceso).ratio()
        if ratio > puntos:
            mejor, puntos = s, ratio
    return (mejor, sesiones) if puntos >= 0.7 else (None, sesiones)


def _listar(sesiones) -> str:  # noqa: ANN001
    nombres = sorted({_nombre_limpio(s) for s in sesiones})
    return ", ".join(n for n in nombres if n)


# ── Volumen general ──────────────────────────────────────────────────

def _a_porcentaje(valor) -> int | None:  # noqa: ANN001
    """Lo que mande el modelo, convertido a 0-100. None si no hay forma.

    Hace falta porque el modelo no respeta el esquema. Visto de verdad:
    pidiéndole "baja el volumen de Spotify" mandó `level="0.5"`, y el
    `int()` de antes reventaba con "invalid literal for int()". NOVA
    contestaba con el error de Python en alto.

    Un decimal entre 0 y 1 es una fracción y vale por su tanto por
    ciento: 0.5 son 50. Un entero es ya el porcentaje. La diferencia
    entre "1" (uno por ciento) y "1.0" (todo) sale de si trae coma, que
    es la única pista que hay.
    """
    if valor is None:
        return None
    texto = str(valor).strip().replace("%", "").replace(",", ".")
    try:
        numero = float(texto)
    except ValueError:
        return None
    if 0 < numero <= 1 and "." in texto:
        numero *= 100
    return max(0, min(100, round(numero)))


def volumen_general(level: int) -> ToolResult:
    """Sube o baja el volumen maestro de Windows."""
    nivel = _a_porcentaje(level)
    if nivel is None:
        return ToolResult(ok=False, message="¿A qué volumen lo pongo, del cero al cien?")
    try:
        from comtypes import CLSCTX_ALL
        from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume

        altavoces = AudioUtilities.GetSpeakers()
        interfaz = altavoces.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)  # noqa: SLF001
        interfaz.QueryInterface(IAudioEndpointVolume).SetMasterVolumeLevelScalar(
            nivel / 100.0, None
        )
    except Exception as exc:  # noqa: BLE001
        log.debug("no pude cambiar el volumen general", exc_info=True)
        return ToolResult(ok=False, message=f"No pude cambiar el volumen: {exc}")
    return ToolResult(ok=True, message=f"Volumen al {nivel}%.")


# ── Volumen por aplicación ───────────────────────────────────────────

def volumen_app(name: str, level: int) -> ToolResult:
    """Cambia el volumen de UNA aplicación, sin tocar las demás."""
    nivel = _a_porcentaje(level)
    if nivel is None:
        return ToolResult(ok=False, message=f"¿A qué volumen pongo {name}, del cero al cien?")
    try:
        sesion, sesiones = _buscar_sesion(name)
    except Exception as exc:  # noqa: BLE001
        log.debug("no pude leer el mezclador", exc_info=True)
        return ToolResult(ok=False, message=f"No pude leer el mezclador de Windows: {exc}")

    if not sesiones:
        return ToolResult(ok=False, message="Ahora mismo no hay ninguna aplicación sonando.")
    if sesion is None:
        # Decir qué SÍ está sonando ahorra el siguiente turno entero.
        return ToolResult(
            ok=False,
            message=f"No encuentro «{name}» entre lo que está sonando. "
                    f"Ahora suenan: {_listar(sesiones)}.",
        )

    try:
        _volumen_de(sesion).SetMasterVolume(nivel / 100.0, None)
    except Exception as exc:  # noqa: BLE001
        return ToolResult(ok=False, message=f"No pude cambiar el volumen de {name}: {exc}")
    return ToolResult(ok=True, message=f"{_nombre_limpio(sesion).title()} al {nivel}%.",
                      data={"app": _nombre_limpio(sesion), "nivel": nivel})


def silenciar_app(name: str, silenciar: bool = True) -> ToolResult:
    """Silencia o devuelve el sonido a una aplicación concreta."""
    try:
        sesion, sesiones = _buscar_sesion(name)
    except Exception as exc:  # noqa: BLE001
        return ToolResult(ok=False, message=f"No pude leer el mezclador: {exc}")

    if not sesiones:
        return ToolResult(ok=False, message="Ahora mismo no hay ninguna aplicación sonando.")
    if sesion is None:
        return ToolResult(
            ok=False,
            message=f"No encuentro «{name}» sonando. Ahora suenan: {_listar(sesiones)}.",
        )
    try:
        _volumen_de(sesion).SetMute(bool(silenciar), None)
    except Exception as exc:  # noqa: BLE001
        return ToolResult(ok=False, message=f"No pude silenciar {name}: {exc}")

    verbo = "silenciado" if silenciar else "devuelto el sonido a"
    return ToolResult(ok=True, message=f"He {verbo} {_nombre_limpio(sesion).title()}.")


def que_suena() -> ToolResult:
    """Qué aplicaciones están reproduciendo audio y a qué volumen."""
    try:
        sesiones = _sesiones()
    except Exception as exc:  # noqa: BLE001
        return ToolResult(ok=False, message=f"No pude leer el mezclador: {exc}")

    if not sesiones:
        return ToolResult(ok=True, message="Ahora mismo no suena nada.")

    partes = []
    for s in sesiones:
        try:
            vol = _volumen_de(s)
            nivel = int(round(vol.GetMasterVolume() * 100))
            estado = "silenciado" if vol.GetMute() else f"al {nivel}%"
        except Exception:  # noqa: BLE001
            estado = "sonando"
        partes.append(f"{_nombre_limpio(s).title()} {estado}")
    return ToolResult(ok=True, message="Suenan: " + ", ".join(partes) + ".")


def register(reg) -> None:  # noqa: ANN001
    reg.register(Tool(
        name="pc.volume",
        description="Ajusta el volumen general del sistema (0-100)",
        handler=volumen_general,
        schema={
            "type": "object",
            "properties": {"level": {"type": "integer"}},
            "required": ["level"],
        },
        risk=Risk.MEDIUM,
    ))
    reg.register(Tool(
        name="app.volume",
        description="Volumen de UNA app concreta: «baja Spotify», «sube el juego»",
        handler=volumen_app,
        schema={
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "level": {"type": "integer"},
            },
            "required": ["name", "level"],
        },
        risk=Risk.MEDIUM,
    ))
    reg.register(Tool(
        name="app.mute",
        description="Silencia una app concreta, o le devuelve el sonido",
        handler=silenciar_app,
        schema={
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "silenciar": {"type": "boolean"},
            },
            "required": ["name"],
        },
        risk=Risk.MEDIUM,
    ))
    reg.register(Tool(
        name="pc.playing",
        description="Qué apps suenan ahora y a qué volumen",
        handler=que_suena,
        risk=Risk.SAFE,
        responde_sola=True,
    ))
