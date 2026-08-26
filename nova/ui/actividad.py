"""Traducir lo que hace NOVA a algo que se lee de un vistazo.

La barra de actividad tiene que responder a "¿qué está haciendo?" sin que
haya que leer una frase entera.  Por eso cada acción lleva un tipo —que
decide el color— y un texto corto en español.

Vive aparte del panel porque no es dibujo: es la traducción de un nombre
de herramienta a lenguaje humano, y eso se puede probar sin montar Qt.
"""

from __future__ import annotations

# Nombre de herramienta → (tipo para el color, plantilla del texto).
# El tipo importa tanto como el texto: abrir Discord y cerrar Discord no
# pueden parecer lo mismo mirando de reojo.
_ACCIONES = {
    "app.open": ("abrir", "Abriendo {}"),
    "app.close": ("cerrar", "Cerrando {}"),
    "app.find": ("buscar", "Buscando la app {}"),
    "app.refresh_index": ("sistema", "Revisando qué tienes instalado"),
    "web.search": ("buscar", "Buscando en internet: {}"),
    "web.open": ("buscar", "Abriendo en el navegador {}"),
    "file.create": ("archivo", "Creando {}"),
    "folder.create": ("archivo", "Creando la carpeta {}"),
    "file.read": ("archivo", "Leyendo {}"),
    "file.delete": ("cerrar", "Borrando {}"),
    "workspace.list": ("archivo", "Mirando tu carpeta de trabajo"),
    "screen.capture": ("archivo", "Capturando la pantalla"),
    "memory.remember": ("memoria", "Guardando lo que me dijiste"),
    "memory.recall": ("memoria", "Recordando lo que sé de ti"),
    "memory.forget": ("cerrar", "Olvidando {}"),
    "pc.status": ("sistema", "Mirando cómo va el PC"),
    "pc.hardware": ("sistema", "Mirando tu hardware"),
    "pc.active_window": ("sistema", "Mirando qué tienes delante"),
    "pc.running_apps": ("sistema", "Mirando qué tienes abierto"),
    "pc.network": ("sistema", "Comprobando la conexión"),
    "pc.volume": ("sistema", "Ajustando el volumen"),
}

MAX_DETALLE = 34


def describir(herramienta: str, argumento: str = "") -> tuple[str, str]:
    """Devuelve (tipo, texto) para la barra de actividad."""
    tipo, plantilla = _ACCIONES.get(herramienta, ("sistema", "Usando {}"))
    dato = (argumento or "").strip()
    if len(dato) > MAX_DETALLE:
        dato = dato[:MAX_DETALLE].rstrip() + "…"

    if "{}" not in plantilla:
        return tipo, plantilla
    if not dato:
        # Sin dato, la plantilla quedaría coja ("Abriendo "). Se recorta
        # al verbo antes que enseñar una frase a medias.
        return tipo, plantilla.replace(" {}", "").replace("{}", "").strip() or herramienta
    return tipo, plantilla.format(dato)


def dato_relevante(argumentos: dict) -> str:
    """El argumento que el usuario reconocería, de todos los que haya."""
    if not isinstance(argumentos, dict):
        return ""
    for clave in ("name", "query", "path", "url", "text", "command"):
        valor = argumentos.get(clave)
        if isinstance(valor, str) and valor.strip():
            return valor.strip()
    return ""
